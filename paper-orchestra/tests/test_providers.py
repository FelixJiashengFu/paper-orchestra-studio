"""Offline tests for the provider registry and the unified dispatcher.

Run from the repo root:  python -m unittest tests/test_providers.py -v
No real API key or network is needed: a local fake OpenAI/Anthropic server is used.
"""

import json
import os
import re
import sys
import tempfile
import threading
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_KEYS = [
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
    "DEEPSEEK_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "VERTEX_AI_PROJECT", "VERTEX_AI_LOCATION",
    "DASHSCOPE_API_KEY", "OPENROUTER_API_KEY", "MOONSHOT_API_KEY", "PO_AUTO_MODEL",
    "PO_DEFAULT_PROVIDER", "PO_PROVIDERS_FILE", "OLLAMA_BASE_URL", "SILICONFLOW_API_KEY",
    "QWEN_DEFAULT_MODEL", "MOONSHOT_DEFAULT_MODEL", "ARK_API_KEY", "RELAY_TOKEN", "RELAY_API_KEY",
    "MYPROXY_KEY", "MYPROXY_API_KEY",
    "GEMINI_BASE_URL", "GOOGLE_GENAI_API_VERSION",
    # read from the environment / .env by the Claude code: blanked so a user's settings cannot change results
    "PO_CLAUDE_MAX_TOKENS", "PO_CLAUDE_EFFORT", "PO_CLAUDE_IMAGE_MAX_EDGE", "PO_RELEVANCE_SCREEN",
]
# Further variables read by the provider registry and auto routing.
_ROUTING_ENV = re.compile(r"PO_[A-Z0-9_]+|[A-Z0-9_]+_(?:API_KEY|BASE_URL|DEFAULT_MODEL|TOKEN)")


class FakeLLM(BaseHTTPRequestHandler):
    requests_seen = []
    reject_images = False
    reply_text = '```json\n{"ok": true}\n```'
    model_caps = {}  # Models API: model id -> max_tokens (others answer 404)
    max_tokens_limit = None  # Messages API: 400 above this max_tokens, like a lower-cap model

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeLLM.requests_seen.append({"path": self.path, "body": None, "headers": dict(self.headers)})
        model = self.path.rstrip("/").rsplit("/", 1)[-1]
        if "/models/" in self.path and model in FakeLLM.model_caps:
            self._send(200, {"type": "model", "id": model, "display_name": model,
                             "created_at": "2026-01-01T00:00:00Z", "max_input_tokens": 1000000,
                             "max_tokens": FakeLLM.model_caps[model]})
        else:
            self._send(404, {"type": "error", "error": {"type": "not_found_error", "message": "model"}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLLM.requests_seen.append({"path": self.path, "body": body, "headers": dict(self.headers)})
        if self.path.endswith("/chat/completions"):
            content = body["messages"][-1]["content"]
            if FakeLLM.reject_images and isinstance(content, list) and any(
                p.get("type") == "image_url" for p in content
            ):
                self._send(400, {"error": {"message": "This model does not support image input"}})
                return
            self._send(200, {
                "id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": FakeLLM.reply_text}}],
            })
        elif self.path.endswith("/messages") and self.path.startswith("/notemp/") and "temperature" in body:
            # Like Claude 5-series models: sampling parameters are a 400.
            self._send(400, {"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "temperature: this model does not support sampling parameters"}})
        elif (self.path.endswith("/messages") and FakeLLM.max_tokens_limit
              and body.get("max_tokens", 0) > FakeLLM.max_tokens_limit):
            self._send(400, {"type": "error", "error": {
                "type": "invalid_request_error",
                "message": f"max_tokens: {body['max_tokens']} > {FakeLLM.max_tokens_limit}, which is the maximum "
                           f"allowed number of output tokens for {body['model']}"}})
        elif self.path.endswith("/messages") and body.get("stream") and not self.path.startswith("/jsononly/"):
            # SSE reply with a thinking block first, as adaptive-thinking models answer.
            # /runaway/: thinks until max_tokens (no text) unless effort is medium or low;
            # /runaway-all/: at every effort; /cutoff/: the text is cut off by max_tokens
            # unless effort is medium or low.
            effort = (body.get("output_config") or {}).get("effort")
            low = effort in ("medium", "low")
            chunks, stop = ['{"ok": ', '"claude-stream"}'], "end_turn"
            if self.path.startswith("/runaway-all/") or (self.path.startswith("/runaway/") and not low):
                chunks, stop = [], "max_tokens"
            elif self.path.startswith("/cutoff/") and not low:
                chunks, stop = ['{"ok": "cl'], "max_tokens"

            def ev(name, data):
                return f"event: {name}\ndata: {json.dumps(data)}\n\n"
            events = [
                ev("message_start", {"type": "message_start", "message": {
                    "id": "m", "type": "message", "role": "assistant", "model": body["model"], "content": [],
                    "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 1}}}),
                ev("content_block_start", {"type": "content_block_start", "index": 0,
                                           "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
                ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "thinking_delta", "thinking": "THINKING-NOT-OUTPUT"}}),
                ev("content_block_delta", {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "signature_delta", "signature": "sig"}}),
                ev("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ]
            if chunks:
                events.append(ev("content_block_start", {"type": "content_block_start", "index": 1,
                                                         "content_block": {"type": "text", "text": ""}}))
                for chunk in chunks:
                    events.append(ev("content_block_delta", {"type": "content_block_delta", "index": 1,
                                                             "delta": {"type": "text_delta", "text": chunk}}))
                events.append(ev("content_block_stop", {"type": "content_block_stop", "index": 1}))
            if not self.path.startswith("/nostop/"):  # /nostop/: no message_delta (stop_reason) at all
                events += [
                    ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop,
                                                                            "stop_sequence": None},
                                         "usage": {"output_tokens": 9}}),
                    ev("message_stop", {"type": "message_stop"}),
                ]
            raw = "".join(events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif self.path.endswith("/messages"):
            self._send(200, {
                "id": "m", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": '{"ok": "claude"}'}],
                "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1},
            })
        else:
            self._send(404, {"error": "nope"})

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeLLM)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        # Everything a user's .env may set for model routing (per-task overrides,
        # <NAME>_DEFAULT_MODEL, other platforms' keys) is blanked too, so the
        # results do not depend on the machine the tests run on.
        keys = set(_KEYS) | {k for k in os.environ if _ROUTING_ENV.fullmatch(k)}
        self._saved = {k: os.environ.get(k) for k in keys}
        for k in keys:
            os.environ[k] = ""
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PO_PROVIDERS_FILE"] = os.path.join(self.tmp.name, "providers.json")
        FakeLLM.requests_seen.clear()
        FakeLLM.reject_images = False
        FakeLLM.reply_text = '```json\n{"ok": true}\n```'
        FakeLLM.model_caps = {}
        FakeLLM.max_tokens_limit = None
        import utils.anthropic_utils as A
        A._output_caps.clear()
        import utils.providers as P
        P._registry_cache = None
        P._clients.clear()
        P._text_only_models.clear()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_empty_base_url_lines_fall_back_to_official_endpoints(self):
        # The stock .env has empty OPENAI_BASE_URL= / ANTHROPIC_BASE_URL= lines,
        # which python-dotenv exports as "". The SDKs would read those and build
        # scheme-less URLs; the registry must pass the official URL instead.
        import utils.providers as P
        os.environ["OPENAI_BASE_URL"] = ""
        os.environ["ANTHROPIC_BASE_URL"] = ""
        os.environ["OPENAI_API_KEY"] = "sk-test-openai"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        spec, model = P.resolve_model("claude-sonnet-4-6")
        self.assertEqual(spec.name, "anthropic")
        self.assertEqual(str(P.get_client(spec).base_url).rstrip("/"), "https://api.anthropic.com")
        spec, model = P.resolve_model("gpt-4.1")
        self.assertEqual(spec.name, "openai")
        self.assertEqual(str(P.get_client(spec).base_url).rstrip("/"), "https://api.openai.com/v1")
        # An explicit base URL still wins.
        os.environ["OPENAI_BASE_URL"] = self.base
        P._clients.clear()
        spec, _ = P.resolve_model("gpt-4.1")
        self.assertEqual(str(P.get_client(spec).base_url).rstrip("/"), self.base)

    def _gemini_urls(self, spec, config=None):
        """URLs a generate_content call on get_client(spec) would request."""
        import google.genai._api_client as GA
        import utils.providers as P
        seen = []

        def fake_request(client_self, http_request, *args, **kwargs):
            seen.append(http_request.url)
            raise RuntimeError("stop before the network")

        P._clients.clear()
        with mock.patch.object(GA.BaseApiClient, "_request", fake_request):
            with self.assertRaises(RuntimeError):
                P.get_client(spec).models.generate_content(
                    model="gemini-3-flash-preview", contents="ping", config=config)
        return seen

    def test_gemini_key_clients_call_v1beta_where_preview_models_live(self):
        # google-genai 3.x calls v1 by default, which answers "models/gemini-3-flash-preview
        # is not found for API version v1": every preview model the project defaults to
        # (gemini-3.1-pro-preview, gemini-3-pro-image-preview) failed with a valid key.
        import utils.providers as P
        from google.genai import types
        os.environ["GEMINI_API_KEY"] = "AIza-test-gemini-key"
        spec = P.get_provider("gemini")
        self.assertEqual(self._gemini_urls(spec), [
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent"])
        # A per-request timeout (the connection test sends one) keeps the version.
        ping = types.GenerateContentConfig(max_output_tokens=8, http_options=types.HttpOptions(timeout=5000))
        self.assertIn("/v1beta/models/", self._gemini_urls(spec, ping)[0])
        # A gateway base URL gets the same path the pre-3.0 SDKs built.
        os.environ["GEMINI_BASE_URL"] = "https://relay.example.com"
        self.assertEqual(self._gemini_urls(spec)[0],
                         "https://relay.example.com/v1beta/models/gemini-3-flash-preview:generateContent")
        # GOOGLE_GENAI_API_VERSION still chooses, as the SDK documents.
        os.environ["GEMINI_BASE_URL"] = ""
        os.environ["GOOGLE_GENAI_API_VERSION"] = "v1"
        self.assertIn("/v1/models/", self._gemini_urls(spec)[0])

    def test_vertex_clients_keep_the_sdk_api_version(self):
        import utils.providers as P
        self.assertIsNone(P.gemini_api_version(vertex=True))
        self.assertEqual(P.gemini_api_version(vertex=False), "v1beta")
        os.environ["GOOGLE_GENAI_API_VERSION"] = "v1alpha"
        self.assertIsNone(P.gemini_api_version(vertex=False))

    def write_providers(self, data):
        with open(os.environ["PO_PROVIDERS_FILE"], "w", encoding="utf-8") as f:
            json.dump(data, f)
        import utils.providers as P
        P._registry_cache = None

    def write_providers_text(self, text, encoding="utf-8"):
        with open(os.environ["PO_PROVIDERS_FILE"], "w", encoding=encoding) as f:
            f.write(text)
        import utils.providers as P
        P._registry_cache = None

    # ---------------- resolution ----------------

    def test_legacy_names_unchanged(self):
        from utils.providers import resolve_model
        self.assertEqual(resolve_model("claude-sonnet-4-6")[0].name, "anthropic")
        self.assertEqual(resolve_model("deepseek-chat")[0].name, "deepseek")
        self.assertEqual(resolve_model("gpt-4.1")[0].name, "openai")
        self.assertEqual(resolve_model("o3")[0].name, "openai")
        self.assertEqual(resolve_model("gemini-3.1-pro-preview")[0].name, "gemini")
        self.assertEqual(resolve_model("something-unknown")[0].name, "gemini")  # old default

    def test_prefix_and_ollama_tag(self):
        from utils.providers import resolve_model
        spec, model = resolve_model("qwen:qwen-max")
        self.assertEqual((spec.name, model), ("qwen", "qwen-max"))
        spec, model = resolve_model("ollama:qwen2.5:14b")
        self.assertEqual((spec.name, model), ("ollama", "qwen2.5:14b"))
        spec, model = resolve_model("openrouter:anthropic/claude-sonnet-4")
        self.assertEqual((spec.name, model), ("openrouter", "anthropic/claude-sonnet-4"))
        spec, model = resolve_model("kimi:kimi-k2")  # alias
        self.assertEqual(spec.name, "moonshot")
        # Unknown prefix is part of the model name.
        self.assertEqual(resolve_model("nonsense:model")[1], "nonsense:model")

    def test_hunyuan_and_qianfan_presets(self):
        # Added 2026-11-19 (Tencent HY on TokenHub, Baidu ERNIE on Qianfan v2).
        import utils.providers as P
        from utils.llm_backend_utils import resolve_task_model
        os.environ["HUNYUAN_API_KEY"] = "hy-key"
        spec, model = P.resolve_model("hy:hy3")  # alias
        self.assertEqual((spec.name, model), ("hunyuan", "hy3"))
        self.assertEqual(str(P.get_client(spec).base_url).rstrip("/"), "https://tokenhub.tencentmaas.com/v1")
        self.assertEqual(P.resolve_model("hy4")[0].name, "hunyuan")
        self.assertEqual(P.resolve_model("hunyuan-role-latest")[0].name, "hunyuan")
        self.assertEqual(P.resolve_model("hy-mt2-pro")[0].name, "hunyuan")
        self.assertEqual(resolve_task_model("auto", "outline"), "hunyuan:hy4")
        os.environ["HUNYUAN_API_KEY"] = ""
        os.environ["TOKENHUB_API_KEY"] = "tk"  # the alternative key name works too
        self.assertEqual(P.resolve_model("hy4")[0].name, "hunyuan")
        os.environ["TOKENHUB_API_KEY"] = ""
        P._clients.clear()
        os.environ["QIANFAN_API_KEY"] = "bce-v3/ALTAK-x/y"
        spec, model = P.resolve_model("ernie-5.1")
        self.assertEqual((spec.name, model), ("qianfan", "ernie-5.1"))
        self.assertEqual(str(P.get_client(spec).base_url).rstrip("/"), "https://qianfan.baidubce.com/v2")
        self.assertEqual(P.resolve_model("baidu:ernie-5.5-flash-preview")[0].name, "qianfan")
        self.assertEqual(resolve_task_model("auto", "outline"), "qianfan:ernie-5.1")

    def test_antdigital_and_longcat_presets(self):
        # Ant Digital DTMaaS and Meituan LongCat (added 2026-11-19), both kept out of auto.
        import utils.providers as P
        from utils.llm_backend_utils import resolve_task_model
        os.environ["ANTDIGITAL_API_KEY"] = "ak"
        spec, model = P.resolve_model("ling-3.1-flash")
        self.assertEqual((spec.name, model), ("antdigital", "ling-3.1-flash"))
        self.assertEqual(str(P.get_client(spec).base_url).rstrip("/"), "https://maas-api.antdigital.com/v1")
        self.assertEqual(P.resolve_model("ant:lingdt-3.0-flash")[0].name, "antdigital")
        # Ant Ling's own endpoint (or Bailian) through the usual base-URL variable.
        os.environ["ANTDIGITAL_BASE_URL"] = "https://api.ant-ling.com/v1"
        P._clients.clear()
        self.assertEqual(str(P.get_client(spec).base_url).rstrip("/"), "https://api.ant-ling.com/v1")
        os.environ.pop("ANTDIGITAL_BASE_URL")
        P._clients.clear()
        os.environ["LONGCAT_API_KEY"] = "lc"
        spec, model = P.resolve_model("LongCat-2.5")
        self.assertEqual((spec.name, model), ("longcat", "LongCat-2.5"))
        self.assertEqual(str(P.get_client(spec).base_url).rstrip("/"), "https://api.longcat.chat/openai")
        self.assertEqual(P.resolve_model("meituan:LongCat-2.5")[0].name, "longcat")
        # No default model: "auto" keeps using the Qwen key's default.
        os.environ["DASHSCOPE_API_KEY"] = "sk"
        self.assertEqual(resolve_task_model("auto", "outline"), "qwen:qwen-max")
        # Ollama tags with these names stay local.
        self.assertEqual(P.resolve_model("ling-lite:7b")[0].name, "ollama")

    def test_unisound_is_no_longer_builtin(self):
        import utils.providers as P
        self.assertNotIn("unisound", {s.name for s in P._builtin_providers()})

    def test_doubao_and_minimax_are_no_longer_builtin(self):
        # Taken off the platform list on 2026-11-19. Their keys may still sit in
        # .env; they no longer make a provider count as configured.
        import utils.providers as P
        names = {s.name for s in P._builtin_providers()}
        self.assertFalse(names & {"doubao", "minimax"})
        os.environ["ARK_API_KEY"] = "ark"
        os.environ["MINIMAX_API_KEY"] = "mm"
        try:
            self.assertEqual(P.resolve_model("doubao:ep-123")[1], "doubao:ep-123")  # not a known prefix
            self.assertNotIn(P.resolve_model("doubao-seed-1.6")[0].name, {"doubao", "minimax"})
        finally:
            os.environ.pop("ARK_API_KEY")
            os.environ.pop("MINIMAX_API_KEY")

    def test_preset_by_name_only_with_key(self):
        from utils.providers import resolve_model
        # No key: the family's own provider, so the error names DASHSCOPE_API_KEY.
        self.assertEqual(resolve_model("qwen-max")[0].name, "qwen")
        os.environ["DASHSCOPE_API_KEY"] = "sk-test"
        self.assertEqual(resolve_model("qwen-max")[0].name, "qwen")

    def test_unkeyed_family_is_not_sent_to_slug_gateways(self):
        from utils.providers import resolve_model
        os.environ["OPENROUTER_API_KEY"] = "sk-or"
        # OpenRouter needs "anthropic/..." slugs: bare names stay with their family.
        self.assertEqual(resolve_model("claude-sonnet-4-6")[0].name, "anthropic")
        self.assertEqual(resolve_model("deepseek-reasoner")[0].name, "deepseek")
        self.assertEqual(resolve_model("anthropic/claude-sonnet-4")[0].name, "openrouter")
        self.assertEqual(resolve_model("llama-3.3-70b")[0].name, "openrouter")  # unrecognised
        os.environ["OPENAI_API_KEY"] = "sk-o"
        self.assertEqual(resolve_model("claude-sonnet-4-6")[0].name, "openai")  # one-api style proxy
        # OpenAI fine-tunes keep going to OpenAI.
        self.assertEqual(resolve_model("ft:gpt-4o-mini:org::abc123"), (resolve_model("gpt-4o")[0], "ft:gpt-4o-mini:org::abc123"))

    def test_legacy_backends_keep_tagged_and_unanchored_names(self):
        """Names the original code sent to OpenAI / DeepSeek / Gemini still go there when keyed."""
        from utils.providers import resolve_model
        os.environ["OPENAI_API_KEY"] = "sk-o"
        os.environ["OPENAI_BASE_URL"] = "http://gpu-box:11434/v1"
        os.environ["DEEPSEEK_API_KEY"] = "sk-d"
        os.environ["OPENROUTER_API_KEY"] = "sk-or"  # set for some other tool
        os.environ["SILICONFLOW_API_KEY"] = "sk-sf"
        for name in ("gpt-oss:20b", "gpt-oss:120b", "gpt-4o:latest", "azure-gpt-4o", "my-gpt4-deploy",
                     "openai/gpt-4o"):
            spec, model = resolve_model(name)
            self.assertEqual((spec.name, model), ("openai", name), name)
            self.assertEqual(spec.resolved_base_url(), "http://gpu-box:11434/v1")
        self.assertEqual(resolve_model("deepseek-r1:14b")[0].name, "deepseek")
        self.assertEqual(resolve_model("deepseek-ai/DeepSeek-V3")[0].name, "deepseek")  # DEEPSEEK_BASE_URL relay
        # Not legacy names: the new routes still apply.
        self.assertEqual(resolve_model("llama3:8b")[0].name, "ollama")
        self.assertEqual(resolve_model("meta-llama/llama-3-70b")[0].name, "openrouter")
        # An explicitly configured local server takes Ollama tags, legacy names included.
        os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434/v1"
        self.assertEqual(resolve_model("gpt-oss:20b")[0].name, "ollama")
        self.assertEqual(resolve_model("openai:gpt-oss:20b")[0].name, "openai")

    def test_gemini_sdk_names_stay_with_gemini(self):
        from utils.providers import resolve_model
        os.environ["GEMINI_API_KEY"] = "g"
        os.environ["OPENAI_API_KEY"] = "sk-o"
        os.environ["OPENROUTER_API_KEY"] = "sk-or"
        for name in ("models/gemini-2.5-pro", "learnlm-2.0-flash-experimental", "gemini-3.1-pro-preview",
                     "tunedModels/my-tune-123", "something-unknown"):
            self.assertEqual(resolve_model(name)[0].name, "gemini", name)
        self.assertEqual(resolve_model("gpt-4.1")[0].name, "openai")
        os.environ["GEMINI_API_KEY"] = ""
        self.assertEqual(resolve_model("something-unknown")[0].name, "openrouter")  # no Gemini: a gateway

    def test_google_api_key_is_not_a_gemini_key(self):
        from utils.llm_backend_utils import has_gemini_credentials
        from utils.providers import resolve_model
        os.environ["GOOGLE_API_KEY"] = "AIza-for-another-tool"
        os.environ["OPENAI_API_KEY"] = "sk-o"
        self.assertFalse(has_gemini_credentials())
        self.assertEqual(resolve_model("llama-3.3-70b")[0].name, "openai")
        os.environ["GEMINI_API_KEY"] = "g"
        self.assertTrue(has_gemini_credentials())

    def test_ollama_tags_without_prefix(self):
        from utils.providers import resolve_model
        self.assertEqual(resolve_model("qwen:7b"), (resolve_model("ollama:x")[0], "qwen:7b"))
        self.assertEqual(resolve_model("mistral:7b-instruct")[0].name, "ollama")
        self.assertEqual(resolve_model("llama3:8b")[0].name, "ollama")
        self.assertEqual(resolve_model("qwen:qwen-max"), (resolve_model("qwen:x")[0], "qwen-max"))
        self.assertEqual(resolve_model("ollama:qwen2.5:14b")[1], "qwen2.5:14b")
        # QAT builds such as gemma4:12b-it-qat keep their full tag, with or without the prefix.
        self.assertEqual(resolve_model("ollama:gemma4:12b-it-qat")[1:], ("gemma4:12b-it-qat",))
        self.assertEqual(resolve_model("gemma4:12b-it-qat")[0].name, "ollama")

    def test_unset_inline_key_counts_as_missing(self):
        self.write_providers({"providers": [{"name": "myproxy", "api_key": "${MYPROXY_KEY}"}]})
        from utils.providers import get_provider, resolve_model
        spec = get_provider("myproxy")
        self.assertEqual(spec.resolved_api_key(), "")
        self.assertFalse(spec.has_credentials())
        os.environ["OPENAI_API_KEY"] = "sk-o"
        self.assertEqual(resolve_model("claude-sonnet-4-6")[0].name, "openai")  # not the keyless proxy
        os.environ["MYPROXY_API_KEY"] = "sk-env"  # api_key_env still applies
        self.assertEqual(spec.resolved_api_key(), "sk-env")
        os.environ["MYPROXY_KEY"] = "sk-inline"
        self.assertEqual(spec.resolved_api_key(), "sk-inline")

    def test_providers_json_validation(self):
        from utils.providers import get_provider
        for bad in (
            {"providers": [{"name": "x", "headers": ["a"]}]},
            {"providers": [{"name": "x", "api_key_env": 5}]},
            {"providers": [{"name": "x", "api_key_env": [5]}]},
            {"providers": [{"name": "x", "models": 5}]},
            {"providers": [{"name": "x", "vision": "false"}]},
            {"providers": [{"name": 42}]},
            {"providers": {"name": "x"}},
            {"providers": ["x"]},
        ):
            self.write_providers(bad)
            with self.assertRaises(ValueError, msg=json.dumps(bad)) as ctx:
                get_provider("openai")
            self.assertNotIn("{", str(ctx.exception).split("):", 1)[-1].replace("(", ""))  # no raw dict
        self.write_providers({"providers": [{"name": "x", "aliases": "rl", "models": "m1"}]})
        self.assertIsNone(get_provider("r"))
        self.assertEqual(get_provider("rl").name, "x")
        self.assertEqual(get_provider("x").models, ["m1"])
        # BOM and blank files are fine.
        self.write_providers_text('[{"name": "bom"}]', encoding="utf-8-sig")
        self.assertEqual(get_provider("bom").name, "bom")
        self.write_providers_text("  \n")
        self.assertEqual(get_provider("openai").name, "openai")

    def test_gateway_for_unknown_names(self):
        from utils.providers import resolve_model
        os.environ["OPENAI_API_KEY"] = "sk-test"
        self.assertEqual(resolve_model("llama-3.3-70b")[0].name, "openai")
        # Claude name without Anthropic key goes to the gateway (e.g. a one-api proxy).
        self.assertEqual(resolve_model("claude-sonnet-4-6")[0].name, "openai")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant"
        self.assertEqual(resolve_model("claude-sonnet-4-6")[0].name, "anthropic")

    def test_custom_provider_file(self):
        self.write_providers({
            "providers": [{"name": "myproxy", "type": "openai", "base_url": self.base,
                           "api_key": "k", "models": ["fancy-model"], "vision": False}],
            "auto_model": "myproxy:fancy-model",
        })
        from utils.providers import resolve_model
        from utils.llm_backend_utils import resolve_task_model, supports_vision, has_any_llm_credentials
        self.assertEqual(resolve_model("fancy-model")[0].name, "myproxy")
        self.assertEqual(resolve_model("random-name")[0].name, "myproxy")  # first custom = gateway
        self.assertEqual(resolve_task_model("auto", "section_writing"), "myproxy:fancy-model")
        self.assertFalse(supports_vision("myproxy:fancy-model"))
        self.assertTrue(has_any_llm_credentials())

    def test_task_override_env(self):
        from utils.llm_backend_utils import resolve_task_model
        os.environ["PO_TASK_MODEL_OUTLINE"] = "qwen:qwq-plus"
        try:
            self.assertEqual(resolve_task_model("auto", "outline"), "qwen:qwq-plus")
            self.assertEqual(resolve_task_model("gpt-4.1", "outline"), "gpt-4.1")
        finally:
            os.environ.pop("PO_TASK_MODEL_OUTLINE")

    def test_task_override_beats_the_unified_auto_model(self):
        # Combined setup: PO_AUTO_MODEL plus per-task models. A task with
        # its own model uses it; an emptied task falls back to PO_AUTO_MODEL.
        from utils.llm_backend_utils import describe_auto_routing, resolve_task_model
        os.environ["PO_AUTO_MODEL"] = "anthropic:claude-sonnet-5-5"
        os.environ["PO_TASK_MODEL_OUTLINE"] = "claude-opus-5-5"
        os.environ["PO_TASK_MODEL_SECTION_WRITING"] = "deepseek-flash"
        os.environ["PO_TASK_MODEL_PLOT_PLAN"] = "   "
        try:
            self.assertEqual(resolve_task_model("auto", "outline"), "claude-opus-5-5")
            self.assertEqual(resolve_task_model("auto", "section_writing"), "deepseek-flash")
            self.assertEqual(resolve_task_model("auto", "plot_plan"), "anthropic:claude-sonnet-5-5")
            self.assertEqual(resolve_task_model("gpt-6.2-sol", "outline"), "gpt-6.2-sol")
            routing = describe_auto_routing()
            self.assertEqual(routing["outline"], "claude-opus-5-5")
            self.assertIn("plot_plan", routing)
        finally:
            for k in ("PO_AUTO_MODEL", "PO_TASK_MODEL_OUTLINE", "PO_TASK_MODEL_SECTION_WRITING",
                      "PO_TASK_MODEL_PLOT_PLAN"):
                os.environ.pop(k, None)

    def test_auto_image_model_uses_gemini_image_when_a_gemini_key_is_set(self):
        # "auto" used to reach can_generate_images() literally, so every diagram fell
        # back to Matplotlib even with a Gemini key.
        import utils.llm_backend_utils as L
        import utils.paper_banana_utils as PB
        self.assertEqual(L.resolve_image_model("auto"), "")  # no Gemini key: Matplotlib
        self.assertEqual(L.resolve_image_model("openai:gpt-image-1"), "openai:gpt-image-1")
        os.environ["GEMINI_API_KEY"] = "AIza-test-gemini-key"
        try:
            self.assertEqual(L.resolve_image_model("auto"), "gemini-3-pro-image")
            self.assertEqual(L.resolve_image_model(" AUTO "), "gemini-3-pro-image")
            self.assertEqual(L.resolve_image_model(""), "gemini-3-pro-image")
            self.assertEqual(L.resolve_image_model("gemini-3.1-flash-image"), "gemini-3.1-flash-image")
            seen = []
            with mock.patch.object(PB, "generate_image",
                                   lambda image_model_name, **kw: seen.append(image_model_name) or "b64"):
                out = PB.generate_figure_visuals("diagram", "boxes and arrows",
                                                 model_name="auto", image_model_name="auto")
            self.assertEqual(out, "b64")
            self.assertEqual(seen, ["gemini-3-pro-image"])
        finally:
            os.environ["GEMINI_API_KEY"] = ""

    def test_finer_steps_follow_their_parent_task_unless_pinned(self):
        # The steps split out on 2026-11-19 run exactly as before until pinned.
        from utils.llm_backend_utils import TASK_PARENTS, resolve_task_model
        os.environ["PO_AUTO_MODEL"] = "anthropic:claude-sonnet-5-5"
        os.environ["PO_TASK_MODEL_PLOT_CODE"] = "deepseek-flash"
        os.environ["PO_TASK_MODEL_PLOT_STYLE"] = "claude-opus-5-5"
        try:
            for step, parent in TASK_PARENTS.items():
                if step != "plot_style":
                    self.assertEqual(resolve_task_model("auto", step), resolve_task_model("auto", parent), step)
            self.assertEqual(resolve_task_model("auto", "plot_retrieval"), "deepseek-flash")
            self.assertEqual(resolve_task_model("auto", "diagram_code"), "deepseek-flash")
            self.assertEqual(resolve_task_model("auto", "plot_style"), "claude-opus-5-5")
            self.assertEqual(resolve_task_model("auto", "plot_caption"), "anthropic:claude-sonnet-5-5")
            self.assertEqual(resolve_task_model("gpt-6.2-sol", "plot_style"), "gpt-6.2-sol")  # explicit wins
            os.environ["PO_AUTO_MODEL"] = ""
            os.environ["DEEPSEEK_API_KEY"] = "sk-test"
            self.assertEqual(resolve_task_model("auto", "relevance_screen"), "deepseek-flash")  # = literature_query
        finally:
            for k in ("PO_AUTO_MODEL", "PO_TASK_MODEL_PLOT_CODE", "PO_TASK_MODEL_PLOT_STYLE"):
                os.environ.pop(k, None)

    def test_auto_written_as_a_task_model_means_unset(self):
        from utils.llm_backend_utils import resolve_task_model
        os.environ["PO_TASK_MODEL_OUTLINE"] = "AUTO"
        os.environ["PO_TASK_MODEL_PLOT_STYLE"] = "auto"
        os.environ["PO_TASK_MODEL_PLOT_CODE"] = "deepseek-flash"
        os.environ["PO_AUTO_MODEL"] = "auto"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        try:
            self.assertEqual(resolve_task_model("auto", "outline"), "claude-sonnet-5-5")
            self.assertEqual(resolve_task_model("auto", "plot_style"), "deepseek-flash")
        finally:
            for k in ("PO_TASK_MODEL_OUTLINE", "PO_TASK_MODEL_PLOT_STYLE", "PO_TASK_MODEL_PLOT_CODE", "PO_AUTO_MODEL"):
                os.environ.pop(k, None)
            os.environ["ANTHROPIC_API_KEY"] = ""

    def test_image_model_from_the_per_step_panel(self):
        import utils.llm_backend_utils as L
        os.environ["PO_TASK_MODEL_IMAGE"] = "openai:gpt-image-1"
        try:
            self.assertEqual(L.resolve_image_model("auto"), "openai:gpt-image-1")
            self.assertEqual(L.resolve_image_model("gemini-3.1-flash-image"), "gemini-3.1-flash-image")
            os.environ["PO_TASK_MODEL_IMAGE"] = "auto"
            self.assertEqual(L.resolve_image_model("auto"), "")
        finally:
            os.environ.pop("PO_TASK_MODEL_IMAGE", None)

    def test_literature_search_backend_choices(self):
        from utils.llm_backend_utils import literature_search_backend
        with mock.patch("builtins.print"):
            self.assertEqual(literature_search_backend(), ("", ""))  # no Gemini key: scholarly APIs
            os.environ["GEMINI_API_KEY"] = "AIza-test-gemini-key"
            self.assertEqual(literature_search_backend(), ("gemini", "gemini-3-flash-preview"))  # as before
            try:
                for value, expected in (
                    ("gemini-4-hydrogen", ("gemini", "gemini-4-hydrogen")),
                    ("off", ("", "")),
                    ("关闭", ("", "")),
                    ("deepseek-flash", ("", "")),  # no DeepSeek key
                    ("claude-sonnet-5-5", ("", "")),  # no web search on this path
                ):
                    os.environ["PO_TASK_MODEL_LITERATURE_SEARCH"] = value
                    self.assertEqual(literature_search_backend(), expected, value)
                os.environ["DEEPSEEK_API_KEY"] = "sk-test"
                os.environ["PO_TASK_MODEL_LITERATURE_SEARCH"] = "deepseek:deepseek-flash"
                self.assertEqual(literature_search_backend(), ("deepseek", "deepseek-flash"))
            finally:
                os.environ["GEMINI_API_KEY"] = ""
                os.environ.pop("PO_TASK_MODEL_LITERATURE_SEARCH", None)

    def test_deepseek_web_search_request(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk"
        os.environ["DEEPSEEK_BASE_URL"] = self.base
        FakeLLM.reply_text = '```json\n{"candidates": [{"title": "T", "year": 2025}]}\n```'
        from utils.llm_backend_utils import call_deepseek_with_web_search
        out = call_deepseek_with_web_search("find papers", "deepseek-flash")
        self.assertEqual(out["parsed_response"]["candidates"][0]["title"], "T")
        body = FakeLLM.requests_seen[-1]["body"]
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["tools"], [{"type": "deepseek_web_search"}, {"type": "deepseek_read_url"}])
        self.assertEqual(body["messages"][-1]["content"], "find papers")

    def test_deepseek_flash_reads_images_other_deepseek_models_do_not(self):
        # DeepSeek docs (guides/vision, 2026-11-19): deepseek-flash takes images.
        os.environ["DEEPSEEK_API_KEY"] = "sk"
        os.environ["DEEPSEEK_BASE_URL"] = self.base
        from utils.llm_backend_utils import call_llm_with_images, supports_vision
        img = os.path.join(self.tmp.name, "c.png")
        with open(img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        self.assertTrue(supports_vision("deepseek-flash"))
        self.assertFalse(supports_vision("deepseek-v4-pro"))
        call_llm_with_images("look", [img], "deepseek-flash")
        content = FakeLLM.requests_seen[-1]["body"]["messages"][-1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual([p["type"] for p in content], ["text", "image_url"])
        call_llm_with_images("look", [img], "deepseek-v4-pro")
        self.assertIsInstance(FakeLLM.requests_seen[-1]["body"]["messages"][-1]["content"], str)

    def test_auto_routes_to_current_models(self):
        # Ids as the providers' model lists showed them on 2026-11-18: deepseek-chat /
        # deepseek-reasoner were no longer listed, o3 shuts down 2026-12-11, and Claude
        # runs stay at Sonnet 5.5 or newer.
        from utils.llm_backend_utils import describe_auto_routing, resolve_task_model
        retired = {"deepseek-chat", "deepseek-reasoner", "o3", "claude-sonnet-4-5-20250929",
                   "claude-sonnet-4-6", "gpt-4.1", "gemini-3-pro-image-preview"}
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
        self.assertEqual(set(describe_auto_routing().values()), {"claude-sonnet-5-5"})
        os.environ["ANTHROPIC_API_KEY"] = ""
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        self.assertEqual(resolve_task_model("auto", "outline"), "deepseek-v4-pro")
        self.assertEqual(resolve_task_model("auto", "citation_parse"), "deepseek-flash")
        os.environ["DEEPSEEK_API_KEY"] = ""
        for key, expected in (("OPENAI_API_KEY", {"gpt-6.2-sol", "gpt-6.1-terra"}),
                              ("GEMINI_API_KEY", {"gemini-4-argon", "gemini-3.8-flash"})):
            os.environ[key] = "k-test"
            models = set(describe_auto_routing().values())
            self.assertEqual(models, expected, key)
            self.assertFalse(models & retired)
            os.environ[key] = ""

    def test_retired_gemini_image_preview_uses_its_ga_successor(self):
        # Projects saved earlier name gemini-3-pro-image-preview (shut down 2026-06-25).
        import utils.llm_backend_utils as L
        os.environ["GEMINI_API_KEY"] = "AIza-test-gemini-key"
        seen = []
        with mock.patch.object(L, "generate_image_with_gemini",
                               lambda model_name, **kw: seen.append(model_name) or "b64"):
            self.assertEqual(L.generate_image("gemini-3-pro-image-preview", "a diagram"), "b64")
            L.generate_image("gemini:gemini-3-pro-image-preview", "a diagram")
            L.generate_image("gemini-3.1-flash-image", "a diagram")  # others untouched
        self.assertEqual(seen, ["gemini-3-pro-image", "gemini-3-pro-image", "gemini-3.1-flash-image"])
        os.environ["GEMINI_API_KEY"] = ""

    def test_auto_with_only_a_preset_key(self):
        from utils.llm_backend_utils import credential_problems, describe_auto_routing
        os.environ["DASHSCOPE_API_KEY"] = "sk"
        self.assertEqual(set(describe_auto_routing().values()), {"qwen:qwen-max"})
        self.assertEqual(credential_problems(["auto", "auto"]), [])
        os.environ["DASHSCOPE_API_KEY"] = ""
        os.environ["XAI_API_KEY"] = "xai"  # xAI has no default model: fail early, clearly
        problems = credential_problems(["auto"])
        self.assertEqual(len(problems), 1)
        self.assertIn("DEFAULT_MODEL", problems[0])

    def test_explicit_local_model_needs_no_key(self):
        from utils.llm_backend_utils import credential_problems
        self.assertEqual(credential_problems(["ollama:qwen2.5:14b", "ollama:qwen2.5:14b"]), [])
        self.assertIn("ANTHROPIC_API_KEY", credential_problems(["claude-sonnet-4-6"])[0])

    def test_image_generation_needs_an_image_endpoint(self):
        from utils.llm_backend_utils import can_generate_images
        os.environ["OPENAI_API_KEY"] = "sk"
        self.assertFalse(can_generate_images("gemini-3-pro-image-preview"))  # chat gateway only
        # Bare names keep the original rule (Gemini key + "gemini" in the name): Matplotlib otherwise.
        self.assertFalse(can_generate_images("gpt-image-1"))
        self.assertFalse(can_generate_images("dall-e-3"))
        self.assertTrue(can_generate_images("openai:gpt-image-1"))  # explicit opt-in
        os.environ["GEMINI_API_KEY"] = "g"
        self.assertTrue(can_generate_images("gemini-3-pro-image-preview"))
        self.assertFalse(can_generate_images("imagen-4.0-generate-001"))  # not served by generate_content
        self.assertTrue(can_generate_images("gemini:imagen-4.0-generate-001"))

    def test_auto_with_only_preset_and_default_model(self):
        from utils.llm_backend_utils import resolve_task_model
        os.environ["MOONSHOT_API_KEY"] = "sk"
        os.environ["MOONSHOT_DEFAULT_MODEL"] = "kimi-k2"
        try:
            self.assertEqual(resolve_task_model("auto", "outline"), "moonshot:kimi-k2")
        finally:
            os.environ.pop("MOONSHOT_DEFAULT_MODEL")

    # ---------------- wire calls against the fake server ----------------

    def test_openai_compatible_call_and_vision_fallback(self):
        self.write_providers({"providers": [
            {"name": "local", "type": "openai", "base_url": self.base, "api_key": "k"}]})
        from utils.llm_backend_utils import call_llm_with_text_prompt, call_llm_with_images
        out = call_llm_with_text_prompt("hi", "local:my-model")
        self.assertEqual(out["parsed_response"], {"ok": True})
        self.assertEqual(FakeLLM.requests_seen[-1]["body"]["model"], "my-model")

        img = os.path.join(self.tmp.name, "a.png")
        with open(img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        FakeLLM.reject_images = True
        out = call_llm_with_images("describe", [img], "local:text-only-model")
        self.assertEqual(out["parsed_response"], {"ok": True})
        last = FakeLLM.requests_seen[-1]["body"]["messages"][-1]["content"]
        self.assertIsInstance(last, str)  # retried as plain text

    def test_parse_failure_does_not_disable_images(self):
        self.write_providers({"providers": [
            {"name": "local", "type": "openai", "base_url": self.base, "api_key": "k"}]})
        from utils.llm_backend_utils import call_llm_with_images, supports_vision
        img = os.path.join(self.tmp.name, "c.png")
        with open(img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        FakeLLM.reply_text = "The image shows a bar chart; this model does not support JSON."
        from unittest import mock
        with self.assertRaises(AssertionError), mock.patch("utils.openai_utils.time.sleep"):
            call_llm_with_images("critique", [img], "local:vision-model",
                                 check_parsed_response_not_none=True,
                                 result_parsing_func=lambda r: None)
        self.assertTrue(supports_vision("local:vision-model"))
        last = FakeLLM.requests_seen[-1]["body"]["messages"][-1]["content"]
        self.assertIsInstance(last, list)  # images were still sent on every retry

    def test_vision_false_drops_images_on_anthropic_protocol(self):
        self.write_providers({"providers": [
            {"name": "claudeproxy", "type": "anthropic", "base_url": f"http://127.0.0.1:{self.port}",
             "api_key": "k", "vision": False}]})
        from utils.llm_backend_utils import call_llm_with_images
        img = os.path.join(self.tmp.name, "d.png")
        with open(img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        call_llm_with_images("look", [img], "claudeproxy:claude-x")
        blocks = FakeLLM.requests_seen[-1]["body"]["messages"][-1]["content"]
        self.assertEqual([b["type"] for b in blocks], ["text", "text"])
        self.assertIn("omitted", blocks[-1]["text"])

    def test_anthropic_compatible_call(self):
        self.write_providers({"providers": [
            {"name": "claudeproxy", "type": "anthropic", "base_url": f"http://127.0.0.1:{self.port}",
             "api_key": "k"}]})
        from utils.llm_backend_utils import call_llm_with_text_prompt
        out = call_llm_with_text_prompt("hi", "claudeproxy:claude-x",
                                        generation_configs={"system_instruction": "be brief"})
        self.assertEqual(out["parsed_response"], {"ok": "claude"})
        body = FakeLLM.requests_seen[-1]["body"]
        self.assertEqual(body["system"], "be brief")

    def test_new_claude_models_stream_without_sampling_params(self):
        # Claude 5-series models reject temperature and think by default:
        # no temperature, the model's full 128K output budget, streamed; thinking text is not output.
        self.write_providers({"providers": [
            {"name": "claudeproxy", "type": "anthropic", "base_url": f"http://127.0.0.1:{self.port}",
             "api_key": "k"}]})
        from utils.llm_backend_utils import call_llm_with_text_prompt
        out = call_llm_with_text_prompt("hi", "claudeproxy:claude-sonnet-5-5",
                                        generation_configs={"temperature": 0.7})
        self.assertEqual(out["parsed_response"], {"ok": "claude-stream"})
        self.assertNotIn("THINKING", out["raw_response"])
        body = FakeLLM.requests_seen[-1]["body"]
        self.assertTrue(body.get("stream"))
        self.assertNotIn("temperature", body)
        self.assertEqual(body["max_tokens"], 128000)  # the Models API did not answer: 128K default
        self.assertNotIn("output_config", body)

    def test_new_claude_effort_setting(self):
        os.environ["PO_CLAUDE_EFFORT"] = "medium"
        os.environ["PO_CLAUDE_MAX_TOKENS"] = "32000"
        self.write_providers({"providers": [
            {"name": "claudeproxy", "type": "anthropic", "base_url": f"http://127.0.0.1:{self.port}",
             "api_key": "k"}]})
        from utils.llm_backend_utils import call_llm_with_text_prompt
        call_llm_with_text_prompt("hi", "claudeproxy:claude-opus-5-5")
        body = FakeLLM.requests_seen[-1]["body"]
        self.assertEqual(body["output_config"], {"effort": "medium"})
        self.assertEqual(body["max_tokens"], 32000)

    @staticmethod
    def _sdk_takes_temperature():
        import inspect
        from anthropic.resources.messages import Messages
        return "temperature" in inspect.signature(Messages.create).parameters

    def test_older_claude_request_unchanged(self):
        self.write_providers({"providers": [
            {"name": "claudeproxy", "type": "anthropic", "base_url": f"http://127.0.0.1:{self.port}",
             "api_key": "k"}]})
        from utils.llm_backend_utils import call_llm_with_text_prompt
        call_llm_with_text_prompt("hi", "claudeproxy:claude-sonnet-4-6",
                                  generation_configs={"temperature": 0.7})
        body = FakeLLM.requests_seen[-1]["body"]
        if self._sdk_takes_temperature():
            self.assertEqual(body["temperature"], 0.7)
        else:  # newer SDKs dropped sampling arguments: the call still goes through
            self.assertNotIn("temperature", body)
        self.assertEqual(body["max_tokens"], 16384)
        self.assertFalse(body.get("stream", False))

    def test_unknown_model_rejecting_temperature_is_retried_without_it(self):
        self.write_providers({"providers": [
            {"name": "claudeproxy", "type": "anthropic", "base_url": f"http://127.0.0.1:{self.port}/notemp",
             "api_key": "k"}]})
        from unittest import mock
        from utils import anthropic_utils as A
        from utils.llm_backend_utils import call_llm_with_text_prompt
        orig = A._create_message

        def via_body(client, kw, stream):
            # Put temperature in the body so that SDKs without the argument
            # (2.x) still send it and the 400 branch runs on every SDK.
            kw = dict(kw)
            if "temperature" in kw:
                kw["extra_body"] = {**(kw.get("extra_body") or {}), "temperature": kw.pop("temperature")}
            return orig(client, kw, stream)

        with mock.patch.object(A, "_create_message", side_effect=via_body):
            out = call_llm_with_text_prompt("hi", "claudeproxy:some-future-model",
                                            generation_configs={"temperature": 0.7})
        self.assertEqual(out["parsed_response"], {"ok": "claude"})
        bodies = [r["body"] for r in FakeLLM.requests_seen if r["path"].startswith("/notemp/")]
        self.assertEqual(len(bodies), 2)  # a 400, then the retry
        self.assertEqual(bodies[0]["temperature"], 0.7)
        self.assertNotIn("temperature", bodies[1])

    def _sdk_without_temperature(self, api_version):
        """Calls claude-sonnet-4-6 as an SDK without a temperature argument would."""
        from unittest import mock
        from utils import anthropic_utils as A
        sent = []

        def create(client, kwargs, stream):
            if "temperature" in kwargs:
                raise TypeError("Messages.create() got an unexpected keyword argument 'temperature'")
            sent.append(dict(kwargs.get("extra_body") or {}))
            if len(sent) == 1:
                raise RuntimeError("overloaded")  # one ordinary retry
            return mock.Mock(stop_reason="end_turn", content=[mock.Mock(type="text", text='{"ok": 1}')])

        self._claude_at("v1")
        A._notes_printed.clear()
        with mock.patch.object(A, "_create_message", side_effect=create), \
                mock.patch.object(A, "_sdk_api_version", return_value=api_version), \
                mock.patch.object(A.usage_tracker, "record_response"), \
                mock.patch.object(A.usage_tracker, "record_error"):
            out = A.call_claude_with_text_prompt("x", "claudeproxy:claude-sonnet-4-6",
                                                 generation_configs={"temperature": 0.2})
        self.assertEqual(out["parsed_response"], {"ok": 1})
        return sent

    def test_sdk_1x_without_temperature_argument_sends_it_in_the_body(self):
        # anthropic 1.x: no temperature argument, API version 2023-06-01 still takes it.
        self.assertEqual(self._sdk_without_temperature("2023-06-01"),
                         [{"temperature": 0.2}, {"temperature": 0.2}])

    def test_sdk_for_the_new_api_version_sends_no_temperature(self):
        self.assertEqual(self._sdk_without_temperature("2026-10-21"), [{}, {}])

    # ---- output budget (max_tokens) of new models ----

    def test_output_budget_comes_from_the_models_api_and_is_cached(self):
        FakeLLM.model_caps = {"claude-sonnet-5-5": 96000}
        self._claude_at("v1")
        from utils.llm_backend_utils import call_llm_with_text_prompt
        call_llm_with_text_prompt("a", "claudeproxy:claude-sonnet-5-5")
        call_llm_with_text_prompt("b", "claudeproxy:claude-sonnet-5-5")
        posts = [r for r in FakeLLM.requests_seen if r["body"] is not None]
        gets = [r for r in FakeLLM.requests_seen if r["body"] is None]
        self.assertEqual([r["body"]["max_tokens"] for r in posts], [96000, 96000])
        self.assertEqual([r["path"] for r in gets], ["/v1/v1/models/claude-sonnet-5-5"])  # asked once

    def test_po_claude_max_tokens_never_exceeds_the_cap(self):
        os.environ["PO_CLAUDE_MAX_TOKENS"] = "500000"
        self._claude_at("v1")
        from utils.llm_backend_utils import call_llm_with_text_prompt
        call_llm_with_text_prompt("a", "claudeproxy:claude-opus-5-5")
        self.assertEqual(FakeLLM.requests_seen[-1]["body"]["max_tokens"], 128000)

    def test_lower_cap_named_by_the_api_is_used_on_retry(self):
        FakeLLM.max_tokens_limit = 64000  # e.g. a proxy or an older deployment with a 64K cap
        self._claude_at("v1")
        from utils.llm_backend_utils import call_llm_with_text_prompt
        out = call_llm_with_text_prompt("a", "claudeproxy:claude-sonnet-5-5")
        self.assertEqual(out["parsed_response"], {"ok": "claude-stream"})
        posts = [r["body"]["max_tokens"] for r in FakeLLM.requests_seen if r["body"] is not None]
        self.assertEqual(posts, [128000, 64000])

    # ---- adaptive thinking that uses up max_tokens ----

    def _claude_at(self, prefix):
        self.write_providers({"providers": [
            {"name": "claudeproxy", "type": "anthropic", "base_url": f"http://127.0.0.1:{self.port}/{prefix}",
             "api_key": "k"}]})

    def _efforts(self, prefix):
        return [(r["body"].get("output_config") or {}).get("effort")
                for r in FakeLLM.requests_seen if r["path"].startswith(f"/{prefix}/") and r["body"] is not None]

    def test_thinking_until_max_tokens_is_retried_at_lower_effort(self):
        self._claude_at("runaway")
        from utils.llm_backend_utils import call_llm_with_text_prompt
        out = call_llm_with_text_prompt("plot code", "claudeproxy:claude-sonnet-5-5")
        self.assertEqual(out["parsed_response"], {"ok": "claude-stream"})
        self.assertEqual(self._efforts("runaway"), [None, "medium"])  # default high -> medium

    def test_cut_off_answer_is_retried_at_lower_effort(self):
        self._claude_at("cutoff")
        from utils.llm_backend_utils import call_llm_with_text_prompt
        out = call_llm_with_text_prompt("plot code", "claudeproxy:claude-sonnet-5-5")
        self.assertEqual(out["raw_response"], '{"ok": "claude-stream"}')
        self.assertEqual(self._efforts("cutoff"), [None, "medium"])

    def test_effort_ladder_starts_from_the_configured_or_default_level(self):
        os.environ["PO_CLAUDE_EFFORT"] = "max"
        self._claude_at("runaway")
        from utils.llm_backend_utils import call_llm_with_text_prompt
        call_llm_with_text_prompt("x", "claudeproxy:claude-sonnet-5-5")
        self.assertEqual(self._efforts("runaway"), ["max", "xhigh", "high", "medium"])
        FakeLLM.requests_seen.clear()
        os.environ["PO_CLAUDE_EFFORT"] = ""
        call_llm_with_text_prompt("x", "claudeproxy:claude-opus-5-5")  # API default is medium here
        self.assertEqual(self._efforts("runaway"), [None, "low"])

    def test_effort_ladder_is_bounded(self):
        self._claude_at("runaway-all")
        from unittest import mock
        from utils.llm_backend_utils import call_llm_with_text_prompt
        with self.assertRaises(ValueError), mock.patch("utils.anthropic_utils.time.sleep"):
            call_llm_with_text_prompt("x", "claudeproxy:claude-sonnet-5-5")
        # high (default) -> medium -> low, then the ordinary retries at low; 5 attempts in all.
        self.assertEqual(self._efforts("runaway-all"), [None, "medium", "low", "low", "low"])

    def test_older_claude_max_tokens_reply_returned_as_before(self):
        # Older models get no effort setting: a cut-off reply is returned as it was.
        self.write_providers({"providers": [
            {"name": "claudeproxy", "type": "anthropic", "base_url": f"http://127.0.0.1:{self.port}",
             "api_key": "k"}]})
        from unittest import mock
        from utils import anthropic_utils as A
        resp = mock.Mock(stop_reason="max_tokens", content=[mock.Mock(type="text", text="CUT")])
        with mock.patch.object(A, "_create_message", return_value=resp) as create, \
                mock.patch.object(A.usage_tracker, "record_response"):
            out = A.call_claude_with_text_prompt("x", "claudeproxy:claude-sonnet-4-6",
                                                 result_parsing_func=lambda r: r)
        self.assertEqual(out["raw_response"], "CUT")
        self.assertEqual(create.call_count, 1)
        self.assertNotIn("extra_body", create.call_args[0][1])

    def test_cut_off_reply_is_kept_if_the_lower_effort_retries_fail(self):
        self._claude_at("v1")
        from unittest import mock
        from utils import anthropic_utils as A
        cut = mock.Mock(stop_reason="max_tokens", content=[mock.Mock(type="text", text="\\section{Intro} cut")])
        boom = RuntimeError("overloaded")
        calls = []

        def create(client, kwargs, stream):
            calls.append((kwargs.get("extra_body") or {}).get("output_config", {}).get("effort"))
            if len(calls) == 1:
                return cut
            raise boom

        with mock.patch.object(A, "_create_message", side_effect=create), \
                mock.patch.object(A.usage_tracker, "record_response"), \
                mock.patch.object(A.usage_tracker, "record_error"), mock.patch.object(A.time, "sleep"):
            out = A.call_claude_with_text_prompt("x", "claudeproxy:claude-sonnet-5-5",
                                                 result_parsing_func=lambda r: r)
        self.assertEqual(out["raw_response"], "\\section{Intro} cut")  # as the code before the step-down returned
        self.assertEqual(calls, [None, "medium", "medium", "medium", "medium"])

    def test_unnamed_max_tokens_limit_steps_through_16384(self):
        from unittest import mock
        from utils import anthropic_utils as A
        self._claude_at("v1")
        sent = []

        class Err(Exception):
            status_code = 400

        def create(client, kwargs, stream):
            sent.append(kwargs["max_tokens"])
            if kwargs["max_tokens"] > 16384:
                raise Err("max_tokens is too large for this deployment")
            return mock.Mock(stop_reason="end_turn", content=[mock.Mock(type="text", text='{"ok": 1}')])

        with mock.patch.object(A, "_create_message", side_effect=create), \
                mock.patch.object(A.usage_tracker, "record_response"), \
                mock.patch.object(A.usage_tracker, "record_error"):
            out = A.call_claude_with_text_prompt("x", "claudeproxy:claude-sonnet-5-5")
        self.assertEqual(out["parsed_response"], {"ok": 1})
        self.assertEqual(sent, [128000, 16384])

    def _strict_gateway(self, first_reply):
        """A gateway that answers 400 whenever output_config is sent."""
        from unittest import mock
        sent = []

        class Err(Exception):
            status_code = 400

        def create(client, kwargs, stream):
            effort = ((kwargs.get("extra_body") or {}).get("output_config") or {}).get("effort")
            sent.append(effort)
            if effort:
                raise Err("output_config: Extra inputs are not permitted")
            if len(sent) == 1 and first_reply is not None:
                return first_reply
            return mock.Mock(stop_reason="end_turn", content=[mock.Mock(type="text", text='{"ok": 2}')])

        return sent, create

    def _run_gateway(self, create):
        from unittest import mock
        from utils import anthropic_utils as A
        self._claude_at("v1")
        with mock.patch.object(A, "_create_message", side_effect=create), \
                mock.patch.object(A.usage_tracker, "record_response"), \
                mock.patch.object(A.usage_tracker, "record_error"), mock.patch.object(A.time, "sleep"):
            return A.call_claude_with_text_prompt("x", "claudeproxy:claude-sonnet-5-5")

    def test_gateway_rejecting_the_configured_effort_is_asked_without_it(self):
        os.environ["PO_CLAUDE_EFFORT"] = "medium"
        sent, create = self._strict_gateway(None)
        out = self._run_gateway(create)
        self.assertEqual(out["parsed_response"], {"ok": 2})
        self.assertEqual(sent, ["medium", None])

    def test_step_down_refused_by_a_gateway_ends_as_before(self):
        from unittest import mock
        # Thinking used up max_tokens with no text: the step-down is refused and
        # the default effort is not asked again (it is the one that ran out).
        sent, create = self._strict_gateway(mock.Mock(stop_reason="max_tokens", content=[]))
        with self.assertRaises(Exception):
            self._run_gateway(create)
        self.assertEqual(sent, [None, "medium"])
        # With a cut-off reply in hand, that reply is returned, as before.
        cut = mock.Mock(stop_reason="max_tokens", content=[mock.Mock(type="text", text='{"part": 1}')])
        sent, create = self._strict_gateway(cut)
        out = self._run_gateway(create)
        self.assertEqual(out["parsed_response"], {"part": 1})
        self.assertEqual(sent, [None, "medium"])

    def test_dotted_model_ids_know_their_default_effort(self):
        from utils import anthropic_utils as A
        for name in ("claude-opus-5-5", "claude-opus-5.5", "anthropic/claude-haiku-5.5"):
            self.assertEqual(A._lower_effort(None, name), "low", name)  # default medium -> low
        self.assertEqual(A._lower_effort(None, "claude-sonnet-5.5"), "medium")  # default high

    # ---- refusals ----

    def _refusal_run(self, replies):
        from unittest import mock
        from utils import anthropic_utils as A
        self._claude_at("v1")
        with mock.patch.object(A, "_create_message", side_effect=replies) as create, \
                mock.patch.object(A.usage_tracker, "record_response"), \
                mock.patch.object(A.usage_tracker, "record_error"), mock.patch.object(A.time, "sleep"):
            try:
                return A.call_claude_with_text_prompt("review", "claudeproxy:claude-sonnet-5-5"), create.call_count
            except A.ClaudeRefusal as e:
                return e, create.call_count

    def test_one_off_refusal_is_asked_once_more(self):
        from unittest import mock
        refused = mock.Mock(stop_reason="refusal", content=[])
        ok = mock.Mock(stop_reason="end_turn", content=[mock.Mock(type="text", text='{"Overall": 6}')])
        out, calls = self._refusal_run([refused, ok])
        self.assertEqual(out["parsed_response"], {"Overall": 6})
        self.assertEqual(calls, 2)

    def test_second_refusal_is_final(self):
        from unittest import mock
        from utils import anthropic_utils as A
        refused = mock.Mock(stop_reason="refusal", content=[])
        out, calls = self._refusal_run([refused, refused, refused])
        self.assertIsInstance(out, A.ClaudeRefusal)
        self.assertEqual(calls, 2)  # not the full five attempts

    # ---- streams that end early ----

    def test_stream_cut_off_before_stop_reason_is_asked_again(self):
        from unittest import mock
        from utils import anthropic_utils as A
        A._streams_without_stop_reason.clear()
        self._claude_at("v1")
        cut = mock.Mock(stop_reason=None, content=[mock.Mock(type="text", text="```latex\n\\section{Intro} The")])
        full = mock.Mock(stop_reason="end_turn", content=[mock.Mock(type="text", text="```latex\nFULL\n```")])
        with mock.patch.object(A, "_create_message", side_effect=[cut, full]) as create, \
                mock.patch.object(A.usage_tracker, "record_response"), \
                mock.patch.object(A.usage_tracker, "record_error"):
            out = A.call_claude_with_text_prompt("x", "claudeproxy:claude-sonnet-5-5",
                                                 result_parsing_func=lambda r: r)
        self.assertEqual(create.call_count, 2)
        self.assertEqual(out["raw_response"], "```latex\nFULL\n```")

    def test_endpoint_that_never_sends_stop_reason_still_works(self):
        A = __import__("utils.anthropic_utils", fromlist=["x"])
        A._streams_without_stop_reason.clear()
        self.addCleanup(A._streams_without_stop_reason.clear)
        self._claude_at("nostop")
        from utils.llm_backend_utils import call_llm_with_text_prompt
        out = call_llm_with_text_prompt("a", "claudeproxy:claude-sonnet-5-5")
        self.assertEqual(out["parsed_response"], {"ok": "claude-stream"})
        posts = [r for r in FakeLLM.requests_seen if r["body"] is not None]
        self.assertEqual(len(posts), 2)  # asked once more, then accepted
        call_llm_with_text_prompt("b", "claudeproxy:claude-sonnet-5-5")
        posts = [r for r in FakeLLM.requests_seen if r["body"] is not None]
        self.assertEqual(len(posts), 3)  # remembered: no extra request any more

    def test_endpoint_that_ignores_stream_true_falls_back_to_a_plain_request(self):
        self._claude_at("jsononly")
        from unittest import mock
        from utils.llm_backend_utils import call_llm_with_text_prompt
        with mock.patch("utils.anthropic_utils.time.sleep"):
            out = call_llm_with_text_prompt("a", "claudeproxy:claude-sonnet-5-5")
        self.assertEqual(out["parsed_response"], {"ok": "claude"})
        posts = [r["body"] for r in FakeLLM.requests_seen if r["body"] is not None]
        self.assertEqual([bool(b.get("stream")) for b in posts], [True, True, False])
        self.assertEqual(posts[-1]["max_tokens"], 16384)  # within the SDK's non-streaming limit

    # ---- images over Claude's size limits ----

    @staticmethod
    def _png(w, h, color=(200, 30, 30)):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (w, h), color).save(buf, format="PNG")
        return buf.getvalue()

    def _sent_images(self):
        import base64
        import io
        from PIL import Image
        blocks = FakeLLM.requests_seen[-1]["body"]["messages"][-1]["content"]
        out = []
        for b in blocks:
            if b["type"] == "image":
                im = Image.open(io.BytesIO(base64.b64decode(b["source"]["data"])))
                out.append((im.size, im.format, b["source"]["media_type"]))
        return out

    def test_oversized_image_is_scaled_for_claude(self):
        self._claude_at("v1")
        from utils.llm_backend_utils import call_llm_with_multimodal_parts
        big = self._png(9000, 300)
        call_llm_with_multimodal_parts(
            [{"type": "text", "text": "caption"}, {"type": "image_bytes", "data": big, "mime_type": "image/jpeg"}],
            "claudeproxy:claude-sonnet-5-5")
        self.assertEqual(self._sent_images(), [((2576, 86), "PNG", "image/png")])

    def test_image_within_limits_is_sent_byte_for_byte(self):
        self._claude_at("v1")
        import base64
        from utils.llm_backend_utils import call_llm_with_multimodal_parts
        small = self._png(2576, 1400)
        call_llm_with_multimodal_parts([{"type": "image_bytes", "data": small, "mime_type": "image/png"}],
                                       "claudeproxy:claude-sonnet-5-5")
        sent = FakeLLM.requests_seen[-1]["body"]["messages"][-1]["content"][0]["source"]["data"]
        self.assertEqual(base64.b64decode(sent), small)

    def test_jpeg_stays_jpeg_and_many_images_use_the_2000px_limit(self):
        import io
        from PIL import Image
        from utils.anthropic_utils import fit_image_for_claude
        buf = io.BytesIO()
        Image.new("RGB", (8400, 4200), (10, 120, 200)).save(buf, format="JPEG")
        out = Image.open(io.BytesIO(fit_image_for_claude(buf.getvalue())))
        self.assertEqual((out.format, out.size), ("JPEG", (2576, 1288)))

        self._claude_at("v1")
        from utils.llm_backend_utils import call_llm_with_multimodal_parts
        parts = [{"type": "image_bytes", "data": self._png(2100, 30), "mime_type": "image/png"}] * 21
        call_llm_with_multimodal_parts(parts, "claudeproxy:claude-sonnet-5-5")
        sizes = {s for s, _, _ in self._sent_images()}
        self.assertEqual(sizes, {(2000, 29)})

    def test_image_limit_env_and_unreadable_images(self):
        os.environ["PO_CLAUDE_IMAGE_MAX_EDGE"] = "1000"
        import io
        from PIL import Image
        from utils.anthropic_utils import fit_image_for_claude
        out = Image.open(io.BytesIO(fit_image_for_claude(self._png(3000, 1500))))
        self.assertEqual(out.size, (1000, 500))
        os.environ["PO_CLAUDE_IMAGE_MAX_EDGE"] = "99999"  # never above the API's 8000 px
        out = Image.open(io.BytesIO(fit_image_for_claude(self._png(9000, 10))))
        self.assertEqual(out.size[0], 8000)
        junk = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        self.assertEqual(fit_image_for_claude(junk), junk)

    def test_16bit_grayscale_png_is_rescaled_not_clipped(self):
        import io
        from PIL import Image
        from utils.anthropic_utils import fit_image_for_claude
        im = Image.new("I;16", (3000, 100))
        im.putdata([int(x * 65535 / 2999) for _ in range(100) for x in range(3000)])
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        out = Image.open(io.BytesIO(fit_image_for_claude(buf.getvalue()))).convert("L")
        self.assertEqual(out.size, (2576, 86))
        mean = sum(out.getdata()) / (out.size[0] * out.size[1])
        self.assertTrue(110 < mean < 145, mean)  # a 0..65535 ramp stays a mid-grey ramp

    def test_transparent_png_keeps_alpha_when_scaled(self):
        import io
        from PIL import Image
        from utils.anthropic_utils import fit_image_for_claude
        buf = io.BytesIO()
        Image.new("RGBA", (5000, 100), (0, 0, 0, 0)).save(buf, format="PNG")
        out = Image.open(io.BytesIO(fit_image_for_claude(buf.getvalue())))
        self.assertEqual((out.format, out.mode, out.size), ("PNG", "RGBA", (2576, 52)))

    def test_image_scaling_depends_on_the_model(self):
        self._claude_at("v1")
        import base64
        from utils.llm_backend_utils import call_llm_with_multimodal_parts
        wide, figure = self._png(9000, 300), self._png(4800, 3600)
        parts = [{"type": "image_bytes", "data": wide, "mime_type": "image/png"},
                 {"type": "image_bytes", "data": figure, "mime_type": "image/png"}]
        # Older Claude models: only the API's hard 8000 px limit (Anthropic scales them further anyway).
        call_llm_with_multimodal_parts(parts, "claudeproxy:claude-sonnet-4-6")
        self.assertEqual([s for s, _, _ in self._sent_images()], [(8000, 267), (4800, 3600)])
        # Other models behind an Anthropic-compatible endpoint: images as before, untouched.
        call_llm_with_multimodal_parts(parts, "claudeproxy:kimi-k2")
        blocks = FakeLLM.requests_seen[-1]["body"]["messages"][-1]["content"]
        self.assertEqual([base64.b64decode(b["source"]["data"]) for b in blocks if b["type"] == "image"],
                         [wide, figure])

    def test_image_over_the_byte_limit_is_shrunk_until_it_fits(self):
        import base64
        import io
        from PIL import Image
        from utils import anthropic_utils as A
        noise = Image.frombytes("RGB", (2400, 2400), os.urandom(2400 * 2400 * 3))
        buf = io.BytesIO()
        noise.save(buf, format="PNG")  # ~17 MB: a JPEG at full size is still over the limit
        data = A.fit_image_for_claude(buf.getvalue(), model_name="claude-sonnet-5-5")
        self.assertLessEqual(len(data), A._CLAUDE_IMAGE_MAX_BYTES)
        self.assertLessEqual(len(base64.b64encode(data)), 5 * 1024 * 1024)  # the API's 5 MB, as base64
        self.assertEqual(Image.open(io.BytesIO(data)).format, "JPEG")

    def test_exif_rotation_and_multi_frame_jpegs(self):
        import io
        from PIL import Image
        from utils.anthropic_utils import fit_image_for_claude
        im = Image.new("RGB", (3000, 1000), (10, 200, 10))
        exif = im.getexif()
        exif[0x0112] = 6  # shown rotated by 90 degrees: 1000 x 3000
        buf = io.BytesIO()
        im.save(buf, format="JPEG", exif=exif)
        out = Image.open(io.BytesIO(fit_image_for_claude(buf.getvalue())))
        self.assertEqual(out.size, (859, 2576))  # the pixels are turned, as the tag said
        buf = io.BytesIO()
        Image.new("RGB", (4000, 3000)).save(buf, format="MPO", save_all=True,
                                            append_images=[Image.new("RGB", (4000, 3000))])
        out = Image.open(io.BytesIO(fit_image_for_claude(buf.getvalue())))
        self.assertEqual((out.format, out.size), ("JPEG", (2576, 1932)))

    def test_deepseek_text_only_strips_images(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk"
        os.environ["DEEPSEEK_BASE_URL"] = self.base
        from utils.llm_backend_utils import call_llm_with_images
        img = os.path.join(self.tmp.name, "b.png")
        with open(img, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        call_llm_with_images("look", [img], "deepseek-chat")
        content = FakeLLM.requests_seen[-1]["body"]["messages"][-1]["content"]
        self.assertIsInstance(content, str)
        self.assertEqual(content, "look\n\n[Note: 1 image attachment(s) omitted because the selected model "
                                  "is text-only.]")  # the original DeepSeek text

    def test_deepseek_multimodal_parts_text_as_original(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk"
        os.environ["DEEPSEEK_BASE_URL"] = self.base
        from utils.llm_backend_utils import call_llm_with_multimodal_parts
        parts = [{"type": "text", "text": "PART-A"}, {"type": "text", "text": "PART-B"},
                 {"type": "image_bytes", "data": b"\xff\xd8xx", "mime_type": "image/jpeg"},
                 {"type": "text", "text": "PART-C"}]
        call_llm_with_multimodal_parts(parts=parts, model_name="deepseek-chat", system_prompt="SYS",
                                       result_parsing_func=lambda s: s)
        messages = FakeLLM.requests_seen[-1]["body"]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "SYS"})
        self.assertEqual(messages[-1]["content"], "PART-A\nPART-B\nPART-C")  # "\n"-joined, no note


class ScholarTests(unittest.TestCase):
    def test_redacts_keys_in_request_errors(self):
        from utils.scholar_utils import _redact
        msg = ("HTTPSConnectionPool(host='api.openalex.org', port=443): Max retries exceeded with url: "
               "/works?search=x&api_key=OA_SECRET_123456&mailto=a%40b.org (Caused by ...)")
        out = _redact(msg)
        self.assertNotIn("OA_SECRET", out)
        self.assertIn("api_key=***&mailto", out)

    def test_title_lookup_does_not_borrow_another_papers_abstract(self):
        import utils.scholar_utils as S
        resnet = {"title": "Deep Residual Learning for Image Recognition", "year": 2016, "abstract": "",
                  "doi": None, "source": "semantic_scholar"}
        survey = {"title": "Deep Residual Learning for Image Recognition: A Survey", "year": 2022,
                  "abstract": "We survey ...", "doi": "10.1/survey", "source": "openalex"}
        same = {"title": "Deep residual learning for image recognition.", "year": 2016,
                "abstract": "Deeper neural networks are more difficult to train.", "doi": None, "source": "crossref"}
        saved_sources, saved_env = dict(S.SOURCES), os.environ.get("PO_SCHOLAR_SOURCES")
        try:
            os.environ["PO_SCHOLAR_SOURCES"] = "semantic_scholar,openalex,crossref"
            S.SOURCES["semantic_scholar"] = lambda q, n: [dict(resnet)]
            S.SOURCES["openalex"] = lambda q, n: [dict(survey)]
            S.SOURCES["crossref"] = lambda q, n: [dict(same)]
            hit = S.title_lookup("Deep Residual Learning for Image Recognition")
            self.assertEqual(hit["year"], 2016)
            self.assertTrue(hit["abstract"].startswith("Deeper"))
            self.assertEqual(hit["source"], "semantic_scholar+crossref")
        finally:
            S.SOURCES.clear()
            S.SOURCES.update(saved_sources)
            if saved_env is None:
                os.environ.pop("PO_SCHOLAR_SOURCES", None)
            else:
                os.environ["PO_SCHOLAR_SOURCES"] = saved_env

    def _patched_scholar(self, fake_get):
        """Context: requests.get faked, limiters without spacing, no sleeping, fresh cooldowns."""
        import contextlib
        from unittest import mock
        import utils.scholar_utils as S

        @contextlib.contextmanager
        def ctx():
            saved_limiters, saved_env = dict(S._LIMITERS), os.environ.get("PO_SCHOLAR_SOURCES")
            os.environ.pop("PO_SCHOLAR_SOURCES", None)
            for name in list(S._LIMITERS):
                S._LIMITERS[name] = S.RateLimiter(f"test_enrich_{name}_{os.getpid()}", 0.0)
            S._rate_state.clear()
            try:
                with mock.patch.object(S.requests, "get", side_effect=fake_get), \
                        mock.patch.object(S.time, "sleep"), mock.patch("builtins.print"):
                    yield S
            finally:
                S._LIMITERS.clear()
                S._LIMITERS.update(saved_limiters)
                S._rate_state.clear()
                if saved_env is None:
                    os.environ.pop("PO_SCHOLAR_SOURCES", None)
                else:
                    os.environ["PO_SCHOLAR_SOURCES"] = saved_env
        return ctx()

    def test_enrichment_asks_semantic_scholar_only_when_it_answers(self):
        import requests
        from urllib.parse import urlparse
        hosts = []
        state = {"s2_down": False}

        class R:
            def __init__(self, code, js):
                self.status_code, self._js, self.headers, self.text = code, js, {}, ""

            def json(self):
                return self._js

        def fake_get(url, params=None, headers=None, timeout=None):
            host = urlparse(url).netloc
            hosts.append(host)
            if "semanticscholar" in host:
                if state["s2_down"]:
                    raise requests.ConnectionError("Failed to establish a new connection: refused")
                return R(200, {"data": [{"title": params["query"], "year": 2020, "abstract": None,
                                         "authors": [], "publicationDate": "2020-01-01"}]})
            if "openalex" in host:
                return R(200, {"results": [{"title": params.get("search"), "publication_year": 2020,
                                            "authorships": [], "abstract_inverted_index": {"Abs": [0]}}]})
            raise requests.ConnectionError(f"{host} unreachable")

        with self._patched_scholar(fake_get) as S:
            # S2 answers (a hit without abstract): that is the result, as in the original.
            hit = S.enrich_lookup("Some Paper Title")
            self.assertEqual(hit["source"], "semantic_scholar")
            self.assertFalse(hit["abstract"])
            self.assertEqual(set(hosts), {"api.semanticscholar.org"})
            # S2 unreachable: the other sources fill in; dead hosts are skipped after a few errors.
            state["s2_down"] = True
            hosts.clear()
            hit = S.enrich_lookup("Some Paper Title")
            self.assertEqual(hit["source"], "openalex")
            first = len(hosts)
            for _ in range(5):
                S.enrich_lookup("Another Paper Title")
            # S2 is skipped once it has failed 3 times in a row; OpenAlex answers each lookup.
            self.assertEqual(hosts[first:], ["api.openalex.org"] * 5)
            self.assertTrue(S._cooling_down("semantic_scholar"))
            self.assertFalse(S._cooling_down("openalex"))
            # A dead host is skipped too, instead of 3 attempts + back-off on every lookup.
            os.environ["PO_SCHOLAR_SOURCES"] = "semantic_scholar,crossref"
            hosts.clear()
            for _ in range(4):
                S.enrich_lookup("Third Paper Title")
            self.assertEqual(hosts, ["api.crossref.org"] * 3)
            self.assertTrue(S._cooling_down("crossref"))

    def test_non_gemini_discovery_matches_the_original(self):
        """S2 6 + 6 for the two queries with the original reasons; other sources only top up."""
        import threading
        from unittest import mock
        from urllib.parse import urlparse
        import methods.agents.literature_review_agent as A

        mode = {"s2": 6}

        class R:
            def __init__(self, code, js=None, text=""):
                self.status_code, self._js, self.headers, self.text = code, js, {}, text

            def json(self):
                return self._js

        def fake_get(url, params=None, headers=None, timeout=None):
            host = urlparse(url).netloc
            if "semanticscholar" in host:
                q, n = params["query"], min(int(params["limit"]), mode["s2"])
                return R(200, {"data": [{"title": f"S2 {q} {i}", "year": 2020, "abstract": "a", "authors": [],
                                         "publicationDate": "2020-01-01"} for i in range(n)]})
            if "arxiv" in host:
                return R(200, text='<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
            if "openalex" in host:
                return R(200, {"results": [{"title": f"OA {params['search']}", "publication_year": 2020,
                                            "authorships": []}]})
            return R(404, {})

        agent = object.__new__(A.HybridLiteratureAgent)
        agent.model_name, agent.print_lock = "gpt-4.1", threading.Lock()
        task = {"section": "Related Work", "focus": "f", "context": "c", "search_type": "exploration"}
        llm = {"parsed_response": {"queries": ["query one", "query two"], "likely_titles": []}}
        with self._patched_scholar(fake_get), mock.patch.object(A, "call_llm_with_text_prompt", return_value=llm):
            cands = agent._discover_candidates_via_scholar_apis(task, "p", "2026-11")
            self.assertEqual([c["title"] for c in cands],
                             [f"S2 query one {i}" for i in range(6)] + [f"S2 query two {i}" for i in range(6)])
            self.assertEqual(cands[0]["reason"], "Semantic Scholar match for query: query one")
            mode["s2"] = 1  # S2 nearly empty: the original two hits first, then the other sources
            cands = agent._discover_candidates_via_scholar_apis(task, "p", "2026-11")
            self.assertEqual([c["title"] for c in cands][:2], ["S2 query one 0", "S2 query two 0"])
            self.assertIn("OA query one", [c["title"] for c in cands])
            self.assertIn("OpenAlex match for query: query one", [c["reason"] for c in cands])

    def test_norm_title_keeps_cjk(self):
        from utils.scholar_utils import _norm_title
        self.assertEqual(_norm_title("基于扩散先验的深度估计"), "基于扩散先验的深度估计")
        self.assertEqual(_norm_title("Deep  Learning!"), _norm_title("deep learning"))

    def test_limiter_backoff_is_capped_and_shared(self):
        import time
        from utils.scholar_utils import RateLimiter
        lim = RateLimiter(f"test_{os.getpid()}", 0.05, cross_process=True)
        lim.backoff(10_000)
        self.assertLessEqual(lim._last - time.time(), RateLimiter.MAX_BACKOFF)
        other = RateLimiter(lim.name, 0.05, cross_process=True)  # e.g. another process
        self.assertAlmostEqual(other._update_shared(lambda last: last), lim._last, places=3)
        lim._last = 0.0
        lim._write_stamp(0.0)
        t0 = time.time()
        lim.wait()
        other.wait()
        self.assertGreaterEqual(time.time() - t0, 0.04)
        for path in (lim._stamp_path, lim._lock_path):
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
