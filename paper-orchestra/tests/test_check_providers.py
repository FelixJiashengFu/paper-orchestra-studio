"""tools/check_providers.py: the JSON contract, pings against a local fake server, scholar probes.

Offline: LLM pings go to a local fake OpenAI/Anthropic server; scholar probes use a patched
requests.get. Provider variables from the developer's .env are blanked.
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_llm_server import FakeServer, IsolatedEnv  # noqa: E402

TOOL = os.path.join(REPO_ROOT, "tools", "check_providers.py")
SECRET = "sk-test-SUPERSECRET-0123456789"
PROVIDER_KEYS = {"name", "label", "kind", "configured", "model", "model_source", "ok", "busy", "latency_ms", "error"}
SCHOLAR_KEYS = {"name", "ok", "latency_ms", "hits", "error", "busy"}


def load_tool():
    spec = importlib.util.spec_from_file_location("check_providers_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CheckProvidersCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeServer().start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.iso = IsolatedEnv()
        self.tmp = tempfile.TemporaryDirectory()
        self.providers_file = os.path.join(self.tmp.name, "providers.json")
        with open(self.providers_file, "w", encoding="utf-8") as f:
            json.dump({"providers": [
                {"name": "claudeproxy", "type": "anthropic", "base_url": self.server.root, "api_key": "k-123456",
                 "default_model": "claude-x"},
                {"name": "badkey", "type": "openai", "base_url": self.server.url("/auth"), "api_key": SECRET,
                 "default_model": "m"},
                {"name": "deadproxy", "type": "openai", "base_url": "http://127.0.0.1:9/v1", "api_key": "k-123456",
                 "default_model": "m"},
                {"name": "nomodel", "type": "openai", "base_url": self.server.url(), "api_key": "k-123456"},
            ]}, f)
        FakeServer.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args, **env):
        env = self.iso.env_for_subprocess(
            PO_PROVIDERS_FILE=self.providers_file, PO_SCHOLAR_SOURCES="openalex,arxiv",
            OPENAI_API_KEY=SECRET, OPENAI_BASE_URL=self.server.url(), **env)
        proc = subprocess.run([sys.executable, TOOL, *args], cwd=REPO_ROOT, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(SECRET, proc.stdout)
        self.assertNotIn("SUPERSECRET", proc.stderr)
        return proc

    def run_json(self, *args, **env):
        proc = self.run_tool("--json", *args, **env)
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, proc.stdout)  # exactly one JSON object on stdout
        report = json.loads(lines[0])
        self.assertEqual(set(report), {"providers", "scholar"})
        for row in report["providers"]:
            self.assertEqual(set(row), PROVIDER_KEYS)
            self.assertIn(row["kind"], ("openai", "anthropic", "gemini", "cli"))
        for row in report["scholar"]:
            self.assertEqual(set(row), SCHOLAR_KEYS)
        return {r["name"]: r for r in report["providers"]}, report["scholar"]

    def test_configuration_only(self):
        rows, scholar = self.run_json()
        self.assertEqual(FakeServer.seen(), [])  # no request without --ping
        self.assertTrue(rows["openai"]["configured"])
        self.assertEqual(rows["openai"]["model"], "gpt-6.2-sol")
        self.assertEqual(rows["anthropic"]["label"], "Anthropic · Claude")
        self.assertFalse(rows["anthropic"]["configured"])
        self.assertEqual(rows["qwen"]["model"], "qwen-max")
        self.assertIsNone(rows["xai"]["model"])
        self.assertEqual(rows["hunyuan"]["label"], "腾讯混元 HY · TokenHub")
        self.assertEqual(rows["qianfan"]["model"], "ernie-5.1")
        self.assertNotIn("doubao", rows)
        self.assertNotIn("minimax", rows)
        self.assertEqual(rows["claudeproxy"]["label"], "claudeproxy")
        self.assertEqual(rows["claudeproxy"]["kind"], "anthropic")
        for row in rows.values():
            self.assertIsNone(row["ok"])
            self.assertIsNone(row["latency_ms"])
        self.assertEqual([s["name"] for s in scholar], ["openalex", "arxiv"])
        self.assertTrue(all(s["ok"] is None and s["hits"] is None for s in scholar))

    def test_ping_configured_providers(self):
        rows, _ = self.run_json("--ping", OPENAI_DEFAULT_MODEL="gpt-test")
        self.assertIs(rows["openai"]["ok"], True)
        self.assertEqual(rows["openai"]["model"], "gpt-test")
        self.assertIsInstance(rows["openai"]["latency_ms"], int)
        self.assertIsNone(rows["openai"]["error"])
        self.assertIs(rows["claudeproxy"]["ok"], True)
        self.assertIs(rows["badkey"]["ok"], False)
        self.assertIn("401", rows["badkey"]["error"])
        self.assertIs(rows["deadproxy"]["ok"], False)
        self.assertTrue(rows["deadproxy"]["error"])
        self.assertIsNone(rows["nomodel"]["ok"])  # configured, but no model to ping
        self.assertIn("NOMODEL_DEFAULT_MODEL", rows["nomodel"]["error"])
        self.assertIsNone(rows["qwen"]["ok"])  # not configured: not pinged

        bodies = {r["path"]: r["body"] for r in FakeServer.seen()}
        self.assertEqual(bodies["/v1/chat/completions"]["model"], "gpt-test")
        self.assertEqual(bodies["/v1/chat/completions"]["max_tokens"], 8)
        self.assertEqual(bodies["/v1/messages"]["max_tokens"], 8)
        self.assertEqual(len(FakeServer.seen()), 3)  # openai, claudeproxy, badkey

    def test_ping_only_the_requested_model(self):
        rows, _ = self.run_json("--ping", "--model", "claudeproxy:claude-other")
        self.assertIs(rows["claudeproxy"]["ok"], True)
        self.assertEqual(rows["claudeproxy"]["model"], "claude-other")
        self.assertIsNone(rows["openai"]["ok"])
        self.assertEqual([r["body"]["model"] for r in FakeServer.seen()], ["claude-other"])

    def test_ping_model_overrides_one_provider_and_tests_the_rest(self):
        # The diagnostics page lets each row pick its own test model ("测试模型").
        rows, _ = self.run_json("--ping", "--ping-model", "claudeproxy=claude-flash",
                                "--ping-model", "openai=openai:gpt-mini", "--ping-model", "bogus=x",
                                "--ping-model", "nomodel=auto", OPENAI_DEFAULT_MODEL="gpt-test")
        self.assertEqual(rows["claudeproxy"]["model"], "claude-flash")
        self.assertEqual(rows["claudeproxy"]["model_source"], "test")
        self.assertEqual(rows["openai"]["model"], "gpt-mini")  # "openai:" prefix dropped
        self.assertIs(rows["badkey"]["ok"], False)  # still tested with its own default
        self.assertEqual(rows["badkey"]["model_source"], "default")
        self.assertIsNone(rows["nomodel"]["ok"])  # "auto" is not a model: ignored
        sent = sorted(r["body"]["model"] for r in FakeServer.seen())
        self.assertIn("claude-flash", sent)
        self.assertIn("gpt-mini", sent)
        self.assertNotIn("gpt-test", sent)

    def test_parse_ping_models(self):
        tool = load_tool()
        with mock.patch("sys.stderr", new=io.StringIO()):
            got = tool.parse_ping_models(
                ["Gemini=gemini-3.8-flash", "gemini=  ", "x", "=m", "qwen=qwen:qwen-max", "qwen = qwen-plus "],
                ["gemini", "qwen"])
        self.assertEqual(got, {"gemini": "gemini-3.8-flash", "qwen": "qwen-plus"})

    def test_list_models_per_configured_provider(self):
        with open(self.providers_file, "w", encoding="utf-8") as f:
            json.dump({"providers": [
                {"name": "claudeproxy", "type": "anthropic", "base_url": self.server.root, "api_key": "k-123456"},
                {"name": "badkey", "type": "openai", "base_url": self.server.url("/auth"), "api_key": SECRET},
                {"name": "nolist", "type": "openai", "base_url": self.server.url("/nolist"), "api_key": "k-123456"},
            ]}, f)
        proc = self.run_tool("--json", "--list-models", GEMINI_API_KEY="AIza-test-gemini-key",
                             GEMINI_BASE_URL=self.server.root)
        report = json.loads(proc.stdout)
        self.assertEqual(set(report), {"providers", "scholar", "models"})
        rows = {r["name"]: r for r in report["models"]}
        configured = {"openai", "gemini", "claudeproxy", "badkey", "nolist"}
        self.assertLessEqual(configured, set(rows))
        # Anything else is a local server that happens to run on this machine.
        self.assertLessEqual(set(rows) - configured, {"ollama", "lmstudio", "vllm"})
        for row in rows.values():
            self.assertEqual(set(row), {"name", "label", "kind", "ok", "error", "models"})
        # Newest first; embeddings, speech and non-Gemini image models left out.
        self.assertEqual(rows["openai"]["models"], ["gpt-new", "gpt-old"])
        # Bare ids where they resolve to the provider (no Anthropic key here, so
        # claude-* goes to the configured Anthropic-protocol proxy)...
        self.assertEqual(rows["claudeproxy"]["models"], ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"])
        self.assertEqual(rows["gemini"]["models"],
                         ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3-pro-image-preview", "gemma-3-27b-it"])
        self.assertIs(rows["badkey"]["ok"], False)
        self.assertIn("401", rows["badkey"]["error"])
        self.assertIn("手动输入", rows["nolist"]["error"])
        tool = load_tool()
        with mock.patch.dict(os.environ, {"PO_PROVIDERS_FILE": self.providers_file, "ANTHROPIC_API_KEY": "",
                                          "OPENAI_API_KEY": SECRET}):
            import utils.providers as P
            P._registry_cache = None
            # ...and "provider:id" where a bare id would land elsewhere.
            self.assertEqual(tool.model_ref(P.get_provider("nolist"), "gpt-new"), "nolist:gpt-new")
            self.assertEqual(tool.model_ref(P.get_provider("openai"), "gpt-new"), "gpt-new")
            P._registry_cache = None
        self.assertIsNone(rows["openai"]["error"])
        gemini_paths = [r["path"] for r in FakeServer.seen() if "v1beta" in r["path"]]
        self.assertTrue(gemini_paths and all(p.startswith("/v1beta/models") for p in gemini_paths))
        self.assertFalse([r for r in FakeServer.seen() if r["body"] is not None])  # no paid request

    def test_presets_without_default_still_have_a_test_model(self):
        tool = load_tool()
        import utils.providers as P
        specs = {s.name: s for s in P._builtin_providers()}
        self.assertEqual(tool.default_model(specs["antdigital"]), "ling-3.1-flash")
        self.assertEqual(tool.default_model(specs["longcat"]), "LongCat-2.5")
        self.assertEqual(specs["antdigital"].resolved_default_model(), "")  # kept out of auto

    def test_list_models_includes_a_running_local_server(self):
        # Ollama's URL is not set, but it answers at its default address: its models
        # are listed with the "ollama:" prefix. LM Studio / vLLM (not running) are left out.
        tool = load_tool()
        with mock.patch.dict(os.environ, {"PO_PROVIDERS_FILE": self.providers_file, "OPENAI_API_KEY": "",
                                          "OLLAMA_BASE_URL": "", "LMSTUDIO_BASE_URL": "", "VLLM_BASE_URL": ""}):
            import utils.providers as P
            P._registry_cache = None
            P._clients.clear()
            real_list = tool.list_provider_models

            def fake_list(spec, timeout):
                if spec.name == "ollama":
                    self.assertLessEqual(timeout, tool.LOCAL_PROBE_TIMEOUT)
                    return ["gemma4:12b-it-qat", "qwen3.8:latest"]
                if spec.name in ("lmstudio", "vllm"):
                    raise ConnectionError("Connection refused")
                return real_list(spec, timeout)

            with open(self.providers_file, "w", encoding="utf-8") as f:
                json.dump({"providers": []}, f)
            P._registry_cache = None
            with mock.patch.object(tool, "list_provider_models", fake_list):
                rows = {r["name"]: r for r in tool.list_models(5)}
            P._registry_cache = None
        self.assertEqual(rows["ollama"]["models"], ["ollama:gemma4:12b-it-qat", "ollama:qwen3.8:latest"])
        self.assertNotIn("lmstudio", rows)
        self.assertNotIn("vllm", rows)
        # The ref routes back to Ollama with the tag intact.
        from utils.providers import resolve_model
        spec, bare = resolve_model("ollama:gemma4:12b-it-qat")
        self.assertEqual((spec.name, bare), ("ollama", "gemma4:12b-it-qat"))

    def test_list_models_text(self):
        out = self.run_tool("--list-models").stdout
        self.assertIn("各平台可用模型", out)
        self.assertIn("gpt-new", out)

    def test_ping_timeout(self):
        with open(self.providers_file, "w", encoding="utf-8") as f:
            json.dump({"providers": [{"name": "slowproxy", "type": "openai", "base_url": self.server.url("/slow"),
                                      "api_key": "k-123456", "default_model": "m"}]}, f)
        rows, _ = self.run_json("--ping", "--model", "slowproxy:m", "--timeout", "1")
        self.assertIs(rows["slowproxy"]["ok"], False)
        self.assertIn("timed out", rows["slowproxy"]["error"].lower())

    def test_broken_providers_file_still_reports_builtins(self):
        with open(self.providers_file, "w", encoding="utf-8") as f:
            f.write("{ not json")
        rows, _ = self.run_json()
        self.assertTrue(rows["openai"]["configured"])
        self.assertNotIn("claudeproxy", rows)

    def test_custom_entry_overriding_a_builtin_is_listed_once(self):
        with open(self.providers_file, "w", encoding="utf-8") as f:
            json.dump({"providers": [{"name": "openai", "type": "openai", "base_url": self.server.url(),
                                      "api_key": "k-123456", "default_model": "relay-model"}]}, f)
        proc = self.run_tool("--json", "--ping")
        rows = [r for r in json.loads(proc.stdout)["providers"] if r["name"] == "openai"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["model"], rows[0]["ok"]), ("relay-model", True))

    def test_human_table(self):
        proc = self.run_tool("--ping")
        self.assertIn("LLM 服务商", proc.stdout)
        self.assertIn("学术检索源", proc.stdout)
        self.assertIn("可用", proc.stdout)
        self.assertIn("失败", proc.stdout)


class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


class ScholarProbeTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        import utils.scholar_utils as S

        self.S = S
        self.saved_env = os.environ.get("PO_SCHOLAR_SOURCES")
        os.environ["PO_SCHOLAR_SOURCES"] = "openalex,crossref,dblp,europepmc"
        self.saved_limiters = dict(S._LIMITERS)
        for name in list(S._LIMITERS):
            S._LIMITERS[name] = S.RateLimiter(f"test_probe_{name}", 0.0)

    def tearDown(self):
        self.S._LIMITERS.clear()
        self.S._LIMITERS.update(self.saved_limiters)
        if self.saved_env is None:
            os.environ.pop("PO_SCHOLAR_SOURCES", None)
        else:
            os.environ["PO_SCHOLAR_SOURCES"] = self.saved_env

    def test_probe_results(self):
        import requests

        calls = []

        def fake_get(url, params=None, headers=None, timeout=None):
            calls.append(url)
            if "openalex" in url:
                return FakeResponse(200, {"results": [{"title": "Deep Learning", "authorships": [],
                                                       "publication_year": 2015}]})
            if "crossref" in url:
                raise requests.ConnectionError(
                    "HTTPSConnectionPool(host='api.crossref.org', port=443): Max retries exceeded with url: "
                    "/works?query=x&api_key=TOPSECRET123 (Caused by NewConnectionError('<x>: Failed to "
                    "establish a new connection: [Errno 111] Connection refused'))")
            if "dblp" in url:
                return FakeResponse(404, {})
            return FakeResponse(200, {"resultList": {"result": []}})  # europepmc: reachable, 0 hits

        with mock.patch.object(self.S.requests, "get", side_effect=fake_get), mock.patch("builtins.print"):
            report = self.tool.collect(scholar=True, timeout=5)
        rows = {r["name"]: r for r in report["scholar"]}
        self.assertEqual(list(rows), ["openalex", "crossref", "dblp", "europepmc"])
        self.assertEqual((rows["openalex"]["ok"], rows["openalex"]["hits"]), (True, 1))
        self.assertIsInstance(rows["openalex"]["latency_ms"], int)
        self.assertIs(rows["crossref"]["ok"], False)
        self.assertNotIn("TOPSECRET", rows["crossref"]["error"])
        self.assertIn("连接被拒绝", rows["crossref"]["error"])
        self.assertEqual((rows["dblp"]["ok"], rows["dblp"]["hits"]), (False, None))
        self.assertIn("HTTP 404", rows["dblp"]["error"])
        self.assertEqual((rows["europepmc"]["ok"], rows["europepmc"]["hits"]), (True, 0))
        self.assertEqual(sum("crossref" in u for u in calls), 1)  # probe mode: no retries

    def test_come_back_later_answers_are_busy_not_failures(self):
        # dblp 202 (queued), OpenAlex 503, Crossref 429: shown as 暂时繁忙, not 失败.
        statuses = {"dblp": 202, "openalex": 503, "crossref": 429}

        def fake_get(url, params=None, headers=None, timeout=None):
            for name, code in statuses.items():
                if name in url:
                    return FakeResponse(code, {})
            return FakeResponse(404, {})  # europepmc: a real failure

        with mock.patch.object(self.S.requests, "get", side_effect=fake_get), \
                mock.patch.object(self.S, "_PROBE_MAX_WAIT", 0.0), mock.patch("builtins.print"):
            report = self.tool.collect(scholar=True, timeout=5)
        rows = {r["name"]: r for r in report["scholar"]}
        for name in statuses:
            self.assertIs(rows[name]["busy"], True, name)
            self.assertIs(rows[name]["ok"], False)
        self.assertIn("排队", rows["dblp"]["error"])
        self.assertIn("限流", rows["crossref"]["error"])
        self.assertIn("繁忙", rows["openalex"]["error"])
        self.assertIs(rows["europepmc"]["busy"], False)
        text = self.tool.render_text(report, ping=False, scholar=True)
        self.assertIn("暂时繁忙", text)
        self.S._rate_state.clear()

    def test_probe_mode_is_scoped(self):
        S = self.S
        self.assertIsNone(S.last_http_result("openalex"))
        with mock.patch.object(S.requests, "get", return_value=FakeResponse(503, {})), \
                mock.patch.object(S.time, "sleep"), mock.patch("builtins.print"):
            S.openalex_search("x", 1)  # outside probe mode: nothing recorded
            # A second 503 in a row puts the overloaded source on a cooldown,
            # so the lookup moves on instead of using its third attempt.
            self.assertEqual(S.requests.get.call_count, 2)
            self.assertTrue(S._cooling_down("openalex"))
            self.assertIsNone(S.last_http_result("openalex"))
            S._rate_state.clear()
            with S.probe_mode():
                S.openalex_search("x", 1)
                self.assertEqual(S.last_http_result("openalex")["status"], 503)
            # probe mode: one retry of a transient answer (2 attempts), then give up
            self.assertEqual(S.requests.get.call_count, 4)
            self.assertIsNone(S.last_http_result("openalex"))
        S._rate_state.clear()

    def test_202_is_retried_like_429_and_5xx(self):
        # dblp answers 202 while it queues a query: come back later, not a failure.
        S = self.S
        self.assertTrue(S.is_transient_status(202))
        self.assertTrue(S.is_transient_status(429))
        self.assertTrue(S.is_transient_status(503))
        self.assertFalse(S.is_transient_status(404))
        self.assertFalse(S.is_transient_status(None))
        ok = FakeResponse(200, {"result": {"hits": {"hit": []}}})
        with mock.patch.object(S.requests, "get", side_effect=[FakeResponse(202), ok]) as get, \
                mock.patch.object(S.time, "sleep"), mock.patch("builtins.print"):
            rsp = S._http_get("dblp", "https://dblp.org/search/publ/api")
        self.assertIs(rsp, ok)
        self.assertEqual(get.call_count, 2)
        S._rate_state.clear()

    def test_timeout_is_a_grey_retry_note_until_the_last_try(self):
        # Europe PMC sometimes takes over 15 s: a retry note, not a red error line.
        S = self.S
        S._rate_state.clear()
        ok = FakeResponse(200, {})
        slow = S.requests.ReadTimeout("HTTPSConnectionPool(host='www.ebi.ac.uk', port=443): Read timed out.")
        with mock.patch.object(S.requests, "get", side_effect=[slow, ok]), \
                mock.patch.object(S.time, "sleep"), mock.patch("builtins.print") as pr:
            self.assertIs(S._http_get("europepmc", "https://www.ebi.ac.uk/europepmc/x"), ok)
        lines = [c.args[0] for c in pr.call_args_list]
        self.assertEqual(len(lines), 1)
        self.assertIn("europepmc slow to answer", lines[0])
        self.assertIn("(2/3)", lines[0])
        self.assertNotIn("error", lines[0].lower())
        S._rate_state.clear()
        with mock.patch.object(S.requests, "get", side_effect=[slow, slow, slow]), \
                mock.patch.object(S.time, "sleep"), mock.patch("builtins.print") as pr:
            self.assertIsNone(S._http_get("europepmc", "https://www.ebi.ac.uk/europepmc/x"))
        lines = [c.args[0] for c in pr.call_args_list]
        self.assertEqual(sum("slow to answer" in l for l in lines), 2)
        self.assertIn("request error", lines[-1])  # the last try is still reported as a failure
        S._rate_state.clear()

    def test_long_retry_after_moves_on_instead_of_waiting(self):
        # OpenAlex answering 503 with Retry-After: 60 used to hold every lookup
        # for minutes; now the source rests and the next one is asked at once.
        S = self.S
        S._rate_state.clear()
        busy = FakeResponse(503)
        busy.headers = {"Retry-After": "60"}
        sleeps = []
        with mock.patch.object(S.requests, "get", return_value=busy) as get, \
                mock.patch.object(S.time, "sleep", side_effect=sleeps.append), mock.patch("builtins.print") as pr:
            self.assertIsNone(S._http_get("openalex", "https://api.openalex.org/works"))
            self.assertEqual(get.call_count, 1)
            self.assertTrue(S._cooling_down("openalex"))
            self.assertIsNone(S._http_get("openalex", "https://api.openalex.org/works"))  # skipped, no request
            self.assertEqual(get.call_count, 1)
        self.assertFalse([t for t in sleeps if t > 1])
        self.assertIn("using the other sources meanwhile", pr.call_args_list[0].args[0])
        S._rate_state.clear()

    def test_429_slows_the_pace_and_successes_restore_it(self):
        S = self.S
        S._rate_state.clear()
        lim = S.RateLimiter("test_pace", 1.0)
        with mock.patch("builtins.print"):
            lim.slow_down()
            self.assertAlmostEqual(lim.min_interval, 1.5)
            for _ in range(10):
                lim.slow_down()
        self.assertAlmostEqual(lim.min_interval, 4.0)  # capped at 4x the base
        for _ in range(S.RateLimiter.SPEEDUP_AFTER * 30):
            lim.note_success()
        self.assertAlmostEqual(lim.min_interval, 1.0)

    def test_backoff_holds_after_slowing_down(self):
        S = self.S
        lim = S.RateLimiter("test_backoff", 1.0)
        with mock.patch("builtins.print"):
            for _ in range(5):
                lim.slow_down()  # 4 s between requests now
        t0 = S.time.time()
        lim.backoff(8)
        # Another process adds only the base interval to the shared slot.
        self.assertGreaterEqual(lim._last + lim.base_interval, t0 + 8 - 0.05)

    def test_a_429_streak_never_shortens_a_longer_cooldown(self):
        S = self.S
        S._rate_state.clear()
        self.addCleanup(S._rate_state.clear)
        with mock.patch("builtins.print"):
            S._cool_down("openalex", 300, "test", "test")
            until = S._rate_state["openalex"]["until"]
            for _ in range(S._COOLDOWN_AFTER_429):
                S._note_status("openalex", 429)
        self.assertGreaterEqual(S._rate_state["openalex"]["until"], until)

    def test_time_budget_turns_lookups_fail_fast(self):
        S = self.S
        S._rate_state.clear()
        S.reset_time_budget()
        self.addCleanup(S.reset_time_budget)
        S.start_time_budget(1)
        S._budget["deadline"] = S.time.time() - 1  # the budget has run out
        with mock.patch.object(S.requests, "get", return_value=FakeResponse(429)) as get, \
                mock.patch.object(S.time, "sleep"), mock.patch("builtins.print") as pr:
            self.assertIsNone(S._http_get("crossref", "https://api.crossref.org/works"))
            self.assertIsNone(S._http_get("dblp", "https://dblp.org/x"))
        self.assertEqual(get.call_count, 2)  # one attempt each, no retries
        lines = [c.args[0] for c in pr.call_args_list]
        self.assertEqual(sum("asked once, without waiting" in l for l in lines), 1)
        # A new budget while one runs keeps the first deadline; 0 means none.
        S.reset_time_budget()
        S.start_time_budget(0)
        self.assertFalse(S._over_budget())
        S._rate_state.clear()

    def test_probe_waits_briefly_before_retrying(self):
        S = self.S
        busy = FakeResponse(429)
        busy.headers = {"Retry-After": "120"}
        waits = []
        with mock.patch.object(S.requests, "get", return_value=busy), \
                mock.patch.object(S._LIMITERS["openalex"], "backoff", side_effect=waits.append), \
                mock.patch("builtins.print"):
            with S.probe_mode():
                self.assertIsNone(S._http_get("openalex", "https://api.openalex.org/works"))
        self.assertTrue(waits)
        self.assertLessEqual(max(waits), S._PROBE_MAX_WAIT)  # never 120 s inside a diagnostics click
        S._rate_state.clear()

    def test_short_error_masks_keys(self):
        err = self.tool.short_error(
            f"Error code: 401 - Incorrect API key provided: {SECRET}; x-api-key: abcdef123456; Bearer zzzzzz",
            secrets=(SECRET,))
        self.assertNotIn("SUPERSECRET", err)
        self.assertNotIn("abcdef123456", err)
        self.assertNotIn("zzzzzz", err)
        self.assertLessEqual(len(self.tool.short_error("x" * 1000)), 240)

    def test_short_error_reasons_with_urllib3_2_and_sdk_causes(self):
        # urllib3 2.x quotes the reason with "..." (and the host with '...' inside it).
        refused = ("HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded with url: /x?api_key=S3CRET "
                   "(Caused by NewConnectionError(\"HTTPConnection(host='127.0.0.1', port=9): Failed to "
                   "establish a new connection: [Errno 111] Connection refused\"))")
        dns = ("HTTPSConnectionPool(host='api.openalex.org', port=443): Max retries exceeded with url: /works "
               "(Caused by NameResolutionError(\"HTTPSConnection(host='api.openalex.org', port=443): Failed to "
               "resolve 'api.openalex.org' ([Errno -2] Name or service not known)\"))")
        out = self.tool.short_error(refused)
        self.assertTrue(out.startswith("网络错误（连接被拒绝）：127.0.0.1: NewConnectionError"), out)
        self.assertNotIn("S3CRET", out)
        out = self.tool.short_error(dns)
        self.assertTrue(out.startswith("网络错误（域名解析失败）：api.openalex.org"), out)
        self.assertIn("Name or service not known", out)

        # SDK connection errors: "Connection error." alone hides the reason, kept from __cause__.
        class APIConnectionError(Exception):
            pass

        try:
            try:
                raise ConnectionRefusedError(111, "Connection refused")
            except ConnectionRefusedError as inner:
                raise APIConnectionError("Connection error.") from inner
        except APIConnectionError as e:
            out = self.tool.short_error(e)
        self.assertTrue(out.startswith("网络错误（连接被拒绝）：APIConnectionError: Connection error."), out)
        self.assertIn("ConnectionRefusedError", out)

    def test_timed_out_ping_keeps_configuration_rows(self):
        progress = {"rows": {"openai": {"name": "openai", "label": "OpenAI", "kind": "openai", "configured": True,
                                        "model": "gpt-4.1", "ok": None, "latency_ms": None, "error": None},
                             "qwen": {"name": "qwen", "label": "Q", "kind": "openai", "configured": False,
                                      "model": "qwen-max", "ok": None, "latency_ms": None, "error": None}},
                    "targets": {"openai": "gpt-4.1"}}
        rows = {r["name"]: r for r in self.tool._timed_out_provider_rows(progress, [], 30)}
        self.assertEqual(set(rows), {"openai", "qwen"})
        self.assertIs(rows["openai"]["ok"], False)
        self.assertIn("超时", rows["openai"]["error"])
        self.assertIsNone(rows["qwen"]["ok"])


class _FakeSpec:
    def __init__(self, name, kind, model="m-default"):
        self.name, self.kind, self.builtin, self._model = name, kind, True, model

    def has_credentials(self):
        return True

    def resolved_default_model(self):
        return self._model

    def resolved_api_key(self):
        return "k-123456789"


class _APIError(Exception):
    """Shaped like google-genai's APIError (code + status)."""

    def __init__(self, code, status, message):
        super().__init__(f"{code} {status}. {message}")
        self.code, self.status = code, status


class GeminiPingTests(unittest.TestCase):
    """The Gemini test asks for little thinking and tolerates a busy server."""

    def setUp(self):
        self.tool = load_tool()
        self.calls = []

    def fake_client(self, *outcomes):
        outcomes = list(outcomes)
        calls = self.calls

        class Models:
            def generate_content(self, model, contents, config):
                calls.append(config)
                out = outcomes.pop(0)
                if isinstance(out, Exception):
                    raise out
                return out

        class Client:
            models = Models()

        return Client()

    def ping(self, client, timeout=30.0):
        with mock.patch("utils.providers.get_client", return_value=client), \
                mock.patch.object(self.tool.time, "sleep"):
            return self.tool.ping_provider(_FakeSpec("gemini", "gemini"), "gemini-3.8-flash", timeout)

    def test_low_thinking_and_fallback_when_the_model_rejects_it(self):
        client = self.fake_client(Exception("400 INVALID_ARGUMENT. Thinking level is not supported."), "ok")
        self.ping(client)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(str(self.calls[0].thinking_config.thinking_level).split(".")[-1].upper(), "LOW")
        self.assertIsNone(self.calls[1].thinking_config)
        self.assertEqual(self.calls[0].max_output_tokens, self.tool.PING_MAX_TOKENS)

    def test_busy_answer_is_retried_once(self):
        busy = lambda: _APIError(503, "UNAVAILABLE", "The model is overloaded.")  # noqa: E731
        self.ping(self.fake_client(busy(), "ok"))
        self.assertEqual(len(self.calls), 2)
        with self.assertRaises(Exception):
            self.ping(self.fake_client(busy(), busy(), "unused"))

    def test_busy_then_thinking_rejected_still_fails_loudly_or_succeeds(self):
        thinking_400 = lambda: _APIError(400, "INVALID_ARGUMENT", "thinking_level is not supported")  # noqa: E731
        busy = lambda: _APIError(503, "UNAVAILABLE", "overloaded")  # noqa: E731
        # busy, then the thinking 400, then success without thinking: fine
        self.calls.clear()
        self.ping(self.fake_client(busy(), thinking_400(), "ok"))
        self.assertEqual(len(self.calls), 3)
        # thinking 400, then busy twice: the error must surface (never reported as 可用)
        self.calls.clear()
        with self.assertRaises(Exception):
            self.ping(self.fake_client(thinking_400(), busy(), busy(), "unused"))
        self.assertEqual(len(self.calls), 3)

    def test_no_retry_without_time_left(self):
        with self.assertRaises(Exception):
            self.ping(self.fake_client(_APIError(504, "DEADLINE_EXCEEDED", "late"), "unused"), timeout=5.0)

    def test_busy_is_judged_by_status_not_by_words(self):
        busy = self.tool.is_busy_error
        self.assertTrue(busy(_APIError(504, "DEADLINE_EXCEEDED", "x")))
        self.assertTrue(busy(Exception("ServerError: Deadline expired before operation could complete.")))
        e = Exception("Overloaded")
        e.status_code = 529
        self.assertTrue(busy(e))
        nf = Exception("HTTP 404: The model qwen-x is unavailable")
        nf.status_code = 404
        self.assertFalse(busy(nf))
        self.assertFalse(busy(Exception("ProxyError: Tunnel connection failed: 503 Service Unavailable")))
        self.assertFalse(busy(Exception("HTTP 400: max_tokens must be <= 503")))

    def test_deadline_is_reported_as_busy(self):
        specs = [_FakeSpec("gemini", "gemini", "gemini-3.8-flash"), _FakeSpec("quotaprov", "openai")]

        def fake_ping(spec, model, timeout, timing=None):
            if spec.name == "gemini":
                raise Exception("ServerError: Deadline expired before operation could complete.")
            raise Exception("HTTP 429: You exceeded your current quota, please check your plan and billing")

        with mock.patch.object(self.tool, "_all_providers", return_value=specs), \
                mock.patch.object(self.tool, "ping_provider", side_effect=fake_ping):
            rows = {r["name"]: r for r in self.tool.check_providers(True, [], 5.0)}
        self.assertIs(rows["gemini"]["busy"], True)
        self.assertIs(rows["gemini"]["ok"], False)
        self.assertIn("稍后再测", rows["gemini"]["error"])
        self.assertIs(rows["quotaprov"]["busy"], False)
        self.assertIn("没有额度", rows["quotaprov"]["error"])


if __name__ == "__main__":
    unittest.main()
