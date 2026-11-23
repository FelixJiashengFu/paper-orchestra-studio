"""Fault injection for the e2e "faults" scenario (PO_E2E_INJECT_FAULTS=1).

The new per-run reports must be additive and opt-out-safe: if they break, the
paper run must still finish with all its original outputs. This module makes
them break from the outside, at points whose failure their callers promise to
tolerate:

  utils.usage_tracker.extract_tokens  -> raises (record_response() must swallow it)
  utils.citation_audit.audit_run      -> raises (run_reports must only warn)
  utils.citation_audit.build_audit    -> raises

(run_e2e.py additionally turns the report file paths into directories, so every
report write fails with an OS error.) A module or function that does not exist
is skipped, so the scenario also runs against code without these reports.
Each fault that fires is appended to PO_E2E_FAULT_LOG as one JSON line.
"""

import importlib.abc
import importlib.machinery
import json
import os
import sys
import threading
import time

FAULT_MESSAGE = "PO_E2E injected fault"

TARGETS = {
    "utils.usage_tracker": ["extract_tokens"],
    "utils.citation_audit": ["audit_run", "build_audit"],
}

_lock = threading.Lock()


def _log(module: str, func: str) -> None:
    path = os.environ.get("PO_E2E_FAULT_LOG", "").strip()
    if not path:
        return
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), "module": module, "function": func}) + "\n")


def _raiser(module: str, func: str):
    def fault(*args, **kwargs):
        _log(module, func)
        raise RuntimeError(f"{FAULT_MESSAGE} in {module}.{func}")

    fault.__name__ = func
    return fault


def _patch(module) -> None:
    for func in TARGETS.get(module.__name__, []):
        if callable(getattr(module, func, None)):
            setattr(module, func, _raiser(module.__name__, func))


class _Loader(importlib.abc.Loader):
    def __init__(self, inner):
        self._inner = inner

    def create_module(self, spec):
        return self._inner.create_module(spec)

    def exec_module(self, module):
        self._inner.exec_module(module)
        _patch(module)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname not in TARGETS:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and spec.loader is not None:
            spec.loader = _Loader(spec.loader)
        return spec


def install() -> None:
    if not any(isinstance(f, _Finder) for f in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
    for name in TARGETS:  # already imported (unlikely this early)
        if name in sys.modules:
            _patch(sys.modules[name])
