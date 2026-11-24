#!/usr/bin/env python3
"""Copy the shared MCP server, UI bundles, bridge, skills and agents into every platform bundle.

    python3 plugins/scripts/sync_platforms.py          # write
    python3 plugins/scripts/sync_platforms.py --check  # report drift, exit 1 if any

Nothing is ever deleted: files that exist only in a platform bundle are left alone.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import sys
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platforms as P  # noqa: E402

_KEEP_AGENT_KEYS = ("name", "description")


def minimal_agent(text: str) -> str:
    """Keep only `name` / `description` frontmatter (hosts with a strict agent schema)."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return text
    kept: List[str] = []
    keep = False
    for line in m.group(1).split("\n"):
        top = re.match(r"^([A-Za-z_][\w-]*):", line)
        if top:
            keep = top.group(1) in _KEEP_AGENT_KEYS
        if keep:
            kept.append(line)
    return "---\n" + "\n".join(kept) + "\n---\n" + text[m.end():]


def plan() -> List[Tuple[Path, bytes, bool]]:
    """Return (destination, content, executable) for every managed file."""
    out: List[Tuple[Path, bytes, bool]] = []
    server = P.SHARED_SERVER.read_bytes()
    ui = {fn: (P.SHARED_UI_DIR / fn).read_bytes() for fn in P.UI_FILES}
    bridge = {fn: (P.BRIDGE_SRC_DIR / fn).read_bytes() for fn in P.BRIDGE_FILES}

    for root in [p.root for p in P.PLATFORMS.values()] + list(P.EXTRA_SERVER_TARGETS):
        out.append((root / "server" / P.SHARED_SERVER.name, server, True))
        for fn, data in ui.items():
            out.append((root / "assets" / "ui" / fn, data, False))

    for plat in P.PLATFORMS.values():
        if plat.root / "mcp" != P.BRIDGE_SRC_DIR:
            for fn, data in bridge.items():
                out.append((plat.root / "mcp" / fn, data, False))
        for skill in P.shared_skill_names():
            if skill in plat.tuned_skills:
                continue
            for src in sorted((P.SHARED_SKILLS_DIR / skill).rglob("*")):
                if src.is_file():
                    rel = src.relative_to(P.SHARED_SKILLS_DIR)
                    out.append((plat.root / "skills" / rel, src.read_bytes(), False))
        if plat.agents != "none":
            for name in P.shared_agent_names():
                text = (P.SHARED_AGENTS_DIR / name).read_text(encoding="utf-8")
                if plat.agents == "minimal":
                    text = minimal_agent(text)
                out.append((plat.root / "agents" / name, text.encode("utf-8"), False))
    return out


def drift() -> List[str]:
    problems = []
    for dest, data, _ in plan():
        rel = dest.relative_to(P.REPO_ROOT).as_posix()
        if not dest.is_file():
            problems.append(f"missing: {rel}")
        elif dest.read_bytes() != data:
            problems.append(f"out of date: {rel}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="report drift without writing")
    args = ap.parse_args()
    if args.check:
        problems = drift()
        for line in problems:
            print(line)
        print(f"{len(problems)} file(s) out of sync" if problems else "All platform bundles are in sync.")
        return 1 if problems else 0
    print(f"Synced {write()} file(s) across {len(P.PLATFORMS)} platforms.")
    return 0


def write() -> int:
    """Write every managed file that is missing or different; return how many changed."""
    written = 0
    for dest, data, executable in plan():
        if dest.is_file() and dest.read_bytes() == data:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        mode = 0o644 | (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH if executable else 0)
        os.chmod(dest, mode)
        written += 1
    return written


if __name__ == "__main__":
    raise SystemExit(main())
