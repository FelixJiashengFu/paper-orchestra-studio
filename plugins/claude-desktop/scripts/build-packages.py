#!/usr/bin/env python3
"""Sync shared MCP server into all plugin targets and build .plugin & .mcpb archives."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile

EXCLUDED_NAMES = {".DS_Store", "__pycache__"}


def _sync_server(shared_server: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(shared_server, dest)
    mode = dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    os.chmod(dest, mode)


def _zip_dir_via_tmp(src_dir: Path, out_file: Path) -> int:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_zip = tempfile.mkstemp(prefix=f"{out_file.stem}-", suffix=".zip")
    os.close(fd)
    count = 0
    try:
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(src_dir):
                dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_NAMES)
                root_p = Path(root)
                for fn in sorted(files):
                    if fn in EXCLUDED_NAMES or fn.endswith((".pyc", ".pyo")):
                        continue
                    fp = root_p / fn
                    rel = fp.relative_to(src_dir).as_posix()
                    zf.write(fp, arcname=rel)
                    count += 1
        shutil.copy2(tmp_zip, out_file)
        os.chmod(out_file, 0o644)
    finally:
        if os.path.exists(tmp_zip):
            os.unlink(tmp_zip)
    return count


def main() -> int:
    claude_dir = Path(__file__).resolve().parents[1]
    plugins_root = claude_dir.parent
    shared_server = plugins_root / "shared" / "mcp_server" / "paper_orchestra_mcp.py"
    if not shared_server.is_file():
        raise FileNotFoundError(f"Shared server not found: {shared_server}")

    targets = [
        plugins_root / "antigravity" / "server" / "paper_orchestra_mcp.py",
        claude_dir / "cowork-plugin" / "server" / "paper_orchestra_mcp.py",
        claude_dir / "desktop-extension" / "server" / "paper_orchestra_mcp.py",
    ]
    for tgt in targets:
        _sync_server(shared_server, tgt)
        print(f"Synced MCP server -> {tgt}")

    ui_dist = plugins_root / "shared" / "ui_dist"
    ui_files = ["workbench.html", "tool-tree.html"]
    ui_targets = [
        plugins_root / "antigravity" / "assets" / "ui",
        claude_dir / "cowork-plugin" / "assets" / "ui",
        claude_dir / "desktop-extension" / "assets" / "ui",
    ]
    for ui_fn in ui_files:
        src_ui = ui_dist / ui_fn
        if not src_ui.is_file():
            raise FileNotFoundError(f"Shared UI bundle not found: {src_ui}")
        for ui_dir in ui_targets:
            ui_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_ui, ui_dir / ui_fn)
            os.chmod(ui_dir / ui_fn, 0o644)
        print(f"Synced UI bundle {ui_fn} -> {len(ui_targets)} targets")

    plugin_out = claude_dir / "dist" / "paper-orchestra-studio.plugin"
    mcpb_out = claude_dir / "dist" / "paper-orchestra-studio.mcpb"
    n_plugin = _zip_dir_via_tmp(claude_dir / "cowork-plugin", plugin_out)
    n_mcpb = _zip_dir_via_tmp(claude_dir / "desktop-extension", mcpb_out)
    print(f"Built {plugin_out} ({n_plugin} files, {plugin_out.stat().st_size} bytes)")
    print(f"Built {mcpb_out} ({n_mcpb} files, {mcpb_out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
