#!/usr/bin/env python3
"""Start the shared PaperOrchestra MCP server in Muse host-agent mode.

A Muse plugin manifest declares an MCP server as an argv only (no `env`), so
this launcher sets the mode and then runs the shared server unchanged:

    PO_HOST_AGENT=muse   every backend LLM step runs on the signed-in Muse Code
                         CLI (`muse exec`) and no other LLM API is called.

Set PO_HOST_AGENT=off in the environment to start the server without it.

Muse starts stdio servers with a small fixed set of environment variables
(HOME, PATH, ...), so settings that other hosts pass through `env` are read
from an optional file instead, one KEY=VALUE per line:

    ~/.config/muse/paper-orchestra.env        (or $XDG_CONFIG_HOME/muse/...)

for example PAPER_ORCHESTRA_REPO, PAPER_ORCHESTRA_PYTHON, PO_DEFAULT_PDF_DIR,
PO_DEFAULT_HTML_DIR, MUSE_DEFAULT_MODEL, PO_MUSE_REASONING_EFFORT. Values
already present in the environment win. LLM provider keys do not belong there:
this mode calls none of them.
"""

import os
import runpy
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent / "paper_orchestra_mcp.py"


def env_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "").strip() or str(Path.home() / ".config")
    return Path(base) / "muse" / "paper-orchestra.env"


def load_env_file(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key.replace("_", "").isalnum() and not key[0].isdigit():
            os.environ.setdefault(key, os.path.expanduser(value))


def main() -> None:
    load_env_file(env_file())
    os.environ.setdefault("PO_HOST_AGENT", "muse")
    sys.argv[0] = str(SERVER)
    runpy.run_path(str(SERVER), run_name="__main__")


if __name__ == "__main__":
    main()
