"""Images over a platform's limits are scaled down, not dropped (utils/image_fit.py).

Found 2026-11-19: LongCat answered a 2331x2331 figure with
"Image total pixels exceeds maximum allowed size (5242880px), actual: 5434593px"
and the caption was then written without the figure.
"""

import base64
import io
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image  # noqa: E402

from utils import image_fit as F  # noqa: E402
from utils import providers as P  # noqa: E402


def png(w, h, color=(30, 120, 200)):
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, format="PNG")
    return out.getvalue()


def size_of(raw):
    with Image.open(io.BytesIO(raw)) as im:
        return im.size


class APIError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status_code = status


class FitImageTests(unittest.TestCase):
    def test_under_the_limits_is_untouched(self):
        raw = png(1200, 800)
        self.assertIs(F.fit_image(raw, max_edge=2000, max_pixels=5_242_880, max_bytes=10**7, quiet=True), raw)
        self.assertIs(F.fit_image(raw), raw)

    def test_pixel_limit_keeps_aspect_and_lands_under(self):
        raw = png(2331, 2331)
        out = F.fit_image(raw, max_pixels=5_242_880, label="longcat", quiet=True)
        w, h = size_of(out)
        self.assertLessEqual(w * h, 5_242_880)
        self.assertEqual(w, h)
        self.assertGreater(w * h, 5_242_880 * 0.9)  # not needlessly small

        wide = F.fit_image(png(4000, 1000), max_pixels=1_000_000, quiet=True)
        w, h = size_of(wide)
        self.assertLessEqual(w * h, 1_000_000)
        self.assertAlmostEqual(w / h, 4.0, delta=0.05)

    def test_edge_and_bytes_limits(self):
        w, h = size_of(F.fit_image(png(3000, 1500), max_edge=2048, quiet=True))
        self.assertEqual(max(w, h), 2048)
        noisy = io.BytesIO()
        Image.effect_noise((1500, 1500), 100).convert("RGB").save(noisy, format="PNG")
        out = F.fit_image(noisy.getvalue(), max_bytes=400_000, quiet=True)
        self.assertLessEqual(len(out), 400_000)

    def test_log_line_names_the_platform_and_limit(self):
        with mock.patch("builtins.print") as pr:
            F.fit_image(png(2331, 2331), max_pixels=5_242_880, label="longcat")
        line = pr.call_args[0][0]
        self.assertIn("[longcat] Image 2331x2331 scaled to", line)
        self.assertIn("5,242,880 px per image", line)
        for word in ("warn", "error", "fail"):  # the desktop log would colour it
            self.assertNotIn(word, line.lower())

    def test_unreadable_bytes_come_back_unchanged(self):
        self.assertEqual(F.fit_image(b"not an image", max_pixels=10, quiet=True), b"not an image")

    def test_total_pixel_budget_is_shared(self):
        limits = F.ImageLimits()
        out = F.fit_with_limits(png(2000, 2000), limits, n_images=4, total_pixels=4_000_000)
        w, h = size_of(out)
        self.assertLessEqual(w * h, 1_000_000)


class ErrorParsingTests(unittest.TestCase):
    sizes = [(2331, 2331, 400_000)]

    def test_longcat_message(self):
        err = APIError("Error code: 400 - Image total pixels exceeds maximum allowed size (5242880px), "
                       "actual: 5434593px")
        lim = F.image_limits_from_error(err, self.sizes)
        self.assertEqual(lim.max_pixels, 5_242_880)
        self.assertIsNone(lim.max_edge)

    def test_other_wordings(self):
        lim = F.image_limits_from_error(APIError("image width must not exceed 2048 pixels"), self.sizes)
        self.assertEqual(lim.max_edge, 2048)
        lim = F.image_limits_from_error(APIError("Image size exceeds the limit of 10MB"), self.sizes)
        self.assertEqual(lim.max_bytes, int(10_000_000 * 0.74))
        lim = F.image_limits_from_error(APIError("图片过大，最大支持 4096x4096"), self.sizes)
        self.assertEqual(lim.max_pixels, 4096 * 4096)
        lim = F.image_limits_from_error(APIError("图片分辨率超过上限 1024x1024"), self.sizes)
        self.assertEqual(lim.max_edge, 1024)
        # No number: aim at half the pixels.
        lim = F.image_limits_from_error(APIError("image too large"), self.sizes)
        self.assertEqual(lim.max_pixels, int(2331 * 2331 * 0.5))

    def test_not_a_size_error(self):
        self.assertIsNone(F.image_limits_from_error(APIError("This model does not support image input"),
                                                    self.sizes))
        self.assertIsNone(F.image_limits_from_error(APIError("context length exceeds maximum"), self.sizes))
        self.assertIsNone(F.image_limits_from_error(APIError("Image total pixels exceeds maximum", 500),
                                                    self.sizes))
        self.assertIsNone(F.image_limits_from_error(Exception("image too large"), self.sizes))
        self.assertIsNone(F.image_limits_from_error(APIError("image too large"), []))
        # A bare "maximum" / "limit" with no number is not taken as a size error.
        self.assertIsNone(F.image_limits_from_error(APIError("maximum number of images per request reached"),
                                                    self.sizes))
        self.assertIsNone(F.image_limits_from_error(APIError("image request hit the rate limit"), self.sizes))

    def test_units(self):
        # Token counts are not byte sizes; "M pixels" / megapixels are pixel counts.
        lim = F.image_limits_from_error(APIError("image too large: prompt exceeds 128k tokens"), self.sizes)
        self.assertIsNone(lim.max_bytes)
        lim = F.image_limits_from_error(APIError("image exceeds the limit of 5.2M pixels"), self.sizes)
        self.assertEqual((lim.max_pixels, lim.max_bytes), (5_200_000, None))
        lim = F.image_limits_from_error(APIError("image larger than 36 megapixels"), self.sizes)
        self.assertEqual(lim.max_pixels, 36_000_000)
        lim = F.image_limits_from_error(APIError("image file size exceeds 20M"), self.sizes)
        self.assertEqual(lim.max_bytes, int(20_000_000 * 0.74))
        found = F.size_error_limits(APIError("image too large"), self.sizes)
        self.assertFalse(found[1])  # a guess, not a stated limit


class FakeChat(BaseHTTPRequestHandler):
    max_pixels = 5_242_880
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        content = body["messages"][-1]["content"]
        sizes = []
        if isinstance(content, list):
            for p in content:
                if p.get("type") == "image_url":
                    raw = base64.b64decode(p["image_url"]["url"].split(",", 1)[1])
                    sizes.append(size_of(raw))
        FakeChat.seen.append({"sizes": sizes, "text_only": isinstance(content, str)})
        total = sum(w * h for w, h in sizes)
        if total > FakeChat.max_pixels:
            msg = (f"Image total pixels exceeds maximum allowed size ({FakeChat.max_pixels}px), "
                   f"actual: {total}px")
            data = json.dumps({"error": {"message": msg}}).encode()
            self.send_response(400)
        else:
            data = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                               "choices": [{"index": 0, "finish_reason": "stop",
                                            "message": {"role": "assistant", "content": "a caption"}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class OpenAIPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeChat)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeChat.seen = []
        FakeChat.max_pixels = 5_242_880
        F._learned.clear()
        self.addCleanup(F._learned.clear)

    def spec(self, **kw):
        return P.ProviderSpec(name="fakecat", kind="openai", base_url=self.base, api_key="k", builtin=False, **kw)

    def call(self, spec, images, model="Cat-1"):
        from utils.openai_utils import call_openai_models_with_content
        content = [{"type": "text", "text": "caption this"}] + [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(i).decode()}}
            for i in images]
        with mock.patch("builtins.print"):
            return call_openai_models_with_content(content, model, provider=spec, result_parsing_func=str,
                                                   max_retries=3, base_interval_sec=0)

    def test_size_error_scales_down_and_keeps_the_image(self):
        res = self.call(self.spec(), [png(2331, 2331)])
        self.assertEqual(res["parsed_response"], "a caption")
        self.assertEqual(len(FakeChat.seen), 2)
        self.assertEqual(FakeChat.seen[0]["sizes"], [(2331, 2331)])
        (w, h), = FakeChat.seen[1]["sizes"]
        self.assertLessEqual(w * h, 5_242_880)
        self.assertFalse(FakeChat.seen[1]["text_only"])
        # Remembered for the model: the next call is scaled before sending.
        FakeChat.seen = []
        self.call(self.spec(), [png(2331, 2331)])
        self.assertEqual(len(FakeChat.seen), 1)
        self.assertFalse(FakeChat.seen[0]["text_only"])

    def test_limits_are_kept_only_after_the_resend_works(self):
        FakeChat.max_pixels = 1  # nothing fits: every resend fails too
        self.call(self.spec(), [png(800, 800)])
        self.assertIsNone(F.learned_limits("fakecat", "Cat-1"))
        # The next call starts from the original size again (no shrinking spiral).
        FakeChat.seen = []
        FakeChat.max_pixels = 5_242_880
        self.call(self.spec(), [png(800, 800)])
        self.assertEqual(FakeChat.seen[0]["sizes"], [(800, 800)])

    def test_request_byte_budget_scales_big_batches(self):
        # A dozen large noisy pages together are over the request budget:
        # each one is scaled to its share, and nothing is dropped.
        import random
        from PIL import Image
        random.seed(1)

        def noisy(w, h):
            im = Image.frombytes("L", (w, h), bytes(random.getrandbits(8) for _ in range(w * h)))
            out = io.BytesIO()
            im.save(out, format="PNG")
            return out.getvalue()

        pages = [noisy(900, 1100) for _ in range(4)]
        content = [{"type": "text", "text": "x"}] + [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(p).decode()}}
            for p in pages]
        total = sum(len(p) for p in pages)
        with mock.patch("builtins.print"):
            out = F.fit_data_url_images(content, F.ImageLimits(), label="t", total_bytes=total // 2)
        sizes = F.data_url_image_sizes(out)
        self.assertEqual(len(sizes), 4)
        self.assertLessEqual(sum(s[2] for s in sizes), total // 2)
        # Under the budget: untouched.
        self.assertIs(F.fit_data_url_images(content, F.ImageLimits(), total_bytes=total * 2), content)

    def test_request_budget_setting(self):
        with mock.patch.dict(os.environ, {"PO_IMAGE_REQUEST_MAX_MB": ""}):
            self.assertEqual(F.request_image_budget(), F.DEFAULT_REQUEST_IMAGE_BYTES)
            self.assertEqual(F.request_image_budget(5_000_000), 5_000_000)
        with mock.patch.dict(os.environ, {"PO_IMAGE_REQUEST_MAX_MB": "0"}):
            self.assertIsNone(F.request_image_budget())
        with mock.patch.dict(os.environ, {"PO_IMAGE_REQUEST_MAX_MB": "8"}):
            self.assertEqual(F.request_image_budget(), 8_000_000)

    def test_stalled_upload_is_detected(self):
        from utils.openai_utils import _upload_stalled

        class WriteTimeout(Exception):
            pass

        try:
            try:
                raise WriteTimeout("timed out")
            except WriteTimeout as inner:
                raise RuntimeError("Request timed out.") from inner
        except RuntimeError as e:
            self.assertTrue(_upload_stalled(e))
        self.assertFalse(_upload_stalled(RuntimeError("Request timed out.")))

    def test_known_limit_avoids_the_failed_request(self):
        self.call(self.spec(image_max_pixels=5_242_880), [png(2331, 2331)])
        self.assertEqual(len(FakeChat.seen), 1)
        (w, h), = FakeChat.seen[0]["sizes"]
        self.assertLessEqual(w * h, 5_242_880)

    def test_limit_counting_all_images_together(self):
        # Two images each under the stated limit but over it together: the
        # retry halves the pixels instead of dropping the images.
        FakeChat.max_pixels = 3_000_000
        self.call(self.spec(), [png(1500, 1500), png(1500, 1500)])
        last = FakeChat.seen[-1]
        self.assertFalse(last["text_only"])
        self.assertEqual(len(last["sizes"]), 2)
        self.assertLessEqual(sum(w * h for w, h in last["sizes"]), 3_000_000)

    def test_still_too_large_falls_back_to_text_only(self):
        FakeChat.max_pixels = 1  # nothing fits
        res = self.call(self.spec(), [png(800, 800)])
        self.assertEqual(res["parsed_response"], "a caption")
        self.assertTrue(FakeChat.seen[-1]["text_only"])
        self.assertLessEqual(len(FakeChat.seen), 5)

    def test_providers_json_fields(self):
        spec = P._spec_from_dict({"name": "relay", "image_max_pixels": 5242880, "image_max_edge": 4096.0,
                                  "image_max_bytes": "10MB", "image_max_total_pixels": -1})
        self.assertEqual((spec.image_max_pixels, spec.image_max_edge), (5_242_880, 4096))
        self.assertIsNone(spec.image_max_bytes)  # not a number: ignored, not an error
        self.assertIsNone(spec.image_max_total_pixels)


class PresetLimitTests(unittest.TestCase):
    def test_longcat_preset_carries_its_documented_limits(self):
        spec = P.get_provider("longcat")
        self.assertEqual((spec.image_max_pixels, spec.image_max_edge), (5_242_880, 5120))
        from utils.openai_utils import fit_images_for_provider
        content = [{"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(png(2331, 2331)).decode()}}]
        with mock.patch("builtins.print"):
            out = fit_images_for_provider(content, spec, "LongCat-2.5")
        w, h = size_of(base64.b64decode(out[0]["image_url"]["url"].split(",", 1)[1]))
        self.assertLessEqual(w * h, 5_242_880)
        # Platforms without known limits are left exactly as before.
        same = fit_images_for_provider(content, P.get_provider("openai"), "gpt-x")
        self.assertIs(same, content)


class DispatchTests(unittest.TestCase):
    def test_gemini_and_anthropic_protocols_use_spec_limits(self):
        from utils import llm_backend_utils as L
        sent = {}

        def fake_gemini(contents, **kw):
            sent["gemini"] = [c for c in contents if getattr(c, "inline_data", None)]
            return {"raw_response": "ok", "parsed_response": "ok"}

        def fake_claude(content, **kw):
            sent["claude"] = [b for b in content if b["type"] == "image"]
            return {"raw_response": "ok", "parsed_response": "ok"}

        parts = [{"type": "text", "text": "x"}, {"type": "image_bytes", "data": png(3000, 2000), "mime_type": "image/png"}]
        g = P.ProviderSpec(name="gw", kind="gemini", api_key="k", builtin=False, image_max_edge=1000)
        a = P.ProviderSpec(name="aw", kind="anthropic", api_key="k", builtin=False, image_max_pixels=500_000)
        with mock.patch.object(L, "call_gemini_with_contents", side_effect=fake_gemini), \
                mock.patch.object(L, "call_claude_with_content", side_effect=fake_claude), \
                mock.patch("builtins.print"):
            L._dispatch_parts(g, "gem-x", parts, {}, str, False)
            L._dispatch_parts(a, "relay-model", parts, {}, str, False)
        self.assertEqual(max(size_of(sent["gemini"][0].inline_data.data)), 1000)
        w, h = size_of(base64.b64decode(sent["claude"][0]["source"]["data"]))
        self.assertLessEqual(w * h, 500_000)


if __name__ == "__main__":
    unittest.main()
