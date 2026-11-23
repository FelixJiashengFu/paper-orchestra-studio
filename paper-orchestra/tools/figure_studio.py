#!/usr/bin/env python
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Interactive Figure Review Bench, Region-Circle Local Edit, & 3-Route Plotting CLI.

Outputs ONE JSON object on stdout (the last line) following tools/submission_tools.py:
  python tools/figure_studio.py list          --dir DIR
  python tools/figure_studio.py ensure-bundle --dir DIR --image PATH [--title TITLE]
  python tools/figure_studio.py revise        --bundle DIR --instruction TEXT [--bbox x,y,w,h] [--model auto] [--base-dir DIR]
  python tools/figure_studio.py restore       --bundle DIR --version vN [--base-dir DIR]
  python tools/figure_studio.py create        --out-dir DIR --mode data_code|schematic_svg|concept_image --prompt TEXT [...]
"""

import argparse
import base64
import contextlib
from datetime import datetime, timezone
import io
import json
import os
import re
import shutil
import sys
import traceback
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils import usage_tracker  # noqa: E402
from utils.providers import install_output_redaction  # noqa: E402


_REAL_STDOUT = sys.stdout
_EVENT_PREFIX = "@@PO_STUDIO_EVENT "


class StudioError(Exception):
    pass


def emit(payload: Dict[str, Any]) -> None:
    target = sys.stdout if sys.stdout is not sys.stderr else _REAL_STDOUT
    print(json.dumps(payload, ensure_ascii=False), file=target, flush=True)


def _est_tokens(text: str) -> int:
    return max(1, int(len(text) / 2.8)) if text else 0


def _emit_event(kind: str, **fields: Any) -> None:
    try:
        payload: Dict[str, Any] = {
            "kind": kind,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        print(
            _EVENT_PREFIX + json.dumps(payload, ensure_ascii=False),
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        pass


def _emit_progress(
    stage: str,
    status: str,
    message: str,
    *,
    model: str = "",
    detail: str = "",
    est_input_tokens: int = 0,
) -> None:
    fields: Dict[str, Any] = {
        "stage": stage,
        "status": status,
        "message": message,
    }
    if model:
        fields["model"] = model
    if detail:
        fields["detail"] = detail
    if est_input_tokens > 0:
        fields["estInputTokens"] = int(est_input_tokens)
    _emit_event("progress", **fields)


@contextlib.contextmanager
def _track_studio_usage(work_dir: str):
    """Binds usage_tracker to work_dir and emits live token updates on stderr."""
    os.makedirs(work_dir, exist_ok=True)
    usage_tracker.begin_run(work_dir)

    def _on_usage(rep: Dict[str, Any]) -> None:
        _emit_event("usage", usage=rep)

    usage_tracker.add_listener(_on_usage)
    holder: Dict[str, Any] = {"final": None}
    try:
        _emit_event("usage", usage=usage_tracker.report(work_dir))
        yield holder
    finally:
        usage_tracker.remove_listener(_on_usage)
        final_rep = usage_tracker.report(work_dir)
        usage_tracker.end_run(work_dir)
        holder["final"] = final_rep
        _emit_event("usage", usage=final_rep)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip().lower()).strip("_")
    return base[:48] or "figure"


def _parse_bbox(raw: Optional[str]) -> Optional[Dict[str, float]]:
    if not raw or not raw.strip():
        return None
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise StudioError("框选坐标 --bbox 格式应为 x,y,w,h（0~1 归一化数值）。")
    try:
        x, y, w, h = (float(p) for p in parts)
    except ValueError as exc:
        raise StudioError(f"框选坐标无效：{raw}") from exc
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.01, min(1.0 - x, w))
    h = max(0.01, min(1.0 - y, h))
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


def _annotate_bbox_png(img_path: str, bbox: Dict[str, float]) -> Tuple[bytes, str]:
    """Draws a crisp red bounding box onto an in-memory PNG copy of `img_path`."""
    with Image.open(img_path) as im:
        rgb = im.convert("RGB")
        width, height = rgb.size
        left = int(round(bbox["x"] * width))
        top = int(round(bbox["y"] * height))
        right = int(round((bbox["x"] + bbox["w"]) * width))
        bottom = int(round((bbox["y"] + bbox["h"]) * height))
        left = max(0, min(width - 2, left))
        top = max(0, min(height - 2, top))
        right = max(left + 2, min(width - 1, right))
        bottom = max(top + 2, min(height - 1, bottom))
        draw = ImageDraw.Draw(rgb)
        stroke = max(3, min(width, height) // 140)
        for off in range(stroke):
            draw.rectangle(
                [left + off, top + off, max(left + off, right - off), max(top + off, bottom - off)],
                outline=(220, 38, 38),
            )
        tag_h = 18
        tag_y0 = max(0, top - tag_h)
        draw.rectangle([left, tag_y0, min(width, left + 96), tag_y0 + tag_h], fill=(220, 38, 38))
        draw.text((left + 4, tag_y0 + 3), "EDIT REGION", fill=(255, 255, 255))
        buf = io.BytesIO()
        rgb.save(buf, format="PNG")
        desc = (
            f"Normalized region x={bbox['x']:.3f}, y={bbox['y']:.3f}, w={bbox['w']:.3f}, h={bbox['h']:.3f} "
            f"(pixels [{left}, {top}, {right}, {bottom}] on {width}x{height} image, marked with red rectangle 'EDIT REGION')."
        )
        return buf.getvalue(), desc


def _sync_target_image(src_img: str, manifest: Dict[str, Any], bundle_dir: str, base_dir: Optional[str]) -> List[str]:
    """Copies the active version image to target_rel_path and matching pipeline figure folders."""
    target_rel = manifest.get("target_rel_path") or ""
    if not base_dir:
        base_dir = os.path.abspath(os.path.join(bundle_dir, "..", "..", ".."))
    synced: List[str] = []
    if not os.path.isfile(src_img) or not target_rel:
        return synced
    target_abs = os.path.join(base_dir, target_rel)
    basename = os.path.basename(target_abs)
    candidates = [target_abs]
    for sub in ("latex_writeup/figures", "content_refinement_workdir/figures", "raw_materials/figures"):
        sub_dir = os.path.join(base_dir, sub)
        if os.path.isdir(sub_dir):
            candidates.append(os.path.join(sub_dir, basename))

    for dst in candidates:
        if os.path.abspath(dst) == os.path.abspath(src_img):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        src_ext = os.path.splitext(src_img)[1].lower()
        dst_ext = os.path.splitext(dst)[1].lower()
        if src_ext == dst_ext or dst_ext == "":
            shutil.copy2(src_img, dst)
        else:
            with Image.open(src_img) as im:
                if dst_ext in (".jpg", ".jpeg"):
                    im.convert("RGB").save(dst, format="JPEG", quality=95)
                else:
                    im.save(dst)
        synced.append(dst)
    return synced


def _enrich_bundle(bundle_dir: str, base_dir: str) -> Dict[str, Any]:
    manifest_path = os.path.join(bundle_dir, "manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    active = manifest.get("active_version") or "v1"
    versions = manifest.get("versions") or []
    enriched_versions = []
    active_image = ""
    active_svg = ""
    for v in versions:
        v_copy = dict(v)
        img_rel = v_copy.get("image_rel") or ""
        abs_img = os.path.join(bundle_dir, img_rel) if img_rel else ""
        v_copy["abs_image_path"] = abs_img if abs_img and os.path.isfile(abs_img) else ""
        svg_rel = v_copy.get("svg_rel") or ""
        abs_svg = os.path.join(bundle_dir, svg_rel) if svg_rel else ""
        v_copy["abs_svg_path"] = abs_svg if abs_svg and os.path.isfile(abs_svg) else ""
        code_rel = v_copy.get("code_rel") or ""
        abs_code = os.path.join(bundle_dir, code_rel) if code_rel else ""
        if abs_code and os.path.isfile(abs_code):
            with open(abs_code, "r", encoding="utf-8", errors="replace") as cf:
                v_copy["code"] = cf.read()
        else:
            v_copy["code"] = ""
        desc_rel = v_copy.get("description_rel") or ""
        abs_desc = os.path.join(bundle_dir, desc_rel) if desc_rel else ""
        if abs_desc and os.path.isfile(abs_desc):
            with open(abs_desc, "r", encoding="utf-8", errors="replace") as df:
                v_copy["description"] = df.read()
        else:
            v_copy["description"] = ""
        if v_copy.get("version") == active:
            active_image = v_copy["abs_image_path"]
            active_svg = v_copy["abs_svg_path"]
        enriched_versions.append(v_copy)
    if not active_image and enriched_versions:
        active_image = enriched_versions[-1].get("abs_image_path") or ""
        active_svg = enriched_versions[-1].get("abs_svg_path") or ""

    target_rel = manifest.get("target_rel_path") or ""
    target_abs = os.path.join(base_dir, target_rel) if target_rel else active_image
    return {
        "figureId": manifest.get("figure_id") or os.path.basename(bundle_dir),
        "title": manifest.get("title") or manifest.get("figure_id") or os.path.basename(bundle_dir),
        "taskName": manifest.get("task_name") or "plot",
        "caption": manifest.get("caption") or "",
        "aspectRatio": manifest.get("aspect_ratio") or "16:9",
        "activeVersion": active,
        "targetRelPath": target_rel,
        "targetImagePath": target_abs,
        "activeImagePath": active_image,
        "activeSvgPath": active_svg,
        "bundleDir": bundle_dir,
        "versions": enriched_versions,
    }


def _next_version_name(versions: List[Dict[str, Any]]) -> str:
    nums = []
    for v in versions:
        m = re.match(r"^v(\d+)$", str(v.get("version") or ""))
        if m:
            nums.append(int(m.group(1)))
    return f"v{(max(nums) + 1) if nums else 1}"


def _exec_plot_code(code_text: str, work_dir: Optional[str] = None) -> Tuple[Optional[str], str]:
    """Executes Python matplotlib code safely using paper_banana_utils, capturing syntax/runtime errors."""
    from utils.paper_banana_utils import _clean_python_code, execute_plot_code_worker

    code_clean = _clean_python_code(code_text)
    if not code_clean:
        return None, "模型没有返回有效的 Python 绘图代码。"
    try:
        compile(code_clean, "<plot>", "exec")
    except SyntaxError as exc:
        return None, f"SyntaxError line {exc.lineno}: {exc.msg}"

    old_cwd = os.getcwd()
    try:
        if work_dir and os.path.isdir(work_dir):
            os.chdir(work_dir)
        img_b64 = execute_plot_code_worker(code_clean)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-600:]}"
    finally:
        os.chdir(old_cwd)

    if not img_b64:
        return None, "绘图脚本运行后未能捕获到非空图形，请确认已调用 matplotlib 绘制元素。"
    return img_b64, ""


def _profile_data_brief(data_file: str) -> str:
    if not data_file or not os.path.isfile(data_file):
        return ""
    ext = os.path.splitext(data_file)[1].lower()
    if ext == ".json":
        with open(data_file, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read(6000)
        return f"JSON data snapshot (`{os.path.basename(data_file)}`):\n{raw}"
    import pandas as pd

    if ext in (".csv", ".tsv"):
        sep = "\t" if ext == ".tsv" else ","
        df = pd.read_csv(data_file, sep=sep)
    elif ext in (".xlsx", ".xls"):
        try:
            df = pd.read_excel(data_file)
        except Exception as exc:
            raise StudioError(f"读取 Excel 文件失败（建议另存为 CSV 后重试）：{exc}") from exc
    else:
        with open(data_file, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(6000)

    cols = [f"{col} ({df[col].dtype}, null={int(df[col].isna().sum())})" for col in df.columns]
    stats_txt = ""
    num_df = df.select_dtypes(include=["number"])
    if not num_df.empty:
        stats_txt = num_df.describe().round(4).to_string()
    sample_csv = df.head(20).to_csv(index=False)
    return (
        f"Dataset `{os.path.basename(data_file)}` shape={df.shape}\n"
        f"Columns: {', '.join(cols)}\n\n"
        f"Numeric summary:\n{stats_txt}\n\n"
        f"First 20 rows (CSV):\n{sample_csv}"
    )


def _svg_to_png_bytes(svg_bytes: bytes) -> Optional[bytes]:
    try:
        import pymupdf

        doc = pymupdf.open(stream=svg_bytes, filetype="svg")
        page = doc[0]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
        return pix.tobytes("png")
    except Exception:
        return None


def cmd_list(args: argparse.Namespace) -> Dict[str, Any]:
    base_dir = os.path.abspath(args.dir)
    bundles_root = os.path.join(base_dir, "figures", "bundles")
    bundles: List[Dict[str, Any]] = []
    tracked_targets = set()

    if os.path.isdir(bundles_root):
        for entry in sorted(os.listdir(bundles_root)):
            bdir = os.path.join(bundles_root, entry)
            if os.path.isfile(os.path.join(bdir, "manifest.json")):
                b = _enrich_bundle(bdir, base_dir)
                bundles.append(b)
                if b.get("targetImagePath"):
                    tracked_targets.add(os.path.abspath(b["targetImagePath"]))

    standalone: List[Dict[str, Any]] = []
    seen_paths = set()
    for rel_dir in ("figures", "latex_writeup/figures", "content_refinement_workdir/figures", "raw_materials/figures"):
        fdir = os.path.join(base_dir, rel_dir)
        if not os.path.isdir(fdir):
            continue
        for fn in sorted(os.listdir(fdir)):
            if fn.startswith(".") or not fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".svg")):
                continue
            p = os.path.abspath(os.path.join(fdir, fn))
            if not os.path.isfile(p) or p in seen_paths or p in tracked_targets:
                continue
            # Also skip if basename already tracked in canonical base_dir/figures/
            if os.path.abspath(os.path.join(base_dir, "figures", fn)) in tracked_targets:
                continue
            seen_paths.add(p)
            standalone.append({"name": fn, "path": p, "relDir": rel_dir})

    return {
        "ok": True,
        "dir": base_dir,
        "bundles": bundles,
        "standalone": standalone,
    }


def cmd_ensure_bundle(args: argparse.Namespace) -> Dict[str, Any]:
    base_dir = os.path.abspath(args.dir)
    img_path = os.path.abspath(args.image)
    if not os.path.isfile(img_path):
        raise StudioError(f"找不到图片文件：{img_path}")

    stem = os.path.splitext(os.path.basename(img_path))[0]
    slug = _slugify(stem)
    bundle_dir = os.path.join(base_dir, "figures", "bundles", slug)
    if os.path.isfile(os.path.join(bundle_dir, "manifest.json")):
        return {"ok": True, "bundle": _enrich_bundle(bundle_dir, base_dir)}

    v1_dir = os.path.join(bundle_dir, "v1")
    os.makedirs(v1_dir, exist_ok=True)
    ext = os.path.splitext(img_path)[1].lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
        ext = ".png"
    img_rel = f"v1/image{ext}"
    dst_v1_img = os.path.join(bundle_dir, img_rel)
    if ext == ".svg":
        with open(img_path, "rb") as fh:
            svg_bytes = fh.read()
        with open(os.path.join(v1_dir, "image.svg"), "wb") as fh:
            fh.write(svg_bytes)
        png_bytes = _svg_to_png_bytes(svg_bytes)
        if png_bytes:
            img_rel = "v1/image.png"
            dst_v1_img = os.path.join(bundle_dir, img_rel)
            with open(dst_v1_img, "wb") as fh:
                fh.write(png_bytes)
        svg_rel = "v1/image.svg"
    else:
        shutil.copy2(img_path, dst_v1_img)
        svg_rel = None

    code_rel = None
    if args.code_file and os.path.isfile(args.code_file):
        code_rel = "v1/plot.py"
        shutil.copy2(args.code_file, os.path.join(bundle_dir, code_rel))

    desc_rel = "v1/description.txt"
    with open(os.path.join(bundle_dir, desc_rel), "w", encoding="utf-8") as fh:
        fh.write(args.description or f"Imported figure: {os.path.basename(img_path)}")

    meta = {
        "version": "v1",
        "created_at": _utc_now(),
        "instruction": "initial import",
        "bbox": None,
        "image_rel": img_rel,
        "svg_rel": svg_rel,
        "code_rel": code_rel,
        "description_rel": desc_rel,
    }
    with open(os.path.join(v1_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    target_rel = os.path.relpath(img_path, base_dir)
    if target_rel.startswith(".."):
        os.makedirs(os.path.join(base_dir, "figures"), exist_ok=True)
        canonical = os.path.join(base_dir, "figures", os.path.basename(img_path))
        if ext != ".svg":
            shutil.copy2(dst_v1_img, canonical)
        target_rel = os.path.join("figures", os.path.basename(img_path))

    manifest = {
        "figure_id": slug,
        "title": args.title or stem,
        "task_name": "diagram" if ext == ".svg" else "plot",
        "caption": "",
        "aspect_ratio": "16:9",
        "active_version": "v1",
        "target_rel_path": target_rel.replace(os.sep, "/"),
        "versions": [meta],
    }
    with open(os.path.join(bundle_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return {"ok": True, "bundle": _enrich_bundle(bundle_dir, base_dir)}


def cmd_restore(args: argparse.Namespace) -> Dict[str, Any]:
    bundle_dir = os.path.abspath(args.bundle)
    manifest_path = os.path.join(bundle_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise StudioError(f"找不到图表版本清单：{manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    target_ver = next((v for v in manifest.get("versions", []) if v.get("version") == args.version), None)
    if not target_ver:
        raise StudioError(f"找不到版本 {args.version}")
    src_img = os.path.join(bundle_dir, target_ver["image_rel"])
    if not os.path.isfile(src_img):
        raise StudioError(f"版本图片文件缺失：{src_img}")
    manifest["active_version"] = args.version
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    base_dir = os.path.abspath(args.base_dir) if args.base_dir else os.path.abspath(os.path.join(bundle_dir, "..", "..", ".."))
    synced = _sync_target_image(src_img, manifest, bundle_dir, base_dir)
    return {"ok": True, "synced": synced, "bundle": _enrich_bundle(bundle_dir, base_dir)}


def cmd_revise(args: argparse.Namespace) -> Dict[str, Any]:
    from utils.llm_backend_utils import (
        call_llm_with_multimodal_parts,
        call_llm_with_text_prompt,
        has_any_llm_credentials,
        resolve_task_model,
        supports_vision,
    )
    from utils.paper_banana_utils import (
        _clean_python_code,
        generate_figure_visuals_with_code,
        identity_parse,
    )

    bundle_dir = os.path.abspath(args.bundle)
    manifest_path = os.path.join(bundle_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        raise StudioError(f"找不到图表版本清单：{manifest_path}")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    instruction = (args.instruction or "").strip()
    if not instruction:
        raise StudioError("请输入修改说明。")
    bbox = _parse_bbox(args.bbox)
    base_dir = os.path.abspath(args.base_dir) if args.base_dir else os.path.abspath(os.path.join(bundle_dir, "..", "..", ".."))

    active_id = manifest.get("active_version") or "v1"
    versions = manifest.get("versions") or []
    active_ver = next((v for v in versions if v.get("version") == active_id), versions[-1] if versions else None)
    if not active_ver:
        raise StudioError("图表版本清单为空。")

    active_img_path = os.path.join(bundle_dir, active_ver["image_rel"])
    if not os.path.isfile(active_img_path):
        raise StudioError(f"当前激活图片不存在：{active_img_path}")

    active_code = ""
    if active_ver.get("code_rel"):
        cp = os.path.join(bundle_dir, active_ver["code_rel"])
        if os.path.isfile(cp):
            with open(cp, "r", encoding="utf-8", errors="replace") as cf:
                active_code = cf.read()

    active_desc = ""
    if active_ver.get("description_rel"):
        dp = os.path.join(bundle_dir, active_ver["description_rel"])
        if os.path.isfile(dp):
            with open(dp, "r", encoding="utf-8", errors="replace") as df:
                active_desc = df.read()

    active_svg = ""
    if active_ver.get("svg_rel"):
        sp = os.path.join(bundle_dir, active_ver["svg_rel"])
        if os.path.isfile(sp):
            with open(sp, "r", encoding="utf-8", errors="replace") as sf:
                active_svg = sf.read()

    if not active_code and not active_svg and not has_any_llm_credentials():
        raise StudioError("未检测到可用大模型密钥，请先在「环境与算力」配置 API 密钥。")

    with _track_studio_usage(bundle_dir) as usage_holder:
        _emit_progress(
            "prepare",
            "running",
            f"正在装载当前激活版本 {active_id}" + (" 并绘制红色局部选区参考框…" if bbox is not None else "…"),
        )
        bbox_bytes: Optional[bytes] = None
        bbox_desc = "Full figure (no specific sub-region boxed)."
        if bbox is not None:
            bbox_bytes, bbox_desc = _annotate_bbox_png(active_img_path, bbox)
        else:
            with open(active_img_path, "rb") as fh:
                bbox_bytes = fh.read()

        next_ver = _next_version_name(versions)
        next_dir = os.path.join(bundle_dir, next_ver)
        active_dir = os.path.join(bundle_dir, active_id)

        new_code = ""
        new_svg = ""
        new_desc = active_desc
        img_b64 = ""
        _emit_progress("prepare", "completed", f"已锁定新版本目标 {next_ver}")

        if active_svg:
            target_model = resolve_task_model(args.model, "plot_code")
            prompt = (
                "You are revising an existing publication-grade SVG diagram.\n"
                f"Requested localized change: {instruction}\n"
                f"Target region note: {bbox_desc}\n"
                "Keep all unaffected elements, layout geometry, and labels intact. Return ONLY the complete updated `<svg ...>...</svg>` XML.\n\n"
                f"Current SVG:\n```xml\n{active_svg}\n```"
            )
            _emit_progress(
                "llm",
                "running",
                f"正在请求 {target_model} 局部修改 SVG 矢量源码…",
                model=target_model,
                est_input_tokens=_est_tokens(prompt),
            )
            parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
            if bbox_bytes and supports_vision(target_model):
                parts.append({"type": "image_bytes", "data": bbox_bytes, "mime_type": "image/png"})
            resp = call_llm_with_multimodal_parts(
                parts=parts,
                model_name=target_model,
                system_prompt="Edit only the specified region or aspect of the SVG diagram. Output valid standalone SVG XML.",
                result_parsing_func=identity_parse,
            )
            raw = resp.get("parsed_response", "")
            m = re.search(r"(<svg\b[\s\S]*</svg>)", raw, re.IGNORECASE)
            new_svg = m.group(1).strip() if m else raw.strip()
            _emit_progress("llm", "completed", "已返回修订 SVG，正验证 XML 与栅格化预览…", model=target_model)
            _emit_progress("render", "running", "正在渲染 SVG 预览…")
            try:
                ET.fromstring(new_svg)
            except ET.ParseError as exc:
                raise StudioError(f"生成的 SVG XML 格式有误：{exc}") from exc
            png_bytes = _svg_to_png_bytes(new_svg.encode("utf-8"))
            if png_bytes:
                img_b64 = base64.b64encode(png_bytes).decode("utf-8")
            _emit_progress("render", "completed", "SVG 校验与预览生成完成")
        elif active_code.strip():
            target_model = resolve_task_model(args.model, "plot_code")
            base_prompt = (
                "You are revising a scientific figure generated by Python Matplotlib.\n"
                f"User edit instruction: {instruction}\n"
                f"Target region: {bbox_desc}\n"
                "Rules:\n"
                "1. Modify ONLY what the user asked to change (in the specified region if boxed); keep all verified underlying data numbers and unaffected layout/styles intact.\n"
                "2. Keep clean publication styling (white background, legible sans-serif typography, colorblind-safe palette, no overlapping text).\n"
                "3. Return ONLY ```python ... ``` code.\n\n"
                f"Current Python script:\n```python\n{active_code}\n```"
            )
            _emit_progress(
                "llm",
                "running",
                f"正在请求 {target_model} 局改 Matplotlib 绘图脚本…",
                model=target_model,
                est_input_tokens=_est_tokens(base_prompt),
            )
            parts = [{"type": "text", "text": base_prompt}]
            if bbox_bytes and supports_vision(target_model):
                parts.append({"type": "image_bytes", "data": bbox_bytes, "mime_type": "image/png"})
            resp = call_llm_with_multimodal_parts(
                parts=parts,
                model_name=target_model,
                system_prompt="Precision scientific plot code editor.",
                result_parsing_func=identity_parse,
            )
            candidate_code = _clean_python_code(resp.get("parsed_response", ""))
            _emit_progress("llm", "completed", "已返回修订脚本", model=target_model)
            last_err = ""
            for attempt in range(1, 4):
                _emit_progress("render", "running", f"正在沙盒渲染 Matplotlib 图表（第 {attempt}/3 次尝试）…")
                img_b64, err = _exec_plot_code(candidate_code, work_dir=active_dir)
                if img_b64:
                    new_code = candidate_code
                    _emit_progress("render", "completed", f"第 {attempt} 次沙盒渲染成功")
                    break
                last_err = err
                fix_prompt = (
                    f"The revised Matplotlib script failed with error:\n{err}\n\n"
                    "Fix the Python script while keeping the requested edit intact. Return ONLY ```python ... ``` code:\n"
                    f"```python\n{candidate_code}\n```"
                )
                _emit_progress(
                    "render",
                    "running",
                    f"第 {attempt} 次渲染报错，正请求 {target_model} 自动修复脚本…",
                    model=target_model,
                    detail=err[:200],
                    est_input_tokens=_est_tokens(fix_prompt),
                )
                fix_resp = call_llm_with_text_prompt(
                    prompt=fix_prompt,
                    model_name=target_model,
                    generation_configs={"system_instruction": "Fix Python matplotlib plotting script errors."},
                    result_parsing_func=identity_parse,
                )
                candidate_code = _clean_python_code(fix_resp.get("parsed_response", ""))
            if not img_b64:
                _emit_progress("render", "error", "修订脚本连续 3 次渲染失败", detail=last_err[:300])
                raise StudioError(f"修改后的绘图代码连续执行失败：{last_err}")
        else:
            target_model = resolve_task_model(args.model, "plot_critic")
            prompt = (
                "You are performing a targeted local revision of an existing scientific figure.\n"
                f"User edit instruction: {instruction}\n"
                f"Target region: {bbox_desc}\n"
                "Update the figure description so ONLY the requested change (and any connected layout adjustments needed to avoid overlap) is applied while keeping everything else unchanged.\n"
                f"Current figure description:\n{active_desc or manifest.get('title', '')}\n\n"
                "Return ONLY the complete revised detailed figure description."
            )
            _emit_progress(
                "llm",
                "running",
                f"正在请求 {target_model} 分析选区并重构图注描述…",
                model=target_model,
                est_input_tokens=_est_tokens(prompt),
            )
            parts = [{"type": "text", "text": prompt}]
            if bbox_bytes and supports_vision(target_model):
                parts.append({"type": "image_bytes", "data": bbox_bytes, "mime_type": "image/png"})
            resp = call_llm_with_multimodal_parts(
                parts=parts,
                model_name=target_model,
                system_prompt="Targeted scientific figure editor.",
                result_parsing_func=identity_parse,
            )
            new_desc = resp.get("parsed_response", "").strip() or active_desc
            _emit_progress("llm", "completed", "局部修订说明已生成", model=target_model)
            _emit_progress("render", "running", "正在重新生成目标图表…")
            img_b64, new_code = generate_figure_visuals_with_code(
                task_name=manifest.get("task_name") or "diagram",
                figure_description=new_desc,
                model_name=args.model,
                image_model_name=args.image_model,
                aspect_ratio=manifest.get("aspect_ratio") or "16:9",
            )
            if not img_b64:
                _emit_progress("render", "error", "图表重绘失败")
                raise StudioError("重新生成图表失败，请检查模型设置后重试。")
            _emit_progress("render", "completed", "新版本图表生成完成")

        _emit_progress("sync", "running", f"正在归档 {next_ver} 版本快照并同步至论文图表目录…")
        os.makedirs(next_dir, exist_ok=True)
        # Preserve any data_snapshot.* from active version so future revisions can reload data
        if os.path.isdir(active_dir):
            for fn in os.listdir(active_dir):
                if fn.startswith("data_snapshot"):
                    shutil.copy2(os.path.join(active_dir, fn), os.path.join(next_dir, fn))

        svg_rel = None
        img_rel = f"{next_ver}/image.jpg"
        if new_svg:
            svg_rel = f"{next_ver}/image.svg"
            with open(os.path.join(bundle_dir, svg_rel), "w", encoding="utf-8") as sf:
                sf.write(new_svg)
            img_rel = f"{next_ver}/image.png" if img_b64 else f"{next_ver}/image.svg"

        if img_b64:
            with open(os.path.join(bundle_dir, img_rel), "wb") as fh:
                fh.write(base64.b64decode(img_b64))

        code_rel = None
        if new_code.strip():
            code_rel = f"{next_ver}/plot.py"
            with open(os.path.join(bundle_dir, code_rel), "w", encoding="utf-8") as cf:
                cf.write(new_code)

        desc_rel = f"{next_ver}/description.txt"
        with open(os.path.join(bundle_dir, desc_rel), "w", encoding="utf-8") as df:
            df.write(new_desc)

        meta = {
            "version": next_ver,
            "created_at": _utc_now(),
            "instruction": instruction,
            "bbox": bbox,
            "image_rel": img_rel,
            "svg_rel": svg_rel,
            "code_rel": code_rel,
            "description_rel": desc_rel,
        }
        with open(os.path.join(next_dir, "meta.json"), "w", encoding="utf-8") as mf:
            json.dump(meta, mf, ensure_ascii=False, indent=2)

        manifest["versions"].append(meta)
        manifest["active_version"] = next_ver
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)

        synced = _sync_target_image(os.path.join(bundle_dir, img_rel), manifest, bundle_dir, base_dir)
        _emit_progress("sync", "completed", f"已激活 {next_ver} 并同步 {len(synced)} 个目标路径")

    return {"ok": True, "synced": synced, "bundle": _enrich_bundle(bundle_dir, base_dir), "usageReport": usage_holder["final"]}


def cmd_create(args: argparse.Namespace) -> Dict[str, Any]:
    from utils.llm_backend_utils import (
        call_llm_with_multimodal_parts,
        call_llm_with_text_prompt,
        has_any_llm_credentials,
        resolve_task_model,
        supports_vision,
    )
    from utils.paper_banana_utils import (
        PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT,
        _clean_python_code,
        generate_figure_visuals_with_code,
        identity_parse,
    )

    if not has_any_llm_credentials():
        raise StudioError("未检测到可用大模型密钥，请先在「环境与算力」配置 API 密钥。")

    out_dir = os.path.abspath(args.out_dir)
    title = (args.title or "").strip() or (args.prompt or "custom_figure").strip()[:36]
    slug = _slugify(title)
    bundle_dir = os.path.join(out_dir, "figures", "bundles", slug)
    if os.path.exists(bundle_dir):
        slug = f"{slug}_{datetime.now().strftime('%H%M%S')}"
        bundle_dir = os.path.join(out_dir, "figures", "bundles", slug)

    v1_dir = os.path.join(bundle_dir, "v1")
    os.makedirs(v1_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "figures"), exist_ok=True)

    with _track_studio_usage(out_dir) as usage_holder:
        _emit_progress("prepare", "running", "正在初始化图表版本库与输入数据/参考图…")
        snapshot_name = ""
        data_brief = ""
        if args.data_file and os.path.isfile(args.data_file):
            ext = os.path.splitext(args.data_file)[1].lower()
            snapshot_name = f"data_snapshot{ext}"
            shutil.copy2(args.data_file, os.path.join(v1_dir, snapshot_name))
            data_brief = _profile_data_brief(args.data_file)

        ref_bytes: Optional[bytes] = None
        ref_mime = "image/png"
        if args.ref_image and os.path.isfile(args.ref_image):
            with open(args.ref_image, "rb") as fh:
                ref_bytes = fh.read()
            low = args.ref_image.lower()
            if low.endswith((".jpg", ".jpeg")):
                ref_mime = "image/jpeg"

        mode = args.mode
        new_code = ""
        new_svg = ""
        img_b64 = ""
        desc = args.prompt.strip()
        _emit_progress("prepare", "completed", f"已初始化版本目录 {slug}（路线：{mode}）")

        if mode == "schematic_svg":
            target_model = resolve_task_model(args.model, "diagram_code")
            prompt = (
                "Design a publication-ready vector schematic / architecture diagram in clean standalone SVG XML.\n"
                f"Figure title/topic: {title}\n"
                f"Specification:\n{args.prompt}\n"
                + ("\nMatch the visual layout aesthetic, proportions, and palette style of the attached reference image.\n" if ref_bytes else "")
                + "\nRequirements: pure white background <rect>, rounded boxes with clean strokes, crisp directional arrows (<marker>), harmonious sans-serif fonts (Arial/Helvetica), zero text clipping/overlap, explicit viewBox. Return ONLY `<svg ...>...</svg>`."
            )
            _emit_progress(
                "llm",
                "running",
                f"正在调用 {target_model} 生成独立 SVG 矢量架构图…",
                model=target_model,
                est_input_tokens=_est_tokens(prompt),
            )
            parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
            if ref_bytes and supports_vision(target_model):
                parts.append({"type": "image_bytes", "data": ref_bytes, "mime_type": ref_mime})
            resp = call_llm_with_multimodal_parts(
                parts=parts,
                model_name=target_model,
                system_prompt="Scientific vector diagram SVG designer.",
                result_parsing_func=identity_parse,
            )
            raw = resp.get("parsed_response", "")
            m = re.search(r"(<svg\b[\s\S]*</svg>)", raw, re.IGNORECASE)
            new_svg = m.group(1).strip() if m else raw.strip()
            _emit_progress("llm", "completed", "SVG 生成完成，正验证结构与渲染 PNG 预览…", model=target_model)
            _emit_progress("render", "running", "正在校验 SVG 并生成高分预览…")
            try:
                ET.fromstring(new_svg)
            except ET.ParseError as exc:
                raise StudioError(f"生成的 SVG XML 无法解析：{exc}") from exc
            png_bytes = _svg_to_png_bytes(new_svg.encode("utf-8"))
            if png_bytes:
                img_b64 = base64.b64encode(png_bytes).decode("utf-8")
            _emit_progress("render", "completed", "SVG 校验与预览生成完成")
        elif mode == "concept_image":
            enriched_desc = f"Title: {title}\nVisual Specification: {args.prompt}"
            if ref_bytes and supports_vision(resolve_task_model(args.model, "plot_plan")):
                plan_model = resolve_task_model(args.model, "plot_plan")
                _emit_progress("llm", "running", f"正在使用 {plan_model} 提取参考图风格要素…", model=plan_model)
                style_resp = call_llm_with_multimodal_parts(
                    parts=[
                        {"type": "text", "text": "Extract ONLY the layout composition, palette hex codes, card style, and visual hierarchy from this reference diagram (ignore its text/content) in 120 words."},
                        {"type": "image_bytes", "data": ref_bytes, "mime_type": ref_mime},
                    ],
                    model_name=plan_model,
                    system_prompt="Visual style analyst.",
                    result_parsing_func=identity_parse,
                )
                enriched_desc += f"\n\nVisual Style Reference:\n{style_resp.get('parsed_response', '')}"
                _emit_progress("llm", "completed", "参考风格解析完成", model=plan_model)
            _emit_progress("render", "running", "正在生成概念总览图…")
            img_b64, new_code = generate_figure_visuals_with_code(
                task_name="diagram",
                figure_description=enriched_desc,
                model_name=args.model,
                image_model_name=args.image_model,
                aspect_ratio="16:9",
            )
            desc = enriched_desc
            _emit_progress("render", "completed", "概念图生成完成")
        else:  # data_code
            target_model = resolve_task_model(args.model, "plot_code")
            prompt = (
                "Write a self-contained Python Matplotlib script to create a Nature/Science publication-grade figure.\n"
                f"Title/Objective: {title}\n"
                f"Instructions:\n{args.prompt}\n\n"
            )
            if data_brief and snapshot_name:
                prompt += (
                    f"Real Data File available in the current working directory as `./{snapshot_name}`:\n{data_brief}\n\n"
                    f"IMPORTANT: Load actual values from `./{snapshot_name}` (do NOT fabricate numbers). "
                )
            if ref_bytes:
                prompt += (
                    "An example reference figure is attached: replicate its chart family, multi-panel aesthetic, color palette harmony, grid/spine cleanliness, and typography feel using the user's real specification/data above.\n"
                )
            prompt += (
                "Use ONLY standard library + numpy + pandas + matplotlib (do NOT import seaborn, scipy, sklearn, or openpyxl). "
                "Use crisp 300 DPI white background, colorblind-safe Okabe-Ito or viridis colors, legible font sizes, clear axis labels with units, and tight layout. "
                "Return ONLY ```python ... ``` code."
            )
            _emit_progress(
                "llm",
                "running",
                f"正在调用 {target_model} 编写 Matplotlib 出版级绘图代码…",
                model=target_model,
                est_input_tokens=_est_tokens(prompt),
            )
            parts = [{"type": "text", "text": prompt}]
            if ref_bytes and supports_vision(target_model):
                parts.append({"type": "image_bytes", "data": ref_bytes, "mime_type": ref_mime})
            resp = call_llm_with_multimodal_parts(
                parts=parts,
                model_name=target_model,
                system_prompt=PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT,
                result_parsing_func=identity_parse,
            )
            candidate_code = _clean_python_code(resp.get("parsed_response", ""))
            _emit_progress("llm", "completed", "绘图代码生成完成", model=target_model)
            last_err = ""
            for attempt in range(1, 4):
                _emit_progress("render", "running", f"正在沙盒执行绘图代码（第 {attempt}/3 次尝试）…")
                img_b64, err = _exec_plot_code(candidate_code, work_dir=v1_dir)
                if img_b64:
                    new_code = candidate_code
                    _emit_progress("render", "completed", f"第 {attempt} 次沙盒渲染成功")
                    break
                last_err = err
                fix_prompt = (
                    f"Your plotting script failed with error:\n{err}\n"
                    f"(Working directory contains `./{snapshot_name}` if a data file was provided.)\n"
                    "Use ONLY numpy, pandas, matplotlib, and stdlib. Fix and return ONLY ```python ... ```:\n"
                    f"```python\n{candidate_code}\n```"
                )
                _emit_progress(
                    "render",
                    "running",
                    f"第 {attempt} 次渲染报错，正请求 {target_model} 自动修复…",
                    model=target_model,
                    detail=err[:200],
                    est_input_tokens=_est_tokens(fix_prompt),
                )
                fix_resp = call_llm_with_text_prompt(
                    prompt=fix_prompt,
                    model_name=target_model,
                    generation_configs={"system_instruction": PLOT_VISUALIZER_AGENT_SYSTEM_PROMPT},
                    result_parsing_func=identity_parse,
                )
                candidate_code = _clean_python_code(fix_resp.get("parsed_response", ""))
            if not img_b64:
                _emit_progress("render", "error", "绘图代码多次执行失败", detail=last_err[:300])
                raise StudioError(f"绘图代码多次执行失败：{last_err}")

        _emit_progress("sync", "running", "正在写入 v1 版本库并同步至图表目录…")
        svg_rel = None
        img_rel = "v1/image.jpg"
        target_name = f"{slug}.jpg"
        if new_svg:
            svg_rel = "v1/image.svg"
            with open(os.path.join(bundle_dir, svg_rel), "wb" if isinstance(new_svg, bytes) else "w", encoding=None if isinstance(new_svg, bytes) else "utf-8") as sf:
                sf.write(new_svg)
            if img_b64:
                img_rel = "v1/image.png"
                target_name = f"{slug}.png"
            else:
                img_rel = "v1/image.svg"
                target_name = f"{slug}.svg"

        if img_b64:
            with open(os.path.join(bundle_dir, img_rel), "wb") as fh:
                fh.write(base64.b64decode(img_b64))

        code_rel = None
        if new_code.strip():
            code_rel = "v1/plot.py"
            with open(os.path.join(bundle_dir, code_rel), "w", encoding="utf-8") as cf:
                cf.write(new_code)

        desc_rel = "v1/description.txt"
        with open(os.path.join(bundle_dir, desc_rel), "w", encoding="utf-8") as df:
            df.write(desc)

        meta = {
            "version": "v1",
            "created_at": _utc_now(),
            "instruction": f"create ({mode})",
            "bbox": None,
            "image_rel": img_rel,
            "svg_rel": svg_rel,
            "code_rel": code_rel,
            "description_rel": desc_rel,
        }
        with open(os.path.join(v1_dir, "meta.json"), "w", encoding="utf-8") as mf:
            json.dump(meta, mf, ensure_ascii=False, indent=2)

        manifest = {
            "figure_id": slug,
            "title": title,
            "task_name": "diagram" if mode != "data_code" else "plot",
            "caption": "",
            "aspect_ratio": "16:9",
            "active_version": "v1",
            "target_rel_path": f"figures/{target_name}",
            "versions": [meta],
        }
        with open(os.path.join(bundle_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)

        _sync_target_image(os.path.join(bundle_dir, img_rel), manifest, bundle_dir, out_dir)
        if new_svg:
            shutil.copy2(os.path.join(bundle_dir, svg_rel), os.path.join(out_dir, "figures", f"{slug}.svg"))
        _emit_progress("sync", "completed", "已创建 v1 版本档案")

    return {"ok": True, "bundle": _enrich_bundle(bundle_dir, out_dir), "usageReport": usage_holder["final"]}


def main(argv: Optional[List[str]] = None) -> int:
    install_output_redaction()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("--dir", required=True)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("ensure-bundle")
    p.add_argument("--dir", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--code-file", default="")
    p.add_argument("--description", default="")
    p.set_defaults(fn=cmd_ensure_bundle)

    p = sub.add_parser("revise")
    p.add_argument("--bundle", required=True)
    p.add_argument("--instruction", required=True)
    p.add_argument("--bbox", default="")
    p.add_argument("--model", default="auto")
    p.add_argument("--image-model", default="auto")
    p.add_argument("--base-dir", default="")
    p.set_defaults(fn=cmd_revise)

    p = sub.add_parser("restore")
    p.add_argument("--bundle", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--base-dir", default="")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("create")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--mode", choices=["data_code", "schematic_svg", "concept_image"], default="data_code")
    p.add_argument("--prompt", required=True)
    p.add_argument("--title", default="")
    p.add_argument("--data-file", default="")
    p.add_argument("--ref-image", default="")
    p.add_argument("--model", default="auto")
    p.add_argument("--image-model", default="auto")
    p.set_defaults(fn=cmd_create)

    global _REAL_STDOUT
    args = ap.parse_args(argv)
    orig_stdout = sys.stdout
    _REAL_STDOUT = orig_stdout
    try:
        sys.stdout = sys.stderr
        result = args.fn(args)
    except StudioError as exc:
        result = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        result = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        sys.stdout = orig_stdout
    emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
