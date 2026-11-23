"""Token usage accounting (utils/usage_tracker.py) and its hooks in the three call paths.

Offline: a local fake OpenAI/Anthropic server and fake Gemini clients are used.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_llm_server import FakeServer, IsolatedEnv, reset_provider_caches  # noqa: E402
os.environ.setdefault("PO_PRICING_ONLINE", "0")  # no price-list downloads in tests
from utils import usage_tracker as U  # noqa: E402


def totals():
    return U.report()["totals"]


def row(provider, model):
    """The by_model row without its price estimate (tested in test_pricing.py)."""
    for r in U.report()["by_model"]:
        if (r["provider"], r["model"]) == (provider, model):
            r.pop("cost", None)
            return r
    return None


class TrackerCoreTests(unittest.TestCase):
    def setUp(self):
        U.end_run()
        U.reset()

    def tearDown(self):
        U.end_run()
        U.reset()

    def test_report_shape(self):
        U.record("qwen", "qwen-max", 10, 5)
        U.record_error("qwen", "qwen-max")
        rep = U.report()
        self.assertEqual(set(rep), {"generated_at", "totals", "by_model", "cost"})  # qwen-max has a list price
        self.assertEqual(rep["cost"]["currency"], "CAD")
        self.assertEqual(rep["by_model"][0].pop("cost")["currency"], "CNY")
        self.assertRegex(rep["generated_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")
        self.assertEqual(rep["totals"], {"calls": 2, "errors": 1, "input_tokens": 10, "output_tokens": 5})
        self.assertEqual(rep["by_model"], [{"provider": "qwen", "model": "qwen-max", "calls": 2, "errors": 1,
                                            "input_tokens": 10, "output_tokens": 5}])

    def test_threads_accumulate_exactly(self):
        n_threads, per_thread = 16, 300

        def work(i):
            for _ in range(per_thread):
                U.record("p", f"m{i % 4}", 3, 2)
                if i % 2:
                    U.record_error("p", f"m{i % 4}")

        threads = [threading.Thread(target=work, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        t = totals()
        ok_calls = n_threads * per_thread
        err_calls = (n_threads // 2) * per_thread
        self.assertEqual(t["calls"], ok_calls + err_calls)
        self.assertEqual(t["errors"], err_calls)
        self.assertEqual(t["input_tokens"], 3 * ok_calls)
        self.assertEqual(t["output_tokens"], 2 * ok_calls)
        self.assertEqual([r["model"] for r in U.report()["by_model"]], ["m0", "m1", "m2", "m3"])

    def test_threads_with_live_report_file(self):
        with tempfile.TemporaryDirectory() as d:
            U.begin_run(d)
            threads = [threading.Thread(target=lambda: [U.record("p", "m", 1, 1) for _ in range(50)])
                       for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with open(os.path.join(d, "usage_report.json"), encoding="utf-8") as f:
                live = json.load(f)
            self.assertEqual(live["totals"]["calls"], 400)  # the last write saw every call
            self.assertEqual([n for n in os.listdir(d) if n.endswith(".tmp")], [])

    def test_extract_tokens_shapes(self):
        openai_rsp = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4))
        self.assertEqual(U.extract_tokens(openai_rsp), (12, 4))
        claude_rsp = SimpleNamespace(usage=SimpleNamespace(
            input_tokens=10, output_tokens=6, cache_creation_input_tokens=3, cache_read_input_tokens=None))
        self.assertEqual(U.extract_tokens(claude_rsp), (13, 6))
        gemini_rsp = SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=20, candidates_token_count=5, thoughts_token_count=7,
            tool_use_prompt_token_count=2))
        self.assertEqual(U.extract_tokens(gemini_rsp), (22, 12))
        image_rsp = SimpleNamespace(usage=SimpleNamespace(input_tokens=4, output_tokens=9, total_tokens=13))
        self.assertEqual(U.extract_tokens(image_rsp), (4, 9))
        self.assertEqual(U.extract_tokens({"usage": {"prompt_tokens": 1, "completion_tokens": 2}}), (1, 2))
        # Missing usage: the call still counts, with 0 tokens.
        for missing in (None, object(), SimpleNamespace(usage=None), SimpleNamespace(usage_metadata=None),
                        SimpleNamespace(usage=SimpleNamespace(prompt_tokens=None, completion_tokens="x"))):
            self.assertEqual(U.extract_tokens(missing), (0, 0))
        U.record_response("gemini", "g", SimpleNamespace(usage_metadata=None))
        self.assertEqual(row("gemini", "g"), {"provider": "gemini", "model": "g", "calls": 1, "errors": 0,
                                              "input_tokens": 0, "output_tokens": 0})

    def test_never_raises(self):
        class Weird:
            @property
            def usage(self):
                raise RuntimeError("boom")

        U.record_response("p", "m", Weird())  # usage access explodes: counted with 0 tokens
        U.record(None, None, "nan", -5)
        self.assertEqual(totals()["calls"], 2)
        self.assertEqual(row("unknown", "unknown")["input_tokens"], 0)
        with tempfile.TemporaryDirectory() as d:
            U.begin_run(os.path.join(d, "missing", "dir"))  # folder does not exist: no file, no error
            U.record("p", "m", 1, 1)
            self.assertIsNone(U.end_run())
        with mock.patch("utils.usage_tracker.atomic_write_json", side_effect=OSError("disk full")):
            with tempfile.TemporaryDirectory() as d:
                self.assertIsNone(U.write_report(d))

    def test_overlapping_runs_keep_their_own_reports(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            U.begin_run(a)
            U.record("p", "m", 10, 1)
            U.begin_run(b)  # e.g. a second Streamlit session: must not wipe run a
            U.record("p", "m", 5, 1)
            U.end_run(b)
            U.record("p", "m", 7, 1)
            self.assertEqual(U.end_run(a), os.path.join(a, "usage_report.json"))
            with open(os.path.join(a, "usage_report.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["totals"]["input_tokens"], 22)  # overlap counted in both
            with open(os.path.join(b, "usage_report.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["totals"]["input_tokens"], 5)  # b's file is b's only
            self.assertEqual(U.report(a)["totals"]["calls"], 3)  # still readable after end_run
            self.assertEqual(U.report(b)["totals"]["calls"], 1)

    def test_begin_and_end_run(self):
        U.record("old", "run", 100, 100)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "usage_report.json")
            with open(path, "w") as f:
                f.write("stale")
            U.begin_run(d)  # reset + an empty report replaces the stale one
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["totals"]["calls"], 0)
            U.record("p", "m", 2, 3)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["totals"]["input_tokens"], 2)  # refreshed live
            self.assertEqual(U.end_run(), path)
            U.record("p", "m", 50, 50)  # after end_run: counted in memory, file untouched
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["totals"]["input_tokens"], 2)


class CallPathHookTests(unittest.TestCase):
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
        FakeServer.clear()
        U.end_run()
        U.reset()

    def tearDown(self):
        U.end_run()
        U.reset()
        self.tmp.cleanup()
        self.env.__exit__(None, None, None)

    def providers(self, entries):
        with open(os.environ["PO_PROVIDERS_FILE"], "w", encoding="utf-8") as f:
            json.dump({"providers": entries}, f)
        reset_provider_caches()

    def test_openai_protocol_tokens(self):
        self.providers([{"name": "local", "type": "openai", "base_url": self.server.url(), "api_key": "k"},
                        {"name": "bare", "type": "openai", "base_url": self.server.url("/nousage"),
                         "api_key": "k"}])
        from utils.llm_backend_utils import call_llm_with_text_prompt

        call_llm_with_text_prompt("hi", "local:m1")
        call_llm_with_text_prompt("hi again", "local:m1")
        call_llm_with_text_prompt("hi", "bare:m2")
        self.assertEqual(row("local", "m1"), {"provider": "local", "model": "m1", "calls": 2, "errors": 0,
                                              "input_tokens": 22, "output_tokens": 14})
        self.assertEqual(row("bare", "m2")["calls"], 1)
        self.assertEqual(row("bare", "m2")["input_tokens"], 0)

    def test_openai_protocol_error_is_counted(self):
        self.providers([{"name": "badkey", "type": "openai", "base_url": self.server.url("/auth"),
                         "api_key": "sk-test-NOPE-123456"}])
        from utils.llm_backend_utils import call_llm_with_text_prompt

        with self.assertRaises(Exception):
            call_llm_with_text_prompt("hi", "badkey:m")  # 401: raised at once, as before
        self.assertEqual(row("badkey", "m")["calls"], 1)
        self.assertEqual(row("badkey", "m")["errors"], 1)

    def test_anthropic_protocol_tokens(self):
        self.providers([{"name": "claudeproxy", "type": "anthropic", "base_url": self.server.root,
                         "api_key": "k"}])
        from utils.llm_backend_utils import call_llm_with_text_prompt

        out = call_llm_with_text_prompt("hi", "claudeproxy:claude-x")
        self.assertEqual(out["parsed_response"], {"ok": "claude"})
        self.assertEqual(row("claudeproxy", "claude-x")["input_tokens"], 7)  # 5 + 2 cached
        self.assertEqual(row("claudeproxy", "claude-x")["output_tokens"], 3)

    def test_openai_image_generation_tokens(self):
        self.providers([{"name": "imgs", "type": "openai", "base_url": self.server.url(), "api_key": "k"}])
        from utils.llm_backend_utils import generate_image

        self.assertEqual(generate_image("imgs:gpt-image-1", "a diagram"), "aGVsbG8=")
        self.assertEqual(row("imgs", "gpt-image-1"), {"provider": "imgs", "model": "gpt-image-1", "calls": 1,
                                                      "errors": 0, "input_tokens": 4, "output_tokens": 9, "images": 1,
                                                  "image_output_tokens": 9})

    def test_gemini_text_and_image_tokens(self):
        import utils.gemini_utils as G

        meta = SimpleNamespace(prompt_token_count=30, candidates_token_count=4, thoughts_token_count=6,
                               tool_use_prompt_token_count=None)
        text_rsp = SimpleNamespace(text='{"a": 1}', usage_metadata=meta)
        part = SimpleNamespace(inline_data=SimpleNamespace(data=b"PNG"))
        image_rsp = SimpleNamespace(
            candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))],
            usage_metadata=SimpleNamespace(prompt_token_count=8, candidates_token_count=1290),
        )
        calls = []

        def fake_generate(model, contents, config):
            calls.append(model)
            return image_rsp if "image" in model else text_rsp

        fake_client = SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate))
        with mock.patch.object(G, "get_genai_client", return_value=fake_client):
            out = G.call_gemini_with_contents(contents=["x"], model_name="gemini-3-flash-preview")
            self.assertEqual(out["parsed_response"], {"a": 1})
            self.assertIsNotNone(G.generate_image_with_gemini("gemini-3-pro-image-preview", "a cat"))
        self.assertEqual(row("gemini", "gemini-3-flash-preview")["input_tokens"], 30)
        self.assertEqual(row("gemini", "gemini-3-flash-preview")["output_tokens"], 10)
        self.assertEqual(row("gemini", "gemini-3-pro-image-preview")["output_tokens"], 1290)

    def test_gemini_error_is_counted(self):
        import utils.gemini_utils as G

        def boom(**kw):
            raise RuntimeError("400 INVALID_ARGUMENT: bad request")

        fake_client = SimpleNamespace(models=SimpleNamespace(generate_content=boom))
        with mock.patch.object(G, "get_genai_client", return_value=fake_client):
            with self.assertRaises(RuntimeError):
                G.call_gemini_with_contents(contents=["x"], model_name="gemini-x")
        self.assertEqual(row("gemini", "gemini-x")["errors"], 1)


if __name__ == "__main__":
    unittest.main()
