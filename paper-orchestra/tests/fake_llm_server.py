"""A local fake OpenAI / Anthropic endpoint for offline tests (not a test module itself).

Paths (any prefix before /v1 selects a behaviour):
  /v1/...        normal replies, with usage data
  /nousage/v1/   replies without usage data
  /auth/v1/      HTTP 401 whose message echoes the bearer key (like a careless gateway)
  /slow/v1/      sleeps SLOW_SECONDS before answering
  /nolist/v1/    has no GET /models (HTTP 404), like some gateways

GET /v1/models answers in the Anthropic shape when the request carries an
anthropic-version header, else in the OpenAI shape; GET /v1beta/models answers
in the Gemini shape (MODELS lists what each one returns).
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SLOW_SECONDS = 3.0


MODELS = {
    # (id, created): listed oldest first on purpose; the tool sorts newest first.
    "openai": [("gpt-old", 100), ("text-embedding-3-small", 150), ("gpt-new", 300), ("whisper-1", 50),
               ("gpt-image-1", 200)],
    "anthropic": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],  # newest first, like the API
    "gemini": [("gemini-3.6-flash", ["generateContent", "countTokens"]),
               ("gemini-embedding-001", ["embedContent"]),
               ("gemini-3.8-flash", ["generateContent"]),
               ("gemma-3-27b-it", ["generateContent"]),
               ("gemini-3-pro-image-preview", ["generateContent"]),
               ("gemini-3.8-flash-tts", ["generateContent"])],
}


class FakeHandler(BaseHTTPRequestHandler):
    requests_seen = []
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        with FakeHandler.lock:
            FakeHandler.requests_seen.append({"path": self.path, "body": body, "headers": dict(self.headers)})
        path = self.path
        if path.startswith("/slow/"):
            time.sleep(SLOW_SECONDS)
        if path.startswith("/auth/"):
            key = (self.headers.get("Authorization") or self.headers.get("x-api-key") or "").replace("Bearer ", "")
            self._send(401, {"error": {"message": f"Incorrect API key provided: {key}", "type": "invalid_request_error"}})
            return
        usage = not path.startswith("/nousage/")
        if path.endswith("/chat/completions"):
            rsp = {
                "id": "x", "object": "chat.completion", "created": 0, "model": body.get("model"),
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": '```json\n{"ok": true}\n```'}}],
            }
            if usage:
                rsp["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
            self._send(200, rsp)
        elif path.endswith("/messages"):
            rsp = {
                "id": "m", "type": "message", "role": "assistant", "model": body.get("model"),
                "content": [{"type": "text", "text": '{"ok": "claude"}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 3, "cache_read_input_tokens": 2},
            }
            self._send(200, rsp)
        elif path.endswith("/images/generations"):
            rsp = {"created": 0, "data": [{"b64_json": "aGVsbG8="}]}
            if usage:
                rsp["usage"] = {"input_tokens": 4, "output_tokens": 9, "total_tokens": 13,
                                "input_tokens_details": {"text_tokens": 4, "image_tokens": 0}}
            self._send(200, rsp)
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_GET(self):
        with FakeHandler.lock:
            FakeHandler.requests_seen.append({"path": self.path, "body": None, "headers": dict(self.headers)})
        path = self.path.split("?", 1)[0]
        if path.startswith("/slow/"):
            time.sleep(SLOW_SECONDS)
        if path.startswith("/auth/"):
            key = (self.headers.get("Authorization") or self.headers.get("x-api-key") or "").replace("Bearer ", "")
            self._send(401, {"error": {"message": f"Incorrect API key provided: {key}", "type": "invalid_request_error"}})
        elif path.startswith("/nolist/") or not path.endswith("/models"):
            self._send(404, {"error": {"message": "not found"}})
        elif path.endswith("/v1beta/models"):
            self._send(200, {"models": [
                {"name": f"models/{m}", "supportedGenerationMethods": actions}
                for m, actions in MODELS["gemini"]]})
        elif self.headers.get("anthropic-version"):
            data = [{"type": "model", "id": m, "display_name": m, "created_at": "2026-01-01T00:00:00Z"}
                    for m in MODELS["anthropic"]]
            self._send(200, {"data": data, "has_more": False,
                             "first_id": data[0]["id"], "last_id": data[-1]["id"]})
        else:
            self._send(200, {"object": "list", "data": [
                {"id": m, "object": "model", "created": created, "owned_by": "fake"}
                for m, created in MODELS["openai"]]})

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client gave up (timeout tests)


class FakeServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.root = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, prefix: str = "") -> str:
        return f"{self.root}{prefix}/v1"

    @staticmethod
    def clear():
        with FakeHandler.lock:
            FakeHandler.requests_seen.clear()

    @staticmethod
    def seen():
        with FakeHandler.lock:
            return list(FakeHandler.requests_seen)


# --------------------------------------------------------------------------
# Environment isolation: tests must never reach a real provider, even when the
# developer's .env holds keys (load_dotenv does not override variables that
# are already set, so blanking them is enough).
# --------------------------------------------------------------------------

_EXTRA_ENV = [
    "VERTEX_AI_PROJECT", "VERTEX_AI_LOCATION", "PO_AUTO_MODEL", "PO_DEFAULT_PROVIDER",
    "PO_PROVIDERS_FILE", "PO_SCHOLAR_SOURCES", "CORE_API_KEY",
]


def provider_env_names():
    import os
    import sys

    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    from utils.providers import _builtin_providers, _env_prefix

    names = set(_EXTRA_ENV)
    for spec in _builtin_providers():
        names.update(spec.api_key_env)
        names.update(spec.base_url_env)
        names.add(f"{_env_prefix(spec.name)}_DEFAULT_MODEL")
        names.add(f"{_env_prefix(spec.name)}_API_KEY")
    return sorted(names)


class IsolatedEnv:
    """Blanks every provider variable; restores os.environ and the registry caches on exit."""

    def __init__(self, extra_names=()):
        self.names = provider_env_names() + list(extra_names)

    def __enter__(self):
        import os

        self.saved = {k: os.environ.get(k) for k in self.names}
        for k in self.names:
            os.environ[k] = ""
        reset_provider_caches()
        return self

    def __exit__(self, *exc):
        import os

        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reset_provider_caches()
        return False

    def env_for_subprocess(self, **overrides):
        import os

        env = dict(os.environ)
        for k in self.names:
            env[k] = ""
        env.update({"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
                    "PYTHONIOENCODING": "utf-8"})
        env.update(overrides)
        return env


def reset_provider_caches():
    import utils.providers as P

    P._registry_cache = None
    P._clients.clear()
    P._text_only_models.clear()
