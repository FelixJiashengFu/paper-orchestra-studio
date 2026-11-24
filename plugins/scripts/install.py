#!/usr/bin/env python3
"""Package or install the PaperOrchestra Studio plugin for one of the agent platforms (the original five plus Muse).

    python3 plugins/scripts/install.py --platform all            # build zips + print next steps
    python3 plugins/scripts/install.py --platform antigravity --apply   # also copy into the host folder

Without --apply nothing outside this repository is touched. Zips are written to plugins/dist/.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import platforms as P  # noqa: E402
import sync_platforms  # noqa: E402
import verify_platforms  # noqa: E402

SKIP = {".DS_Store", "__pycache__"}
REPO_SLUG = "FelixJiashengFu/paper-orchestra-studio"

NEXT_STEPS = {
    "claude-code": [
        f"claude plugin marketplace add {REPO_SLUG}        # or: claude plugin marketplace add {{repo}}",
        "claude plugin install paper-orchestra-studio@paper-orchestra-studio",
    ],
    "antigravity": [
        "agy plugin install {root}                         # or re-run this script with --apply",
        "agy plugin validate {root}",
    ],
    "codex": [
        f"codex plugin marketplace add {REPO_SLUG}         # or: codex plugin marketplace add {{repo}}",
        "Then enable `paper-orchestra-studio` in the Codex / ChatGPT desktop Plugins view.",
        "Optional always-on rules: append {root}/AGENTS.md to ~/.codex/AGENTS.md.",
    ],
    "qoder": [
        "qoder plugins install {root}                      # or install {zip} as a local plugin in Qoder Desktop",
        "qoder plugins validate {root}",
    ],
    "workbuddy": [
        "WorkBuddy: Plugins -> add marketplace from the folder {repo} (or install {zip}), then install `paper-orchestra-studio`.",
        "CodeBuddy CLI: codebuddy --plugin-dir {root}        # session only",
        "CodeBuddy CLI: codebuddy plugin install paper-orchestra-studio@paper-orchestra-studio   # after adding the marketplace",
    ],
    "muse": [
        "muse plugins validate {root} --json                 # must print \"valid\": true",
        "muse plugins install {root} --scope user",
        "muse plugins approve paper-orchestra-studio           # trust its skills and two MCP servers",
        "Start a new Muse session and ask it to call po_check_environment: hostAgent.name must be \"muse\".",
        "Optional settings (output folders, model, effort): ~/.config/muse/paper-orchestra.env, see {root}/README.md.",
    ],
}


def build_zip(plat: P.Platform, version: str) -> Path:
    out = P.PLUGINS_ROOT / "dist" / f"{P.PLUGIN_NAME}-{version}-{plat.key}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(plat.root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP)
            for fn in sorted(files):
                if fn in SKIP or fn.endswith((".pyc", ".pyo", ".zip")):
                    continue
                fp = Path(root) / fn
                zf.write(fp, arcname=fp.relative_to(plat.root).as_posix())
    return out


def apply_antigravity(plat: P.Platform) -> Path:
    dest = Path.home() / ".gemini" / "config" / "plugins" / P.PLUGIN_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(plat.root, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*SKIP))
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--platform", required=True, choices=list(P.PLATFORMS) + ["all"])
    ap.add_argument("--apply", action="store_true", help="copy into the host's plugin folder where that is a documented install path (Antigravity)")
    args = ap.parse_args()

    sync_platforms.write()
    version = verify_platforms.server_version()
    keys = list(P.PLATFORMS) if args.platform == "all" else [args.platform]
    for key in keys:
        plat = P.PLATFORMS[key]
        zip_path = build_zip(plat, version)
        print(f"\n== {plat.label} ==")
        print(f"bundle: {plat.root}")
        print(f"zip:    {zip_path}")
        if args.apply and key == "antigravity":
            print(f"copied: {apply_antigravity(plat)}")
        elif args.apply:
            print("--apply: this host installs through its own plugin manager; follow the steps below.")
        for line in NEXT_STEPS[key]:
            print("  " + line.format(root=plat.root, repo=P.REPO_ROOT, zip=zip_path))
    print("\nVerify parity at any time: python3 plugins/scripts/verify_platforms.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
