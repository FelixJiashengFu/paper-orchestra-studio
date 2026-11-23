"""Scales an image copy down to fit a platform's limits instead of dropping it.

Figures are often saved at 300 dpi (2000-3000+ px per side, 5+ megapixels).
Several platforms reject such images with a 400 (e.g. LongCat: "Image total
pixels exceeds maximum allowed size (5242880px)"); the old fallback then
resent the request without the image, so the model never saw the figure.

`fit_image` returns the original bytes when they already fit, otherwise a
proportionally scaled copy (same format where possible, JPEG if still too
many bytes). Only the copy that is sent changes; figure files are untouched.
Any failure (no Pillow, unreadable image) returns the original bytes, so the
API's own answer and the existing retry logic still decide.
"""

import base64
import io
import os
import math
import re
import threading
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

# Pixel budgets are aimed this far under the stated limit, so rounding of
# the new width/height can never land a few pixels over it.
_PIXEL_MARGIN = 0.98


@dataclass(frozen=True)
class ImageLimits:
    """Per-image limits; None = no limit known."""

    max_pixels: Optional[int] = None  # width * height
    max_edge: Optional[int] = None  # longest side, px
    max_bytes: Optional[int] = None  # raw (decoded) bytes of one image

    def any(self) -> bool:
        return bool(self.max_pixels or self.max_edge or self.max_bytes)

    def tighter(self, other: "ImageLimits") -> "ImageLimits":
        def pick(a, b):
            vals = [v for v in (a, b) if v]
            return min(vals) if vals else None

        return ImageLimits(
            max_pixels=pick(self.max_pixels, other.max_pixels),
            max_edge=pick(self.max_edge, other.max_edge),
            max_bytes=pick(self.max_bytes, other.max_bytes),
        )

    def describe(self) -> str:
        bits = []
        if self.max_pixels:
            bits.append(f"{self.max_pixels:,} px")
        if self.max_edge:
            bits.append(f"{self.max_edge} px per side")
        if self.max_bytes:
            bits.append(f"{self.max_bytes / 1_000_000:.1f} MB")
        return ", ".join(bits) or "no limit"


def fit_image(
    img_bytes: bytes,
    max_edge: Optional[int] = None,
    max_pixels: Optional[int] = None,
    max_bytes: Optional[int] = None,
    label: str = "",
    target_name: str = "",
    quiet: bool = False,
) -> bytes:
    """Returns `img_bytes` unchanged when within all given limits, else a
    proportionally scaled copy. `label` is the platform shown in the log line
    (" >> [label] Image WxH scaled to ..."), `target_name` an optional
    " for <name>" suffix."""
    if not (max_edge or max_pixels or max_bytes):
        return img_bytes
    try:
        from PIL import Image, ImageOps  # matplotlib depends on Pillow
    except ImportError:
        return img_bytes
    tag = f" >> [{label}] " if label else " >> "
    suffix = f" for {target_name}" if target_name else ""
    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            w, h = im.size
            edge_ok = not max_edge or max(w, h) <= max_edge
            pixels_ok = not max_pixels or w * h <= max_pixels
            bytes_ok = not max_bytes or len(img_bytes) <= max_bytes
            if edge_ok and pixels_ok and bytes_ok:
                return img_bytes
            fmt = (im.format or "PNG").upper()
            is_jpeg = fmt in ("JPEG", "JPG", "MPO")  # MPO: a JPEG with extra frames (phone photos)
            if getattr(im, "is_animated", False) and fmt != "MPO":
                return img_bytes

            # Long edge of the copy: the tightest of the edge and pixel limits.
            target = max(w, h)
            if max_edge:
                target = min(target, max_edge)
            if max_pixels and w * h > max_pixels:
                k = math.sqrt(max_pixels * _PIXEL_MARGIN / float(w * h))
                target = min(target, max(1, int(max(w, h) * k)))
            if is_jpeg and target * 2 <= max(w, h):
                im.draft("RGB", (max(1, w * target // max(w, h)), max(1, h * target // max(w, h))))
            has_alpha = "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info)
            # Re-encoding drops EXIF, so apply its rotation to the pixels first.
            src = ImageOps.exif_transpose(im) if im.getexif().get(0x0112, 1) != 1 else im
            if src.mode.startswith("I;16") or src.mode in ("I", "F"):
                # 16/32-bit grayscale: convert("RGB") would clip values to 0..255
                # (an almost white picture); rescale to 8 bit first.
                gray = src.convert("F")
                lo, hi = gray.getextrema()
                if src.mode.startswith("I;16"):
                    lo, hi = 0.0, 65535.0
                span = (hi - lo) or 1.0
                img = gray.point(lambda v: (v - lo) * 255.0 / span).convert("L").convert("RGB")
            else:
                mode = "RGBA" if has_alpha else "RGB"
                img = src if src.mode == mode else src.convert(mode)
            resample = getattr(Image, "Resampling", Image).LANCZOS

            def shrink_to(edge: int):
                sw, sh = img.size
                if max(sw, sh) <= edge:
                    return img
                k = edge / float(max(sw, sh))
                return img.resize((max(1, round(sw * k)), max(1, round(sh * k))), resample, reducing_gap=3.0)

            def encode(pic, as_jpeg: bool, quality: int = 90) -> bytes:
                out = io.BytesIO()
                if as_jpeg:
                    flat = pic
                    if pic.mode == "RGBA":
                        flat = Image.new("RGB", pic.size, (255, 255, 255))
                        flat.paste(pic, mask=pic.getchannel("A"))
                    elif pic.mode != "RGB":
                        flat = pic.convert("RGB")
                    flat.save(out, format="JPEG", quality=quality)
                elif fmt == "WEBP":
                    pic.save(out, format="WEBP", quality=quality)
                else:
                    pic.save(out, format="PNG")
                return out.getvalue()

            def fits_pixels(pic) -> bool:
                return not max_pixels or pic.size[0] * pic.size[1] <= max_pixels

            pic = shrink_to(target)
            for _ in range(4):  # rounding guard: never a few pixels over the limit
                if fits_pixels(pic) or max(pic.size) <= 1:
                    break
                target = max(1, int(max(pic.size) * 0.99))
                pic = shrink_to(target)
            data = encode(pic, is_jpeg)
            if max_bytes and len(data) > max_bytes and not is_jpeg:
                data = encode(pic, True)
            # Still too large (noisy heatmaps, photos): smaller and lower quality until it fits.
            if max_bytes:
                edge, quality = max(pic.size), 85
                for _ in range(8):
                    if len(data) <= max_bytes:
                        break
                    edge = max(200, int(edge * 0.8))
                    pic = shrink_to(edge)
                    data = encode(pic, True, quality)
                    quality = max(60, quality - 10)
                if len(data) > max_bytes:
                    return img_bytes  # let the API's own answer (and the text-only retry) decide
            if not quiet:
                if pic.size == (w, h):
                    print(f"{tag}Image {w}x{h} re-encoded{suffix} "
                          f"({len(img_bytes) // 1000} KB -> {len(data) // 1000} KB).")
                else:
                    reasons = []
                    if max_pixels and w * h > max_pixels:
                        reasons.append(f"limit {max_pixels:,} px per image")
                    if max_edge and max(w, h) > max_edge:
                        reasons.append(f"limit {max_edge} px per side")
                    if not reasons and max_bytes:
                        reasons.append(f"to fit the {max_bytes / 1_000_000:.1f} MB limit")
                    why = "; ".join(reasons)
                    print(f"{tag}Image {w}x{h} scaled to {pic.size[0]}x{pic.size[1]}{suffix} ({why}).")
            return data
    except Image.DecompressionBombError:
        if not quiet:
            print(f"{tag}Image is too large to scale down here (Pillow's pixel limit); sent as is.")
        return img_bytes
    except Exception:
        return img_bytes


# All images of one request together, raw bytes (about 16 MB once base64
# encoded). A dozen full-page screenshots at 2448x3168 (~24 MB) stalled an
# upload to a Chinese platform for over an hour (2026-11-19). Override with
# PO_IMAGE_REQUEST_MAX_MB (0 = no limit) or a platform's image_max_total_bytes.
DEFAULT_REQUEST_IMAGE_BYTES = 12_000_000


def request_image_budget(provider_value: Optional[int] = None) -> Optional[int]:
    """The per-request image byte budget: the platform's own value, else
    PO_IMAGE_REQUEST_MAX_MB, else DEFAULT_REQUEST_IMAGE_BYTES (None = none)."""
    if provider_value:
        return provider_value
    env = os.getenv("PO_IMAGE_REQUEST_MAX_MB", "").strip()
    if env:
        try:
            mb = float(env)
            return int(mb * 1_000_000) if mb > 0 else None
        except ValueError:
            pass
    return DEFAULT_REQUEST_IMAGE_BYTES


def fit_with_limits(img_bytes: bytes, limits: ImageLimits, label: str = "", n_images: int = 1,
                    total_pixels: Optional[int] = None, byte_share: Optional[int] = None) -> bytes:
    """`fit_image` with an ImageLimits; a per-request pixel budget
    (`total_pixels`) is shared evenly by the request's `n_images` images, and
    `byte_share` caps this image's bytes (its share of a request budget)."""
    max_pixels = limits.max_pixels
    if total_pixels:
        share = total_pixels // max(1, n_images)
        max_pixels = min(max_pixels, share) if max_pixels else share
    max_bytes = limits.max_bytes
    if byte_share:
        max_bytes = min(max_bytes, byte_share) if max_bytes else byte_share
    return fit_image(img_bytes, max_edge=limits.max_edge, max_pixels=max_pixels,
                     max_bytes=max_bytes, label=label)


# --------------------------------------------------------------------------
# OpenAI-style content (image_url data URLs)
# --------------------------------------------------------------------------

_DATA_URL_RE = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.*)$", re.DOTALL)


def _mime_of(raw: bytes, fallback: str) -> str:
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return fallback


def count_data_url_images(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")


def fit_data_url_images(content: Any, limits: ImageLimits, label: str = "",
                        total_pixels: Optional[int] = None, total_bytes: Optional[int] = None) -> Any:
    """Returns `content` with every base64 image_url part fitted to `limits`
    (a new list; parts that already fit are reused as they are). Remote
    http(s) image URLs are left alone. When the images together are over
    `total_bytes`, each one gets an even share of it."""
    if not isinstance(content, list) or not (limits.any() or total_pixels or total_bytes):
        return content
    n = count_data_url_images(content)
    if not n:
        return content
    byte_share = None
    if total_bytes:
        total = sum(s[2] for s in data_url_image_sizes(content))
        if total > total_bytes:
            byte_share = max(50_000, total_bytes // n)
            if label:
                print(f" >> [{label}] {n} images, {total / 1_000_000:.1f} MB together: scaling them to about "
                      f"{total_bytes / 1_000_000:.0f} MB so the upload does not stall.")
    if not (limits.any() or total_pixels or byte_share):
        return content
    out: List[Any] = []
    for part in content:
        url = None
        if isinstance(part, dict) and part.get("type") == "image_url":
            iu = part.get("image_url")
            url = iu.get("url") if isinstance(iu, dict) else iu if isinstance(iu, str) else None
        m = _DATA_URL_RE.match(url) if isinstance(url, str) else None
        if not m:
            out.append(part)
            continue
        try:
            raw = base64.b64decode(m.group(2))
        except Exception:
            out.append(part)
            continue
        fitted = fit_with_limits(raw, limits, label=label, n_images=n, total_pixels=total_pixels,
                                 byte_share=byte_share)
        if fitted is raw:
            out.append(part)
            continue
        new_url = f"data:{_mime_of(fitted, m.group(1))};base64,{base64.b64encode(fitted).decode('ascii')}"
        new_part = dict(part)
        iu = part.get("image_url")
        new_part["image_url"] = {**iu, "url": new_url} if isinstance(iu, dict) else {"url": new_url}
        out.append(new_part)
    return out


def data_url_image_sizes(content: Any) -> List[Tuple[int, int, int]]:
    """(width, height, raw bytes) of each base64 image in `content`; images
    that cannot be read are skipped."""
    sizes: List[Tuple[int, int, int]] = []
    if not isinstance(content, list):
        return sizes
    try:
        from PIL import Image
    except ImportError:
        return sizes
    for part in content:
        if not (isinstance(part, dict) and part.get("type") == "image_url"):
            continue
        iu = part.get("image_url")
        url = iu.get("url") if isinstance(iu, dict) else iu
        m = _DATA_URL_RE.match(url) if isinstance(url, str) else None
        if not m:
            continue
        try:
            raw = base64.b64decode(m.group(2))
            with Image.open(io.BytesIO(raw)) as im:
                sizes.append((im.size[0], im.size[1], len(raw)))
        except Exception:
            continue
    return sizes


# --------------------------------------------------------------------------
# Limits learned from errors ("... exceeds maximum allowed size (5242880px)")
# --------------------------------------------------------------------------

_learned: Dict[Tuple[str, str], ImageLimits] = {}
_learned_lock = threading.Lock()

# Words that on their own say an error is about the image's size...
_SIZE_HINTS = (
    "too large", "too big", "exceed", "larger than", "greater than", "too many pixels", "resolution",
    "dimension", "file size", "image size",
    "过大", "超过", "超出", "太大", "分辨率", "尺寸",
)
# ...and weaker ones that count only next to a stated limit ("max 4096 px").
_WEAK_SIZE_HINTS = ("maximum", "max ", "limit", "上限", "最大")
_NUM = r"(\d[\d,_]*(?:\.\d+)?)"
# "5242880px", "5.2M pixels", "36 megapixels", "5000万像素"
_PX_RE = re.compile(_NUM + r"\s*(k|m|mega|万|百万)?\s*(?:px|pixels?|像素)", re.IGNORECASE)
_MP_RE = re.compile(_NUM + r"\s*(?:mp|megapixels?)\b", re.IGNORECASE)
_WXH_RE = re.compile(r"(\d{2,5})\s*[x×*]\s*(\d{2,5})")
# Byte sizes only: "10MB", "7.5 MiB", "500 KB", "20M" (not "128k tokens" / "5.2M pixels").
_BYTES_RE = re.compile(
    _NUM + r"\s*(mib|mb|kib|kb|bytes?|字节|[mk]\b(?!\s*(?:tokens?|pixels?|px|images?|像素|张|个)))",
    re.IGNORECASE)
_PX_MULT = {"k": 1000, "m": 1_000_000, "mega": 1_000_000, "万": 10_000, "百万": 1_000_000}
_ACTUAL_RE = re.compile(r"(actual|got|received|current|your|实际|当前|上传)", re.IGNORECASE)


def _num(s: str) -> float:
    return float(s.replace(",", "").replace("_", ""))


def _limit_numbers(regex: re.Pattern, msg: str) -> List[float]:
    """Numbers matched by `regex`, minus those labelled as the actual value
    ("actual: 5434593px")."""
    vals = []
    for m in regex.finditer(msg):
        before = msg[max(0, m.start() - 20):m.start()]
        if _ACTUAL_RE.search(before):
            continue
        vals.append(m)
    return vals


def image_limits_from_error(err: Exception, sizes: List[Tuple[int, int, int]]) -> Optional[ImageLimits]:
    """Limits implied by a 4xx error about image size, or None when the
    error is not about image size. `sizes` are the (w, h, bytes) of the
    images that were sent; when the error names no number, the next try aims
    at about half the pixels of the largest image (see size_error_limits)."""
    found = size_error_limits(err, sizes)
    return found[0] if found else None


def size_error_limits(err: Exception, sizes: List[Tuple[int, int, int]]) -> Optional[Tuple[ImageLimits, bool]]:
    """(limits, stated) for a 4xx error about image size, else None.
    stated=False: the error names no limit and the limits are a guess."""
    if getattr(err, "status_code", None) not in (400, 413, 422) or not sizes:
        return None
    msg = str(err)
    low = msg.lower()
    about_image = any(k in low for k in ("image", "img", "picture", "pixel", "图片", "图像", "像素"))
    strong = any(k in low for k in _SIZE_HINTS)
    if not about_image or not (strong or any(k in low for k in _WEAK_SIZE_HINTS)):
        return None
    max_pixels = max_edge = max_bytes = None

    px = [_num(m.group(1)) * _PX_MULT.get((m.group(2) or "").lower(), 1)
          for m in _limit_numbers(_PX_RE, msg)]
    px += [_num(m.group(1)) * 1_000_000 for m in _limit_numbers(_MP_RE, msg)]
    big = [v for v in px if v >= 20_000]
    small = [v for v in px if 16 <= v < 20_000]
    if big:
        max_pixels = int(min(big))
    if small:
        # "max 4096 px" / "width must be <= 2048 pixels": an edge limit, unless
        # the text clearly says it is a minimum.
        if not re.search(r"(at least|minimum|min\b|不小于|至少|最小)", low):
            max_edge = int(min(small))
    if max_pixels is None and max_edge is None:
        dims = [m for m in _WXH_RE.finditer(msg)
                if not _ACTUAL_RE.search(msg[max(0, m.start() - 20):m.start()])]
        if dims:
            d = min(dims, key=lambda m: int(m.group(1)) * int(m.group(2)))
            a, b = int(d.group(1)), int(d.group(2))
            biggest = max(sizes, key=lambda s: s[0] * s[1])
            if max(biggest[0], biggest[1]) > max(a, b):
                max_edge = max(a, b)
            else:
                max_pixels = a * b

    mult = {"m": 1_000_000, "mb": 1_000_000, "mib": 1_048_576, "k": 1000, "kb": 1000, "kib": 1024,
            "byte": 1, "bytes": 1, "字节": 1}
    byte_vals = []
    for m in _limit_numbers(_BYTES_RE, msg):
        unit = m.group(2).lower()
        byte_vals.append(_num(m.group(1)) * mult.get(unit, 1))
    byte_vals = [v for v in byte_vals if v >= 50_000]
    if byte_vals:
        # The stated limit usually counts base64 text: 3/4 of it is raw bytes.
        max_bytes = int(min(byte_vals) * 0.74)

    if max_pixels or max_edge or max_bytes:
        return ImageLimits(max_pixels=max_pixels, max_edge=max_edge, max_bytes=max_bytes), True
    if not strong:  # "maximum" / "limit" without a number: not clearly about size
        return None
    biggest = max(sizes, key=lambda s: s[0] * s[1])
    return ImageLimits(max_pixels=max(200 * 200, int(biggest[0] * biggest[1] * 0.5))), False


def remember_limits(provider: str, model: str, limits: ImageLimits) -> ImageLimits:
    """Stores limits for (provider, model) for the rest of the run (call it
    once a resend at these limits went through) and returns the combined
    (tightest) limits."""
    key = (provider, model)
    with _learned_lock:
        cur = _learned.get(key)
        merged = cur.tighter(limits) if cur else limits
        _learned[key] = merged
        return merged


def learned_limits(provider: str, model: str) -> Optional[ImageLimits]:
    with _learned_lock:
        return _learned.get((provider, model))


def shrink_more(limits: ImageLimits, sizes: List[Tuple[int, int, int]]) -> ImageLimits:
    """A tighter pixel budget than what was just sent (for a repeated size
    error): about half the pixels of the largest image."""
    if not sizes:
        return limits
    biggest = max(s[0] * s[1] for s in sizes)
    return replace(limits, max_pixels=min(limits.max_pixels or biggest, max(200 * 200, int(biggest * 0.5))))
