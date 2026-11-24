#!/usr/bin/env python3
# Copyright 2026 PaperOrchestra Studio Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Zero-dependency MCP stdio server for PaperOrchestra Studio.

Works with Antigravity, Claude Desktop (Chat / Cowork / MCPB Extension),
and Claude Code without requiring third-party MCP SDK packages at startup.
All protocol messages flow over newline-delimited JSON-RPC 2.0 on stdout;
all diagnostics and child stdout redirection go strictly to stderr.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import html as html_lib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid
import zipfile

SERVER_NAME = "paper-orchestra-studio"
SERVER_VERSION = "1.1.0"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

_SECRET_KEY_RE = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD)", re.IGNORECASE)
_SECRET_TOKEN_RES = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
)

_DB_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    template_name TEXT NOT NULL,
    writer_model TEXT NOT NULL,
    reflection_model TEXT NOT NULL,
    plotting_model TEXT NOT NULL,
    image_model TEXT NOT NULL,
    research_cutoff TEXT NOT NULL DEFAULT '',
    use_plotting INTEGER NOT NULL DEFAULT 1,
    plotting_max_critic_rounds INTEGER NOT NULL DEFAULT 3,
    idea_markdown TEXT NOT NULL DEFAULT '',
    experimental_log_markdown TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_figures (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    current_stage TEXT NOT NULL,
    template_name TEXT NOT NULL,
    writer_model TEXT NOT NULL,
    reflection_model TEXT NOT NULL,
    use_plotting INTEGER NOT NULL DEFAULT 1,
    run_dir TEXT NOT NULL,
    pdf_path TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    exit_code INTEGER,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    line TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS library_items (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    folder TEXT NOT NULL DEFAULT '默认文献夹',
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '',
    year INTEGER,
    venue TEXT NOT NULL DEFAULT '',
    doi TEXT NOT NULL DEFAULT '',
    arxiv_id TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    pdf_path TEXT NOT NULL DEFAULT '',
    bibtex TEXT NOT NULL DEFAULT '',
    gb7714 TEXT NOT NULL DEFAULT '',
    abstract_text TEXT NOT NULL DEFAULT '',
    ai_notes TEXT NOT NULL DEFAULT '',
    key_claims_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    read_status TEXT NOT NULL DEFAULT 'unread',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_memories (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'direction',
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ssh_hosts (
    id TEXT PRIMARY KEY,
    alias TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 22,
    username TEXT NOT NULL DEFAULT 'root',
    auth_type TEXT NOT NULL DEFAULT 'key_path',
    key_path TEXT NOT NULL DEFAULT '',
    remote_work_dir TEXT NOT NULL DEFAULT '~',
    notes TEXT NOT NULL DEFAULT '',
    last_status TEXT NOT NULL DEFAULT '',
    last_gpu_info TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_figures_project_id ON project_figures(project_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_runs_project_id ON runs(project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_logs_run_id ON run_logs(run_id, id);
CREATE INDEX IF NOT EXISTS idx_library_items_updated ON library_items(updated_at DESC);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_err(msg: str) -> None:
    try:
        sys.stderr.write(f"[{SERVER_NAME}] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


def _parse_dotenv(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if not k:
            continue
        v = v.strip()
        if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].strip()
        out[k] = v
    return out


def _collect_secrets(extra_env: Dict[str, str]) -> List[str]:
    secrets: List[str] = []
    for mapping in (os.environ, extra_env):
        for k, v in mapping.items():
            if not v or not isinstance(v, str):
                continue
            if _SECRET_KEY_RE.search(k):
                val = v.strip()
                if len(val) >= 6 and val.lower() not in ("true", "false", "auto", "none", "null"):
                    secrets.append(val)
    return sorted(set(secrets), key=len, reverse=True)


def _redact_text(text: str, secrets: List[str]) -> str:
    if not text:
        return text
    out = text
    for sec in secrets:
        if sec in out:
            out = out.replace(sec, "***REDACTED***")
    for pat in _SECRET_TOKEN_RES:
        out = pat.sub("***REDACTED***", out)
    return out


def _redact_obj(obj: Any, secrets: List[str]) -> Any:
    if isinstance(obj, str):
        return _redact_text(obj, secrets)
    if isinstance(obj, list):
        return [_redact_obj(x, secrets) for x in obj]
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            out[str(k)] = _redact_obj(v, secrets)
        return out
    return obj


def _sanitize_slug(text: str, default: str = "paper") -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip().lower()).strip("._-")
    return slug[:36] or default


def _unique_path(directory: Path, stem: str, ext: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    clean_stem = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", (stem or "PaperOrchestra_Output")).strip(" .")
    clean_stem = re.sub(r"\s+", " ", clean_stem)[:72] or "PaperOrchestra_Output"
    ext = ext if ext.startswith(".") else f".{ext}"
    cand = directory / f"{clean_stem}{ext}"
    idx = 2
    while cand.exists():
        cand = directory / f"{clean_stem}_{idx}{ext}"
        idx += 1
    return cand


def _enriched_path() -> str:
    home = Path.home()
    parts = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/Library/TeX/texbin",
        "/opt/anaconda3/bin",
        str(home / ".local" / "bin"),
        str(home / ".elan" / "bin"),
        os.environ.get("PATH", ""),
    ]
    seen = set()
    ordered: List[str] = []
    for p in os.pathsep.join(parts).split(os.pathsep):
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)
    return os.pathsep.join(ordered)


def _which_tool(name: str) -> Optional[str]:
    return shutil.which(name, path=_enriched_path())


def _clean_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if val.startswith("${") and val.endswith("}"):
        return ""
    return val


class RuntimeContext:
    """Resolved environment, SQLite DB connection helper, and secret redactor."""

    def __init__(self) -> None:
        home = Path.home()
        raw_data = _clean_env("PAPER_ORCHESTRA_PLUGIN_DATA")
        if raw_data:
            self.data_dir = Path(os.path.expanduser(raw_data)).resolve()
        else:
            self.data_dir = (home / ".local" / "share" / "paper-orchestra-studio-plugin" / "data").resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self._find_db_path(home)
        self._init_db()
        settings = self._read_app_settings()

        self.repo_dir = self._find_repo_dir(home, settings)
        self.python_bin = self._find_python_bin(home, settings, self.repo_dir)
        self.env_file = self._find_env_file(home, self.repo_dir)
        self.dotenv_vars = _parse_dotenv(self.env_file) if self.env_file else {}
        self.secrets = _collect_secrets(self.dotenv_vars)
        self.output_root_dir = self._find_output_root(home, settings, self.repo_dir)

    def _find_db_path(self, home: Path) -> Path:
        env_db = _clean_env("PAPER_ORCHESTRA_DB")
        if env_db:
            p = Path(os.path.expanduser(env_db))
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        candidates: List[Path] = []
        if sys.platform == "darwin":
            candidates.append(home / "Library" / "Application Support" / "com.paperorchestra.studio" / "paper_orchestra_studio.db")
        elif sys.platform.startswith("win"):
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                candidates.append(Path(appdata) / "com.paperorchestra.studio" / "paper_orchestra_studio.db")
        else:
            candidates.append(home / ".local" / "share" / "com.paperorchestra.studio" / "paper_orchestra_studio.db")
        for c in candidates:
            if c.is_file() or c.parent.is_dir():
                return c
        return self.data_dir / "paper_orchestra_studio.db"

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(_DB_SCHEMA_SQL)

    def _read_app_settings(self) -> Dict[str, str]:
        try:
            with self.connect() as conn:
                rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
            return {str(r["key"]): str(r["value"]) for r in rows}
        except sqlite3.Error:
            return {}

    def _find_repo_dir(self, home: Path, settings: Dict[str, str]) -> Optional[Path]:
        candidates: List[Path] = []
        env_repo = _clean_env("PAPER_ORCHESTRA_REPO")
        if env_repo:
            candidates.append(Path(os.path.expanduser(env_repo)))
        db_repo = settings.get("paper_orchestra_dir", "").strip()
        if db_repo:
            candidates.append(Path(os.path.expanduser(db_repo)))
        here = Path(__file__).resolve()
        # Support running from repo checkout: plugins/shared/mcp_server/paper_orchestra_mcp.py
        if len(here.parents) >= 4:
            candidates.append(here.parents[3] / "paper-orchestra")
        candidates.extend([
            home / "paper-orchestra",
            home / "paper-orchestra-studio" / "paper-orchestra",
            home / "Documents" / "paper-orchestra",
            home / "Desktop" / "paper-orchestra",
        ])
        for c in candidates:
            try:
                if c.is_dir() and (c / "paper_writing_cli.py").is_file():
                    return c.resolve()
            except OSError:
                continue
        return None

    def _find_python_bin(self, home: Path, settings: Dict[str, str], repo_dir: Optional[Path]) -> str:
        cands: List[str] = []
        env_py = _clean_env("PAPER_ORCHESTRA_PYTHON")
        if env_py:
            cands.append(os.path.expanduser(env_py))
        for local_root in (self.data_dir.parent, home / ".local" / "share" / "paper-orchestra-studio-plugin"):
            cands.append(str(local_root / ".venv" / "bin" / "python"))
            cands.append(str(local_root / ".venv" / "Scripts" / "python.exe"))
        db_py = settings.get("python_path", "").strip()
        if db_py:
            cands.append(os.path.expanduser(db_py))
        if repo_dir is not None:
            cands.append(str(repo_dir / ".venv" / "bin" / "python"))
            cands.append(str(repo_dir / ".venv" / "Scripts" / "python.exe"))
        cands.append(str(home / "paper-orchestra" / ".venv" / "bin" / "python"))
        cands.append(sys.executable)
        for c in cands:
            if c and os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        return sys.executable or "python3"

    def _find_env_file(self, home: Path, repo_dir: Optional[Path]) -> Optional[Path]:
        cands: List[Path] = []
        explicit = _clean_env("PAPER_ORCHESTRA_ENV_FILE")
        if explicit:
            cands.append(Path(os.path.expanduser(explicit)))
        if repo_dir is not None:
            cands.append(repo_dir / ".env")
        cands.append(home / "paper-orchestra" / ".env")
        cands.append(self.data_dir / ".env")
        for c in cands:
            try:
                if c.is_file():
                    return c
            except OSError:
                continue
        return None

    def _find_output_root(self, home: Path, settings: Dict[str, str], repo_dir: Optional[Path]) -> Path:
        raw = _clean_env("PAPER_ORCHESTRA_OUTPUT_ROOT") or settings.get("output_root_dir", "").strip()
        if raw:
            p = Path(os.path.expanduser(raw))
            if p.parent.is_dir() or p.is_dir():
                return p
        if repo_dir is not None:
            return repo_dir / "desktop_runs"
        return self.data_dir / "runs"

    def require_repo(self) -> Path:
        if self.repo_dir is None or not (self.repo_dir / "paper_writing_cli.py").is_file():
            raise RuntimeError(
                "Could not locate PaperOrchestra backend (`paper_writing_cli.py`). "
                "Set `PAPER_ORCHESTRA_REPO` env var or check the PaperOrchestra directory in Settings."
            )
        return self.repo_dir

    def refresh_runtime(self) -> None:
        home = Path.home()
        settings = self._read_app_settings()
        self.repo_dir = self._find_repo_dir(home, settings)
        self.python_bin = self._find_python_bin(home, settings, self.repo_dir)
        self.env_file = self._find_env_file(home, self.repo_dir)
        self.dotenv_vars = _parse_dotenv(self.env_file) if self.env_file else {}
        self.secrets = _collect_secrets(self.dotenv_vars)
        self.output_root_dir = self._find_output_root(home, settings, self.repo_dir)

    def studio_app_data_dir(self) -> Path:
        override = _clean_env("PAPER_ORCHESTRA_STUDIO_DATA_DIR")
        if override:
            return Path(os.path.expanduser(override)).resolve()
        home = Path.home()
        if sys.platform == "darwin":
            return home / "Library" / "Application Support" / "com.paperorchestra.studio"
        if sys.platform.startswith("win"):
            local_app = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(home / "AppData" / "Local")
            return Path(local_app) / "com.paperorchestra.studio"
        return home / ".local" / "share" / "com.paperorchestra.studio"

    def studio_root_dir(self) -> Optional[Path]:
        cands: List[Path] = []
        if self.repo_dir is not None:
            cands.append(self.repo_dir.parent)
        here = Path(__file__).resolve()
        if len(here.parents) >= 4:
            cands.append(here.parents[3])
        home = Path.home()
        cands.extend([home / "paper-orchestra-studio", home / "paper-orchestra"])
        for c in cands:
            try:
                if (c / "integrations").is_dir() or (c / "scripts").is_dir():
                    return c.resolve()
            except OSError:
                continue
        return None

    def subprocess_env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        env = {
            k: v
            for k, v in os.environ.items()
            if not (isinstance(v, str) and v.strip().startswith("${") and v.strip().endswith("}"))
        }
        for k, v in self.dotenv_vars.items():
            if not env.get(k):
                env[k] = v
        if extra:
            env.update({k: str(v) for k, v in extra.items() if v is not None})
        env["PATH"] = _enriched_path()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def run_json_tool(
        self,
        rel_script: str,
        args: List[str],
        timeout: int = 120,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        repo = self.require_repo()
        script = repo / rel_script
        if not script.is_file():
            raise RuntimeError(f"Tool script not found: {script}")
        cmd = [self.python_bin, "-B", str(script), *args]
        proc = subprocess.run(
            cmd,
            cwd=str(repo),
            env=self.subprocess_env(extra_env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        parsed: Optional[Dict[str, Any]] = None
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    parsed = obj
                    break
            except json.JSONDecodeError:
                continue
        if parsed is None:
            msg = _redact_text(stderr or stdout or f"Exit code {proc.returncode}", self.secrets)
            raise RuntimeError(f"{rel_script} did not return JSON (rc={proc.returncode}): {msg[-1500:]}")
        redacted = _redact_obj(parsed, self.secrets)
        if redacted.get("error"):
            raise RuntimeError(str(redacted["error"]))
        if proc.returncode != 0 and not redacted.get("ok"):
            raise RuntimeError(f"{rel_script} exited with code {proc.returncode}: {json.dumps(redacted, ensure_ascii=False)}")
        return redacted


# ---------------------------------------------------------------------------
# Background Job Registry (for long pipeline / SOP / analysis runs)
# ---------------------------------------------------------------------------

def _jobs_dir(ctx: RuntimeContext) -> Path:
    d = ctx.data_dir / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    # Reap exited child processes first so finished jobs do not appear alive as zombies.
    if hasattr(os, "waitpid"):
        try:
            reaped, _ = os.waitpid(pid, os.WNOHANG)
            if reaped == pid:
                return False
        except ChildProcessError:
            pass
        except OSError:
            pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _detect_stage(current: str, text: str, use_plotting: bool = True) -> str:
    stage = current or "queued"
    for line in text.splitlines():
        if (
            "=== Initial Baseline Review ===" in line
            or "Starting dedicated formatting loop" in line
            or "ContentRefinementAgent" in line
        ):
            stage = "refinement"
        elif "SectionWritingAgent" in line:
            stage = "writing"
        elif use_plotting and (
            "Starting run_plotting_agent" in line
            or "Processing figure:" in line
            or "Figure processing complete" in line
            or "Saved figures/" in line
        ):
            stage = "plotting"
        elif "Starting Fast Hybrid Literature Agent" in line or "Starting run_literature_agent" in line:
            stage = "literature"
        elif "Final PDF saved to:" in line:
            stage = "completed"
    return stage


def _spawn_background_job(
    ctx: RuntimeContext,
    kind: str,
    cmd: List[str],
    cwd: Path,
    run_dir: Optional[Path] = None,
    run_id: str = "",
    use_plotting: bool = True,
    extra_env: Optional[Dict[str, str]] = None,
    title: str = "",
) -> Dict[str, Any]:
    job_id = f"{kind}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    jdir = _jobs_dir(ctx) / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    log_path = jdir / "output.log"
    meta_path = jdir / "meta.json"

    with open(log_path, "w", encoding="utf-8") as log_fh:
        log_fh.write(f"[{_now_iso()}] Launching {kind}: {' '.join(cmd)}\n")
        log_fh.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=ctx.subprocess_env(extra_env),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    meta = {
        "job_id": job_id,
        "kind": kind,
        "title": title,
        "pid": proc.pid,
        "run_id": run_id,
        "run_dir": str(run_dir) if run_dir else "",
        "log_path": str(log_path),
        "use_plotting": use_plotting,
        "started_at": _now_iso(),
        "stopped": False,
        "cmd": [os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)],
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _inspect_job(ctx: RuntimeContext, meta: Dict[str, Any], tail_lines: int = 40) -> Dict[str, Any]:
    pid = int(meta.get("pid") or 0)
    alive = False if meta.get("exited") else _pid_alive(pid)
    if not alive and not meta.get("exited"):
        meta["exited"] = True
        meta["finished_at"] = meta.get("finished_at") or _now_iso()
        try:
            (_jobs_dir(ctx) / str(meta["job_id"]) / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
    run_dir = Path(meta["run_dir"]) if meta.get("run_dir") else None
    log_path = Path(meta["log_path"]) if meta.get("log_path") else None

    tail_text = ""
    if log_path and log_path.is_file():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail_text = "\n".join(lines[-max(1, tail_lines):])
        tail_text = _redact_text(tail_text, ctx.secrets)

    stage = _detect_stage("running" if alive else "finished", tail_text, bool(meta.get("use_plotting", True)))
    final_pdf = ""
    usage_summary = None
    if run_dir and run_dir.is_dir():
        pdf_cand = run_dir / "final_paper.pdf"
        if pdf_cand.is_file():
            final_pdf = str(pdf_cand)
        usage_cand = run_dir / "usage_report.json"
        if usage_cand.is_file():
            try:
                usage_summary = json.loads(usage_cand.read_text(encoding="utf-8"))
            except Exception:
                pass

    status = "stopped" if meta.get("stopped") else ("running" if alive else ("succeeded" if final_pdf else "finished"))

    # Keep SQLite runs row synchronized when tied to a project run
    run_id = meta.get("run_id") or ""
    if run_id and not alive:
        try:
            with ctx.connect() as conn:
                row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
                if row and row["status"] == "running":
                    db_status = "cancelled" if meta.get("stopped") else ("succeeded" if final_pdf else "finished")
                    conn.execute(
                        "UPDATE runs SET status = ?, current_stage = ?, pdf_path = ?, finished_at = ? WHERE id = ?",
                        (db_status, stage, final_pdf or None, _now_iso(), run_id),
                    )
        except sqlite3.Error:
            pass

    return {
        "job_id": meta["job_id"],
        "kind": meta.get("kind"),
        "title": meta.get("title", ""),
        "pid": pid,
        "status": status,
        "alive": alive,
        "stage": stage,
        "started_at": meta.get("started_at"),
        "run_id": run_id,
        "run_dir": str(run_dir) if run_dir else "",
        "final_pdf": final_pdf,
        "log_path": str(log_path) if log_path else "",
        "usage": usage_summary,
        "tail": tail_text,
    }


# ---------------------------------------------------------------------------
# Research Memories / Preferences Helper
# ---------------------------------------------------------------------------

def _build_preferences_markdown(conn: sqlite3.Connection) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'research_preferences'").fetchone()
    prefs: Dict[str, Any] = {
        "output_language": "",
        "citation_style": "",
        "delivery_format": "",
        "custom_rules": "",
    }
    if row:
        try:
            loaded = json.loads(row["value"])
            if isinstance(loaded, dict):
                prefs.update(loaded)
        except Exception:
            pass

    mem_rows = conn.execute(
        "SELECT id, category, title, content, enabled, updated_at FROM research_memories ORDER BY updated_at DESC"
    ).fetchall()
    memories = [
        {
            "id": str(r["id"]),
            "category": str(r["category"]),
            "title": str(r["title"]),
            "content": str(r["content"]),
            "enabled": bool(r["enabled"]),
            "updated_at": str(r["updated_at"]),
        }
        for r in mem_rows
    ]

    lines: List[str] = []
    if str(prefs.get("output_language") or "").strip():
        lines.append(f"- Preferred output language: {str(prefs['output_language']).strip()}")
    if str(prefs.get("citation_style") or "").strip():
        lines.append(f"- Preferred citation standard: {str(prefs['citation_style']).strip()}")
    if str(prefs.get("delivery_format") or "").strip():
        lines.append(f"- Preferred deliverable format: {str(prefs['delivery_format']).strip()}")
    if str(prefs.get("custom_rules") or "").strip():
        lines.append(f"- Custom standing rules:\n{str(prefs['custom_rules']).strip()}")
    enabled_mem = [m for m in memories if m["enabled"]]
    if enabled_mem:
        lines.append("\n#### Enabled Long-Term Research Memories")
        for m in enabled_mem:
            lines.append(f"- [{m['category']}] **{m['title']}**: {m['content']}")
    return prefs, memories, "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude Artifacts-Style Offline Interactive HTML Renderer
# ---------------------------------------------------------------------------

_SPLIT_SAFE_RE = re.compile(r"(<pre\b[\s\S]*?</pre>|<code\b[\s\S]*?</code>|<[^>]+>)", re.IGNORECASE)
_TAG_BADGES = (
    (re.compile(r"(?:<strong[^>]*>\s*)?【原文】(?:\s*</strong>)?"), '<span class="prov-badge prov-orig">原文</span>'),
    (re.compile(r"(?:<strong[^>]*>\s*)?【审读推导】(?:\s*</strong>)?"), '<span class="prov-badge prov-deriv">审读推导</span>'),
    (re.compile(r"(?:<strong[^>]*>\s*)?【不可核实】(?:\s*</strong>)?"), '<span class="prov-badge prov-unverified">不可核实</span>'),
)

_INTERACTIVE_JS = r"""
(() => {
  const root = document.documentElement;
  const themeBtns = document.querySelectorAll('[data-theme-btn]');
  const setTheme = (t) => {
    root.setAttribute('data-theme', t);
    themeBtns.forEach((b) => b.classList.toggle('is-active', b.getAttribute('data-theme-btn') === t));
    try { localStorage.setItem('po-deep-read-theme', t); } catch (_) {}
  };
  let savedTheme = 'paper';
  try { savedTheme = localStorage.getItem('po-deep-read-theme') || 'paper'; } catch (_) {}
  setTheme(['paper', 'light', 'dark'].includes(savedTheme) ? savedTheme : 'paper');
  themeBtns.forEach((b) => b.addEventListener('click', () => setTheme(b.getAttribute('data-theme-btn'))));

  const copyBtn = document.getElementById('po-copy-md');
  const srcEl = document.getElementById('po-source-md');
  if (copyBtn && srcEl) {
    copyBtn.addEventListener('click', async () => {
      try {
        const md = JSON.parse(srcEl.textContent || '""');
        await navigator.clipboard.writeText(md);
        const orig = copyBtn.textContent;
        copyBtn.textContent = '已复制 Markdown ✓';
        setTimeout(() => { copyBtn.textContent = orig; }, 1800);
      } catch (_) {}
    });
  }

  const printBtn = document.getElementById('po-print-pdf');
  if (printBtn) printBtn.addEventListener('click', () => window.print());

  const article = document.getElementById('po-article');
  const tocList = document.getElementById('po-toc-list');
  const headingEls = article ? Array.from(article.querySelectorAll('h1, h2, h3')) : [];
  if (tocList && headingEls.length > 0) {
    const links = [];
    headingEls.forEach((h, idx) => {
      if (!h.id) h.id = `sec-${idx + 1}`;
      const a = document.createElement('a');
      a.href = `#${h.id}`;
      a.textContent = (h.textContent || '').trim();
      a.className = `po-toc-link lvl-${h.tagName.toLowerCase()}`;
      a.addEventListener('click', (e) => {
        e.preventDefault();
        h.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
      tocList.appendChild(a);
      links.push({ el: h, link: a });
    });
    const setActive = (id) => links.forEach((x) => x.link.classList.toggle('is-active', x.el.id === id));
    if (links.length) setActive(links[0].el.id);
    if ('IntersectionObserver' in window && links.length) {
      const obs = new IntersectionObserver((entries) => {
        const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (vis.length) setActive(vis[0].target.id);
      }, { rootMargin: '-70px 0px -70% 0px', threshold: 0 });
      links.forEach((x) => obs.observe(x.el));
    }
  }

  const parseFlowchart = (code) => {
    const lines = code.split('\n').map((l) => l.trim()).filter((l) => l && !l.startsWith('%%'));
    if (!lines.length || !/^(flowchart|graph)\s+/i.test(lines[0])) return null;
    const dirMatch = /^(?:flowchart|graph)\s+(LR|RL|TD|TB|BT)/i.exec(lines[0]);
    const dir = (dirMatch ? dirMatch[1] : 'LR').toUpperCase();
    const nodes = new Map();
    const edges = [];
    const registerNode = (raw) => {
      const m = /^([A-Za-z0-9_.-]+)\s*(?:\(\((.*?)\)\)|\[(.*?)\]|\((.*?)\)|\{(.*?)\}|"(.*?)")?$/.exec(raw.trim());
      if (!m) {
        const id = raw.trim().replace(/[^A-Za-z0-9_.-]+/g, '_') || `N${nodes.size}`;
        if (!nodes.has(id)) nodes.set(id, raw.trim());
        return id;
      }
      const id = m[1];
      const lbl = (m[2] || m[3] || m[4] || m[5] || m[6] || id).replace(/^["']|["']$/g, '').trim();
      if (!nodes.has(id) || lbl !== id) nodes.set(id, lbl);
      return id;
    };
    for (const line of lines.slice(1)) {
      if (/^(subgraph|end|style|classDef|class|click|linkStyle)\b/i.test(line)) continue;
      const em = /^(.+?)\s*(?:--+>?|==+>?|\.-+>?)\s*(?:\|([^|]+)\|\s*)?(.+?);?$/.exec(line);
      if (em) {
        const from = registerNode(em[1]);
        const to = registerNode(em[3]);
        edges.push({ from, to, label: (em[2] || '').trim() });
      }
    }
    if (!nodes.size || !edges.length) return null;
    return { dir, nodes: Array.from(nodes.entries()).map(([id, label]) => ({ id, label })), edges };
  };

  const renderMermaidBlocks = () => {
    if (!article) return;
    const blocks = [];
    article.querySelectorAll('pre.mermaid > code, pre > code.language-mermaid').forEach((codeEl) => {
      blocks.push({ pre: codeEl.parentElement, code: codeEl.textContent || '' });
    });
    article.querySelectorAll('pre.mermaid').forEach((preEl) => {
      if (!preEl.querySelector('code')) blocks.push({ pre: preEl, code: preEl.textContent || '' });
    });

    blocks.forEach(({ pre, code }, bIdx) => {
      const flow = parseFlowchart(code);
      if (!flow) return;
      const rank = new Map();
      flow.nodes.forEach((n) => rank.set(n.id, 0));
      for (let pass = 0; pass < flow.nodes.length; pass++) {
        for (const e of flow.edges) {
          rank.set(e.to, Math.max(rank.get(e.to) || 0, (rank.get(e.from) || 0) + 1));
        }
      }
      const cols = new Map();
      for (const n of flow.nodes) {
        const r = rank.get(n.id) || 0;
        const arr = cols.get(r) || [];
        arr.push(n);
        cols.set(r, arr);
      }
      const keys = Array.from(cols.keys()).sort((a, b) => a - b);
      const isVertical = flow.dir === 'TD' || flow.dir === 'TB';
      const boxW = 168, boxH = 46, gapX = 52, gapY = 26, pad = 22;
      let maxCross = 1;
      cols.forEach((arr) => { if (arr.length > maxCross) maxCross = arr.length; });

      const pos = new Map();
      let svgW, svgH;
      if (!isVertical) {
        svgW = Math.max(460, pad * 2 + keys.length * boxW + Math.max(0, keys.length - 1) * gapX);
        svgH = Math.max(120, pad * 2 + maxCross * boxH + Math.max(0, maxCross - 1) * gapY);
        keys.forEach((r, ci) => {
          const arr = cols.get(r) || [];
          const span = arr.length * boxH + (arr.length - 1) * gapY;
          const startY = (svgH - span) / 2;
          arr.forEach((n, ri) => pos.set(n.id, { x: pad + ci * (boxW + gapX), y: startY + ri * (boxH + gapY) }));
        });
      } else {
        svgW = Math.max(460, pad * 2 + maxCross * boxW + Math.max(0, maxCross - 1) * gapX);
        svgH = Math.max(160, pad * 2 + keys.length * boxH + Math.max(0, keys.length - 1) * gapY);
        keys.forEach((r, ri) => {
          const arr = cols.get(r) || [];
          const span = arr.length * boxW + (arr.length - 1) * gapX;
          const startX = (svgW - span) / 2;
          arr.forEach((n, ci) => pos.set(n.id, { x: startX + ci * (boxW + gapX), y: pad + ri * (boxH + gapY) }));
        });
      }

      const ns = 'http://www.w3.org/2000/svg';
      const svg = document.createElementNS(ns, 'svg');
      svg.setAttribute('viewBox', `0 0 ${svgW} ${svgH}`);
      svg.setAttribute('class', 'po-mermaid-svg');
      const markerId = `po-arr-${bIdx}`;
      const defs = document.createElementNS(ns, 'defs');
      defs.innerHTML = `<marker id="${markerId}" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="var(--accent)" /></marker>`;
      svg.appendChild(defs);

      flow.edges.forEach((e) => {
        const a = pos.get(e.from), b = pos.get(e.to);
        if (!a || !b) return;
        const p1x = isVertical ? a.x + boxW / 2 : a.x + boxW;
        const p1y = isVertical ? a.y + boxH : a.y + boxH / 2;
        const p2x = isVertical ? b.x + boxW / 2 : b.x;
        const p2y = isVertical ? b.y : b.y + boxH / 2;
        const path = document.createElementNS(ns, 'path');
        const mid = isVertical
          ? `M${p1x},${p1y} C${p1x},${(p1y + p2y) / 2} ${p2x},${(p1y + p2y) / 2} ${p2x},${p2y}`
          : `M${p1x},${p1y} C${(p1x + p2x) / 2},${p1y} ${(p1x + p2x) / 2},${p2y} ${p2x},${p2y}`;
        path.setAttribute('d', mid);
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', 'var(--accent)');
        path.setAttribute('stroke-width', '1.6');
        path.setAttribute('marker-end', `url(#${markerId})`);
        svg.appendChild(path);
      });

      flow.nodes.forEach((n) => {
        const pt = pos.get(n.id);
        if (!pt) return;
        const g = document.createElementNS(ns, 'g');
        const rect = document.createElementNS(ns, 'rect');
        rect.setAttribute('x', String(pt.x));
        rect.setAttribute('y', String(pt.y));
        rect.setAttribute('width', String(boxW));
        rect.setAttribute('height', String(boxH));
        rect.setAttribute('rx', '8');
        rect.setAttribute('fill', 'var(--surface)');
        rect.setAttribute('stroke', 'var(--accent)');
        rect.setAttribute('stroke-width', '1.4');
        const txt = document.createElementNS(ns, 'text');
        txt.setAttribute('x', String(pt.x + boxW / 2));
        txt.setAttribute('y', String(pt.y + boxH / 2 + 4));
        txt.setAttribute('text-anchor', 'middle');
        txt.setAttribute('font-size', '12');
        txt.setAttribute('fill', 'var(--text)');
        txt.textContent = n.label.length > 18 ? n.label.slice(0, 17) + '…' : n.label;
        g.appendChild(rect);
        g.appendChild(txt);
        svg.appendChild(g);
      });

      const wrap = document.createElement('div');
      wrap.className = 'po-mermaid-card';
      const head = document.createElement('div');
      head.className = 'po-mermaid-head';
      head.innerHTML = '<span>Mermaid 结构框图 (离线矢量渲染)</span>';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'mermaid-toggle-btn';
      btn.textContent = '查看源码';
      let showCode = false;
      const bodyWrap = document.createElement('div');
      bodyWrap.className = 'po-mermaid-body';
      bodyWrap.appendChild(svg);
      pre.style.display = 'none';
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(head);
      head.appendChild(btn);
      wrap.appendChild(bodyWrap);
      wrap.appendChild(pre);
      btn.addEventListener('click', () => {
        showCode = !showCode;
        bodyWrap.style.display = showCode ? 'none' : 'block';
        pre.style.display = showCode ? 'block' : 'none';
        btn.textContent = showCode ? '显示流程图' : '查看源码';
      });
    });
  };
  renderMermaidBlocks();
})();
"""


def _fallback_md_to_html(md_text: str) -> str:
    lines = md_text.splitlines()
    out: List[str] = []
    i = 0

    def inline(s: str) -> str:
        esc = html_lib.escape(s)
        esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        esc = re.sub(r"\*(.+?)\*", r"<em>\1</em>", esc)
        return esc

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            lang = html_lib.escape(stripped[3:].strip())
            code_lines: List[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>{html_lib.escape(chr(10).join(code_lines))}</code></pre>")
            continue
        m_h = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m_h:
            lvl = len(m_h.group(1))
            out.append(f"<h{lvl}>{inline(m_h.group(2).strip())}</h{lvl}>")
            i += 1
            continue
        if re.match(r"^[-*_]{3,}\s*$", stripped):
            out.append("<hr />")
            i += 1
            continue
        if stripped.startswith(">"):
            q_lines: List[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                q_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(q_lines))}</p></blockquote>")
            continue
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?\s*[-:]+", lines[i + 1]):
            headers = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows: List[List[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            th = "".join(f"<th>{inline(h)}</th>" for h in headers)
            tb = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>")
            continue
        if re.match(r"^[-*]\s+", stripped):
            items: List[str] = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(it)}</li>" for it in items) + "</ul>")
            continue
        para: List[str] = []
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("#", ">", "```", "-", "*")):
            if "|" in lines[i] and i + 1 < len(lines) and re.match(r"^\s*\|?\s*[-:]+", lines[i + 1]):
                break
            para.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(para))}</p>")
    return "\n".join(out)


def _render_interactive_html(md_text: str, title: str) -> Tuple[str, str]:
    pandoc = _which_tool("pandoc")
    engine = "python-fallback+interactive-artifacts"
    body_html = ""
    if pandoc:
        try:
            proc = subprocess.run(
                [pandoc, "--from=markdown+tex_math_dollars+pipe_tables+fenced_code_blocks", "--to=html5", "--mathml"],
                input=md_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                timeout=30,
            )
            body_html = proc.stdout
            engine = "pandoc-html5+mathml+interactive-artifacts"
        except Exception:
            body_html = _fallback_md_to_html(md_text)
    else:
        body_html = _fallback_md_to_html(md_text)

    parts = _SPLIT_SAFE_RE.split(body_html)
    rebuilt: List[str] = []
    for part in parts:
        if not part:
            continue
        if _SPLIT_SAFE_RE.fullmatch(part):
            rebuilt.append(part)
        else:
            t = part
            for pat, repl in _TAG_BADGES:
                t = pat.sub(repl, t)
            rebuilt.append(t)
    body_html = re.sub(
        r"(<table\b[^>]*>[\s\S]*?</table>)",
        r'<div class="table-wrap">\1</div>',
        "".join(rebuilt),
        flags=re.IGNORECASE,
    )

    css = """
    :root, [data-theme="paper"] {
      --bg: #f5f1e8; --surface: #fffdf8; --subtle: #efeae0; --text: #1c1917;
      --muted: #6b645c; --border: #e2dacb; --accent: #2b4a70; --accent-soft: #e8eff7;
    }
    [data-theme="light"] {
      --bg: #f8fafc; --surface: #ffffff; --subtle: #f1f5f9; --text: #0f172a;
      --muted: #64748b; --border: #cbd5e1; --accent: #1d4ed8; --accent-soft: #eff6ff;
    }
    [data-theme="dark"] {
      --bg: #14161a; --surface: #1d2026; --subtle: #252932; --text: #e2e8f0;
      --muted: #94a3b8; --border: #333945; --accent: #60a5fa; --accent-soft: #1e293b;
    }
    * { box-sizing: border-box; }
    @page { size: A4; margin: 16mm 15mm; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.72; font-size: 15px;
    }
    .po-topbar {
      position: sticky; top: 0; z-index: 20;
      display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
      padding: 10px 20px; background: var(--surface); border-bottom: 1px solid var(--border);
    }
    .po-brand { font-weight: 600; font-size: 14px; color: var(--accent); }
    .po-tools { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .po-btn {
      border: 1px solid var(--border); background: var(--subtle); color: var(--text);
      padding: 5px 10px; border-radius: 6px; font-size: 12.5px; cursor: pointer;
    }
    .po-btn.is-active { background: var(--accent); color: #ffffff; border-color: var(--accent); }
    .po-shell {
      max-width: 1240px; margin: 22px auto; padding: 0 20px;
      display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 22px; align-items: start;
    }
    .po-toc {
      position: sticky; top: 64px; background: var(--surface); border: 1px solid var(--border);
      border-radius: 10px; padding: 14px 12px; max-height: calc(100vh - 88px); overflow-y: auto;
    }
    .po-toc-title { font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 8px; text-transform: uppercase; }
    .po-toc-link {
      display: block; text-decoration: none; color: var(--muted); font-size: 13px;
      padding: 4px 8px; border-radius: 6px; line-height: 1.4; margin-bottom: 2px;
    }
    .po-toc-link.lvl-h3 { padding-left: 18px; font-size: 12.2px; }
    .po-toc-link:hover, .po-toc-link.is-active { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
    .po-article {
      background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
      padding: 28px 34px; min-width: 0;
    }
    h1 { font-size: 22px; border-bottom: 2px solid var(--accent); padding-bottom: 8px; margin-top: 0; }
    h2 { font-size: 17px; color: var(--accent); margin-top: 26px; margin-bottom: 10px; border-left: 3.5px solid var(--accent); padding-left: 10px; }
    h3 { font-size: 15px; margin-top: 18px; }
    blockquote {
      margin: 12px 0; padding: 10px 14px; background: var(--subtle);
      border-left: 3.5px solid var(--accent); border-radius: 6px;
    }
    .table-wrap { overflow-x: auto; margin: 14px 0; border-radius: 8px; border: 1px solid var(--border); }
    table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
    th, td { border-bottom: 1px solid var(--border); border-right: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top; }
    th:last-child, td:last-child { border-right: none; }
    thead th { background: var(--subtle); font-weight: 600; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.88em; background: var(--subtle); padding: 1px 5px; border-radius: 4px; }
    pre { background: var(--subtle); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; overflow-x: auto; font-size: 12.5px; line-height: 1.5; }
    pre code { background: none; padding: 0; }
    .prov-badge { display: inline-block; font-size: 11.5px; font-weight: 700; padding: 1px 7px; border-radius: 999px; margin: 0 3px; vertical-align: baseline; }
    .prov-orig { background: #dbeafe; color: #1e40af; border: 1px solid #93c5fd; }
    .prov-deriv { background: #ede9fe; color: #5b21b6; border: 1px solid #c4b5fd; }
    .prov-unverified { background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }
    .po-mermaid-card { border: 1px solid var(--border); border-radius: 10px; background: var(--bg); margin: 14px 0; overflow: hidden; }
    .po-mermaid-head { display: flex; justify-content: space-between; align-items: center; padding: 7px 12px; background: var(--subtle); font-size: 12px; color: var(--muted); }
    .mermaid-toggle-btn { border: 1px solid var(--border); background: var(--surface); color: var(--text); border-radius: 5px; padding: 2px 8px; font-size: 11.5px; cursor: pointer; }
    .po-mermaid-body { padding: 10px; overflow-x: auto; }
    .po-mermaid-svg { width: 100%; height: auto; display: block; }
    @media (max-width: 900px) { .po-shell { grid-template-columns: 1fr; } .po-toc { position: static; max-height: 220px; } }
    @media print {
      body { background: #ffffff; color: #1e293b; font-size: 10.5pt; }
      .po-topbar, .po-toc, .mermaid-toggle-btn { display: none !important; }
      .po-shell { display: block; margin: 0; padding: 0; max-width: none; }
      .po-article { border: none; padding: 0; background: #ffffff; }
    }
    """
    safe_md_json = json.dumps(md_text, ensure_ascii=False).replace("</", "<\\/")
    html_doc = f"""<!doctype html>
<html lang="zh-CN" data-theme="paper">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html_lib.escape(title)}</title>
  <style>{css}</style>
</head>
<body>
  <header class="po-topbar">
    <span class="po-brand">PaperOrchestra Studio · {html_lib.escape(title)}</span>
    <div class="po-tools">
      <button type="button" class="po-btn is-active" data-theme-btn="paper">护眼纸张</button>
      <button type="button" class="po-btn" data-theme-btn="light">亮白</button>
      <button type="button" class="po-btn" data-theme-btn="dark">深色</button>
      <button type="button" class="po-btn" id="po-copy-md">复制 Markdown</button>
      <button type="button" class="po-btn" id="po-print-pdf">打印 / 导出 PDF</button>
    </div>
  </header>
  <main class="po-shell">
    <aside class="po-toc" aria-label="报告目录">
      <div class="po-toc-title">阅读目录</div>
      <nav id="po-toc-list"></nav>
    </aside>
    <article id="po-article" class="po-article">{body_html}</article>
  </main>
  <script id="po-source-md" type="application/json">{safe_md_json}</script>
  <script>{_INTERACTIVE_JS}</script>
</body>
</html>
"""
    return html_doc, engine


# ---------------------------------------------------------------------------
# Tool Handlers (18 cohesive tools)
# ---------------------------------------------------------------------------

def tool_check_environment(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    repo = ctx.repo_dir
    counts: Dict[str, int] = {}
    with ctx.connect() as conn:
        for tbl in ("projects", "runs", "library_items", "research_memories", "ssh_hosts"):
            counts[tbl] = int(conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0])

    pdf_dir = _clean_env("PO_DEFAULT_PDF_DIR")
    html_dir = _clean_env("PO_DEFAULT_HTML_DIR")
    gdrive_dir = _clean_env("PO_GOOGLE_DRIVE_DIR")

    tools = {
        name: _which_tool(name)
        for name in ("pdflatex", "latexmk", "bibtex", "synctex", "latexdiff", "xelatex", "pandoc", "lean", "lake", "ssh", "scp")
    }
    chrome = None
    for cand in (
        _clean_env("PO_CHROME_BIN"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        _which_tool("google-chrome") or "",
        _which_tool("chromium") or "",
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            chrome = cand
            break
    tools["chrome"] = chrome

    return {
        "ok": True,
        "platform": sys.platform,
        "pluginDataDir": str(ctx.data_dir),
        "dbPath": str(ctx.db_path),
        "dbExists": ctx.db_path.is_file(),
        "repoDir": str(repo) if repo else "",
        "repoFound": repo is not None,
        "pythonBin": ctx.python_bin,
        "envFile": str(ctx.env_file) if ctx.env_file else "",
        "envExists": ctx.env_file is not None,
        "outputRootDir": str(ctx.output_root_dir),
        "defaultPdfDir": pdf_dir,
        "defaultPdfDirReady": bool(pdf_dir and os.path.isdir(pdf_dir)),
        "defaultHtmlDir": html_dir,
        "defaultHtmlDirReady": bool(html_dir and os.path.isdir(html_dir)),
        "googleDriveEnvDir": gdrive_dir,
        "tools": tools,
        "counts": counts,
    }


def tool_check_providers(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    cli_args = ["--json", "--timeout", str(float(args.get("timeout") or 15.0))]
    if args.get("ping"):
        cli_args.append("--ping")
    if args.get("scholar"):
        cli_args.append("--scholar")
    if args.get("list_models"):
        cli_args.append("--list-models")
    for m in args.get("models") or []:
        cli_args.extend(["--model", str(m)])
    for pm in args.get("ping_models") or []:
        cli_args.extend(["--ping-model", str(pm)])
    return ctx.run_json_tool("tools/check_providers.py", cli_args, timeout=max(30, int(float(args.get("timeout") or 15.0) * 4)))


def tool_list_templates(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    repo = ctx.require_repo()
    troot = repo / "templates"
    if not troot.is_dir():
        raise RuntimeError(f"Templates folder not found: {troot}")
    out: List[Dict[str, Any]] = []
    for d in sorted(troot.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        vj = d / "venue.json"
        meta: Dict[str, Any] = {}
        if vj.is_file():
            try:
                meta = json.loads(vj.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        out.append(
            {
                "name": d.name,
                "path": str(d),
                "kind": meta.get("kind") or ("math-modeling" if d.name.startswith("math-modeling") else "conference"),
                "venue_name": meta.get("venue_name") or d.name,
                "short_name": meta.get("short_name") or d.name,
                "field": meta.get("field") or "",
                "review_model": meta.get("review_model") or "",
                "separate_related_work_section": meta.get("separate_related_work_section"),
                "section_order": meta.get("section_order"),
                "abstract_limit": meta.get("abstract_limit") or "",
                "writing_style": meta.get("writing_style") or "",
                "figure_style": meta.get("figure_style") or "",
                "hasTemplateTex": (d / "template.tex").is_file(),
                "hasGuidelines": (d / "guidelines.md").is_file(),
            }
        )
    return {"ok": True, "count": len(out), "templates": out}


def tool_parse_intake_form(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    fp = os.path.abspath(os.path.expanduser(str(args.get("file_path") or "")))
    if not fp or not os.path.isfile(fp):
        raise RuntimeError(f"Intake form file not found: {fp}")
    return ctx.run_json_tool("tools/parse_intake_form.py", [fp], timeout=60)


def _fetch_project(conn: sqlite3.Connection, project_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise RuntimeError(f"Project '{project_id}' not found")
    figs = conn.execute(
        "SELECT id, project_id, file_name, source_path, caption, sort_order, created_at "
        "FROM project_figures WHERE project_id = ? ORDER BY sort_order ASC, created_at ASC",
        (project_id,),
    ).fetchall()
    runs = conn.execute(
        "SELECT * FROM runs WHERE project_id = ? ORDER BY started_at DESC",
        (project_id,),
    ).fetchall()
    return {
        "id": row["id"],
        "title": row["title"],
        "template_name": row["template_name"],
        "writer_model": row["writer_model"],
        "reflection_model": row["reflection_model"],
        "plotting_model": row["plotting_model"],
        "image_model": row["image_model"],
        "research_cutoff": row["research_cutoff"],
        "use_plotting": bool(row["use_plotting"]),
        "plotting_max_critic_rounds": int(row["plotting_max_critic_rounds"]),
        "idea_markdown": row["idea_markdown"],
        "experimental_log_markdown": row["experimental_log_markdown"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "figures": [dict(f) for f in figs],
        "runs": [{**dict(r), "use_plotting": bool(r["use_plotting"])} for r in runs],
    }


def tool_projects(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "list").strip().lower()
    with ctx.connect() as conn:
        if action == "list":
            rows = conn.execute(
                """
                SELECT p.id, p.title, p.template_name, p.use_plotting, p.writer_model,
                       p.reflection_model, p.created_at, p.updated_at,
                       (SELECT COUNT(*) FROM runs r WHERE r.project_id = p.id) AS run_count,
                       (SELECT COUNT(*) FROM project_figures f WHERE f.project_id = p.id) AS figure_count
                FROM projects p
                ORDER BY p.updated_at DESC
                """
            ).fetchall()
            return {"ok": True, "projects": [{**dict(r), "use_plotting": bool(r["use_plotting"])} for r in rows]}

        if action == "get":
            pid = str(args.get("project_id") or "").strip()
            if not pid:
                raise RuntimeError("`project_id` is required for action='get'")
            return {"ok": True, "project": _fetch_project(conn, pid)}

        if action == "create":
            title = str(args.get("title") or "").strip()
            if not title:
                raise RuntimeError("`title` is required for action='create'")
            pid = str(uuid.uuid4())
            now = _now_iso()
            conn.execute(
                """
                INSERT INTO projects (
                    id, title, template_name, writer_model, reflection_model,
                    plotting_model, image_model, research_cutoff, use_plotting,
                    plotting_max_critic_rounds, idea_markdown, experimental_log_markdown,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pid,
                    title,
                    str(args.get("template_name") or "cvpr2025").strip(),
                    str(args.get("writer_model") or "auto").strip(),
                    str(args.get("reflection_model") or "auto").strip(),
                    str(args.get("plotting_model") or "auto").strip(),
                    str(args.get("image_model") or "auto").strip(),
                    str(args.get("research_cutoff") or "").strip(),
                    1 if args.get("use_plotting", True) else 0,
                    max(1, min(10, int(args.get("plotting_max_critic_rounds") or 3))),
                    str(args.get("idea_markdown") or ""),
                    str(args.get("experimental_log_markdown") or ""),
                    now,
                    now,
                ),
            )
            return {"ok": True, "project": _fetch_project(conn, pid)}

        if action == "update":
            pid = str(args.get("project_id") or "").strip()
            if not pid:
                raise RuntimeError("`project_id` is required for action='update'")
            cur = _fetch_project(conn, pid)
            expected_upd = str(args.get("expected_updated_at") or "").strip()
            if expected_upd and cur["updated_at"] != expected_upd:
                raise RuntimeError(
                    "Conflict: project changed since last read (`expected_updated_at` mismatch). "
                    "Re-read the project via action='get' before updating."
                )
            conn.execute(
                """
                UPDATE projects SET
                    title = ?, template_name = ?, writer_model = ?, reflection_model = ?,
                    plotting_model = ?, image_model = ?, research_cutoff = ?, use_plotting = ?,
                    plotting_max_critic_rounds = ?, idea_markdown = ?, experimental_log_markdown = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    str(args["title"]).strip() if "title" in args and args["title"] is not None else cur["title"],
                    str(args["template_name"]).strip() if "template_name" in args and args["template_name"] is not None else cur["template_name"],
                    str(args["writer_model"]).strip() if "writer_model" in args and args["writer_model"] is not None else cur["writer_model"],
                    str(args["reflection_model"]).strip() if "reflection_model" in args and args["reflection_model"] is not None else cur["reflection_model"],
                    str(args["plotting_model"]).strip() if "plotting_model" in args and args["plotting_model"] is not None else cur["plotting_model"],
                    str(args["image_model"]).strip() if "image_model" in args and args["image_model"] is not None else cur["image_model"],
                    str(args["research_cutoff"]).strip() if "research_cutoff" in args and args["research_cutoff"] is not None else cur["research_cutoff"],
                    (1 if args["use_plotting"] else 0) if "use_plotting" in args and args["use_plotting"] is not None else (1 if cur["use_plotting"] else 0),
                    max(1, min(10, int(args["plotting_max_critic_rounds"]))) if "plotting_max_critic_rounds" in args and args["plotting_max_critic_rounds"] is not None else cur["plotting_max_critic_rounds"],
                    str(args["idea_markdown"]) if "idea_markdown" in args and args["idea_markdown"] is not None else cur["idea_markdown"],
                    str(args["experimental_log_markdown"]) if "experimental_log_markdown" in args and args["experimental_log_markdown"] is not None else cur["experimental_log_markdown"],
                    _now_iso(),
                    pid,
                ),
            )
            return {"ok": True, "project": _fetch_project(conn, pid)}

        if action == "delete":
            pid = str(args.get("project_id") or "").strip()
            if not pid:
                raise RuntimeError("`project_id` is required for action='delete'")
            conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
            return {"ok": True, "deleted": pid}

        if action == "add_figure":
            pid = str(args.get("project_id") or "").strip()
            src = Path(os.path.expanduser(str(args.get("figure_path") or ""))).resolve()
            if not pid or not src.is_file():
                raise RuntimeError(f"Project ID or figure file missing: {src}")
            next_order = int(
                conn.execute("SELECT COALESCE(MAX(sort_order) + 1, 0) FROM project_figures WHERE project_id = ?", (pid,)).fetchone()[0]
            )
            fid = str(uuid.uuid4())
            now = _now_iso()
            conn.execute(
                "INSERT INTO project_figures (id, project_id, file_name, source_path, caption, sort_order, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fid, pid, src.name, str(src), str(args.get("figure_caption") or ""), next_order, now),
            )
            conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now, pid))
            return {"ok": True, "figure_id": fid, "project": _fetch_project(conn, pid)}

        if action == "delete_figure":
            fid = str(args.get("figure_id") or "").strip()
            if not fid:
                raise RuntimeError("`figure_id` is required for action='delete_figure'")
            row = conn.execute("SELECT project_id FROM project_figures WHERE id = ?", (fid,)).fetchone()
            conn.execute("DELETE FROM project_figures WHERE id = ?", (fid,))
            if row:
                conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (_now_iso(), row["project_id"]))
            return {"ok": True, "deleted_figure_id": fid}

        if action == "list_runs":
            pid = str(args.get("project_id") or "").strip()
            if pid:
                rows = conn.execute("SELECT * FROM runs WHERE project_id = ? ORDER BY started_at DESC", (pid,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 30").fetchall()
            return {"ok": True, "runs": [{**dict(r), "use_plotting": bool(r["use_plotting"])} for r in rows]}

        if action == "update_figure_caption":
            fid = str(args.get("figure_id") or "").strip()
            if not fid:
                raise RuntimeError("`figure_id` is required for action='update_figure_caption'")
            cap = str(args.get("figure_caption") if args.get("figure_caption") is not None else (args.get("caption") or ""))
            row = conn.execute("SELECT project_id FROM project_figures WHERE id = ?", (fid,)).fetchone()
            if not row:
                raise RuntimeError(f"Figure '{fid}' not found")
            conn.execute("UPDATE project_figures SET caption = ? WHERE id = ?", (cap, fid))
            conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (_now_iso(), row["project_id"]))
            return {"ok": True, "figure_id": fid, "caption": cap, "project": _fetch_project(conn, str(row["project_id"]))}

        if action == "import_cvpr_example":
            repo = ctx.require_repo()
            examples_dir = repo / "frontend" / "examples"
            ex_json = examples_dir / "cvpr_example.json"
            if not ex_json.is_file():
                raise RuntimeError(f"CVPR example file not found at {ex_json}")
            parsed = json.loads(ex_json.read_text(encoding="utf-8"))
            pid = str(uuid.uuid4())
            now = _now_iso()
            conn.execute(
                """
                INSERT INTO projects (
                    id, title, template_name, writer_model, reflection_model,
                    plotting_model, image_model, research_cutoff, use_plotting,
                    plotting_max_critic_rounds, idea_markdown, experimental_log_markdown,
                    created_at, updated_at
                ) VALUES (?, ?, 'cvpr2025', 'auto', 'auto', 'auto', 'auto', '', 1, 3, ?, ?, ?, ?)
                """,
                (
                    pid,
                    "TSAM: Referring Audio-Visual Segmentation (CVPR Example)",
                    str(parsed.get("idea") or ""),
                    str(parsed.get("log") or ""),
                    now,
                    now,
                ),
            )
            fig_rel = str(parsed.get("figure_path") or "").strip()
            if fig_rel:
                fig_src = (examples_dir / fig_rel).resolve()
                if fig_src.is_file():
                    fid = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO project_figures (id, project_id, file_name, source_path, caption, sort_order, created_at) "
                        "VALUES (?, ?, ?, ?, ?, 0, ?)",
                        (fid, pid, fig_src.name, str(fig_src), str(parsed.get("figure_caption") or ""), now),
                    )
            return {"ok": True, "project": _fetch_project(conn, pid)}

        if action == "get_run_logs":
            rid = str(args.get("run_id") or "").strip()
            if not rid:
                raise RuntimeError("`run_id` is required for action='get_run_logs'")
            after_id = max(0, int(args.get("after_id") or 0))
            limit = max(1, min(5000, int(args.get("limit") or 500)))
            run_row = conn.execute("SELECT id, status, current_stage, run_dir FROM runs WHERE id = ?", (rid,)).fetchone()
            if not run_row:
                raise RuntimeError(f"Run '{rid}' not found")
            rows = conn.execute(
                "SELECT id, run_id, stage, line, created_at FROM run_logs WHERE run_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                (rid, after_id, limit),
            ).fetchall()
            logs = [
                {
                    "id": int(r["id"]),
                    "run_id": str(r["run_id"]),
                    "stage": str(r["stage"]),
                    "line": _redact_text(str(r["line"]), ctx.secrets),
                    "created_at": str(r["created_at"]),
                }
                for r in rows
            ]
            return {
                "ok": True,
                "run_id": rid,
                "status": str(run_row["status"]),
                "current_stage": str(run_row["current_stage"]),
                "count": len(logs),
                "nextAfterId": logs[-1]["id"] if logs else after_id,
                "logs": logs,
            }

        if action == "delete_run":
            rid = str(args.get("run_id") or "").strip()
            if not rid:
                raise RuntimeError("`run_id` is required for action='delete_run'")
            run_row = conn.execute("SELECT id, status FROM runs WHERE id = ?", (rid,)).fetchone()
            if not run_row:
                raise RuntimeError(f"Run '{rid}' not found")
            if str(run_row["status"]).lower() == "running":
                raise RuntimeError("Cannot delete a run while status is 'running'; stop it first.")
            conn.execute("DELETE FROM runs WHERE id = ?", (rid,))
            return {"ok": True, "deleted_run_id": rid}

        if action == "resume_run":
            rid = str(args.get("run_id") or "").strip()
            if not rid:
                raise RuntimeError("`run_id` is required for action='resume_run'")
            run_row = conn.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()
            if not run_row:
                raise RuntimeError(f"Run '{rid}' not found")
            if str(run_row["status"]).lower() == "running":
                raise RuntimeError("Run is already marked 'running'.")
            run_dir = Path(str(run_row["run_dir"])).resolve()
            raw_dir = run_dir / "raw_materials"
            if not run_dir.is_dir() or not raw_dir.is_dir():
                raise RuntimeError(f"Run raw_materials directory missing for resume: {raw_dir}")
            conn.execute(
                "UPDATE runs SET status = 'running', finished_at = NULL, exit_code = NULL, error_message = NULL WHERE id = ?",
                (rid,),
            )
            conn.commit()
            res = tool_start_paper_run(
                ctx,
                {
                    "raw_materials_dir": str(raw_dir),
                    "output_dir": str(run_dir),
                    "template_name": str(run_row["template_name"] or "cvpr2025"),
                    "writer_model": str(run_row["writer_model"] or "auto"),
                    "reflection_model": str(run_row["reflection_model"] or "auto"),
                    "use_plotting": bool(run_row["use_plotting"]),
                    "resume": True,
                    "_existing_run_id": rid,
                },
            )
            res["resumed"] = True
            return res

        raise RuntimeError(f"Unsupported project action: {action}")


def tool_start_paper_run(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    repo = ctx.require_repo()
    cli = repo / "paper_writing_cli.py"
    project_id = str(args.get("project_id") or "").strip()
    raw_dir_arg = str(args.get("raw_materials_dir") or "").strip()
    template_arg = str(args.get("template_name") or "").strip()
    latex_tpl_arg = str(args.get("latex_template_dir") or "").strip()
    resume = bool(args.get("resume", False))

    run_id = str(args.get("_existing_run_id") or "").strip()
    if project_id:
        with ctx.connect() as conn:
            proj = _fetch_project(conn, project_id)
            _, _, prefs_md = _build_preferences_markdown(conn)
        if not proj["idea_markdown"].strip():
            raise RuntimeError("Project `idea_markdown` is empty; populate it before starting a run.")
        if not proj["experimental_log_markdown"].strip():
            raise RuntimeError("Project `experimental_log_markdown` is empty; populate it before starting a run.")
        template_name = template_arg or proj["template_name"]
        template_dir = Path(latex_tpl_arg) if latex_tpl_arg else (repo / "templates" / template_name)
        if not template_dir.is_dir():
            raise RuntimeError(f"LaTeX template directory not found: {template_dir}")

        run_id = str(uuid.uuid4())
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(args.get("output_dir") or (ctx.output_root_dir / f"{_sanitize_slug(proj['title'])}_{stamp}_{run_id[:8]}")).resolve()
        raw_dir = out_dir / "raw_materials"
        fig_dir = raw_dir / "figures"
        fig_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "idea_sparse.md").write_text(proj["idea_markdown"], encoding="utf-8")
        (raw_dir / "experimental_log.md").write_text(proj["experimental_log_markdown"], encoding="utf-8")
        if prefs_md.strip():
            (raw_dir / "research_preferences.md").write_text(prefs_md, encoding="utf-8")
        if proj["figures"]:
            fig_infos = []
            for fig in proj["figures"]:
                src = Path(fig["source_path"])
                if not src.is_file():
                    raise RuntimeError(f"Attached figure file missing: {src}")
                shutil.copy2(src, fig_dir / fig["file_name"])
                fig_infos.append({"name": fig["file_name"], "caption": fig.get("caption", "")})
            (fig_dir / "info.json").write_text(json.dumps(fig_infos, ensure_ascii=False, indent=2), encoding="utf-8")

        writer_model = str(args.get("writer_model") or proj["writer_model"] or "auto")
        reflection_model = str(args.get("reflection_model") or proj["reflection_model"] or "auto")
        plotting_model = str(args.get("plotting_model") or proj["plotting_model"] or "auto")
        image_model = str(args.get("image_model") or proj["image_model"] or "auto")
        use_plotting = bool(args.get("use_plotting") if "use_plotting" in args else proj["use_plotting"])
        max_rounds = int(args.get("plotting_max_critic_rounds") or proj["plotting_max_critic_rounds"] or 3)
        cutoff = str(args.get("research_cutoff") or proj["research_cutoff"] or datetime.now().strftime("%Y-%m"))
        title = proj["title"]

        with ctx.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    id, project_id, status, current_stage, template_name,
                    writer_model, reflection_model, use_plotting, run_dir,
                    pdf_path, started_at
                ) VALUES (?, ?, 'running', 'outline', ?, ?, ?, ?, ?, NULL, ?)
                """,
                (run_id, project_id, template_name, writer_model, reflection_model, 1 if use_plotting else 0, str(out_dir), _now_iso()),
            )
    else:
        if not raw_dir_arg:
            raise RuntimeError("Provide either `project_id` or `raw_materials_dir`.")
        raw_dir = Path(os.path.expanduser(raw_dir_arg)).resolve()
        if not raw_dir.is_dir():
            raise RuntimeError(f"raw_materials_dir not found: {raw_dir}")
        template_name = template_arg or "cvpr2025"
        template_dir = Path(os.path.expanduser(latex_tpl_arg)).resolve() if latex_tpl_arg else (repo / "templates" / template_name)
        if not template_dir.is_dir():
            raise RuntimeError(f"LaTeX template directory not found: {template_dir}")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(os.path.expanduser(str(args.get("output_dir") or (ctx.output_root_dir / f"paper_run_{stamp}")))).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        writer_model = str(args.get("writer_model") or "auto")
        reflection_model = str(args.get("reflection_model") or "auto")
        plotting_model = str(args.get("plotting_model") or "auto")
        image_model = str(args.get("image_model") or "auto")
        use_plotting = bool(args.get("use_plotting", False))
        max_rounds = int(args.get("plotting_max_critic_rounds") or 3)
        cutoff = str(args.get("research_cutoff") or datetime.now().strftime("%Y-%m"))
        title = template_name

    cmd = [
        ctx.python_bin,
        "-u",
        str(cli),
        "--raw_materials_dir",
        str(raw_dir),
        "--output_dir",
        str(out_dir),
        "--latex_template_dir",
        str(template_dir),
        "--idea_filename",
        str(args.get("idea_filename") or "idea_sparse.md"),
        "--experimental_log_filename",
        str(args.get("experimental_log_filename") or "experimental_log.md"),
        "--writer_model_name",
        writer_model,
        "--reflection_model_name",
        reflection_model,
        "--use_plotting",
        "true" if use_plotting else "false",
        "--research_cutoff",
        cutoff,
    ]
    if use_plotting:
        cmd.extend([
            "--plotting_model_name",
            plotting_model,
            "--image_model_name",
            image_model,
            "--plotting_max_critic_rounds",
            str(max_rounds),
        ])
    if resume:
        cmd.append("--resume")

    extra_env: Dict[str, str] = {}
    overrides = args.get("step_model_overrides") or {}
    if isinstance(overrides, dict):
        for k, v in overrides.items():
            if not v or str(v).strip().lower() == "auto":
                continue
            key_str = str(k).strip().upper()
            env_name = key_str if key_str.startswith("PO_") else f"PO_TASK_MODEL_{key_str}"
            extra_env[env_name] = str(v).strip()

    meta = _spawn_background_job(
        ctx,
        kind="paper_run",
        cmd=cmd,
        cwd=repo,
        run_dir=out_dir,
        run_id=run_id,
        use_plotting=use_plotting,
        extra_env=extra_env,
        title=title,
    )
    return {
        "ok": True,
        "job_id": meta["job_id"],
        "run_id": run_id,
        "pid": meta["pid"],
        "run_dir": str(out_dir),
        "log_path": meta["log_path"],
        "template_name": template_name,
        "note": "Pipeline started in background. Poll with `po_job_manage` (`action='status'` or `'logs'`).",
    }


def tool_job_manage(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "list").strip().lower()
    jroot = _jobs_dir(ctx)
    tail_lines = max(1, int(args.get("tail_lines") or 80))

    if action == "list":
        jobs = []
        for mf in sorted(jroot.glob("*/meta.json"), reverse=True):
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
                jobs.append(_inspect_job(ctx, meta, tail_lines=12))
            except Exception:
                continue
        return {"ok": True, "jobs": jobs[:30]}

    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError("`job_id` is required")
    mf = jroot / job_id / "meta.json"
    if not mf.is_file():
        raise RuntimeError(f"Job not found: {job_id}")
    meta = json.loads(mf.read_text(encoding="utf-8"))

    if action in ("status", "logs"):
        return {"ok": True, **_inspect_job(ctx, meta, tail_lines=tail_lines)}

    if action == "stop":
        pid = int(meta.get("pid") or 0)
        if _pid_alive(pid):
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            deadline = time.time() + 2.0
            while time.time() < deadline and _pid_alive(pid):
                time.sleep(0.2)
            if _pid_alive(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
        meta["stopped"] = True
        meta["stopped_at"] = _now_iso()
        mf.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, **_inspect_job(ctx, meta, tail_lines=30)}

    raise RuntimeError(f"Unsupported job action: {action}")


def tool_inspect_run(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    run_dir = Path(os.path.expanduser(str(args.get("run_dir") or ""))).resolve()
    if not run_dir.is_dir():
        raise RuntimeError(f"Run directory not found: {run_dir}")
    action = str(args.get("action") or "summary").strip().lower()

    if action == "read_artifact":
        rel = str(args.get("relative_path") or "").strip()
        if not rel:
            raise RuntimeError("`relative_path` is required for action='read_artifact'")
        target = (run_dir / rel).resolve()
        if not (target == run_dir or run_dir in target.parents):
            raise RuntimeError("Refused path outside run_dir")
        if not target.is_file():
            raise RuntimeError(f"Artifact not found: {rel}")
        raw = target.read_text(encoding="utf-8", errors="replace")
        max_chars = max(500, int(args.get("max_chars") or 40000))
        truncated = len(raw) > max_chars
        text = _redact_text(raw[:max_chars], ctx.secrets)
        return {"ok": True, "path": str(target), "totalChars": len(raw), "truncated": truncated, "content": text}

    if action == "summary":
        src_dir = None
        for sub in ("content_refinement_workdir", "latex_writeup"):
            cand = run_dir / sub
            if cand.is_dir() and any(cand.glob("*.tex")):
                src_dir = cand
                break
        tex_files = []
        if src_dir:
            tex_files = sorted(str(p.relative_to(run_dir)) for p in src_dir.rglob("*") if p.is_file() and p.suffix.lower() in (".tex", ".bib"))
        peer_reviews = sorted(str(p.relative_to(run_dir)) for p in (run_dir / "peer_reviews").glob("*") if p.is_file()) if (run_dir / "peer_reviews").is_dir() else []

        def read_json_if(p: Path) -> Any:
            if p.is_file():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    return None
            return None

        return {
            "ok": True,
            "run_dir": str(run_dir),
            "finalPdfPath": str(run_dir / "final_paper.pdf") if (run_dir / "final_paper.pdf").is_file() else "",
            "latexSourceDir": src_dir.name if src_dir else "",
            "latexFiles": tex_files[:80],
            "hasOutline": (run_dir / "outline.json").is_file(),
            "hasCitationMap": (run_dir / "literature_agent_output" / "citation_map.json").is_file(),
            "citationAudit": read_json_if(run_dir / "citation_audit.json"),
            "citationAuditMd": str(run_dir / "citation_audit.md") if (run_dir / "citation_audit.md").is_file() else "",
            "citedBib": str(run_dir / "references_cited.bib") if (run_dir / "references_cited.bib").is_file() else "",
            "usageReport": read_json_if(run_dir / "usage_report.json"),
            "timeBreakdown": read_json_if(run_dir / "time_breakdown.json"),
            "peerReviews": peer_reviews,
        }

    if action == "copy_pdf":
        src_pdf = (run_dir / "final_paper.pdf").resolve()
        if run_dir not in src_pdf.parents or not src_pdf.is_file():
            raise RuntimeError(f"Final PDF not found in run_dir: {run_dir / 'final_paper.pdf'}")
        dest_raw = str(args.get("dest_path") or "").strip()
        if dest_raw:
            dest_path = Path(os.path.expanduser(dest_raw)).resolve()
            if dest_path.suffix.lower() != ".pdf":
                raise RuntimeError("`dest_path` must end in .pdf")
        else:
            env_pdf = _clean_env("PO_DEFAULT_PDF_DIR")
            base_dir = Path(env_pdf) if (env_pdf and os.path.isdir(env_pdf)) else (ctx.data_dir / "exports")
            dest_path = _unique_path(base_dir, f"{run_dir.name}_final_paper", ".pdf")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with src_pdf.open("rb") as rf, dest_path.open("xb") as wf:
                shutil.copyfileobj(rf, wf)
        except FileExistsError:
            raise RuntimeError(f"Destination file already exists (refusing to overwrite): {dest_path}")
        except Exception:
            if dest_raw and dest_path.exists():
                try:
                    dest_path.unlink()
                except OSError:
                    pass
            raise
        return {
            "ok": True,
            "sourcePdf": str(src_pdf),
            "destPath": str(dest_path),
            "bytes": dest_path.stat().st_size,
        }

    raise RuntimeError(f"Unsupported inspect action: {action}")


def tool_data_analysis(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "profile").strip().lower()
    data_file = os.path.abspath(os.path.expanduser(str(args.get("file_path") or "")))
    if not data_file or not os.path.isfile(data_file):
        raise RuntimeError(f"Data file not found: {data_file}")

    if action == "profile":
        return ctx.run_json_tool("tools/research_studio.py", ["data-profile", "--file", data_file], timeout=60)

    if action == "analyze":
        repo = ctx.require_repo()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(os.path.expanduser(str(args.get("out_dir") or (ctx.data_dir / "studio_runs" / f"data_{stamp}")))).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        question = str(args.get("question") or "")
        model = str(args.get("model") or "auto")
        cli_args = ["run-data-analysis", "--file", data_file, "--question", question, "--out-dir", str(out_dir), "--model", model]
        if bool(args.get("async_mode", True)):
            cmd = [ctx.python_bin, "-B", str(repo / "tools" / "research_studio.py"), *cli_args]
            meta = _spawn_background_job(ctx, "data_analysis", cmd, cwd=repo, run_dir=out_dir, use_plotting=False, title=Path(data_file).name)
            return {"ok": True, "async": True, "job_id": meta["job_id"], "out_dir": str(out_dir), "log_path": meta["log_path"]}
        return ctx.run_json_tool("tools/research_studio.py", cli_args, timeout=max(60, int(args.get("timeout") or 300)))

    raise RuntimeError(f"Unsupported data_analysis action: {action}")


def tool_run_sop(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    sop = str(args.get("sop") or "").strip()
    valid = ("paper_reading", "lit_review", "topic_ideation", "reproduction", "math_modeling")
    if sop not in valid:
        raise RuntimeError(f"`sop` must be one of {valid}")
    repo = ctx.require_repo()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(os.path.expanduser(str(args.get("out_dir") or (ctx.data_dir / "studio_runs" / f"{sop}_{stamp}")))).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    prefs_md = ""
    if bool(args.get("include_memories", True)):
        with ctx.connect() as conn:
            _, _, prefs_md = _build_preferences_markdown(conn)

    files = [os.path.abspath(os.path.expanduser(str(f))) for f in (args.get("files") or []) if str(f).strip()]
    payload = {
        "topic": str(args.get("topic") or ""),
        "question": str(args.get("question") or args.get("topic") or ""),
        "notes": str(args.get("notes") or ""),
        "files": files,
        "libraryItems": list(args.get("library_items") or []),
        "preferencesMarkdown": prefs_md,
        "queryOnline": bool(args.get("query_online", True)),
        "extra": args.get("extra") or {},
    }
    inp_json = out_dir / "sop_input.json"
    inp_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    model = str(args.get("model") or "auto")
    cli_args = ["run-sop", "--sop", sop, "--input-json", str(inp_json), "--out-dir", str(out_dir), "--model", model]
    if bool(args.get("async_mode", False)):
        cmd = [ctx.python_bin, "-B", str(repo / "tools" / "research_studio.py"), *cli_args]
        meta = _spawn_background_job(ctx, f"sop_{sop}", cmd, cwd=repo, run_dir=out_dir, use_plotting=False, title=sop)
        return {"ok": True, "async": True, "job_id": meta["job_id"], "out_dir": str(out_dir), "log_path": meta["log_path"]}
    return ctx.run_json_tool("tools/research_studio.py", cli_args, timeout=max(60, int(args.get("timeout") or 300)))


_BUILTIN_SKILLS: List[Dict[str, str]] = [
    {
        "id": "builtin-paper-deep-read",
        "name": "论文六步精读 SOP",
        "description": "按 ClawsGO 论文精读 6 步工作流拆解 PDF：材料完整度、全篇概览、章节任务链、关键公式/段落精读、图文一致检查、结构图与审稿批注表。",
        "category": "阅读与选题",
        "content": "---\nname: paper-deep-read\ndescription: 六步论文精读 SOP\n---\n\n请对给定文献执行六步精读：\n1. 材料完整性检查（缺附录/代码/补充材料需显式标出）。\n2. 全文核心问题、主宣称、方法亮点与主结论概览。\n3. 章节职责与逻辑关联表。\n4. 核心段落与关键方程精读（注明假设条件与实验边界）。\n5. 图文一致核查与批判性审稿清单。\n6. 给出 Mermaid 结构框图。",
    },
    {
        "id": "builtin-lit-review-gbt7714",
        "name": "防编造主题式综述 · GB/T 7714",
        "description": "先写文献梳理与证据矩阵，再定研究背景；严格区分作者自身结论与转引结论，标明全文/仅摘要阅读深度并采用 GB/T 7714 顺序引文。",
        "category": "文献综述",
        "content": "---\nname: lit-review-gbt7714\ndescription: 防编造主题式综述与 GB/T 7714 引用\n---\n\n按以下铁律撰写文献综述：\n1. 先梳理文献分析与统一证据对比矩阵，再写研究背景与问题提出。\n2. 正文仅引用真实核验过的条目，严禁编造 DOI/年份/作者。\n3. 长段落连贯学术叙述，禁止两三句话碎段列表体。\n4. 列出可逐句点回来源的出处映射表与 GB/T 7714 参考文献列表。",
    },
    {
        "id": "builtin-data-analysis-7step",
        "name": "七步可复现数据分析规范",
        "description": "输出数据字典、可逆清洗日志、描述统计、带效应量与 95% 置信区间的假设检验，并按「现象-证据-解释-局限」四层总结。",
        "category": "数据分析",
        "content": "---\nname: data-analysis-7step\ndescription: 七步可复现数据分析与四层报告\n---\n\n执行七步数据分析检查：\n1. 明确问题 -> 数据字典 -> 可逆清洗 -> 描述统计 -> 选图绘图 -> 关系/差异检验(报告 n、精确 p、效应量、95% CI) -> 四层结构(现象/证据/解释/局限)。\n严禁重复试错挑显著结果。",
    },
    {
        "id": "builtin-paper-reproduction",
        "name": "算法复现七步检查表",
        "description": "确定复现层级边界、拆分可独立测试的模块、记录数据/代码哈希、建立最小闭环、对齐评价指标语义、完成消融对照。",
        "category": "实验与复现",
        "content": "---\nname: paper-reproduction\ndescription: 文献复现七步工程 SOP\n---\n\n按照七步复现法组织实验文档：\n1. 复现目标与边界声明；2. 算法模块化工程规格；3. 数据版本与哈希清单（缺失数据说明替代验证边界）；4. 最小闭环代码；5. 指标语义对齐（阈值/均值方式）；6. 三层核验对照；7. 可运行 README。",
    },
    {
        "id": "builtin-nature-de-ai-polish",
        "name": "Nature 级去 AI 味英文润色",
        "description": "控制破折号/分号预算，消除空洞套话，首句给结论而非防御性铺垫，确保图注首句加粗总标题及统计学报告完整。",
        "category": "写作与投稿",
        "content": "---\nname: nature-de-ai-polish\ndescription: Nature Portfolio 风格与去 AI 味润色\n---\n\n逐段审修并润色英文学术手稿：\n- 摘要零破折号零分号；正文每段最多一处。\n- 删除 delve / tapestry / realm / seamless / Taken together / It is worth noting 等模板套话。\n- 核对每张图的正文引用与图注首句加粗标题、样本量 n 和误差棒定义是否完整。",
    },
]


def _parse_skill_frontmatter(sid: str, raw: str, updated_at: str, path: str = "") -> Dict[str, Any]:
    name = sid
    desc = ""
    category = "自定义技能"
    if raw.startswith("---"):
        rest = raw[3:]
        end_idx = rest.find("\n---")
        if end_idx != -1:
            for line in rest[:end_idx].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    key = k.strip().lower()
                    val = v.strip().strip('"').strip("'")
                    if key == "name" and val:
                        name = val
                    elif key == "description":
                        desc = val
                    elif key == "category" and val:
                        category = val
    return {
        "id": sid,
        "skill_id": sid,
        "name": name,
        "description": desc,
        "category": category,
        "content": raw,
        "builtin": False,
        "updated_at": updated_at,
        "path": path,
        "chars": len(raw),
        "preview": raw[:200],
    }


def _skill_search_dirs(ctx: RuntimeContext) -> Tuple[Path, List[Path]]:
    studio_skills = ctx.db_path.parent / "skills"
    plugin_skills = ctx.data_dir / "custom_skills"
    if ctx.db_path.parent != ctx.data_dir and (studio_skills.is_dir() or "com.paperorchestra.studio" in str(ctx.db_path)):
        primary = studio_skills
    else:
        primary = plugin_skills
    primary.mkdir(parents=True, exist_ok=True)
    dirs = [studio_skills, plugin_skills] if studio_skills != plugin_skills else [plugin_skills]
    return primary, dirs


def _collect_all_skills(ctx: RuntimeContext) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = [
        {
            **b,
            "skill_id": b["id"],
            "builtin": True,
            "updated_at": "内置",
            "path": "",
            "chars": len(b["content"]),
            "preview": b["content"][:200],
        }
        for b in _BUILTIN_SKILLS
    ]
    _, dirs = _skill_search_dirs(ctx)
    seen_ids = {b["id"] for b in _BUILTIN_SKILLS}
    custom: List[Dict[str, Any]] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            sid = p.stem
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
            except OSError:
                continue
            custom.append(_parse_skill_frontmatter(sid, txt, mtime, str(p)))
    custom.sort(key=lambda x: str(x["name"]).lower())
    items.extend(custom)
    return items


def tool_custom_skills(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "list").strip().lower()
    primary_dir, search_dirs = _skill_search_dirs(ctx)

    if action == "list":
        include_content = bool(args.get("include_content", False))
        skills = _collect_all_skills(ctx)
        if not include_content:
            skills = [{k: v for k, v in s.items() if k != "content"} for s in skills]
        return {"ok": True, "count": len(skills), "skills": skills}

    if action == "get":
        sid = str(args.get("skill_id") or args.get("id") or "").strip()
        if not sid:
            raise RuntimeError("`skill_id` is required for action='get'")
        for s in _collect_all_skills(ctx):
            if s["id"] == sid or s["name"] == sid:
                return {"ok": True, "skill": s}
        raise RuntimeError(f"Skill not found: {sid}")

    if action == "save":
        name = str(args.get("name") or args.get("skill_id") or "").strip()
        content = str(args.get("content") or "")
        if not name or not content.strip():
            raise RuntimeError("`name` and `content` are required for action='save'")
        raw_id = str(args.get("skill_id") or name).strip()
        sid = _sanitize_slug(raw_id, "custom_skill")
        if sid.startswith("builtin-"):
            sid = f"custom-{sid}"
        body = content
        if not content.lstrip().startswith("---"):
            desc = str(args.get("description") or "").strip().replace('"', "'")
            cat = str(args.get("category") or "自定义技能").strip().replace('"', "'")
            safe_name = name.replace('"', "'")
            body = f'---\nname: "{safe_name}"\ndescription: "{desc}"\ncategory: "{cat}"\n---\n\n{content.strip()}\n'
        target = primary_dir / f"{sid}.md"
        target.write_text(body, encoding="utf-8")
        return {"ok": True, "skill_id": sid, "path": str(target), "skill": _parse_skill_frontmatter(sid, body, _now_iso(), str(target))}

    if action == "import_file":
        fp = Path(os.path.expanduser(str(args.get("file_path") or ""))).resolve()
        if not fp.is_file():
            raise RuntimeError(f"Skill file not found: {fp}")
        if fp.stat().st_size > 1024 * 1024:
            raise RuntimeError("Skill file exceeds 1 MiB limit")
        raw = fp.read_text(encoding="utf-8")
        if not raw.strip():
            raise RuntimeError("Skill file is empty")
        default_name = fp.parent.name if fp.name.lower() == "skill.md" else fp.stem
        sid = _sanitize_slug(str(args.get("skill_id") or default_name), "imported_skill")
        if sid.startswith("builtin-"):
            sid = f"custom-{sid}"
        target = primary_dir / f"{sid}.md"
        target.write_text(raw, encoding="utf-8")
        return {"ok": True, "skill_id": sid, "path": str(target), "skill": _parse_skill_frontmatter(sid, raw, _now_iso(), str(target))}

    if action == "delete":
        raw_sid = str(args.get("skill_id") or args.get("name") or "").strip()
        if not raw_sid:
            raise RuntimeError("`skill_id` is required")
        if raw_sid.startswith("builtin-"):
            raise RuntimeError("Built-in Studio skills cannot be deleted.")
        sid = _sanitize_slug(raw_sid, "")
        deleted_paths: List[str] = []
        for d in search_dirs:
            for cand in (d / f"{raw_sid}.md", d / f"{sid}.md"):
                if cand.is_file():
                    cand.unlink()
                    deleted_paths.append(str(cand))
        return {"ok": True, "deleted": raw_sid, "deletedPaths": deleted_paths}

    if action == "fetch_url":
        url = str(args.get("url") or "").strip()
        if not url:
            raise RuntimeError("`url` is required")
        return ctx.run_json_tool("tools/research_studio.py", ["fetch-skill-url", "--url", url], timeout=45)

    if action == "run":
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise RuntimeError("`prompt` is required")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(os.path.expanduser(str(args.get("out_dir") or (ctx.data_dir / "studio_runs" / f"skill_{stamp}")))).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        skill_file_arg = str(args.get("skill_file") or "").strip()
        skill_id_arg = str(args.get("skill_id") or "").strip()
        skill_path: Optional[Path] = None
        if skill_file_arg:
            skill_path = Path(os.path.expanduser(skill_file_arg)).resolve()
        elif skill_id_arg:
            matched = next((s for s in _collect_all_skills(ctx) if s["id"] == skill_id_arg or s["id"] == _sanitize_slug(skill_id_arg, "")), None)
            if matched is None:
                raise RuntimeError(f"Skill not found: {skill_id_arg}")
            if matched.get("path") and Path(matched["path"]).is_file():
                skill_path = Path(matched["path"]).resolve()
            else:
                mat = out_dir / f"{matched['id']}.md"
                mat.write_text(str(matched["content"]), encoding="utf-8")
                skill_path = mat
        if skill_path is None or not skill_path.is_file():
            raise RuntimeError(f"Skill file not found: {skill_path or skill_id_arg}")

        cli_args = [
            "run-skill",
            "--skill-file",
            str(skill_path),
            "--prompt",
            prompt,
            "--out-dir",
            str(out_dir),
            "--model",
            str(args.get("model") or "auto"),
        ]
        files = [os.path.abspath(os.path.expanduser(str(f))) for f in (args.get("files") or []) if str(f).strip()]
        if files:
            fj = out_dir / "files.json"
            fj.write_text(json.dumps(files, ensure_ascii=False), encoding="utf-8")
            cli_args.extend(["--files-json", str(fj)])
        if bool(args.get("include_memories", True)):
            with ctx.connect() as conn:
                _, _, prefs_md = _build_preferences_markdown(conn)
            if prefs_md.strip():
                pf = out_dir / "prefs.md"
                pf.write_text(prefs_md, encoding="utf-8")
                cli_args.extend(["--prefs-file", str(pf)])
        if bool(args.get("async_mode", False)):
            repo = ctx.require_repo()
            cmd = [ctx.python_bin, "-B", str(repo / "tools" / "research_studio.py"), *cli_args]
            meta = _spawn_background_job(ctx, "custom_skill", cmd, cwd=repo, run_dir=out_dir, use_plotting=False, title=skill_path.stem)
            return {"ok": True, "async": True, "job_id": meta["job_id"], "out_dir": str(out_dir)}
        return ctx.run_json_tool("tools/research_studio.py", cli_args, timeout=max(60, int(args.get("timeout") or 240)))

    raise RuntimeError(f"Unsupported custom_skills action: {action}")


def _normalize_bbox_arg(raw: Any) -> str:
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) != 4:
            raise RuntimeError("`bbox` string must contain 4 numbers: 'x,y,w,h'")
        vals = [float(p) for p in parts]
    elif isinstance(raw, (list, tuple)) and len(raw) == 4:
        vals = [float(v) for v in raw]
    elif isinstance(raw, dict):
        vals = [float(raw["x"]), float(raw["y"]), float(raw["w"]), float(raw["h"])]
    else:
        raise RuntimeError("`bbox` must be 'x,y,w,h', [x,y,w,h], or {x,y,w,h}")
    x, y, w, h = vals
    if not all(isinstance(v, float) and (v == v) and abs(v) <= 1.0 for v in vals):
        raise RuntimeError("`bbox` coordinates must be finite numbers in [0, 1]")
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.0 + 1e-9 or y + h > 1.0 + 1e-9:
        raise RuntimeError("`bbox` must satisfy 0 <= x,y, w>0, h>0, x+w <= 1, y+h <= 1")
    return ",".join(f"{min(1.0, max(0.0, v)):.4f}" for v in (x, y, w, h))


def tool_figure_studio(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "list").strip().lower()
    timeout = max(30, int(args.get("timeout") or 180))

    if action == "list":
        d = os.path.abspath(os.path.expanduser(str(args.get("dir_path") or "")))
        if not d:
            raise RuntimeError("`dir_path` is required")
        return ctx.run_json_tool("tools/figure_studio.py", ["list", "--dir", d], timeout=30)

    if action == "ensure_bundle":
        d = os.path.abspath(os.path.expanduser(str(args.get("dir_path") or "")))
        img = os.path.abspath(os.path.expanduser(str(args.get("image") or "")))
        cli = ["ensure-bundle", "--dir", d, "--image", img]
        if args.get("title"):
            cli.extend(["--title", str(args["title"])])
        if args.get("code_file"):
            cli.extend(["--code-file", os.path.abspath(os.path.expanduser(str(args["code_file"])))])
        if args.get("description"):
            cli.extend(["--description", str(args["description"])])
        return ctx.run_json_tool("tools/figure_studio.py", cli, timeout=30)

    if action == "create":
        out_d = os.path.abspath(os.path.expanduser(str(args.get("out_dir") or args.get("dir_path") or (ctx.data_dir / "figures"))))
        os.makedirs(out_d, exist_ok=True)
        cli = [
            "create",
            "--out-dir",
            out_d,
            "--mode",
            str(args.get("mode") or "data_code"),
            "--prompt",
            str(args.get("prompt") or ""),
            "--model",
            str(args.get("model") or "auto"),
            "--image-model",
            str(args.get("image_model") or "auto"),
        ]
        if args.get("title"):
            cli.extend(["--title", str(args["title"])])
        if args.get("data_file"):
            cli.extend(["--data-file", os.path.abspath(os.path.expanduser(str(args["data_file"])))])
        if args.get("ref_image"):
            cli.extend(["--ref-image", os.path.abspath(os.path.expanduser(str(args["ref_image"])))])
        return ctx.run_json_tool("tools/figure_studio.py", cli, timeout=timeout)

    if action == "revise":
        bundle = os.path.abspath(os.path.expanduser(str(args.get("bundle") or "")))
        cli = [
            "revise",
            "--bundle",
            bundle,
            "--instruction",
            str(args.get("instruction") or ""),
            "--model",
            str(args.get("model") or "auto"),
            "--image-model",
            str(args.get("image_model") or "auto"),
        ]
        if args.get("bbox") is not None and args.get("bbox") != "":
            cli.extend(["--bbox", _normalize_bbox_arg(args["bbox"])])
        if args.get("base_dir"):
            cli.extend(["--base-dir", os.path.abspath(os.path.expanduser(str(args["base_dir"])))])
        return ctx.run_json_tool("tools/figure_studio.py", cli, timeout=timeout)

    if action == "restore":
        bundle = os.path.abspath(os.path.expanduser(str(args.get("bundle") or "")))
        cli = ["restore", "--bundle", bundle, "--version", str(args.get("version") or "")]
        if args.get("base_dir"):
            cli.extend(["--base-dir", os.path.abspath(os.path.expanduser(str(args["base_dir"])))])
        return ctx.run_json_tool("tools/figure_studio.py", cli, timeout=30)

    raise RuntimeError(f"Unsupported figure_studio action: {action}")


def _save_library_item_row(conn: sqlite3.Connection, item: Dict[str, Any]) -> Dict[str, Any]:
    title = str(item.get("title") or "").strip()
    if not title:
        raise RuntimeError("Literature item `title` cannot be empty")
    iid = str(item.get("id") or "").strip() or str(uuid.uuid4())
    now = _now_iso()
    existing = conn.execute("SELECT created_at FROM library_items WHERE id = ?", (iid,)).fetchone()
    created_at = str(existing["created_at"]) if existing else now
    claims_json = json.dumps(item.get("key_claims") or [], ensure_ascii=False)
    tags_json = json.dumps(item.get("tags") or [], ensure_ascii=False)
    year_val = item.get("year")
    try:
        year_int = int(year_val) if year_val not in (None, "") else None
    except (ValueError, TypeError):
        year_int = None

    conn.execute(
        """
        INSERT OR REPLACE INTO library_items (
            id, project_id, folder, title, authors, year, venue, doi, arxiv_id, url,
            pdf_path, bibtex, gb7714, abstract_text, ai_notes, key_claims_json, tags_json,
            read_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            iid,
            item.get("project_id"),
            str(item.get("folder") or "默认文献夹").strip(),
            title,
            str(item.get("authors") or "").strip(),
            year_int,
            str(item.get("venue") or "").strip(),
            str(item.get("doi") or "").strip(),
            str(item.get("arxiv_id") or "").strip(),
            str(item.get("url") or "").strip(),
            str(item.get("pdf_path") or "").strip(),
            str(item.get("bibtex") or "").strip(),
            str(item.get("gb7714") or "").strip(),
            str(item.get("abstract_text") or "").strip(),
            str(item.get("ai_notes") or "").strip(),
            claims_json,
            tags_json,
            str(item.get("read_status") or "unread").strip(),
            created_at,
            now,
        ),
    )
    saved = conn.execute("SELECT * FROM library_items WHERE id = ?", (iid,)).fetchone()
    assert saved is not None
    return {
        **dict(saved),
        "key_claims": json.loads(saved["key_claims_json"] or "[]"),
        "tags": json.loads(saved["tags_json"] or "[]"),
    }


def _load_library_rows(conn: sqlite3.Connection, item_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM library_items ORDER BY updated_at DESC").fetchall()
    want = set(str(x) for x in (item_ids or []) if str(x).strip())
    out = []
    for r in rows:
        if want and r["id"] not in want:
            continue
        out.append({
            **dict(r),
            "key_claims": json.loads(r["key_claims_json"] or "[]"),
            "tags": json.loads(r["tags_json"] or "[]"),
        })
    return out


def tool_literature_library(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "list").strip().lower()
    timeout = max(30, int(args.get("timeout") or 180))

    if action == "search_online":
        q = str(args.get("query") or "").strip()
        if not q:
            raise RuntimeError("`query` is required")
        cli = ["search-online", "--query", q, "--limit", str(int(args.get("limit") or 15)), "--model", str(args.get("model") or "auto")]
        if args.get("expand"):
            cli.append("--expand")
        return ctx.run_json_tool("tools/library_tools.py", cli, timeout=timeout)

    if action == "import_doi":
        doi = str(args.get("doi") or "").strip()
        if not doi:
            raise RuntimeError("`doi` is required")
        res = ctx.run_json_tool("tools/library_tools.py", ["import-doi", "--doi", doi], timeout=60)
        if args.get("save_to_library", True) and isinstance(res.get("item"), dict):
            with ctx.connect() as conn:
                res["savedItem"] = _save_library_item_row(conn, res["item"])
        return res

    if action == "import_pdf":
        pdf = os.path.abspath(os.path.expanduser(str(args.get("pdf_path") or "")))
        if not pdf or not os.path.isfile(pdf):
            raise RuntimeError(f"PDF not found: {pdf}")
        cli = ["import-pdf", "--pdf", pdf, "--model", str(args.get("model") or "auto")]
        if args.get("no_llm"):
            cli.append("--no-llm")
        res = ctx.run_json_tool("tools/library_tools.py", cli, timeout=timeout)
        if args.get("save_to_library", True) and isinstance(res.get("item"), dict):
            with ctx.connect() as conn:
                res["savedItem"] = _save_library_item_row(conn, res["item"])
        return res

    if action == "list":
        folder_filter = str(args.get("folder") or "").strip()
        offset = max(0, int(args.get("offset") or 0))
        limit = max(1, min(100, int(args.get("limit") or 25)))
        with ctx.connect() as conn:
            items = _load_library_rows(conn)
        if folder_filter:
            items = [it for it in items if str(it.get("folder") or "") == folder_filter]
        total = len(items)
        page = items[offset : offset + limit]
        next_offset = (offset + limit) if (offset + limit) < total else None
        return {
            "ok": True,
            "total": total,
            "count": len(page),
            "offset": offset,
            "limit": limit,
            "nextOffset": next_offset,
            "items": page,
        }

    if action == "save":
        item = args.get("item")
        if not isinstance(item, dict):
            raise RuntimeError("`item` object is required for action='save'")
        with ctx.connect() as conn:
            saved = _save_library_item_row(conn, item)
        return {"ok": True, "item": saved}

    if action == "delete":
        iid = str(args.get("item_id") or "").strip()
        if not iid:
            raise RuntimeError("`item_id` is required")
        with ctx.connect() as conn:
            conn.execute("DELETE FROM library_items WHERE id = ?", (iid,))
        return {"ok": True, "deleted": iid}

    if action in ("build_graph", "export_bib"):
        inline_items = args.get("items")
        if isinstance(inline_items, list) and inline_items:
            items = inline_items
        else:
            with ctx.connect() as conn:
                items = _load_library_rows(conn, args.get("item_ids"))
        if not items:
            raise RuntimeError("No library items found to process. Import papers first or pass `items`.")
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as tf:
            json.dump(items, tf, ensure_ascii=False)
            tmp_path = tf.name
        try:
            if action == "build_graph":
                return ctx.run_json_tool(
                    "tools/library_tools.py",
                    ["build-graph", "--input-json", tmp_path, "--model", str(args.get("model") or "auto")],
                    timeout=timeout,
                )
            return ctx.run_json_tool(
                "tools/library_tools.py",
                ["export-bib", "--input-json", tmp_path, "--format", str(args.get("format") or "bibtex")],
                timeout=30,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    raise RuntimeError(f"Unsupported literature_library action: {action}")


def tool_latex_copilot(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "trace_citations").strip().lower()
    if action == "edit":
        fp = os.path.abspath(os.path.expanduser(str(args.get("file_path") or "")))
        inst = str(args.get("instruction") or "").strip()
        if not fp or not inst:
            raise RuntimeError("`file_path` and `instruction` are required for action='edit'")
        cli = ["edit", "--file", fp, "--instruction", inst, "--model", str(args.get("model") or "auto")]
        if args.get("selection_start") is not None:
            cli.extend(["--selection-start", str(int(args["selection_start"]))])
        if args.get("selection_end") is not None:
            cli.extend(["--selection-end", str(int(args["selection_end"]))])
        if args.get("error_log"):
            cli.extend(["--error-log", str(args["error_log"])])
        return ctx.run_json_tool("tools/latex_copilot.py", cli, timeout=180)

    if action == "trace_citations":
        rd = os.path.abspath(os.path.expanduser(str(args.get("run_dir") or "")))
        if not rd:
            raise RuntimeError("`run_dir` is required")
        cli = ["trace-citations", "--run-dir", rd]
        if args.get("tex_file"):
            cli.extend(["--tex-file", str(args["tex_file"])])
        return ctx.run_json_tool("tools/latex_copilot.py", cli, timeout=60)

    if action == "polish_abstract":
        mode = str(args.get("mode") or "diagnose").strip().lower()
        cli = ["polish-abstract", "--mode", mode, "--model", str(args.get("model") or "auto")]
        if args.get("text"):
            cli.extend(["--text", str(args["text"])])
        if args.get("intro_text"):
            cli.extend(["--intro-text", str(args["intro_text"])])
        if args.get("file_path"):
            cli.extend(["--file", os.path.abspath(os.path.expanduser(str(args["file_path"])))])
        if args.get("run_dir"):
            cli.extend(["--run-dir", os.path.abspath(os.path.expanduser(str(args["run_dir"])))])
        return ctx.run_json_tool("tools/latex_copilot.py", cli, timeout=180)

    raise RuntimeError(f"Unsupported latex_copilot action: {action}")


def _find_tex_source_and_main(run_dir: Path, tex_file: str = "") -> Tuple[Path, Path]:
    src = None
    for sub in ("content_refinement_workdir", "latex_writeup"):
        cand = run_dir / sub
        if cand.is_dir() and any(cand.glob("*.tex")):
            src = cand
            break
    if src is None and any(run_dir.glob("*.tex")):
        src = run_dir
    if src is None:
        raise RuntimeError(f"No .tex files found inside run_dir: {run_dir}")

    if tex_file.strip():
        chosen = (src / tex_file.strip()).resolve()
        if chosen.is_file():
            return src, chosen
        chosen2 = (run_dir / tex_file.strip()).resolve()
        if chosen2.is_file():
            return chosen2.parent, chosen2
        raise RuntimeError(f"Requested TeX file not found: {tex_file}")

    docs = []
    for p in sorted(src.glob("*.tex")):
        if "\\documentclass" in p.read_text(encoding="utf-8", errors="replace"):
            docs.append(p)
    if not docs:
        docs = sorted(src.glob("*.tex"))
    final_match = [d for d in docs if d.name == "final_refined_paper.tex"]
    if final_match:
        return src, final_match[0]
    refined: List[Tuple[int, float, Path]] = []
    for d in docs:
        m = re.match(r"^refined_paper_v(\d+)\.tex$", d.name)
        if m:
            refined.append((int(m.group(1)), d.stat().st_mtime, d))
    if refined:
        refined.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return src, refined[0][2]
    compiled = [d for d in docs if d.with_suffix(".pdf").is_file()]
    non_tpl_compiled = [d for d in compiled if d.name != "template.tex"]
    if non_tpl_compiled:
        non_tpl_compiled.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return src, non_tpl_compiled[0]
    if compiled:
        compiled.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return src, compiled[0]
    for pref in ("initial_draft.tex", "raw_draft_paper.tex", "template.tex"):
        match = [d for d in docs if d.name == pref]
        if match:
            return src, match[0]
    docs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return src, docs[0]


def _first_synctex_record(out: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    in_result = False
    seen_output = False
    for raw in out.splitlines():
        line = raw.rstrip("\r")
        if line == "SyncTeX result begin":
            in_result = True
            continue
        if not in_result:
            continue
        if line == "SyncTeX result end":
            break
        if line.startswith("Output:"):
            if seen_output:
                break
            seen_output = True
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            fields.setdefault(k, v)
    return fields


def _all_synctex_records(out: str) -> List[Dict[str, str]]:
    records: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    in_result = False
    for raw in out.splitlines():
        line = raw.rstrip("\r")
        if line == "SyncTeX result begin":
            in_result = True
            current = {}
            continue
        if not in_result:
            continue
        if line == "SyncTeX result end":
            if current:
                records.append(current)
                current = {}
            break
        if line.startswith("Output:"):
            if current:
                records.append(current)
            current = {"Output": line.split(":", 1)[1]}
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            current.setdefault(k, v)
    if current:
        records.append(current)
    return records


def _has_synctex_file(pdf_path: Path) -> bool:
    return pdf_path.is_file() and (
        pdf_path.with_suffix(".synctex.gz").is_file()
        or pdf_path.with_suffix(".synctex").is_file()
    )


def _resolve_synctex_pdf(run_dir: Path, preview_pdf: Path, fallback_pdf: Path, explicit_pdf: str) -> Path:
    raw = str(explicit_pdf or "").strip()
    if raw:
        cand = Path(os.path.expanduser(raw)).resolve()
        if cand.is_file() and cand.suffix.lower() == ".pdf" and (cand == run_dir or run_dir in cand.parents):
            return cand
    return preview_pdf if preview_pdf.is_file() else fallback_pdf


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
_MAX_EDIT_BYTES = 16 * 1024 * 1024


def _source_needs_unicode_engine(source_text: str) -> bool:
    if "ctex" in source_text.lower():
        return True
    return bool(_CJK_RE.search(source_text))


def _project_needs_unicode_engine(src_dir: Path, main_tex: Path) -> bool:
    try:
        main_txt = main_tex.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if _source_needs_unicode_engine(main_txt):
        return True
    for p in sorted(src_dir.rglob("*.tex"))[:40]:
        if p == main_tex or p.is_symlink():
            continue
        try:
            if _source_needs_unicode_engine(p.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def _run_is_active(ctx: RuntimeContext, run_dir: Path) -> bool:
    target = str(run_dir.resolve())
    try:
        with ctx.connect() as conn:
            rows = conn.execute("SELECT run_dir FROM runs WHERE LOWER(status) = 'running'").fetchall()
        for r in rows:
            rd = str(r["run_dir"] or "").strip()
            if rd and str(Path(rd).resolve()) == target:
                return True
    except sqlite3.Error:
        return False
    return False


def _file_mtime_ms(p: Path) -> int:
    return int(p.stat().st_mtime_ns // 1_000_000)


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".po-edit.tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        try:
            if target.exists():
                os.chmod(tmp_name, stat.S_IMODE(target.stat().st_mode))
        except OSError:
            pass
        with os.fdopen(fd, "wb") as wf:
            wf.write(data)
            wf.flush()
            os.fsync(wf.fileno())
        os.replace(tmp_name, target)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def _resolve_editable_file(src_dir: Path, requested: str, main_tex: Path) -> Tuple[str, Path]:
    rel_raw = (requested or "").strip() or main_tex.name
    rel_norm = rel_raw.replace("\\", "/").lstrip("/")
    cand = (src_dir / rel_norm).resolve()
    if not (cand == src_dir or src_dir in cand.parents):
        raise RuntimeError("Refused editable path outside TeX source directory.")
    if cand.is_symlink() or not cand.is_file():
        raise RuntimeError(f"Editable TeX/Bib file not found: {rel_norm}")
    if cand.suffix.lower() not in (".tex", ".bib"):
        raise RuntimeError("Only .tex and .bib files can be opened or edited.")
    return cand.relative_to(src_dir).as_posix(), cand


def _list_editable_files(src_dir: Path, main_tex: Path) -> List[str]:
    main_rel = main_tex.relative_to(src_dir).as_posix()
    others: List[str] = []
    for root, dirs, files in os.walk(src_dir, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in ("__pycache__", "__macosx"))
        rp = Path(root)
        for fn in sorted(files):
            if fn.startswith("."):
                continue
            fp = rp / fn
            if fp.is_symlink() or not fp.is_file():
                continue
            if fp.suffix.lower() not in (".tex", ".bib"):
                continue
            rel = fp.relative_to(src_dir).as_posix()
            if rel != main_rel:
                others.append(rel)
    return [main_rel, *sorted(others)]


def _parse_tex_issues(log_text: str, blg_text: str = "") -> Tuple[List[Dict[str, Any]], int]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    file_line_re = re.compile(r"^(.+\.(?:tex|bib|sty|cls)):(\d+):\s*(.+)$")
    for line in log_text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = file_line_re.match(s)
        if m:
            errors.append({"file": Path(m.group(1)).name, "line": int(m.group(2)), "severity": "error", "message": m.group(3)[:300]})
        elif s.startswith("! "):
            errors.append({"file": None, "line": None, "severity": "error", "message": s[2:].strip()[:300]})
        elif s.startswith("LaTeX Warning:") or s.startswith("LaTeX Font Warning:") or (
            (s.startswith("Package ") or s.startswith("Class ")) and " Warning: " in s
        ):
            warnings.append({"file": None, "line": None, "severity": "warning", "message": s[:300]})
    if blg_text:
        blg_re = re.compile(r"^(.*?)---line\s+(\d+)\s+of\s+file\s+(.+)$")
        for line in blg_text.splitlines():
            m = blg_re.match(line.strip())
            if m:
                errors.append({"file": m.group(3).strip(), "line": int(m.group(2)), "severity": "error", "message": f"BibTeX: {m.group(1).strip()}"})
    combined = (errors + warnings)[:40]
    return combined, len(warnings)


def tool_latex_workbench(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "compile").strip().lower()
    run_dir = Path(os.path.expanduser(str(args.get("run_dir") or ""))).resolve()
    if not run_dir.is_dir():
        raise RuntimeError(f"Run directory not found: {run_dir}")

    src_dir, main_tex = _find_tex_source_and_main(run_dir, str(args.get("tex_file") or ""))
    rel_src = src_dir.relative_to(run_dir) if src_dir != run_dir else Path(".")
    backup_root = run_dir / ".edit_backup" / rel_src
    preview_dir = (run_dir / ".live_preview" / rel_src).resolve()
    stem = main_tex.stem
    preview_pdf = preview_dir / f"{stem}.pdf"
    fallback_pdf = main_tex.with_suffix(".pdf")

    if action == "open":
        editable = _list_editable_files(src_dir, main_tex)
        backed_up = [f for f in editable if (backup_root / f).is_file()]
        existing_pdf = preview_pdf if preview_pdf.is_file() else (fallback_pdf if fallback_pdf.is_file() else None)
        return {
            "ok": True,
            "runDir": str(run_dir),
            "sourceDir": str(src_dir),
            "sourceRel": rel_src.as_posix(),
            "mainFile": main_tex.relative_to(src_dir).as_posix(),
            "files": editable,
            "backedUp": backed_up,
            "runActive": _run_is_active(ctx, run_dir),
            "previewPdfPath": str(existing_pdf) if existing_pdf else "",
            "hasSyncTeX": bool(existing_pdf and _has_synctex_file(existing_pdf)),
        }

    if action == "read":
        rel_file, target = _resolve_editable_file(src_dir, str(args.get("tex_file") or args.get("file") or ""), main_tex)
        if target.stat().st_size > _MAX_EDIT_BYTES:
            raise RuntimeError("TeX/Bib file exceeds 16 MiB edit limit.")
        mtime_ms = _file_mtime_ms(target)
        raw = target.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"File is not valid UTF-8: {exc}") from exc
        if target.suffix.lower() == ".tex":
            pdf_candidates = [preview_dir / f"{target.stem}.pdf", target.with_suffix(".pdf"), preview_pdf, fallback_pdf]
        else:
            pdf_candidates = [preview_pdf, fallback_pdf]
        found_pdf = next((cand for cand in pdf_candidates if cand.is_file()), None)
        return {
            "ok": True,
            "file": rel_file,
            "content": content,
            "mtimeMs": mtime_ms,
            "hasBackup": (backup_root / rel_file).is_file(),
            "previewPdfPath": str(found_pdf) if found_pdf else "",
            "hasSyncTeX": bool(found_pdf and _has_synctex_file(found_pdf)),
        }

    if action == "save":
        if _run_is_active(ctx, run_dir):
            raise RuntimeError("Cannot save while paper pipeline run is active (status='running').")
        if "content" not in args or args["content"] is None:
            raise RuntimeError("`content` is required for action='save'")
        if args.get("expected_mtime_ms") is None:
            raise RuntimeError("Read the file first and pass `expected_mtime_ms` for conflict protection.")
        expected_mtime = int(args["expected_mtime_ms"])
        new_text = str(args["content"])
        new_bytes = new_text.encode("utf-8")
        if len(new_bytes) > _MAX_EDIT_BYTES:
            raise RuntimeError("Content exceeds 16 MiB edit limit.")
        rel_file, target = _resolve_editable_file(src_dir, str(args.get("tex_file") or args.get("file") or ""), main_tex)
        current_mtime = _file_mtime_ms(target)
        if expected_mtime != current_mtime:
            raise RuntimeError(
                f"Conflict: '{rel_file}' changed on disk (expected mtimeMs={expected_mtime}, current={current_mtime}). Re-read before saving."
            )
        backup_file = (backup_root / rel_file).resolve()
        if run_dir not in backup_file.parents:
            raise RuntimeError("Unsafe backup path outside run_dir.")
        old_bytes = target.read_bytes()
        if old_bytes == new_bytes:
            return {
                "ok": True,
                "file": rel_file,
                "mtimeMs": current_mtime,
                "hasBackup": backup_file.is_file(),
                "unchanged": True,
            }
        if not backup_file.exists():
            _atomic_write_bytes(backup_file, old_bytes)
        _atomic_write_bytes(target, new_bytes)
        return {
            "ok": True,
            "file": rel_file,
            "mtimeMs": _file_mtime_ms(target),
            "hasBackup": True,
            "unchanged": False,
        }

    if action == "restore":
        if _run_is_active(ctx, run_dir):
            raise RuntimeError("Cannot restore while paper pipeline run is active (status='running').")
        rel_file, target = _resolve_editable_file(src_dir, str(args.get("tex_file") or args.get("file") or ""), main_tex)
        backup_file = (backup_root / rel_file).resolve()
        if run_dir not in backup_file.parents or backup_file.is_symlink() or not backup_file.is_file():
            raise RuntimeError(f"No original backup found in .edit_backup/ for '{rel_file}'.")
        raw = backup_file.read_bytes()
        content = raw.decode("utf-8")
        _atomic_write_bytes(target, raw)
        return {
            "ok": True,
            "file": rel_file,
            "content": content,
            "mtimeMs": _file_mtime_ms(target),
            "hasBackup": True,
        }

    if action == "compile":
        latexmk = _which_tool("latexmk")
        if not latexmk:
            raise RuntimeError("`latexmk` not found in PATH; install MacTeX / TeX Live.")
        xelatex_bin = _which_tool("xelatex")
        needs_unicode = _project_needs_unicode_engine(src_dir, main_tex)
        engine = "xelatex" if (needs_unicode and xelatex_bin) else "pdflatex"
        engine_flag = "-pdfxe" if engine == "xelatex" else "-pdf"
        preview_dir.mkdir(parents=True, exist_ok=True)
        env = ctx.subprocess_env({"max_print_line": "10000", "error_line": "254", "half_error_line": "238"})
        proc = subprocess.run(
            [
                latexmk,
                engine_flag,
                "-interaction=nonstopmode",
                "-file-line-error",
                "-synctex=1",
                "-f",
                f"-outdir={preview_dir}",
                main_tex.name,
            ],
            cwd=str(src_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(60, int(args.get("timeout") or 180)),
        )
        log_file = preview_dir / f"{stem}.log"
        blg_file = preview_dir / f"{stem}.blg"
        log_full = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
        blg_full = blg_file.read_text(encoding="utf-8", errors="replace") if blg_file.is_file() else ""
        log_tail = "\n".join(log_full.splitlines()[-60:]) if log_full else (proc.stderr or proc.stdout or "")[-3000:]
        issues, warn_count = _parse_tex_issues(log_full, blg_full)
        if needs_unicode and not xelatex_bin:
            issues.insert(0, {"file": main_tex.name, "line": None, "severity": "warning", "message": "CJK/ctex source detected but `xelatex` not found in PATH; fell back to pdflatex."})
        has_synctex = any((preview_dir / f"{stem}.{ext}").is_file() for ext in ("synctex.gz", "synctex"))
        return {
            "ok": proc.returncode == 0 and preview_pdf.is_file(),
            "exitCode": proc.returncode,
            "engine": engine,
            "cjkDetected": needs_unicode,
            "sourceDir": str(src_dir),
            "mainTex": main_tex.name,
            "previewDir": str(preview_dir),
            "pdfPath": str(preview_pdf) if preview_pdf.is_file() else "",
            "hasSyncTeX": has_synctex,
            "warnings": warn_count,
            "issues": _redact_obj(issues, ctx.secrets),
            "logTail": _redact_text(log_tail, ctx.secrets),
        }

    if action == "sync_view":
        synctex = _which_tool("synctex")
        if not synctex:
            raise RuntimeError("`synctex` binary not found in PATH.")
        target_pdf = _resolve_synctex_pdf(run_dir, preview_pdf, fallback_pdf, str(args.get("pdf_path") or ""))
        if not target_pdf.is_file():
            raise RuntimeError("Preview PDF not found; compile first with action='compile'.")
        target_tex = (
            _resolve_editable_file(src_dir, str(args.get("tex_file") or ""), main_tex)[1]
            if args.get("tex_file")
            else main_tex
        )
        line = max(1, int(args.get("line") or 1))
        col = max(1, int(args.get("column") or 1))
        raw_end = int(args.get("end_line") or 0)
        end_line = max(line, raw_end) if raw_end > 0 else line
        if end_line <= line:
            query_lines = [line]
        elif end_line - line <= 4:
            query_lines = list(range(line, end_line + 1))
        else:
            query_lines = sorted(
                {line, line + (end_line - line) // 3, line + (2 * (end_line - line)) // 3, end_line}
            )

        first_fields: Dict[str, str] = {}
        target_page: Optional[int] = None
        boxes: List[Dict[str, Any]] = []
        for qline in query_lines:
            qcol = col if qline == line else 1
            proc = subprocess.run(
                [synctex, "view", "-i", f"{qline}:{qcol}:{target_tex}", "-o", str(target_pdf)],
                cwd=str(target_pdf.parent),
                env=ctx.subprocess_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            recs = _all_synctex_records(proc.stdout)
            if not first_fields:
                for rec in recs:
                    if "Page" in rec and str(rec["Page"]).strip().isdigit():
                        first_fields = rec
                        target_page = int(str(rec["Page"]).strip())
                        break
            if target_page is None:
                continue
            for rec in recs:
                pg_raw = str(rec.get("Page", "")).strip()
                if not pg_raw.isdigit() or int(pg_raw) != target_page:
                    continue
                try:
                    h = float(rec.get("h") or rec.get("x") or 0.0)
                    v = float(rec.get("v") or rec.get("y") or 0.0)
                    w = max(float(rec.get("W") or 0.0), 18.0)
                    h_box = max(float(rec.get("H") or 0.0), 10.0)
                except (TypeError, ValueError):
                    continue
                top = max(0.0, v - h_box)
                boxes.append({
                    "page": target_page,
                    "h": round(h, 2),
                    "v": round(v, 2),
                    "top": round(top, 2),
                    "W": round(w, 2),
                    "H": round(h_box, 2),
                })

        highlight: Optional[Dict[str, Any]] = None
        if boxes and target_page is not None:
            min_x = min(b["h"] for b in boxes)
            max_x = max(b["h"] + b["W"] for b in boxes)
            min_y = min(b["top"] for b in boxes)
            max_y = max(b["v"] for b in boxes)
            highlight = {
                "page": target_page,
                "h": round(min_x, 2),
                "v": round(max_y, 2),
                "top": round(min_y, 2),
                "W": round(max(18.0, max_x - min_x), 2),
                "H": round(max(10.0, max_y - min_y), 2),
            }
        return {
            "ok": bool(first_fields and target_page is not None),
            "mainTex": target_tex.name,
            "pdfPath": str(target_pdf),
            "page": target_page,
            "startLine": line,
            "endLine": end_line,
            "highlight": highlight,
            "boxes": boxes[:24],
            "synctex": first_fields,
        }

    if action == "sync_edit":
        synctex = _which_tool("synctex")
        if not synctex:
            raise RuntimeError("`synctex` binary not found in PATH.")
        target_pdf = _resolve_synctex_pdf(run_dir, preview_pdf, fallback_pdf, str(args.get("pdf_path") or ""))
        if not target_pdf.is_file():
            raise RuntimeError("Preview PDF not found; compile first with action='compile'.")
        page = max(1, int(args.get("page") or 1))
        x = float(args.get("x") or 0.0)
        y = float(args.get("y") or 0.0)
        proc = subprocess.run(
            [synctex, "edit", "-o", f"{page}:{x}:{y}:{target_pdf}"],
            cwd=str(target_pdf.parent),
            env=ctx.subprocess_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        fields = _first_synctex_record(proc.stdout)
        raw_input = str(fields.get("Input") or "").strip()
        rel_file = ""
        if raw_input:
            try:
                resolved_in = Path(raw_input).resolve()
                if resolved_in.is_file() and (resolved_in == src_dir or src_dir in resolved_in.parents):
                    rel_file = resolved_in.relative_to(src_dir).as_posix()
                elif (src_dir / Path(raw_input).name).is_file():
                    rel_file = Path(raw_input).name
            except OSError:
                if (src_dir / Path(raw_input).name).is_file():
                    rel_file = Path(raw_input).name
        raw_line = str(fields.get("Line") or "").strip()
        line_num = int(raw_line) if raw_line.lstrip("-").isdigit() else None
        return {
            "ok": "Input" in fields,
            "pdfPath": str(target_pdf),
            "relFile": rel_file,
            "line": line_num,
            "synctex": fields,
        }

    if action == "export_overleaf":
        dest = str(args.get("out_zip") or "").strip()
        if not dest:
            exports_dir = ctx.data_dir / "exports"
            dest_path = _unique_path(exports_dir, f"{run_dir.name}_overleaf", ".zip")
        else:
            dest_path = Path(os.path.expanduser(dest)).resolve()
            if dest_path.suffix.lower() != ".zip":
                dest_path = dest_path.with_suffix(".zip")
            if dest_path.exists() and not bool(args.get("allow_overwrite", False)):
                raise RuntimeError(f"Output zip already exists (pass allow_overwrite=true to replace): {dest_path}")
            dest_path.parent.mkdir(parents=True, exist_ok=True)
        if run_dir == dest_path.parent or run_dir in dest_path.parents:
            raise RuntimeError("Cannot write Overleaf zip inside run_dir.")

        source_exts = {".tex", ".bib", ".bst", ".sty", ".cls", ".clo", ".cfg", ".def", ".ldf", ".fd", ".bbx", ".cbx", ".lbx", ".dbx", ".ist"}
        graphic_exts = {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".ps", ".svg", ".gif", ".tif", ".tiff", ".bmp"}
        skip_dirs = {"pdf_screenshots", "peer_reviews", "__macosx", "__pycache__"}

        cited_bib_path = run_dir / "references_cited.bib"
        cited_swap_target: Optional[str] = None
        bib_candidates = [p for p in src_dir.rglob("*.bib") if p.is_file() and not p.name.startswith(".")]
        if cited_bib_path.is_file() and len(bib_candidates) == 1:
            cited_swap_target = bib_candidates[0].relative_to(src_dir).as_posix()

        files_added: List[str] = []
        with zipfile.ZipFile(dest_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(src_dir, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in skip_dirs)
                root_p = Path(root)
                tex_stems = {Path(f).stem for f in files if f.lower().endswith(".tex")}
                depth = len(root_p.relative_to(src_dir).parts)
                for fn in sorted(files):
                    if fn.startswith("."):
                        continue
                    fp = root_p / fn
                    if fp.is_symlink() or not fp.is_file():
                        continue
                    ext = fp.suffix.lower()
                    if ext not in source_exts and ext not in graphic_exts:
                        continue
                    if ext == ".pdf" and fp.stem in tex_stems:
                        continue
                    if depth == 0 and ext == ".tex" and "\\documentclass" in fp.read_text(encoding="utf-8", errors="replace"):
                        if fp.name != main_tex.name:
                            continue
                    arcname = fp.relative_to(src_dir).as_posix()
                    src_to_write = cited_bib_path if (cited_swap_target and arcname == cited_swap_target) else fp
                    zf.write(src_to_write, arcname=arcname)
                    files_added.append(arcname)

        return {
            "ok": True,
            "zipPath": str(dest_path),
            "bytes": dest_path.stat().st_size,
            "mainTex": main_tex.name,
            "citedBibSwapped": bool(cited_swap_target),
            "fileCount": len(files_added),
            "files": files_added,
        }

    raise RuntimeError(f"Unsupported latex_workbench action: {action}")


def tool_submission_audit(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "manuscript-audit").strip().lower().replace("_", "-")
    run_dir = os.path.abspath(os.path.expanduser(str(args.get("run_dir") or "")))
    proj_dir = os.path.abspath(os.path.expanduser(str(args.get("project_dir") or "")))

    if action == "lean-audit":
        target = proj_dir or run_dir
        if not target:
            raise RuntimeError("`project_dir` is required for lean-audit")
        return ctx.run_json_tool("tools/submission_tools.py", ["lean-audit", target], timeout=180)

    if action == "diff":
        old_t = os.path.abspath(os.path.expanduser(str(args.get("old_tex") or "")))
        new_t = os.path.abspath(os.path.expanduser(str(args.get("new_tex") or "")))
        out_p = os.path.abspath(os.path.expanduser(str(args.get("out_path") or (ctx.data_dir / "exports" / "latexdiff.pdf"))))
        cli = ["diff", old_t, new_t, "--out", out_p]
        if args.get("only_changed_pages"):
            cli.append("--only-changed-pages")
        return ctx.run_json_tool("tools/submission_tools.py", cli, timeout=240)

    if not run_dir or not os.path.isdir(run_dir):
        raise RuntimeError(f"`run_dir` is required and must exist: {run_dir}")

    if action == "arxiv":
        cli = ["arxiv", run_dir]
        if args.get("out_path"):
            cli.extend(["--out", os.path.abspath(os.path.expanduser(str(args["out_path"])))])
        if args.get("keep_comments"):
            cli.append("--keep-comments")
        if args.get("merge_inputs"):
            cli.append("--merge-inputs")
        return ctx.run_json_tool("tools/submission_tools.py", cli, timeout=180)

    if action in ("blind", "versions", "manuscript-audit", "author-report"):
        return ctx.run_json_tool("tools/submission_tools.py", [action, run_dir], timeout=120)

    if action == "diagnose":
        cli = ["diagnose", run_dir]
        if args.get("find_packages"):
            cli.append("--find-packages")
        return ctx.run_json_tool("tools/submission_tools.py", cli, timeout=180)

    if action == "cite-online":
        cli = ["cite-online", run_dir, "--timeout", str(float(args.get("timeout_sec") or 20.0))]
        return ctx.run_json_tool("tools/submission_tools.py", cli, timeout=180)

    raise RuntimeError(f"Unsupported submission_audit action: {action}")


def tool_export_document(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "export").strip().lower()
    if action == "status":
        pdf_dir = _clean_env("PO_DEFAULT_PDF_DIR")
        html_dir = _clean_env("PO_DEFAULT_HTML_DIR")
        try:
            gd = ctx.run_json_tool("tools/doc_exporter.py", ["google-drive-status"], timeout=20)
        except Exception:
            gd = {}
        return {
            "ok": True,
            **gd,
            "defaultPdfDir": pdf_dir if (pdf_dir and os.path.isdir(pdf_dir)) else "",
            "defaultHtmlDir": html_dir if (html_dir and os.path.isdir(html_dir)) else "",
            "pandoc": _which_tool("pandoc") or "",
        }

    in_md_arg = str(args.get("input_md") or "").strip()
    md_content = str(args.get("markdown_content") or "")
    tmp_md: Optional[Path] = None
    if in_md_arg:
        in_md = Path(os.path.expanduser(in_md_arg)).resolve()
        if not in_md.is_file():
            raise RuntimeError(f"Markdown input file not found: {in_md}")
        md_text = in_md.read_text(encoding="utf-8", errors="replace")
    elif md_content.strip():
        fd, tmp_p = tempfile.mkstemp(prefix="po-export-", suffix=".md")
        os.close(fd)
        tmp_md = Path(tmp_p)
        tmp_md.write_text(md_content, encoding="utf-8")
        in_md = tmp_md
        md_text = md_content
    else:
        raise RuntimeError("Provide either `input_md` (path) or `markdown_content` (text).")

    try:
        title = str(args.get("title") or "").strip()
        if not title:
            m = re.search(r"^\s*#\s+(.+)$", md_text, flags=re.MULTILINE)
            title = re.sub(r"[`*_]+", "", m.group(1)).strip()[:100] if m else "PaperOrchestra 研究报告"

        if action == "export_google_drive":
            fmt = str(args.get("format") or "docx").strip().lower()
            if fmt not in ("docx", "pdf"):
                raise RuntimeError("Google Drive export supports `docx` or `pdf`")
            cli = ["export-google-drive", "--input-md", str(in_md), "--format", fmt, "--title", title]
            if args.get("drive_dir"):
                cli.extend(["--drive-dir", os.path.expanduser(str(args["drive_dir"]))])
            return ctx.run_json_tool("tools/doc_exporter.py", cli, timeout=120)

        if action != "export":
            raise RuntimeError(f"Unsupported export action: {action}")

        fmt = str(args.get("format") or "html").strip().lower()
        if fmt not in ("html", "docx", "pdf"):
            raise RuntimeError("`format` must be html, docx, or pdf")

        out_arg = str(args.get("out_path") or "").strip()
        if out_arg:
            out_path = Path(os.path.expanduser(out_arg)).resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            env_dir = ""
            if fmt == "html":
                env_dir = _clean_env("PO_DEFAULT_HTML_DIR")
            elif fmt == "pdf":
                env_dir = _clean_env("PO_DEFAULT_PDF_DIR")
            base_dir = Path(env_dir) if (env_dir and os.path.isdir(env_dir)) else (ctx.data_dir / "exports")
            out_path = _unique_path(base_dir, title, f".{fmt}")

        if fmt == "html" and bool(args.get("interactive_html", True)):
            html_doc, engine = _render_interactive_html(md_text, title)
            out_path.write_text(html_doc, encoding="utf-8")
            return {"ok": True, "format": "html", "path": str(out_path), "engine": engine, "bytes": out_path.stat().st_size}

        return ctx.run_json_tool(
            "tools/doc_exporter.py",
            ["export", "--input-md", str(in_md), "--format", fmt, "--out", str(out_path), "--title", title],
            timeout=120,
        )
    finally:
        if tmp_md and tmp_md.is_file():
            try:
                tmp_md.unlink()
            except OSError:
                pass


def _validate_ssh_fields(alias: str, host: str, port: int, username: str) -> None:
    if not alias.strip():
        raise RuntimeError("SSH host alias cannot be empty.")
    for label, val in (("host", host), ("username", username)):
        v = val.strip()
        if not v or v.startswith("-") or any(c.isspace() or ord(c) < 32 or c == ";" for c in v):
            raise RuntimeError(f"Invalid SSH {label}: {val!r}")
    if not (1 <= port <= 65535):
        raise RuntimeError(f"SSH port must be 1..65535, got {port}")


def _ssh_base_args(host_row: sqlite3.Row) -> Tuple[List[str], str]:
    alias = str(host_row["alias"])
    host = str(host_row["host"]).strip()
    port = int(host_row["port"])
    username = str(host_row["username"]).strip()
    _validate_ssh_fields(alias, host, port, username)
    args = [
        "-p",
        str(port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    key_path = str(host_row["key_path"] or "").strip()
    if key_path:
        kp = os.path.expanduser(key_path)
        if not os.path.isfile(kp):
            raise RuntimeError(f"SSH private key not found: {kp}")
        args.extend(["-i", kp, "-o", "IdentitiesOnly=yes"])
    return args, f"{username}@{host}"


def tool_compute_and_memory(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "list_memories").strip().lower()
    with ctx.connect() as conn:
        if action == "list_memories":
            prefs, memories, md = _build_preferences_markdown(conn)
            return {"ok": True, "preferences": prefs, "memories": memories, "markdown": md}

        if action == "save_memory":
            title = str(args.get("title") or "").strip()
            content = str(args.get("content") or "").strip()
            if not title or not content:
                raise RuntimeError("`title` and `content` are required")
            mid = str(args.get("memory_id") or "").strip() or str(uuid.uuid4())
            cat = str(args.get("category") or "direction").strip() or "direction"
            enabled = 1 if args.get("enabled", True) else 0
            now = _now_iso()
            conn.execute(
                "INSERT OR REPLACE INTO research_memories (id, category, title, content, enabled, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mid, cat, title, content, enabled, now),
            )
            return {"ok": True, "id": mid, "category": cat, "title": title, "enabled": bool(enabled)}

        if action == "delete_memory":
            mid = str(args.get("memory_id") or "").strip()
            if not mid:
                raise RuntimeError("`memory_id` is required")
            conn.execute("DELETE FROM research_memories WHERE id = ?", (mid,))
            return {"ok": True, "deleted": mid}

        if action == "get_preferences":
            prefs, _, _ = _build_preferences_markdown(conn)
            return {"ok": True, "preferences": prefs}

        if action == "save_preferences":
            prefs, _, _ = _build_preferences_markdown(conn)
            for k in ("output_language", "citation_style", "delivery_format", "custom_rules"):
                if k in args and args[k] is not None:
                    prefs[k] = str(args[k])
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES ('research_preferences', ?, ?)",
                (json.dumps(prefs, ensure_ascii=False), _now_iso()),
            )
            return {"ok": True, "preferences": prefs}

        if action == "list_hosts":
            rows = conn.execute(
                "SELECT id, alias, host, port, username, auth_type, key_path, remote_work_dir, "
                "notes, last_status, last_gpu_info, created_at, updated_at FROM ssh_hosts ORDER BY updated_at DESC"
            ).fetchall()
            return {"ok": True, "hosts": [dict(r) for r in rows]}

        if action == "save_host":
            alias = str(args.get("alias") or "").strip()
            host = str(args.get("host") or "").strip()
            port = int(args.get("port") or 22)
            username = str(args.get("username") or "root").strip()
            _validate_ssh_fields(alias, host, port, username)
            hid = str(args.get("host_id") or "").strip() or str(uuid.uuid4())
            key_path = str(args.get("key_path") or "").strip()
            pem = str(args.get("private_key_pem") or "").strip()
            if pem:
                kdir = ctx.data_dir / "ssh_keys"
                kdir.mkdir(parents=True, exist_ok=True)
                safe = re.sub(r"[^A-Za-z0-9_-]+", "", hid) or uuid.uuid4().hex
                kf = kdir / f"{safe}.key"
                kf.write_text(pem + "\n", encoding="utf-8")
                try:
                    os.chmod(kf, 0o600)
                except OSError:
                    pass
                key_path = str(kf)
            existing = conn.execute("SELECT created_at, last_status, last_gpu_info FROM ssh_hosts WHERE id = ?", (hid,)).fetchone()
            now = _now_iso()
            created_at = str(existing["created_at"]) if existing else now
            last_status = str(existing["last_status"]) if existing else ""
            last_gpu = str(existing["last_gpu_info"]) if existing else ""
            conn.execute(
                """
                INSERT OR REPLACE INTO ssh_hosts (
                    id, alias, host, port, username, auth_type, key_path, remote_work_dir,
                    notes, last_status, last_gpu_info, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hid,
                    alias,
                    host,
                    port,
                    username,
                    str(args.get("auth_type") or ("private_key_pem" if pem else "key_path")),
                    key_path,
                    str(args.get("remote_work_dir") or "~").strip() or "~",
                    str(args.get("notes") or ""),
                    last_status,
                    last_gpu,
                    created_at,
                    now,
                ),
            )
            saved = conn.execute("SELECT * FROM ssh_hosts WHERE id = ?", (hid,)).fetchone()
            return {"ok": True, "host": dict(saved) if saved else {"id": hid}}

        if action == "delete_host":
            hid = str(args.get("host_id") or "").strip()
            if not hid:
                raise RuntimeError("`host_id` is required")
            conn.execute("DELETE FROM ssh_hosts WHERE id = ?", (hid,))
            return {"ok": True, "deleted": hid}

        hid = str(args.get("host_id") or "").strip()
        if not hid:
            raise RuntimeError("`host_id` is required for SSH operations")
        hrow = conn.execute("SELECT * FROM ssh_hosts WHERE id = ?", (hid,)).fetchone()
        if not hrow:
            raise RuntimeError(f"SSH host not found: {hid}")

        if action == "test_host":
            ssh_bin = _which_tool("ssh")
            if not ssh_bin:
                raise RuntimeError("`ssh` binary not found in PATH")
            sargs, target = _ssh_base_args(hrow)
            probe = "uname -snrm && (nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null || echo NO_GPU)"
            proc = subprocess.run(
                [ssh_bin, *sargs, target, probe],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            out_m = _redact_text(proc.stdout or "", ctx.secrets)
            err_m = _redact_text(proc.stderr or "", ctx.secrets)
            if proc.returncode != 0:
                conn.execute("UPDATE ssh_hosts SET last_status = 'error', updated_at = ? WHERE id = ?", (_now_iso(), hid))
                return {"ok": False, "error": err_m.strip() or "SSH connection failed", "raw_output": out_m}
            lines = out_m.splitlines()
            sys_info = lines[0].strip() if lines else ""
            gpus = []
            for gl in lines[1:]:
                t = gl.strip()
                if not t or t == "NO_GPU" or t.startswith("NVIDIA-SMI has failed"):
                    continue
                parts = [p.strip() for p in t.split(",")]
                if len(parts) >= 4:
                    gpus.append({
                        "name": parts[0],
                        "utilization": parts[1],
                        "mem_used": parts[2],
                        "mem_total": parts[3],
                    })
            conn.execute(
                "UPDATE ssh_hosts SET last_status = 'connected', last_gpu_info = ?, updated_at = ? WHERE id = ?",
                (json.dumps(gpus, ensure_ascii=False), _now_iso(), hid),
            )
            return {"ok": True, "system_info": sys_info, "gpus": gpus, "raw_output": out_m.strip()}

        if action == "exec_ssh":
            ssh_bin = _which_tool("ssh")
            if not ssh_bin:
                raise RuntimeError("`ssh` binary not found in PATH")
            cmd_str = str(args.get("command") or "").strip()
            if not cmd_str:
                raise RuntimeError("`command` is required")
            sargs, target = _ssh_base_args(hrow)
            wd = str(args.get("work_dir") or hrow["remote_work_dir"] or "").strip()
            if wd and wd != "~":
                quoted_wd = "'" + wd.replace("'", "'\"'\"'") + "'"
                remote_cmd = f"cd {quoted_wd} && {cmd_str}"
            else:
                remote_cmd = cmd_str
            t0 = time.time()
            proc = subprocess.run(
                [ssh_bin, *sargs, target, remote_cmd],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(10, int(args.get("timeout") or 120)),
            )
            return {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "duration_ms": int((time.time() - t0) * 1000),
                "stdout": _redact_text(proc.stdout or "", ctx.secrets),
                "stderr": _redact_text(proc.stderr or "", ctx.secrets),
            }

        if action == "scp_transfer":
            scp_bin = _which_tool("scp")
            if not scp_bin:
                raise RuntimeError("`scp` binary not found in PATH")
            direction = str(args.get("direction") or "").strip().lower()
            local_p = os.path.abspath(os.path.expanduser(str(args.get("local_path") or "")))
            remote_p = str(args.get("remote_path") or "").strip()
            if not remote_p or any(ord(c) < 32 for c in remote_p):
                raise RuntimeError("Invalid `remote_path`")
            sargs, target = _ssh_base_args(hrow)
            # scp uses -P for port whereas ssh uses -p
            clean_args: List[str] = ["-r"]
            skip_next = False
            for idx, item in enumerate(sargs):
                if skip_next:
                    skip_next = False
                    continue
                if item == "-p" and idx + 1 < len(sargs):
                    clean_args.extend(["-P", sargs[idx + 1]])
                    skip_next = True
                else:
                    clean_args.append(item)
            spec = f"{target}:{remote_p}"
            if direction == "upload":
                if not os.path.exists(local_p):
                    raise RuntimeError(f"Local path does not exist: {local_p}")
                cmd_list = [scp_bin, *clean_args, local_p, spec]
            elif direction == "download":
                os.makedirs(os.path.dirname(local_p) or ".", exist_ok=True)
                cmd_list = [scp_bin, *clean_args, spec, local_p]
            else:
                raise RuntimeError("`direction` must be 'upload' or 'download'")
            proc = subprocess.run(
                cmd_list,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(30, int(args.get("timeout") or 180)),
            )
            if proc.returncode != 0:
                raise RuntimeError(_redact_text((proc.stderr or proc.stdout or "SCP failed").strip(), ctx.secrets))
            return {"ok": True, "direction": direction, "local_path": local_p, "remote_path": remote_p}

    raise RuntimeError(f"Unsupported compute_and_memory action: {action}")


# ---------------------------------------------------------------------------
# Desktop TCP Bridge, Settings/Providers Validator & Native MCP App UI
# ---------------------------------------------------------------------------

_BRIDGE_MAX_FRAME = 4 * 1024 * 1024
_ACCEPTED_PROVIDER_TYPES = {
    "openai", "openai-compatible", "openai_compatible", "oai",
    "anthropic", "claude", "gemini", "google",
}
_BUILTIN_PROVIDERS: List[Tuple[str, str, Tuple[str, ...], Tuple[str, ...], bool, str]] = [
    ("openai", "openai", ("OPENAI_API_KEY",), ("oai",), False, ""),
    ("anthropic", "anthropic", ("ANTHROPIC_API_KEY",), ("claude",), False, ""),
    ("deepseek", "openai", ("DEEPSEEK_API_KEY",), (), False, ""),
    ("gemini", "gemini", ("GEMINI_API_KEY",), ("google", "vertex"), False, ""),
    ("qwen", "openai", ("DASHSCOPE_API_KEY", "QWEN_API_KEY"), ("dashscope", "bailian", "aliyun"), False, "qwen-max"),
    ("moonshot", "openai", ("MOONSHOT_API_KEY", "KIMI_API_KEY"), ("kimi",), False, "kimi-k3.1-pro"),
    ("zhipu", "openai", ("ZHIPU_API_KEY", "ZHIPUAI_API_KEY", "GLM_API_KEY"), ("glm", "bigmodel"), False, "glm-4-plus"),
    ("hunyuan", "openai", ("HUNYUAN_API_KEY", "TOKENHUB_API_KEY"), ("hy", "tencent", "tokenhub"), False, "hy4"),
    ("antdigital", "openai", ("ANTDIGITAL_API_KEY", "DTMAAS_API_KEY"), ("ant", "ling", "dtmaas"), False, ""),
    ("longcat", "openai", ("LONGCAT_API_KEY",), ("meituan",), False, ""),
    ("qianfan", "openai", ("QIANFAN_API_KEY", "ERNIE_API_KEY"), ("ernie", "baidu", "wenxin"), False, "ernie-5.1"),
    ("siliconflow", "openai", ("SILICONFLOW_API_KEY",), ("sf",), False, "deepseek-ai/DeepSeek-V3"),
    ("openrouter", "openai", ("OPENROUTER_API_KEY",), (), False, "anthropic/claude-sonnet-5.5"),
    ("xai", "openai", ("XAI_API_KEY", "GROK_API_KEY"), ("grok",), False, ""),
    ("groq", "openai", ("GROQ_API_KEY",), (), False, "llama-3.3-70b-versatile"),
    ("mistral", "openai", ("MISTRAL_API_KEY",), (), False, "mistral-large-latest"),
    ("together", "openai", ("TOGETHER_API_KEY",), (), False, ""),
    ("ollama", "openai", ("OLLAMA_API_KEY",), (), True, ""),
    ("lmstudio", "openai", ("LMSTUDIO_API_KEY",), (), True, ""),
    ("vllm", "openai", ("VLLM_API_KEY",), (), True, ""),
]
_SCHOLAR_ENV_KEYS = (
    "SEMANTIC_SCHOLAR_API_KEY",
    "CORE_API_KEY",
    "CROSSREF_MAILTO",
    "OPENALEX_MAILTO",
    "UNPAYWALL_EMAIL",
    "NCBI_API_KEY",
)


def _read_bridge_descriptor(data_dir: Path) -> Optional[Dict[str, Any]]:
    desc_path = data_dir / "codex-bridge.json"
    if not desc_path.exists() or desc_path.is_symlink():
        return None
    st = os.lstat(desc_path)
    if not stat.S_ISREG(st.st_mode) or st.st_size > 4096:
        raise RuntimeError("Studio bridge descriptor is not a safe regular file.")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise RuntimeError("Studio bridge descriptor is not owned by the current user.")
    data = json.loads(desc_path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise RuntimeError(f"Unsupported bridge version: {data.get('version')}")
    port = int(data.get("port") or 0)
    token = str(data.get("token") or "")
    pid = int(data.get("pid") or 0)
    if port < 1 or port > 65535 or pid <= 0 or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise RuntimeError("Invalid Studio bridge descriptor fields.")
    return data


def _bridge_rpc(desc: Dict[str, Any], payload: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
    frame = json.dumps({"version": 1, "token": desc["token"], **payload}, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(frame) > _BRIDGE_MAX_FRAME:
        raise RuntimeError("Bridge request exceeds 4 MiB frame limit.")
    with socket.create_connection(("127.0.0.1", int(desc["port"])), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(frame)
        buf = bytearray()
        while len(buf) <= _BRIDGE_MAX_FRAME:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if b"\n" in chunk:
                break
        if len(buf) > _BRIDGE_MAX_FRAME:
            raise RuntimeError("Bridge response exceeded 4 MiB frame limit.")
        line = bytes(buf).splitlines()[0].decode("utf-8", errors="replace") if buf else ""
        if not line:
            raise RuntimeError("Studio bridge closed connection without a response.")
        resp = json.loads(line)
        if not resp.get("ok"):
            raise RuntimeError(str(resp.get("error") or "Studio bridge returned an error."))
        return resp


def _find_studio_app_path(ctx: RuntimeContext) -> Optional[Path]:
    env_app = _clean_env("PAPER_ORCHESTRA_STUDIO_APP")
    cands: List[Path] = []
    if env_app:
        cands.append(Path(os.path.expanduser(env_app)))
    home = Path.home()
    if sys.platform == "darwin":
        cands.extend([
            Path("/Applications/PaperOrchestra Studio.app"),
            home / "Applications" / "PaperOrchestra Studio.app",
        ])
        sroot = ctx.studio_root_dir()
        if sroot:
            cands.append(sroot / "paper-orchestra-desktop" / "src-tauri" / "target" / "release" / "bundle" / "macos" / "PaperOrchestra Studio.app")
    elif sys.platform.startswith("win"):
        local_app = Path(os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local"))
        cands.extend([
            local_app / "Programs" / "PaperOrchestra Studio" / "PaperOrchestra Studio.exe",
            local_app / "PaperOrchestra Studio" / "PaperOrchestra Studio.exe",
        ])
    else:
        cands.extend([
            home / ".local" / "bin" / "paper-orchestra-studio",
            Path("/usr/local/bin/paper-orchestra-studio"),
        ])
    for c in cands:
        if c.exists():
            return c.resolve()
    return None


def _integration_paths(ctx: RuntimeContext) -> Dict[str, Any]:
    sroot = ctx.studio_root_dir()
    integ = (sroot / "integrations") if sroot else None
    scripts_dir = (sroot / "scripts") if sroot else None
    out = {
        "studioRoot": str(sroot) if sroot else "",
        "integrationsDir": str(integ) if (integ and integ.is_dir()) else "",
        "liveWorkbenchServer": "",
        "toolTreeHtml": "",
        "canvasConsole": "",
        "qoderPlugin": "",
        "setupBackendSh": "",
        "setupBackendPs1": "",
    }
    if integ and integ.is_dir():
        lw = integ / "live-workbench" / "server.mjs"
        tt = integ / "tool-tree" / "index.html"
        cc = integ / "canvas-console" / "paper-orchestra-collaboration-console.canvas.tsx"
        qp = integ / "qoder-plugin"
        if lw.is_file():
            out["liveWorkbenchServer"] = str(lw)
        if tt.is_file():
            out["toolTreeHtml"] = str(tt)
        if cc.is_file():
            out["canvasConsole"] = str(cc)
        if qp.is_dir():
            out["qoderPlugin"] = str(qp)
    if scripts_dir and scripts_dir.is_dir():
        sh = scripts_dir / "setup-backend.sh"
        ps1 = scripts_dir / "setup-backend.ps1"
        if sh.is_file():
            out["setupBackendSh"] = str(sh)
        if ps1.is_file():
            out["setupBackendPs1"] = str(ps1)
    return out


def _bridge_ping_safe(ctx: RuntimeContext) -> Dict[str, Any]:
    sdata = ctx.studio_app_data_dir()
    try:
        desc = _read_bridge_descriptor(sdata)
    except Exception as exc:
        return {"online": False, "descriptorPath": str(sdata / "codex-bridge.json"), "error": str(exc)}
    if not desc:
        return {"online": False, "descriptorPath": str(sdata / "codex-bridge.json"), "error": ""}
    t0 = time.monotonic()
    try:
        resp = _bridge_rpc(desc, {"method": "ping"}, timeout=2.5)
        return {
            "online": True,
            "descriptorPath": str(sdata / "codex-bridge.json"),
            "port": desc["port"],
            "pid": desc["pid"],
            "latencyMs": int((time.monotonic() - t0) * 1000),
            "version": resp.get("version"),
        }
    except Exception as exc:
        return {
            "online": False,
            "descriptorPath": str(sdata / "codex-bridge.json"),
            "port": desc["port"],
            "pid": desc["pid"],
            "error": str(exc),
        }


def tool_studio_bridge(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "status").strip().lower()
    app_path = _find_studio_app_path(ctx)
    integ = _integration_paths(ctx)

    if action == "status":
        ping = _bridge_ping_safe(ctx)
        return {
            "ok": True,
            "bridge": ping,
            "studioAppFound": app_path is not None,
            "studioAppPath": str(app_path) if app_path else "",
            "integrations": integ,
        }

    if action == "open_studio":
        tab = str(args.get("tab") or "workspace").strip()
        valid_tabs = ("workspace", "studio", "library", "runs", "artifacts", "settings")
        if tab not in valid_tabs:
            raise RuntimeError(f"`tab` must be one of {valid_tabs}")
        ping = _bridge_ping_safe(ctx)
        launched = False
        if not ping["online"]:
            if not bool(args.get("auto_launch", True)):
                raise RuntimeError("Studio Desktop bridge is offline and auto_launch=false.")
            if app_path is None:
                raise RuntimeError("PaperOrchestra Studio Desktop app not found; set PAPER_ORCHESTRA_STUDIO_APP.")
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-a", str(app_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            else:
                subprocess.Popen([str(app_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            launched = True
            deadline = time.monotonic() + max(5.0, float(args.get("launch_timeout") or 15.0))
            while time.monotonic() < deadline:
                ping = _bridge_ping_safe(ctx)
                if ping["online"]:
                    break
                time.sleep(0.5)
        if not ping["online"]:
            return {"ok": False, "launched": launched, "bridge": ping, "error": "Launched Studio app but bridge did not come online in time."}
        desc = _read_bridge_descriptor(ctx.studio_app_data_dir())
        assert desc is not None
        sub = _bridge_rpc(desc, {"method": "submit", "requestId": str(uuid.uuid4()), "command": "show_studio_view", "args": {"tab": tab}}, timeout=8.0)
        return {"ok": True, "launched": launched, "tab": tab, "bridge": ping, "job": _redact_obj(sub.get("job") or sub, ctx.secrets)}

    if action == "launch_workbench":
        target = str(args.get("target") or "live_workbench").strip().lower()
        check_only = bool(args.get("check_only", True))
        open_browser = bool(args.get("open_browser", False))
        if target == "live_workbench":
            srv = integ.get("liveWorkbenchServer")
            if not srv:
                raise RuntimeError("`integrations/live-workbench/server.mjs` not found in Studio checkout.")
            node_bin = _which_tool("node")
            if check_only:
                return {"ok": True, "target": target, "serverScript": srv, "node": node_bin or "", "command": f"node {srv} --open"}
            if not node_bin:
                raise RuntimeError("Node.js (22+) not found in PATH.")
            cmd = [node_bin, srv] + (["--open"] if open_browser else [])
            proc = subprocess.Popen(cmd, cwd=str(Path(srv).parent), env=ctx.subprocess_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            port = int(_clean_env("PAPER_ORCHESTRA_PANEL_PORT") or 63584)
            return {"ok": True, "target": target, "pid": proc.pid, "port": port, "url": f"http://127.0.0.1:{port}/", "serverScript": srv}
        if target == "tool_tree":
            tt = integ.get("toolTreeHtml")
            if not tt:
                raise RuntimeError("`integrations/tool-tree/index.html` not found in Studio checkout.")
            if not check_only and open_browser and sys.platform == "darwin":
                subprocess.Popen(["open", tt], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "target": target, "path": tt}
        if target in ("canvas_console", "qoder_plugin"):
            key = "canvasConsole" if target == "canvas_console" else "qoderPlugin"
            p = integ.get(key, "")
            if not p:
                raise RuntimeError(f"Integration asset not found for target='{target}'")
            return {"ok": True, "target": target, "path": p}
        raise RuntimeError(f"Unsupported launch_workbench target: {target}")

    desc = _read_bridge_descriptor(ctx.studio_app_data_dir())
    if not desc:
        raise RuntimeError("PaperOrchestra Studio Desktop bridge is offline (codex-bridge.json not found). Open Studio or pass action='open_studio'.")

    if action == "list_bridge_jobs":
        return {"ok": True, **_redact_obj(_bridge_rpc(desc, {"method": "list_jobs"}), ctx.secrets)}
    if action == "bridge_job_status":
        jid = str(args.get("job_id") or "").strip()
        if not jid:
            raise RuntimeError("`job_id` is required")
        return {"ok": True, **_redact_obj(_bridge_rpc(desc, {"method": "job", "jobId": jid}), ctx.secrets)}
    if action == "cancel_bridge_job":
        jid = str(args.get("job_id") or "").strip()
        if not jid:
            raise RuntimeError("`job_id` is required")
        return {"ok": True, **_redact_obj(_bridge_rpc(desc, {"method": "cancel", "jobId": jid}), ctx.secrets)}
    if action == "dispatch_bridge_action":
        command = str(args.get("command") or "").strip()
        if not command:
            raise RuntimeError("`command` is required")
        action_args = args.get("action_args") if isinstance(args.get("action_args"), dict) else {}
        sub = _bridge_rpc(desc, {"method": "submit", "requestId": str(uuid.uuid4()), "command": command, "args": action_args}, timeout=10.0)
        job_obj = sub.get("job") or sub
        jid = str(job_obj.get("jobId") or "")
        wait = bool(args.get("wait", True))
        if wait and jid and job_obj.get("state") == "running":
            deadline = time.monotonic() + min(60.0, max(1.0, float(args.get("timeout") or 20.0)))
            while time.monotonic() < deadline:
                cur = _bridge_rpc(desc, {"method": "job", "jobId": jid}, timeout=5.0)
                job_obj = cur.get("job") or cur
                if job_obj.get("state") != "running":
                    break
                time.sleep(0.4)
        return {"ok": True, "job": _redact_obj(job_obj, ctx.secrets)}

    raise RuntimeError(f"Unsupported studio_bridge action: {action}")


def _validate_providers_json_text(raw_text: str) -> Dict[str, Any]:
    text = raw_text.lstrip("\ufeff")
    if not text.strip():
        return {"valid": True, "count": 0, "customProviders": [], "autoModel": "", "taskModels": {}}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {"valid": False, "error": f"providers.json 不是合法的 JSON：{exc}"}

    auto_model = ""
    task_models: Dict[str, str] = {}
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        if isinstance(data.get("auto_model"), str) and data["auto_model"].strip():
            auto_model = data["auto_model"].strip()
        if isinstance(data.get("task_models"), dict):
            for tk, tv in data["task_models"].items():
                if isinstance(tv, str) and tv.strip():
                    task_models[str(tk)] = tv.strip()
        prov_val = data.get("providers")
        if prov_val is None:
            entries = []
        elif isinstance(prov_val, list):
            entries = prov_val
        else:
            return {"valid": False, "error": 'providers.json 中的 "providers" 必须是数组'}
    else:
        return {"valid": False, "error": 'providers.json 顶层必须是 provider 数组，或带 "providers" 数组的对象'}

    custom: List[Dict[str, Any]] = []
    for idx, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            return {"valid": False, "error": f"providers.json 第 {idx} 个 provider 必须是对象"}
        name_raw = entry.get("name")
        if not isinstance(name_raw, str) or not name_raw.strip():
            return {"valid": False, "error": f"providers.json 第 {idx} 个 provider 缺少 name（需为非空字符串）"}
        name = name_raw.strip()
        kind_raw = entry["type"] if "type" in entry else entry.get("kind", "openai")
        if not isinstance(kind_raw, str):
            return {"valid": False, "error": f'provider "{name}" 的 type/kind 必须是字符串'}
        kind_norm = kind_raw.strip().lower()
        if kind_norm not in _ACCEPTED_PROVIDER_TYPES:
            return {"valid": False, "error": f'provider "{name}" 的 type "{kind_raw}" 无效'}
        for skey in ("base_url", "api_key", "default_model"):
            v = entry.get(skey)
            if v is not None and not isinstance(v, str):
                return {"valid": False, "error": f'provider "{name}" 的 {skey} 必须是字符串'}
        for lkey in ("api_key_env", "models", "drop_params", "aliases"):
            v = entry.get(lkey)
            if v is not None and not isinstance(v, str) and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                return {"valid": False, "error": f'provider "{name}" 的 {lkey} 必须是字符串或字符串数组'}
        for bkey in ("vision", "key_optional"):
            v = entry.get(bkey)
            if v is not None and not isinstance(v, bool):
                return {"valid": False, "error": f'provider "{name}" 的 {bkey} 必须是 true 或 false'}
        hdr = entry.get("headers")
        if hdr is not None and not (isinstance(hdr, dict) and all(isinstance(hv, str) for hv in hdr.values())):
            return {"valid": False, "error": f'provider "{name}" 的 headers 必须是值为字符串的对象'}

        key_envs = [entry["api_key_env"]] if isinstance(entry.get("api_key_env"), str) else list(entry.get("api_key_env") or [])
        custom.append({
            "name": name,
            "kind": kind_norm,
            "default_model": str(entry.get("default_model") or ""),
            "api_key_env": key_envs,
            "key_optional": bool(entry.get("key_optional", False)),
            "has_inline_api_key": bool(isinstance(entry.get("api_key"), str) and entry["api_key"].strip()),
            "has_base_url": bool(isinstance(entry.get("base_url"), str) and entry["base_url"].strip()),
        })

    return {"valid": True, "count": len(custom), "customProviders": custom, "autoModel": auto_model, "taskModels": task_models}


def _env_value_nonempty(ctx: RuntimeContext, var_name: str) -> bool:
    if (ctx.dotenv_vars.get(var_name) or "").strip():
        return True
    if _clean_env(var_name):
        return True
    return False


def tool_settings_and_setup(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "get_settings").strip().lower()
    integ = _integration_paths(ctx)

    if action == "get_settings":
        settings = ctx._read_app_settings()
        repo = ctx.repo_dir
        return {
            "ok": True,
            "dbPath": str(ctx.db_path),
            "appSettings": {
                "paper_orchestra_dir": settings.get("paper_orchestra_dir") or (str(repo) if repo else ""),
                "python_path": settings.get("python_path") or ctx.python_bin,
                "output_root_dir": settings.get("output_root_dir") or str(ctx.output_root_dir),
            },
            "resolved": {
                "repoDir": str(repo) if repo else "",
                "pythonBin": ctx.python_bin,
                "outputRootDir": str(ctx.output_root_dir),
                "envFile": str(ctx.env_file) if ctx.env_file else "",
                "providersJsonPath": str(repo / "providers.json") if (repo and (repo / "providers.json").is_file()) else "",
            },
            "setupScripts": {k: v for k, v in integ.items() if k in ("setupBackendSh", "setupBackendPs1")},
        }

    if action == "update_settings":
        validate = bool(args.get("validate", True))
        updates: Dict[str, str] = {}
        for field in ("paper_orchestra_dir", "python_path", "output_root_dir"):
            if field in args and args[field] is not None:
                val = os.path.expanduser(str(args[field]).strip())
                if val and validate:
                    if field == "paper_orchestra_dir" and not (Path(val) / "paper_writing_cli.py").is_file():
                        raise RuntimeError(f"Invalid `paper_orchestra_dir` (paper_writing_cli.py missing): {val}")
                    if field == "python_path" and not (os.path.isfile(val) and os.access(val, os.X_OK)):
                        raise RuntimeError(f"Invalid `python_path` (not executable): {val}")
                updates[field] = val
        if not updates:
            raise RuntimeError("Provide at least one of `paper_orchestra_dir`, `python_path`, `output_root_dir`.")
        now = _now_iso()
        with ctx.connect() as conn:
            for k, v in updates.items():
                conn.execute("INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)", (k, v, now))
        ctx.refresh_runtime()
        return tool_settings_and_setup(ctx, {"action": "get_settings"})

    if action == "validate_providers_json":
        raw_text = args.get("providers_json_text")
        src_path = ""
        if raw_text is None:
            explicit_p = str(args.get("providers_json_path") or "").strip()
            p = Path(os.path.expanduser(explicit_p)).resolve() if explicit_p else (ctx.repo_dir / "providers.json" if ctx.repo_dir else Path(""))
            if not str(p) or not p.is_file():
                return {"ok": True, "exists": False, "valid": True, "count": 0, "customProviders": [], "note": "providers.json not present (using built-in providers only)."}
            src_path = str(p)
            raw_text = p.read_text(encoding="utf-8", errors="replace")
        parsed = _validate_providers_json_text(str(raw_text))
        return {"ok": bool(parsed["valid"]), "exists": True, "path": src_path, **parsed}

    if action == "env_readiness":
        builtins_status = []
        for name, kind, key_envs, aliases, key_opt, def_mod in _BUILTIN_PROVIDERS:
            has_key = any(_env_value_nonempty(ctx, k) for k in key_envs)
            base_var = f"{name.upper()}_BASE_URL"
            has_base = _env_value_nonempty(ctx, base_var)
            configured = bool(has_key or (key_opt and has_base))
            builtins_status.append({
                "name": name,
                "kind": kind,
                "keyEnvNames": list(key_envs),
                "aliases": list(aliases),
                "keyOptional": key_opt,
                "defaultModel": def_mod,
                "baseUrlEnvSet": has_base,
                "configured": configured,
            })
        prov_check = tool_settings_and_setup(ctx, {"action": "validate_providers_json"})
        scholar_status = {k: _env_value_nonempty(ctx, k) for k in _SCHOLAR_ENV_KEYS}
        return {
            "ok": True,
            "envExists": ctx.env_file is not None,
            "envFile": str(ctx.env_file) if ctx.env_file else "",
            "configuredCount": sum(1 for b in builtins_status if b["configured"]),
            "builtins": builtins_status,
            "providersJson": prov_check,
            "scholarEnv": scholar_status,
            "setupScripts": {k: v for k, v in integ.items() if k in ("setupBackendSh", "setupBackendPs1")},
        }

    if action == "setup_info":
        sh = integ.get("setupBackendSh", "")
        ps1 = integ.get("setupBackendPs1", "")
        return {
            "ok": True,
            "platform": sys.platform,
            "setupBackendSh": sh,
            "setupBackendPs1": ps1,
            "recommendedCommand": (f"bash {sh}" if sh else "See docs/INSTALL.md") if not sys.platform.startswith("win") else (f"powershell -ExecutionPolicy Bypass -File {ps1}" if ps1 else "See docs/INSTALL.md"),
            "repoFound": ctx.repo_dir is not None,
            "pythonBin": ctx.python_bin,
        }

    raise RuntimeError(f"Unsupported settings_and_setup action: {action}")


def _load_ui_html(filename: str, env_var: str) -> str:
    env_p = _clean_env(env_var)
    here = Path(__file__).resolve()
    cands: List[Path] = []
    if env_p:
        cands.append(Path(os.path.expanduser(env_p)))
    if len(here.parents) >= 3:
        cands.extend([
            here.parents[1] / "assets" / "ui" / filename,
            here.parents[1] / "ui_dist" / filename,
        ])
    cands.extend([
        here.parent / "ui" / filename,
        Path(__file__).resolve().parent / filename,
    ])
    for c in cands:
        try:
            if c.is_file():
                return c.read_text(encoding="utf-8")
        except OSError:
            continue
    raise RuntimeError(f"UI bundle `{filename}` not found (checked {len(cands)} paths).")


def _clip_dict_fields(d: Dict[str, Any], fields: Tuple[str, ...], max_len: int) -> Dict[str, Any]:
    out = dict(d)
    for f in fields:
        if isinstance(out.get(f), str) and len(out[f]) > max_len:
            out[f] = out[f][:max_len] + "…"
    return out


def _build_ui_snapshot_payload(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    env = tool_check_environment(ctx, {})
    bridge_status = _bridge_ping_safe(ctx)
    with ctx.connect() as conn:
        proj_rows = conn.execute(
            """
            SELECT p.id, p.title, p.template_name, p.writer_model, p.reflection_model, p.use_plotting,
                   p.created_at, p.updated_at,
                   (SELECT COUNT(*) FROM runs r WHERE r.project_id = p.id) AS run_count,
                   (SELECT COUNT(*) FROM project_figures f WHERE f.project_id = p.id) AS figure_count
            FROM projects p ORDER BY p.updated_at DESC LIMIT 30
            """
        ).fetchall()
        projects = [{**dict(r), "use_plotting": bool(r["use_plotting"])} for r in proj_rows]

        run_rows = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 25").fetchall()
        runs = [{**dict(r), "use_plotting": bool(r["use_plotting"])} for r in run_rows]

        pid = str(args.get("project_id") or "").strip() or (projects[0]["id"] if projects else "")
        selected_project: Optional[Dict[str, Any]] = None
        if pid:
            try:
                sp = _fetch_project(conn, pid)
                selected_project = _clip_dict_fields(sp, ("idea_markdown", "experimental_log_markdown"), 16000)
            except Exception:
                selected_project = None

        rid = str(args.get("run_id") or "").strip() or (runs[0]["id"] if runs else "")
        rdir = str(args.get("run_dir") or "").strip()
        if not rdir and rid:
            matched_r = next((r for r in runs if r["id"] == rid), None)
            if matched_r:
                rdir = str(matched_r.get("run_dir") or "")

        run_logs: List[Dict[str, Any]] = []
        if rid:
            lrows = conn.execute(
                "SELECT id, run_id, stage, line, created_at FROM run_logs WHERE run_id = ? ORDER BY id DESC LIMIT 60",
                (rid,),
            ).fetchall()
            run_logs = [
                {"id": int(r["id"]), "stage": str(r["stage"]), "line": _redact_text(str(r["line"])[:300], ctx.secrets), "created_at": str(r["created_at"])}
                for r in reversed(lrows)
            ]

        mem_rows = conn.execute("SELECT id, category, title, content, enabled, updated_at FROM research_memories ORDER BY updated_at DESC LIMIT 40").fetchall()
        memories = [_clip_dict_fields({**dict(r), "enabled": bool(r["enabled"])}, ("content",), 400) for r in mem_rows]

        host_rows = conn.execute(
            "SELECT id, alias, host, port, username, auth_type, remote_work_dir, notes, last_status, last_gpu_info, updated_at FROM ssh_hosts ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
        hosts = [dict(r) for r in host_rows]

        lib_rows = conn.execute("SELECT id, folder, title, authors, year, venue, doi, arxiv_id, read_status FROM library_items ORDER BY updated_at DESC LIMIT 15").fetchall()
        lib_preview = [dict(r) for r in lib_rows]

    jobs: List[Dict[str, Any]] = []
    for mf in sorted(_jobs_dir(ctx).glob("*/meta.json"), reverse=True)[:15]:
        try:
            meta = json.loads(mf.read_text(encoding="utf-8"))
            jobs.append(_inspect_job(ctx, meta, tail_lines=8))
        except Exception:
            continue

    skills = [{k: v for k, v in s.items() if k != "content"} for s in _collect_all_skills(ctx)]
    templates_short: List[Dict[str, str]] = []
    if ctx.repo_dir and (ctx.repo_dir / "templates").is_dir():
        for d in sorted((ctx.repo_dir / "templates").iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                templates_short.append({"name": d.name})

    run_summary: Optional[Dict[str, Any]] = None
    if rdir and os.path.isdir(rdir):
        try:
            run_summary = tool_inspect_run(ctx, {"action": "summary", "run_dir": rdir})
        except Exception:
            run_summary = None

    return {
        "env": env,
        "bridge": bridge_status,
        "projects": projects,
        "selectedProject": selected_project,
        "runs": runs,
        "jobs": jobs,
        "runSummary": run_summary,
        "runLogs": run_logs,
        "skills": skills,
        "skillsCount": len(skills),
        "memories": memories,
        "hosts": hosts,
        "libraryPreview": lib_preview,
        "templates": templates_short,
    }


def tool_open_workbench(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    snap = _build_ui_snapshot_payload(ctx, args)
    out = {
        "ok": True,
        "tab": str(args.get("tab") or "overview"),
        "project_id": str(args.get("project_id") or ""),
        "run_id": str(args.get("run_id") or ""),
        "run_dir": str(args.get("run_dir") or ""),
        "notice": str(args.get("notice") or ""),
        "snapshot": snap,
        "summary": (
            f"PaperOrchestra Workbench opened (tab={args.get('tab') or 'overview'}, "
            f"projects={len(snap['projects'])}, runs={len(snap['runs'])}, "
            f"bridgeOnline={snap['bridge'].get('online')})."
        ),
    }
    return out


def tool_open_tool_tree(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "groupCount": 10,
        "modelToolCount": len([t for t in TOOLS if not ((t[4] or {}).get("ui", {}).get("visibility") == ["app"])]),
        "group": str(args.get("group") or ""),
        "search": str(args.get("search") or ""),
        "risk": str(args.get("risk") or "all"),
        "summary": "Interactive PaperOrchestra Studio Tool Tree opened (10 workflow groups).",
    }


_EXTERNAL_PREVIEW_EXTS = {
    ".pdf", ".html", ".htm", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".tex", ".bib", ".md", ".docx"
}

_PDF_PAGE_RENDER_SCRIPT = r"""
import base64, json, sys
import fitz

pdf_path, req_page, req_dpi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
doc = fitz.open(pdf_path)
try:
    total = len(doc)
    if total <= 0:
        raise RuntimeError("PDF has zero pages")
    page_num = max(1, min(total, req_page))
    page = doc.load_page(page_num - 1)
    rect = page.rect
    best_bytes = b""
    best_w, best_h = 0, 0
    for try_dpi in (req_dpi, min(req_dpi, 110), 85):
        pix = page.get_pixmap(dpi=try_dpi, alpha=False)
        best_w, best_h = pix.width, pix.height
        for quality in (84, 70, 58):
            raw = pix.tobytes("jpeg", jpg_quality=quality)
            best_bytes = raw
            if len(raw) <= 480 * 1024:
                break
        if len(best_bytes) <= 480 * 1024:
            break
    print(json.dumps({
        "ok": True,
        "page": page_num,
        "pageCount": total,
        "width": best_w,
        "height": best_h,
        "pageWidthPt": float(rect.width),
        "pageHeightPt": float(rect.height),
        "bytes": len(best_bytes),
        "dataUrl": "data:image/jpeg;base64," + base64.b64encode(best_bytes).decode("ascii"),
    }))
finally:
    doc.close()
"""


def _render_pdf_page_jpeg(ctx: RuntimeContext, pdf_path: Path, page: int, dpi: int) -> Dict[str, Any]:
    try:
        import fitz  # type: ignore

        doc = fitz.open(str(pdf_path))
        try:
            total = len(doc)
            if total <= 0:
                raise RuntimeError("PDF has zero pages.")
            page_num = max(1, min(total, page))
            pg = doc.load_page(page_num - 1)
            rect = pg.rect
            best_bytes = b""
            best_w, best_h = 0, 0
            for try_dpi in (dpi, min(dpi, 110), 85):
                pix = pg.get_pixmap(dpi=try_dpi, alpha=False)
                best_w, best_h = pix.width, pix.height
                for quality in (84, 70, 58):
                    raw = pix.tobytes("jpeg", jpg_quality=quality)
                    best_bytes = raw
                    if len(raw) <= 480 * 1024:
                        break
                if len(best_bytes) <= 480 * 1024:
                    break
            return {
                "ok": True,
                "path": str(pdf_path),
                "page": page_num,
                "pageCount": total,
                "width": best_w,
                "height": best_h,
                "pageWidthPt": float(rect.width),
                "pageHeightPt": float(rect.height),
                "bytes": len(best_bytes),
                "dataUrl": "data:image/jpeg;base64," + base64.b64encode(best_bytes).decode("ascii"),
            }
        finally:
            doc.close()
    except ImportError:
        pass

    proc = subprocess.run(
        [ctx.python_bin, "-B", "-c", _PDF_PAGE_RENDER_SCRIPT, str(pdf_path), str(page), str(dpi)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "PyMuPDF render failed").strip()
        raise RuntimeError(f"PDF page render failed: {err}")
    data = json.loads((proc.stdout or "").strip())
    data["path"] = str(pdf_path)
    return data


def tool_ui_snapshot(ctx: RuntimeContext, args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(args.get("action") or "snapshot").strip().lower()
    if action == "snapshot":
        return {"ok": True, **_build_ui_snapshot_payload(ctx, args)}
    if action == "read_image_data_url":
        raw_p = str(args.get("path") or "").strip()
        if not raw_p:
            raise RuntimeError("`path` is required for action='read_image_data_url'")
        img_path = Path(os.path.expanduser(raw_p)).resolve()
        if not img_path.is_file():
            raise RuntimeError(f"Image file not found: {img_path}")
        ext = img_path.suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg", ".svg", ".webp"):
            raise RuntimeError(f"Unsupported image extension: {ext}")
        size = img_path.stat().st_size
        if size > 600 * 1024:
            raise RuntimeError(f"Image exceeds 600 KB inline preview limit ({size} bytes).")
        mime = mimetypes.guess_type(img_path.name)[0] or ("image/svg+xml" if ext == ".svg" else "image/png")
        b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
        return {"ok": True, "path": str(img_path), "bytes": size, "dataUrl": f"data:{mime};base64,{b64}"}
    if action == "render_pdf_page":
        raw_p = str(args.get("path") or "").strip()
        if not raw_p or any(ord(c) < 32 for c in raw_p):
            raise RuntimeError("Valid `path` is required for action='render_pdf_page'")
        pdf_path = Path(os.path.expanduser(raw_p)).resolve()
        if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
            raise RuntimeError(f"PDF file not found: {pdf_path}")
        page = max(1, int(args.get("page") or 1))
        dpi = max(72, min(200, int(args.get("dpi") or 140)))
        return _render_pdf_page_jpeg(ctx, pdf_path, page, dpi)
    if action == "open_file_external":
        raw_p = str(args.get("path") or "").strip()
        if not raw_p or any(ord(c) < 32 for c in raw_p):
            raise RuntimeError("Valid `path` is required for action='open_file_external'")
        target = Path(os.path.expanduser(raw_p)).resolve()
        if not target.is_file():
            raise RuntimeError(f"File not found: {target}")
        if target.suffix.lower() not in _EXTERNAL_PREVIEW_EXTS:
            raise RuntimeError(f"Unsupported file extension for external preview: {target.suffix}")
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform.startswith("win"):
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "path": str(target), "opened": True}
    raise RuntimeError(f"Unsupported ui_snapshot action: {action}")


# ---------------------------------------------------------------------------
# Tool Definitions & Schemas
# ---------------------------------------------------------------------------

def _schema(props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    s: Dict[str, Any] = {"type": "object", "properties": props}
    if required:
        s["required"] = required
    return s


TOOLS: List[Tuple[str, str, Dict[str, Any], Any, Optional[Dict[str, Any]]]] = [
    (
        "po_check_environment",
        "Check local PaperOrchestra repository, Python environment, .env credentials presence (never exposes keys), LaTeX toolchain, Pandoc, Chrome, Lean 4, SSH/SCP, export directories, and SQLite counts.",
        _schema({}),
        tool_check_environment,
        None,
    ),
    (
        "po_check_providers",
        "Check configured LLM providers (20+ platforms plus custom providers.json) and 8 scholarly search sources; optionally ping models or scholar APIs or list available models.",
        _schema({
            "ping": {"type": "boolean", "description": "Send a minimal test call to configured LLM providers"},
            "scholar": {"type": "boolean", "description": "Test configured academic search APIs (Semantic Scholar, arXiv, OpenAlex, etc.)"},
            "list_models": {"type": "boolean", "description": "Return curated catalog of provider models"},
            "models": {"type": "array", "items": {"type": "string"}, "description": "Verify credential availability for specific model refs"},
            "ping_models": {"type": "array", "items": {"type": "string"}, "description": "Live-ping specific TASK=MODEL pairs"},
            "timeout": {"type": "number", "description": "Per-probe timeout seconds (default 15)"},
        }),
        tool_check_providers,
        None,
    ),
    (
        "po_list_templates",
        "List all 19 conference, journal, Chinese GB/T 7714, and mathematical modeling (MCM/CUMCM) LaTeX templates with venue.json style rules and section orders.",
        _schema({}),
        tool_list_templates,
        None,
    ),
    (
        "po_parse_intake_form",
        "Parse a PaperOrchestra project intake form (.pdf, .docx, .md, .txt) into structured title, template, research idea, and experimental log fields.",
        _schema({"file_path": {"type": "string", "description": "Path to intake form (.pdf, .docx, .md, .txt)"}}, ["file_path"]),
        tool_parse_intake_form,
        None,
    ),
    (
        "po_projects",
        "Manage PaperOrchestra projects & runs in the shared SQLite workspace (`create`/`list`/`get`/`update` with optional `expected_updated_at` conflict guard, `delete`, `import_cvpr_example`, `add_figure`/`update_figure_caption`/`delete_figure`, `list_runs`, `get_run_logs`, `resume_run`, `delete_run`).",
        _schema({
            "action": {
                "type": "string",
                "enum": [
                    "list", "get", "create", "update", "delete",
                    "import_cvpr_example", "add_figure", "update_figure_caption", "delete_figure",
                    "list_runs", "get_run_logs", "resume_run", "delete_run",
                ],
            },
            "project_id": {"type": "string"},
            "expected_updated_at": {"type": "string", "description": "Optimistic lock timestamp from get/list before update"},
            "title": {"type": "string"},
            "template_name": {"type": "string"},
            "writer_model": {"type": "string"},
            "reflection_model": {"type": "string"},
            "plotting_model": {"type": "string"},
            "image_model": {"type": "string"},
            "research_cutoff": {"type": "string"},
            "use_plotting": {"type": "boolean"},
            "plotting_max_critic_rounds": {"type": "integer"},
            "idea_markdown": {"type": "string"},
            "experimental_log_markdown": {"type": "string"},
            "figure_path": {"type": "string"},
            "figure_caption": {"type": "string"},
            "caption": {"type": "string"},
            "figure_id": {"type": "string"},
            "run_id": {"type": "string"},
            "after_id": {"type": "integer"},
            "limit": {"type": "integer"},
        }, ["action"]),
        tool_projects,
        None,
    ),
    (
        "po_start_paper_run",
        "Start (or resume) a full multi-agent PaperOrchestra paper generation run as a background job, supporting 19 venue templates and per-step model routing via PO_TASK_MODEL_*.",
        _schema({
            "project_id": {"type": "string", "description": "Existing SQLite project_id to materialize materials from"},
            "raw_materials_dir": {"type": "string", "description": "Alternative raw_materials directory when not using project_id"},
            "template_name": {"type": "string", "description": "Template folder name e.g. cvpr2025, journal-nature, math-modeling-mcm"},
            "latex_template_dir": {"type": "string", "description": "Explicit template directory path (overrides template_name)"},
            "output_dir": {"type": "string", "description": "Custom output run directory"},
            "writer_model": {"type": "string"},
            "reflection_model": {"type": "string"},
            "plotting_model": {"type": "string"},
            "image_model": {"type": "string"},
            "research_cutoff": {"type": "string", "description": "YYYY-MM cutoff e.g. 2026-11"},
            "use_plotting": {"type": "boolean"},
            "plotting_max_critic_rounds": {"type": "integer"},
            "resume": {"type": "boolean"},
            "step_model_overrides": {"type": "object", "description": "Map of task key (e.g. outline, section_writing, peer_review, plot_code) to model string"},
        }),
        tool_start_paper_run,
        None,
    ),
    (
        "po_job_manage",
        "List, inspect live status & token usage, tail redacted logs, or stop background PaperOrchestra runs/SOPs.",
        _schema({
            "action": {"type": "string", "enum": ["list", "status", "logs", "stop"]},
            "job_id": {"type": "string"},
            "tail_lines": {"type": "integer", "description": "Log tail lines (default 80)"},
        }, ["action"]),
        tool_job_manage,
        None,
    ),
    (
        "po_inspect_run",
        "Inspect outputs of a PaperOrchestra run directory (`summary`, safe bounded `read_artifact`, or exclusive non-overwriting `copy_pdf`).",
        _schema({
            "run_dir": {"type": "string"},
            "action": {"type": "string", "enum": ["summary", "read_artifact", "copy_pdf"]},
            "relative_path": {"type": "string", "description": "Relative file path inside run_dir for action='read_artifact'"},
            "dest_path": {"type": "string", "description": "Target .pdf path for action='copy_pdf' (defaults to unique file in PO_DEFAULT_PDF_DIR)"},
            "max_chars": {"type": "integer"},
        }, ["run_dir"]),
        tool_inspect_run,
        None,
    ),
    (
        "po_data_analysis",
        "Profile a tabular dataset offline (`profile`: schema/missing/outlier audit) or run the 7-step reproducible self-fixing Python statistical & visualization pipeline (`analyze`).",
        _schema({
            "action": {"type": "string", "enum": ["profile", "analyze"]},
            "file_path": {"type": "string", "description": "Path to .csv, .tsv, .xlsx, .parquet, or .json dataset"},
            "question": {"type": "string"},
            "out_dir": {"type": "string"},
            "model": {"type": "string"},
            "async_mode": {"type": "boolean"},
            "timeout": {"type": "integer"},
        }, ["action", "file_path"]),
        tool_data_analysis,
        None,
    ),
    (
        "po_run_sop",
        "Run any of the 5 structured scientific SOP workflows (`paper_reading` 6-step deep read, `lit_review` Q->E->T->A synthesis, `topic_ideation`, `reproduction` 7-step code/paper plan, `math_modeling` MCM/CUMCM), auto-injecting enabled research memories.",
        _schema({
            "sop": {"type": "string", "enum": ["paper_reading", "lit_review", "topic_ideation", "reproduction", "math_modeling"]},
            "topic": {"type": "string"},
            "question": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}, "description": "Paths to PDF, HTML, TeX, Markdown, or data files"},
            "notes": {"type": "string"},
            "query_online": {"type": "boolean"},
            "library_items": {"type": "array", "items": {"type": "object"}},
            "extra": {"type": "object"},
            "out_dir": {"type": "string"},
            "model": {"type": "string"},
            "include_memories": {"type": "boolean"},
            "async_mode": {"type": "boolean"},
            "timeout": {"type": "integer"},
        }, ["sop"]),
        tool_run_sop,
        None,
    ),
    (
        "po_custom_skills",
        "Manage built-in (5 Studio SOPs) & custom Markdown skills (`list`, `get`, `save`, `import_file`, `delete`), fetch remote SKILL.md URLs (`fetch_url`), or `run` any built-in/custom skill with files & memories.",
        _schema({
            "action": {"type": "string", "enum": ["list", "get", "save", "import_file", "delete", "fetch_url", "run"]},
            "skill_id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "category": {"type": "string"},
            "content": {"type": "string"},
            "file_path": {"type": "string", "description": "Local .md/SKILL.md path for action='import_file'"},
            "include_content": {"type": "boolean", "description": "Include full Markdown content in action='list'"},
            "url": {"type": "string"},
            "skill_file": {"type": "string"},
            "prompt": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}},
            "out_dir": {"type": "string"},
            "model": {"type": "string"},
            "include_memories": {"type": "boolean"},
            "async_mode": {"type": "boolean"},
            "timeout": {"type": "integer"},
        }, ["action"]),
        tool_custom_skills,
        None,
    ),
    (
        "po_figure_studio",
        "Create scientific figures via 3 routes (`data_code` Matplotlib, `schematic_svg`, `concept_image`), register versioned bundles, apply normalized `bbox` ('x,y,w,h' or {x,y,w,h}) region-circle local edits (`revise`), or `restore` any version.",
        _schema({
            "action": {"type": "string", "enum": ["list", "ensure_bundle", "create", "revise", "restore"]},
            "dir_path": {"type": "string"},
            "out_dir": {"type": "string"},
            "bundle": {"type": "string"},
            "image": {"type": "string"},
            "title": {"type": "string"},
            "code_file": {"type": "string"},
            "description": {"type": "string"},
            "mode": {"type": "string", "enum": ["data_code", "schematic_svg", "concept_image"]},
            "prompt": {"type": "string"},
            "data_file": {"type": "string"},
            "ref_image": {"type": "string"},
            "instruction": {"type": "string"},
            "bbox": {"description": "Normalized box in [0,1]: object {x,y,w,h}, array [x,y,w,h], or 'x,y,w,h' string"},
            "version": {"type": "string"},
            "model": {"type": "string"},
            "image_model": {"type": "string"},
            "base_dir": {"type": "string"},
            "timeout": {"type": "integer"},
        }, ["action"]),
        tool_figure_studio,
        None,
    ),
    (
        "po_literature_library",
        "Search 8 scholarly databases (`search_online`), import/browse paginated SQLite library (`import_doi`, `import_pdf`, `list` with folder/offset/limit, `save`, `delete`), generate citation graphs (`build_graph`), and export BibTeX or GB/T 7714 (`export_bib`).",
        _schema({
            "action": {"type": "string", "enum": ["search_online", "import_doi", "import_pdf", "list", "save", "delete", "build_graph", "export_bib"]},
            "query": {"type": "string"},
            "folder": {"type": "string", "description": "Optional folder filter for action='list'"},
            "offset": {"type": "integer", "description": "Pagination offset for action='list' (default 0)"},
            "limit": {"type": "integer", "description": "Page size (1..100 for list, default 25)"},
            "expand": {"type": "boolean"},
            "doi": {"type": "string"},
            "pdf_path": {"type": "string"},
            "item_id": {"type": "string"},
            "item": {"type": "object"},
            "item_ids": {"type": "array", "items": {"type": "string"}},
            "items": {"type": "array", "items": {"type": "object"}},
            "format": {"type": "string", "enum": ["bibtex", "gb7714"]},
            "model": {"type": "string"},
            "no_llm": {"type": "boolean"},
            "save_to_library": {"type": "boolean"},
            "timeout": {"type": "integer"},
        }, ["action"]),
        tool_literature_library,
        None,
    ),
    (
        "po_latex_copilot",
        "AI LaTeX editing copilot (`edit`: supports line selection ranges & error logs), inline claim-to-citation traceability (`trace_citations`), and award-paper abstract diagnosis & sentence change-table polishing (`polish_abstract`).",
        _schema({
            "action": {"type": "string", "enum": ["edit", "trace_citations", "polish_abstract"]},
            "file_path": {"type": "string"},
            "instruction": {"type": "string"},
            "selection_start": {"type": "integer"},
            "selection_end": {"type": "integer"},
            "error_log": {"type": "string"},
            "run_dir": {"type": "string"},
            "tex_file": {"type": "string"},
            "text": {"type": "string", "description": "Optional inline abstract text for action='polish_abstract'"},
            "intro_text": {"type": "string", "description": "Optional inline introduction context for action='polish_abstract'"},
            "mode": {"type": "string", "enum": ["diagnose", "rewrite"], "description": "Offline diagnosis ('diagnose') or AI sentence change-table rewrite ('rewrite')"},
            "model": {"type": "string"},
        }, ["action"]),
        tool_latex_copilot,
        None,
    ),
    (
        "po_latex_workbench",
        "Safe LaTeX live preview workbench: `open` project, `read` with mtimeMs, conflict-guarded `save` with `.edit_backup/` preservation, `restore` original, auto-CJK XeLaTeX `compile` (`-pdfxe` vs `-pdf`) into .live_preview/, SyncTeX `sync_view`/`sync_edit`, and clean `export_overleaf` ZIP.",
        _schema({
            "action": {"type": "string", "enum": ["open", "read", "save", "restore", "compile", "sync_view", "sync_edit", "export_overleaf"]},
            "run_dir": {"type": "string"},
            "tex_file": {"type": "string"},
            "content": {"type": "string", "description": "Updated UTF-8 .tex/.bib text for action='save'"},
            "expected_mtime_ms": {"type": "integer", "description": "Conflict guard mtimeMs returned by action='read'"},
            "line": {"type": "integer"},
            "end_line": {"type": "integer", "description": "Optional selection end line for multi-line sync_view highlight"},
            "column": {"type": "integer"},
            "page": {"type": "integer"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "pdf_path": {"type": "string", "description": "Optional compiled PDF path inside run_dir for sync_view/sync_edit"},
            "out_zip": {"type": "string"},
            "allow_overwrite": {"type": "boolean"},
            "timeout": {"type": "integer"},
        }, ["action", "run_dir"]),
        tool_latex_workbench,
        None,
    ),
    (
        "po_submission_audit",
        "Run pre-submission verification & author report tools: Nature + award-paper style/figure `manuscript-audit`, `author-report` (`report.md`), live `cite-online` DOI/arXiv/S2 verification, `arxiv` submission zip, double-`blind` leak check, LaTeX log `diagnose`, draft `versions`, `latexdiff` PDF (`diff`), and `lean-audit` formal proof verification.",
        _schema({
            "action": {"type": "string", "enum": ["manuscript-audit", "author-report", "cite-online", "arxiv", "blind", "diagnose", "versions", "diff", "lean-audit"]},
            "run_dir": {"type": "string"},
            "project_dir": {"type": "string"},
            "out_path": {"type": "string"},
            "old_tex": {"type": "string"},
            "new_tex": {"type": "string"},
            "keep_comments": {"type": "boolean"},
            "merge_inputs": {"type": "boolean"},
            "find_packages": {"type": "boolean"},
            "only_changed_pages": {"type": "boolean"},
            "timeout_sec": {"type": "number"},
        }, ["action"]),
        tool_submission_audit,
        None,
    ),
    (
        "po_export_document",
        "Export Markdown reports to offline Claude Artifacts-style interactive HTML (TOC sidebar, themes, provenance pills, MathML, SVG Mermaid), styled Word (.docx), publication PDF, or Google Drive (`status` / `export` / `export_google_drive`).",
        _schema({
            "action": {"type": "string", "enum": ["export", "export_google_drive", "status"]},
            "input_md": {"type": "string"},
            "markdown_content": {"type": "string"},
            "format": {"type": "string", "enum": ["html", "docx", "pdf"]},
            "out_path": {"type": "string"},
            "title": {"type": "string"},
            "drive_dir": {"type": "string"},
            "interactive_html": {"type": "boolean", "description": "Default true for html: builds Artifacts-style interactive HTML"},
        }),
        tool_export_document,
        None,
    ),
    (
        "po_compute_and_memory",
        "Manage persistent Research Memories & Preferences (auto-injected into writing and SOPs) and remote SSH/GPU compute hosts (`test_host` with nvidia-smi telemetry, `exec_ssh`, `scp_transfer`).",
        _schema({
            "action": {
                "type": "string",
                "enum": [
                    "list_memories", "save_memory", "delete_memory",
                    "get_preferences", "save_preferences",
                    "list_hosts", "save_host", "delete_host", "test_host", "exec_ssh", "scp_transfer",
                ],
            },
            "memory_id": {"type": "string"},
            "category": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "enabled": {"type": "boolean"},
            "output_language": {"type": "string"},
            "citation_style": {"type": "string"},
            "delivery_format": {"type": "string"},
            "custom_rules": {"type": "string"},
            "host_id": {"type": "string"},
            "alias": {"type": "string"},
            "host": {"type": "string"},
            "port": {"type": "integer"},
            "username": {"type": "string"},
            "auth_type": {"type": "string"},
            "key_path": {"type": "string"},
            "private_key_pem": {"type": "string"},
            "remote_work_dir": {"type": "string"},
            "notes": {"type": "string"},
            "command": {"type": "string"},
            "work_dir": {"type": "string"},
            "direction": {"type": "string", "enum": ["upload", "download"]},
            "local_path": {"type": "string"},
            "remote_path": {"type": "string"},
            "timeout": {"type": "integer"},
        }, ["action"]),
        tool_compute_and_memory,
        None,
    ),
    (
        "po_studio_bridge",
        "Connect to the running PaperOrchestra Studio desktop app over its loopback TCP bridge (`status`, auto-launch & `open_studio` tab, `list_bridge_jobs`, `bridge_job_status`, `cancel_bridge_job`, `dispatch_bridge_action`) or inspect/launch companion integrations (`live_workbench`, `tool_tree`, `canvas_console`).",
        _schema({
            "action": {
                "type": "string",
                "enum": ["status", "open_studio", "list_bridge_jobs", "bridge_job_status", "cancel_bridge_job", "dispatch_bridge_action", "launch_workbench"],
            },
            "tab": {"type": "string", "enum": ["workspace", "studio", "library", "runs", "artifacts", "settings"]},
            "auto_launch": {"type": "boolean"},
            "launch_timeout": {"type": "number"},
            "job_id": {"type": "string"},
            "command": {"type": "string"},
            "action_args": {"type": "object"},
            "wait": {"type": "boolean"},
            "target": {"type": "string", "enum": ["live_workbench", "tool_tree", "canvas_console", "qoder_plugin"]},
            "check_only": {"type": "boolean"},
            "open_browser": {"type": "boolean"},
            "timeout": {"type": "number"},
        }, ["action"]),
        tool_studio_bridge,
        None,
    ),
    (
        "po_settings_and_setup",
        "Inspect/update PaperOrchestra Studio SQLite settings (`get_settings`, `update_settings`), strictly validate `providers.json` (`validate_providers_json`), check boolean 20-platform credential readiness (`env_readiness`, never leaks keys), and discover backend setup scripts (`setup_info`).",
        _schema({
            "action": {"type": "string", "enum": ["get_settings", "update_settings", "validate_providers_json", "env_readiness", "setup_info"]},
            "paper_orchestra_dir": {"type": "string"},
            "python_path": {"type": "string"},
            "output_root_dir": {"type": "string"},
            "validate": {"type": "boolean"},
            "providers_json_text": {"type": "string"},
            "providers_json_path": {"type": "string"},
        }, ["action"]),
        tool_settings_and_setup,
        None,
    ),
    (
        "po_open_workbench",
        "Open the native interactive PaperOrchestra Studio Workbench UI (projects, pipeline monitor, Canvas collaboration intervention, Figure Review Bench bbox editor, LaTeX XeLaTeX editor, library, audit/export, skills & safe tools console).",
        _schema({
            "tab": {"type": "string", "enum": ["overview", "projects", "runs", "library", "figures", "latex", "exports", "skills", "tools"]},
            "project_id": {"type": "string"},
            "run_id": {"type": "string"},
            "run_dir": {"type": "string"},
            "notice": {"type": "string"},
        }),
        tool_open_workbench,
        {
            "ui": {"resourceUri": "ui://paper-orchestra/workbench.html"},
            "com.google.antigravity/ui": {
                "entrypoints": [
                    {"type": "aux_pane", "title": "PaperOrchestra 工作台"},
                    {"type": "global", "title": "PaperOrchestra Studio"},
                ]
            },
        },
    ),
    (
        "po_open_tool_tree",
        "Open the interactive PaperOrchestra Studio Tool Tree UI showing all 10 workflow groups, parameter requirements, risk tiers, and quick delegation to chat.",
        _schema({
            "group": {"type": "string"},
            "search": {"type": "string"},
            "risk": {"type": "string", "enum": ["all", "read", "write", "destructive"]},
        }),
        tool_open_tool_tree,
        {
            "ui": {"resourceUri": "ui://paper-orchestra/tool-tree.html"},
            "com.google.antigravity/ui": {
                "entrypoints": [
                    {"type": "aux_pane", "title": "PaperOrchestra 工具树"},
                ]
            },
        },
    ),
    (
        "po_ui_snapshot",
        "Internal app-only tool powering PaperOrchestra Studio UI views (`snapshot`, `read_image_data_url`, `render_pdf_page`, `open_file_external`).",
        _schema({
            "action": {"type": "string", "enum": ["snapshot", "read_image_data_url", "render_pdf_page", "open_file_external"]},
            "project_id": {"type": "string"},
            "run_id": {"type": "string"},
            "run_dir": {"type": "string"},
            "path": {"type": "string"},
            "page": {"type": "integer"},
            "dpi": {"type": "integer"},
        }),
        tool_ui_snapshot,
        {"ui": {"visibility": ["app"]}},
    ),
]

TOOL_MAP = {name: (desc, schema, fn, meta) for name, desc, schema, fn, meta in TOOLS}


# ---------------------------------------------------------------------------
# Resources & Prompts
# ---------------------------------------------------------------------------

RESOURCES = [
    {
        "uri": "paper-orchestra://templates",
        "name": "PaperOrchestra LaTeX Templates",
        "description": "All 19 conference, journal, Chinese GB/T 7714, and math-modeling LaTeX templates with venue.json profiles.",
        "mimeType": "application/json",
    },
    {
        "uri": "paper-orchestra://memories",
        "name": "Research Memories & Preferences",
        "description": "Long-term lab research preferences and enabled research memories stored in SQLite.",
        "mimeType": "application/json",
    },
    {
        "uri": "paper-orchestra://projects",
        "name": "PaperOrchestra Studio Projects",
        "description": "Saved paper projects and recent run summaries from the local SQLite database.",
        "mimeType": "application/json",
    },
    {
        "uri": "paper-orchestra://skills",
        "name": "PaperOrchestra Built-in & Custom Skills",
        "description": "The 5 built-in Studio scientific SOP skills plus user custom Markdown skills.",
        "mimeType": "application/json",
    },
    {
        "uri": "ui://paper-orchestra/workbench.html",
        "name": "PaperOrchestra Live Workbench UI",
        "description": "Interactive PaperOrchestra Studio workbench UI (SEP-1865 MCP App).",
        "mimeType": "text/html;profile=mcp-app",
        "_meta": {"ui": {"prefersBorder": False}},
    },
    {
        "uri": "ui://paper-orchestra/tool-tree.html",
        "name": "PaperOrchestra Interactive Tool Tree UI",
        "description": "Interactive 10-group PaperOrchestra tool map UI (SEP-1865 MCP App).",
        "mimeType": "text/html;profile=mcp-app",
        "_meta": {"ui": {"prefersBorder": False}},
    },
]

PROMPTS = [
    {
        "name": "deep-read-paper",
        "description": "Run the 6-step evidence-grounded deep paper reading SOP with strict provenance tags and interactive HTML export.",
        "arguments": [
            {"name": "paper_path", "description": "Path to paper PDF/HTML/Markdown", "required": True},
            {"name": "focus_question", "description": "Optional specific aspect to audit", "required": False},
        ],
    },
    {
        "name": "literature-review",
        "description": "Execute anti-fabrication thematic literature review (Q->E->T->A) across online search and local library items.",
        "arguments": [
            {"name": "topic", "description": "Research topic or question", "required": True},
        ],
    },
    {
        "name": "start-paper-pipeline",
        "description": "Validate providers/templates, prepare or create a project, and start a full PaperOrchestra multi-agent run.",
        "arguments": [
            {"name": "template_name", "description": "Target venue template e.g. journal-nature or cvpr2025", "required": False},
        ],
    },
    {
        "name": "pre-submission-checkup",
        "description": "Run Nature manuscript audit, online citation verification, double-blind check, LaTeX diagnosis, and Overleaf zip export on a run_dir.",
        "arguments": [
            {"name": "run_dir", "description": "Path to completed PaperOrchestra run directory", "required": True},
        ],
    },
    {
        "name": "open-live-workbench",
        "description": "Open the interactive PaperOrchestra Workbench UI in Antigravity or Claude Desktop (or inspect/open the Desktop Studio bridge).",
        "arguments": [
            {"name": "tab", "description": "Initial workbench tab: overview, projects, runs, library, figures, latex, exports, skills, tools", "required": False},
        ],
    },
]


def _read_resource(ctx: RuntimeContext, uri: str) -> Dict[str, Any]:
    if uri == "ui://paper-orchestra/workbench.html":
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/html;profile=mcp-app",
                    "text": _load_ui_html("workbench.html", "PO_WORKBENCH_HTML"),
                    "_meta": {"ui": {"prefersBorder": False}},
                }
            ]
        }
    if uri == "ui://paper-orchestra/tool-tree.html":
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/html;profile=mcp-app",
                    "text": _load_ui_html("tool-tree.html", "PO_TOOL_TREE_HTML"),
                    "_meta": {"ui": {"prefersBorder": False}},
                }
            ]
        }
    if uri == "paper-orchestra://templates":
        data = tool_list_templates(ctx, {})
    elif uri == "paper-orchestra://memories":
        data = tool_compute_and_memory(ctx, {"action": "list_memories"})
    elif uri == "paper-orchestra://projects":
        data = tool_projects(ctx, {"action": "list"})
    elif uri == "paper-orchestra://skills":
        data = tool_custom_skills(ctx, {"action": "list", "include_content": True})
    else:
        raise KeyError(f"Unknown resource URI: {uri}")
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, ensure_ascii=False, indent=2)}]}


def _get_prompt(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if name == "deep-read-paper":
        pp = args.get("paper_path", "<paper_path>")
        fq = args.get("focus_question", "")
        text = (
            f"Use `po_run_sop` with `sop='paper_reading'`, `files=['{pp}']`"
            + (f", `question='{fq}'`" if fq else "")
            + ". Preserve strict 【原文】/【审读推导】/【不可核实】 provenance tags. "
            "If requested, export the interactive Artifacts-style HTML via `po_export_document`."
        )
    elif name == "literature-review":
        topic = args.get("topic", "<topic>")
        text = (
            f"Run a thematic Q->E->T->A literature review on '{topic}': first inspect `po_literature_library` (`list` & `search_online`), "
            "then invoke `po_run_sop` with `sop='lit_review'` and export any deliverable with `po_export_document`."
        )
    elif name == "start-paper-pipeline":
        tpl = args.get("template_name", "cvpr2025")
        text = (
            f"1) Call `po_check_environment` and `po_check_providers`.\n"
            f"2) Inspect templates via `po_list_templates` (target: `{tpl}`).\n"
            "3) Create or select a project with `po_projects`, then launch `po_start_paper_run` and monitor via `po_job_manage`."
        )
    elif name == "pre-submission-checkup":
        rd = args.get("run_dir", "<run_dir>")
        text = (
            f"Run pre-submission checks on `{rd}` using `po_submission_audit`: "
            "`manuscript-audit`, `cite-online`, `blind`, `diagnose`, and optionally package `arxiv` or `po_latex_workbench` (`export_overleaf`)."
        )
    elif name == "open-live-workbench":
        tab = args.get("tab", "overview")
        text = (
            f"Call `po_open_workbench` with `tab='{tab}'` to launch the interactive PaperOrchestra Studio workbench UI, "
            "or `po_open_tool_tree` to display the 10-group tool map, or `po_studio_bridge(action='open_studio')` for the Desktop app."
        )
    else:
        raise KeyError(f"Unknown prompt: {name}")
    return {"description": name, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 Stdio Transport Loop
# ---------------------------------------------------------------------------

def _write_msg(payload: Dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _handle_request(ctx: RuntimeContext, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    # Notifications have no id and expect no response
    if req_id is None:
        return None

    try:
        if method == "initialize":
            client_ver = str(params.get("protocolVersion") or "")
            proto = client_ver if client_ver in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            result = {
                "protocolVersion": proto,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            tools_list = []
            for name, desc, schema, _, meta in TOOLS:
                entry: Dict[str, Any] = {"name": name, "description": desc, "inputSchema": schema}
                if meta:
                    entry["_meta"] = meta
                tools_list.append(entry)
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tools_list}}

        if method == "tools/call":
            tname = str(params.get("name") or "")
            targs = params.get("arguments") or {}
            if tname not in TOOL_MAP:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Unknown tool: {tname}"},
                }
            _, _, fn, _ = TOOL_MAP[tname]
            try:
                out = fn(ctx, targs)
                out_redacted = _redact_obj(out, ctx.secrets)
                if tname in ("po_open_workbench", "po_open_tool_tree") and isinstance(out_redacted, dict):
                    content_text = str(out_redacted.get("summary") or f"{tname} ready.")
                else:
                    content_text = json.dumps(out_redacted, ensure_ascii=False, indent=2)
                result_obj: Dict[str, Any] = {
                    "content": [{"type": "text", "text": content_text}],
                    "isError": False,
                }
                if isinstance(out_redacted, dict):
                    sc_bytes = len(json.dumps(out_redacted, ensure_ascii=False).encode("utf-8"))
                    if sc_bytes <= 950_000:
                        result_obj["structuredContent"] = out_redacted
                return {"jsonrpc": "2.0", "id": req_id, "result": result_obj}
            except Exception as exc:  # noqa: BLE001
                err_txt = _redact_text(f"{type(exc).__name__}: {exc}", ctx.secrets)
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps({"ok": False, "error": err_txt}, ensure_ascii=False)}],
                        "isError": True,
                    },
                }

        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": RESOURCES}}

        if method == "resources/read":
            uri = str(params.get("uri") or "")
            return {"jsonrpc": "2.0", "id": req_id, "result": _read_resource(ctx, uri)}

        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": PROMPTS}}

        if method == "prompts/get":
            pname = str(params.get("name") or "")
            pargs = params.get("arguments") or {}
            return {"jsonrpc": "2.0", "id": req_id, "result": _get_prompt(pname, pargs)}

        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    except KeyError as exc:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": str(exc)}}
    except Exception as exc:  # noqa: BLE001
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32000, "message": _redact_text(f"{type(exc).__name__}: {exc}", ctx.secrets)},
        }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="PaperOrchestra Studio zero-dependency MCP stdio server")
    parser.add_argument("--self-check", action="store_true", help="Print environment summary and exit")
    args = parser.parse_args(argv)

    ctx = RuntimeContext()
    if args.self_check:
        res = {
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "tools": len(TOOLS),
            "resources": len(RESOURCES),
            "prompts": len(PROMPTS),
            "env": tool_check_environment(ctx, {}),
        }
        sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
        return 0

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            _write_msg({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}})
            continue
        if isinstance(msg, dict):
            resp = _handle_request(ctx, msg)
            if resp is not None:
                _write_msg(resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
