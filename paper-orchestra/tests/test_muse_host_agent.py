"""Host-agent mode (PO_HOST_AGENT=muse): every LLM step runs on the Muse Code CLI.

Offline: `muse` is a small fake script that records its arguments and answers
in Muse Code 1.4's `--json` event format (or plain text).
"""

import json
import os
import stat
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_llm_server import IsolatedEnv, reset_provider_caches  # noqa: E402

FAKE_MUSE = r'''#!__PYTHON__
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_MUSE_LOG"]
if args[:1] == ["--version"]:
    print("Muse Code 9.9.9 (fake)")
    sys.exit(0)
prompt = open(args[args.index("--prompt-file") + 1], encoding="utf-8").read()
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "prompt": prompt, "cwd": os.getcwd(),
                        "has_meta_key": "META_API_KEY" in os.environ,
                        "leaked": sorted(k for k in os.environ if k in ("OPENAI_API_KEY",) and os.environ[k])}) + "\n")
mode = os.environ.get("FAKE_MUSE_MODE", "ok")
if mode == "fail":
    sys.stderr.write("muse: usage limit reached; run /upgrade\n")
    sys.exit(1)
if mode == "login":
    sys.stderr.write("muse: not logged in\n")
    sys.exit(1)
answer = os.environ.get("FAKE_MUSE_ANSWER", '{"ok": true}')
if "--json" in args:
    def ev(kind, payload, stream="session"):
        print(json.dumps({"schema_version": 1, "stream": {"kind": stream}, "payload_type": kind, "payload": payload}))
    ev("run.lifecycle.started", {"kind": "run_started"})
    ev("run.output.delta", {"kind": "run_output_delta", "text": answer})
    ev("run.usage", {"kind": "run_usage", "models": [{"model": "muse-wonder-1.0", "input_tokens": 1234,
                                                       "output_tokens": 56, "cached_tokens": 0, "calls": 1}]},
       stream="exec.usage")
    ev("run.terminal.completed", {"kind": "run_terminal", "terminal": "completed", "text": answer, "reason": None})
else:
    print(answer)
'''


@unittest.skipIf(sys.platform.startswith("win"), "the fake CLI is a POSIX script")
class MuseHostAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cli = os.path.join(self.tmp.name, "muse")
        with open(self.cli, "w", encoding="utf-8") as f:
            f.write(FAKE_MUSE.replace("__PYTHON__", sys.executable))
        os.chmod(self.cli, os.stat(self.cli).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.tmp.name, "calls.jsonl")
        self.iso = IsolatedEnv(extra_names=[
            "PO_HOST_AGENT", "PO_HOST_AGENT_STRICT", "MUSE_CLI", "FAKE_MUSE_LOG", "FAKE_MUSE_MODE",
            "FAKE_MUSE_ANSWER", "PO_MUSE_JSON", "PO_MUSE_LEAN", "PO_MUSE_REASONING_EFFORT", "PO_MUSE_EXTRA_ARGS",
            "META_API_KEY", "PO_AUTO_MODEL", "PO_DEFAULT_PROVIDER", "PO_TASK_MODEL_OUTLINE",
            "PO_TASK_MODEL_LITERATURE_SEARCH", "PO_TASK_MODEL_IMAGE", "PO_MUSE_ALLOW_API_KEY",
            "PO_PROVIDERS_FILE",
        ])
        self.iso.__enter__()
        for name in ("PO_MUSE_JSON", "PO_MUSE_LEAN", "PO_MUSE_REASONING_EFFORT", "PO_MUSE_EXTRA_ARGS",
                     "FAKE_MUSE_MODE", "FAKE_MUSE_ANSWER", "PO_HOST_AGENT_STRICT", "PO_MUSE_ALLOW_API_KEY"):
            os.environ.pop(name, None)
        os.environ.update({"PO_HOST_AGENT": "muse", "MUSE_CLI": self.cli, "FAKE_MUSE_LOG": self.log,
                           "PO_PROVIDERS_FILE": os.path.join(self.tmp.name, "providers.json")})
        reset_provider_caches()

    def tearDown(self):
        self.iso.__exit__(None, None, None)
        self.tmp.cleanup()

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    # ---- routing ---------------------------------------------------------
    def test_auto_routes_every_task_to_muse(self):
        from utils import llm_backend_utils as L

        os.environ["OPENAI_API_KEY"] = "sk-test-other-provider-000000"
        os.environ["GEMINI_API_KEY"] = "AIzaTESTTESTTESTTEST"
        os.environ["PO_TASK_MODEL_OUTLINE"] = "gpt-6.2-sol"
        reset_provider_caches()
        routing = L.describe_auto_routing()
        self.assertEqual(set(routing.values()), {"muse:default"}, routing)
        self.assertEqual(L.credential_problems(["auto"]), [])

    def test_strict_mode_serves_other_models_with_muse(self):
        from utils.providers import resolve_model

        os.environ["OPENAI_API_KEY"] = "sk-test-other-provider-000000"
        reset_provider_caches()
        for ref in ("gpt-6.2-sol", "claude-sonnet-5-5", "deepseek:deepseek-flash", "gemini-4-argon", "ollama:llama3:8b"):
            spec, model = resolve_model(ref)
            self.assertEqual((spec.name, spec.kind, model), ("muse", "cli", ""), ref)
        self.assertEqual(resolve_model("muse:muse-spark-1.2")[1], "muse-spark-1.2")
        self.assertEqual(resolve_model("muse-ember-1.0"), (resolve_model("muse:default")[0], "muse-ember-1.0"))
        os.environ["MUSE_DEFAULT_MODEL"] = "muse-spark-1.2"
        self.assertEqual(resolve_model("gpt-6.2-sol")[1], "muse-spark-1.2")

    def test_non_strict_keeps_explicit_models_and_pins(self):
        from utils import llm_backend_utils as L
        from utils.providers import resolve_model

        os.environ["PO_HOST_AGENT_STRICT"] = "0"
        os.environ["OPENAI_API_KEY"] = "sk-test-other-provider-000000"
        os.environ["PO_TASK_MODEL_OUTLINE"] = "gpt-6.2-sol"
        reset_provider_caches()
        self.assertEqual(resolve_model("gpt-6.2-sol")[0].name, "openai")
        self.assertEqual(L.resolve_task_model("auto", "outline"), "gpt-6.2-sol")
        self.assertEqual(L.resolve_task_model("auto", "section_writing"), "muse:default")

    def test_no_other_search_or_image_api_in_strict_mode(self):
        from utils import llm_backend_utils as L

        os.environ["GEMINI_API_KEY"] = "AIzaTESTTESTTESTTEST"
        os.environ["PO_TASK_MODEL_LITERATURE_SEARCH"] = "deepseek-flash"
        os.environ["PO_TASK_MODEL_IMAGE"] = "gemini:gemini-3-pro-image"
        reset_provider_caches()
        self.assertEqual(L.literature_search_backend(), ("", ""))
        self.assertEqual(L.resolve_image_model("auto"), "")
        self.assertFalse(L.can_generate_images("gemini-3-pro-image"))
        self.assertFalse(L.can_generate_images("gemini:gemini-3-pro-image"))
        self.assertIsNone(L.generate_image("gemini-3-pro-image", "a diagram"))

    def test_off_by_default(self):
        from utils import llm_backend_utils as L
        from utils.providers import get_provider, resolve_model

        os.environ["PO_HOST_AGENT"] = ""
        os.environ["MUSE_CLI"] = ""
        os.environ["OPENAI_API_KEY"] = "sk-test-other-provider-000000"
        reset_provider_caches()
        self.assertFalse(get_provider("muse").has_credentials())
        self.assertEqual(resolve_model("gpt-6.2-sol")[0].name, "openai")
        self.assertNotIn("muse", " ".join(L.describe_auto_routing().values()))

    # ---- the call itself -------------------------------------------------
    def test_text_call_goes_through_the_cli(self):
        from utils import llm_backend_utils as L, usage_tracker

        os.environ["OPENAI_API_KEY"] = "sk-test-other-provider-000000"
        os.environ["META_API_KEY"] = "meta-key-that-must-not-be-used"
        reset_provider_caches()
        usage_tracker.reset()
        out = L.call_llm_with_text_prompt("Write the outline.", "claude-sonnet-5-5",
                                          generation_configs={"system_instruction": "Be terse.", "temperature": 0.2})
        self.assertEqual(out["parsed_response"], {"ok": True})
        (call,) = self.calls()
        args = call["args"]
        self.assertEqual(args[0], "exec")
        self.assertNotIn("--model", args)  # Muse Code's own configured model
        self.assertEqual(args[args.index("--preset") + 1], "empty")
        for flag in ("--json", "--no-session-log", "--disable-shell", "--disable-write", "--disable-web-tools",
                     "--no-foreign-personal-context"):
            self.assertIn(flag, args)
        for bad in ("--yolo", "--disable-sandbox", "--disable-approval", "--trust-workspace"):
            self.assertNotIn(bad, args)
        self.assertIn("Write the outline.", call["prompt"])
        self.assertIn("Be terse.", call["prompt"])
        self.assertIn("Do not use any tools", call["prompt"])
        self.assertFalse(call["has_meta_key"])  # the subscription sign-in, never a pay-as-you-go key
        self.assertEqual(os.path.realpath(call["cwd"]),
                         os.path.realpath(args[args.index("--workspace") + 1]))
        self.assertFalse(os.path.exists(call["cwd"]))  # the scratch workspace is removed
        stats = usage_tracker.report()
        self.assertIn("muse", json.dumps(stats))
        self.assertIn("1234", json.dumps(stats))

    def test_model_effort_and_plain_output(self):
        from utils import llm_backend_utils as L

        os.environ.update({"PO_MUSE_JSON": "0", "PO_MUSE_REASONING_EFFORT": "low",
                           "FAKE_MUSE_ANSWER": "plain words"})
        out = L.call_llm_with_text_prompt("hi", "muse:muse-spark-1.2", return_json=False)
        self.assertEqual(out["raw_response"], "plain words")
        args = self.calls()[0]["args"]
        self.assertEqual(args[args.index("--model") + 1], "muse-spark-1.2")
        self.assertEqual(args[args.index("--reasoning-effort") + 1], "low")
        self.assertNotIn("--json", args)

    def test_json_answer_is_not_mistaken_for_events(self):
        from utils.agent_cli_utils import parse_output

        answer = '{"title": "x"}\n{"title": "y"}'
        self.assertEqual(parse_output(answer)["text"], answer)

    def test_images_are_left_out(self):
        from utils import llm_backend_utils as L

        img = os.path.join(self.tmp.name, "a.png")
        with open(img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        L.call_llm_with_images("Describe.", [img], "auto-anything", return_json=False)
        self.assertIn("image attachment(s) omitted", self.calls()[0]["prompt"])

    def test_quota_error_is_reported(self):
        from utils.agent_cli_utils import AgentCliError, run_once

        os.environ["FAKE_MUSE_MODE"] = "fail"
        with self.assertRaises(AgentCliError) as ctx:
            run_once("muse", "", "hi")
        self.assertIn("额度", str(ctx.exception))
        self.assertFalse(ctx.exception.fatal)
        os.environ["FAKE_MUSE_MODE"] = "login"
        with self.assertRaises(AgentCliError) as ctx:
            run_once("muse", "", "hi")
        self.assertTrue(ctx.exception.fatal)

    def test_missing_cli(self):
        from utils import llm_backend_utils as L

        os.environ["MUSE_CLI"] = os.path.join(self.tmp.name, "nope")
        problems = L.credential_problems(["auto"])
        self.assertEqual(len(problems), 1)
        self.assertIn("not installed", problems[0])

    def test_check_providers_reports_muse_without_spending_a_prompt(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("check_providers_tool_muse",
                                                      os.path.join(REPO_ROOT, "tools", "check_providers.py"))
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        rows = {r["name"]: r for r in tool.check_providers(True, [], 20.0)}
        self.assertTrue(rows["muse"]["configured"])
        self.assertEqual(rows["muse"]["kind"], "cli")
        self.assertTrue(rows["muse"]["ok"], rows["muse"])
        self.assertEqual(self.calls(), [])  # only `muse --version` ran


if __name__ == "__main__":
    unittest.main()
