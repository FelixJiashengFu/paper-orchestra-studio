"""API keys never reach the run log through the LLM retry warnings (utils.providers.redact_secrets).

Offline: a local fake OpenAI/Anthropic server whose /auth/ endpoint answers 401
and echoes the key back, like a careless gateway.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_llm_server import FakeServer, IsolatedEnv, reset_provider_caches  # noqa: E402

# Deliberately without a well-known prefix: only the "configured key" rule can catch it.
PLAIN_SECRET = "po-test-plain-secret-0123456789"
PREFIXED_SECRET = "sk-test-PREFIXED-abcdef0123456789"


class RedactSecretsTests(unittest.TestCase):
    def setUp(self):
        self.env = IsolatedEnv().__enter__()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PO_PROVIDERS_FILE"] = os.path.join(self.tmp.name, "providers.json")

    def tearDown(self):
        self.tmp.cleanup()
        self.env.__exit__(None, None, None)

    def write_providers(self, text):
        with open(os.environ["PO_PROVIDERS_FILE"], "w", encoding="utf-8") as f:
            f.write(text)
        reset_provider_caches()

    def test_configured_and_key_shaped_values_are_masked(self):
        from utils.providers import redact_secrets

        os.environ["DEEPSEEK_API_KEY"] = "ds-plain-value-998877665544"
        self.write_providers(json.dumps({"providers": [
            {"name": "gw", "type": "openai", "base_url": "http://127.0.0.1:9/v1", "api_key": PLAIN_SECRET}]}))
        text = (f"401 Incorrect API key provided: {PLAIN_SECRET}; env ds-plain-value-998877665544; "
                f"{PREFIXED_SECRET}; Authorization: Bearer abc.def.ghi; https://h/x?key=AIzaSyQQQQQQQQQQQQQQ&alt=json")
        out = redact_secrets(text)
        for leaked in (PLAIN_SECRET, "ds-plain-value-998877665544", "PREFIXED-abcdef0123456789", "abc.def.ghi",
                       "AIzaSyQQQQQQQQQQQQQQ"):
            self.assertNotIn(leaked, out)
        self.assertIn("401 Incorrect API key provided: ***", out)
        self.assertIn("&alt=json", out)

    def test_output_wrapper_masks_tracebacks(self):
        import traceback
        from utils.providers import _RedactingStream

        self.write_providers(json.dumps({"providers": [
            {"name": "gw", "type": "openai", "base_url": "http://127.0.0.1:9/v1", "api_key": PLAIN_SECRET}]}))
        buf = io.StringIO()
        stream = _RedactingStream(buf)
        try:
            raise RuntimeError(f"Error code: 401 - Incorrect API key provided: {PLAIN_SECRET}")
        except RuntimeError:
            print(traceback.format_exc(), file=stream)
        print("Agent SectionWritingAgent starting", file=stream)
        out = buf.getvalue()
        self.assertNotIn(PLAIN_SECRET, out)
        self.assertIn("Incorrect API key provided: ***", out)
        self.assertIn("Agent SectionWritingAgent starting\n", out)
        self.assertEqual(stream.getvalue(), out)  # other attributes come from the wrapped stream

    def test_ordinary_text_is_unchanged(self):
        from utils.providers import redact_secrets

        plain = ("Parsed response is None, task-specific text at https://api.deepseek.com/v1 for model "
                 "deepseek-chat in /home/u/out/my_paper_20261118_013524_ed9e9f05")
        self.assertEqual(redact_secrets(plain), plain)
        self.assertEqual(redact_secrets(ValueError("boom")), "boom")

    def test_never_raises_on_a_broken_providers_file(self):
        from utils.providers import redact_secrets

        self.write_providers("{not json")
        self.assertEqual(redact_secrets(f"x {PREFIXED_SECRET}"), "x sk-***")

        class Unprintable:
            def __str__(self):
                raise RuntimeError("no")

        self.assertIsInstance(redact_secrets(Unprintable()), str)


class RetryWarningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeServer().start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.env = IsolatedEnv().__enter__()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PO_PROVIDERS_FILE"] = os.path.join(self.tmp.name, "providers.json")
        with open(os.environ["PO_PROVIDERS_FILE"], "w", encoding="utf-8") as f:
            json.dump({"providers": [
                {"name": "oaigw", "type": "openai", "base_url": self.server.url("/auth"), "api_key": PLAIN_SECRET},
                {"name": "claudegw", "type": "anthropic", "base_url": f"{self.server.root}/auth",
                 "api_key": PLAIN_SECRET},
            ]}, f)
        reset_provider_caches()

    def tearDown(self):
        self.tmp.cleanup()
        self.env.__exit__(None, None, None)

    def _run(self, model_ref):
        from utils.llm_backend_utils import call_llm_with_text_prompt

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            with self.assertRaises(Exception) as ctx:
                call_llm_with_text_prompt("hi", model_ref)
        return buf.getvalue(), ctx.exception

    def test_openai_protocol_warning_masks_the_echoed_key(self):
        log, err = self._run("oaigw:m")
        self.assertIn("failed on attempt 1", log)
        self.assertIn("Incorrect API key provided: ***", log)
        self.assertNotIn(PLAIN_SECRET, log)
        self.assertEqual(getattr(err, "status_code", None), 401)  # the exception itself is unchanged

    def test_anthropic_protocol_warning_masks_the_echoed_key(self):
        log, err = self._run("claudegw:claude-x")
        self.assertIn("failed on attempt 1", log)
        self.assertNotIn(PLAIN_SECRET, log)
        self.assertEqual(getattr(err, "status_code", None), 401)


if __name__ == "__main__":
    unittest.main()
