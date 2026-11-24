#!/usr/bin/env bash
set -euo pipefail

# Install PaperOrchestra Studio MCP runtime locally and register it in
# Claude Desktop's claude_desktop_config.json safely with timestamped backup.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLUGINS_ROOT="$(cd "${CLAUDE_DIR}/.." && pwd)"
SHARED_SERVER="${PLUGINS_ROOT}/shared/mcp_server/paper_orchestra_mcp.py"

INSTALL_BASE="${HOME}/.local/share/paper-orchestra-studio-plugin"
SERVER_DST="${INSTALL_BASE}/server/paper_orchestra_mcp.py"
DIST_DST="${INSTALL_BASE}/dist"

PYTHON_BIN="${PO_MCP_PYTHON:-$(command -v python3)}"
BACKEND_PYTHON="${PAPER_ORCHESTRA_PYTHON:-}"
REPO_DIR="${PAPER_ORCHESTRA_REPO:-}"
DEFAULT_PDF_DIR="${PO_DEFAULT_PDF_DIR:-}"
DEFAULT_HTML_DIR="${PO_DEFAULT_HTML_DIR:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      PYTHON_BIN="$2"; shift 2 ;;
    --backend-python)
      BACKEND_PYTHON="$2"; shift 2 ;;
    --repo)
      REPO_DIR="$2"; shift 2 ;;
    --pdf-dir)
      DEFAULT_PDF_DIR="$2"; shift 2 ;;
    --html-dir)
      DEFAULT_HTML_DIR="$2"; shift 2 ;;
    *)
      echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

mkdir -p "${INSTALL_BASE}/server" "${INSTALL_BASE}/data" "${INSTALL_BASE}/assets/ui" "${DIST_DST}"
cp "${SHARED_SERVER}" "${SERVER_DST}"
chmod +x "${SERVER_DST}"

for ui_html in workbench.html tool-tree.html; do
  if [[ -f "${PLUGINS_ROOT}/shared/ui_dist/${ui_html}" ]]; then
    cp "${PLUGINS_ROOT}/shared/ui_dist/${ui_html}" "${INSTALL_BASE}/assets/ui/${ui_html}"
  fi
done

for pkg in paper-orchestra-studio.plugin paper-orchestra-studio.mcpb; do
  if [[ -f "${CLAUDE_DIR}/dist/${pkg}" ]]; then
    cp "${CLAUDE_DIR}/dist/${pkg}" "${DIST_DST}/${pkg}"
  fi
done

if [[ "$(uname -s)" == "Darwin" ]]; then
  CFG_PATH="${HOME}/Library/Application Support/Claude/claude_desktop_config.json"
else
  CFG_PATH="${XDG_CONFIG_HOME:-${HOME}/.config}/Claude/claude_desktop_config.json"
fi

mkdir -p "$(dirname "${CFG_PATH}")"
if [[ -f "${CFG_PATH}" ]]; then
  BACKUP_PATH="${CFG_PATH}.bak.$(date +%Y%m%d_%H%M%S)"
  cp "${CFG_PATH}" "${BACKUP_PATH}"
  echo "Backed up existing Claude Desktop config -> ${BACKUP_PATH}"
fi

PO_CFG_PATH="${CFG_PATH}" \
PO_SERVER_DST="${SERVER_DST}" \
PO_SERVER_PY="${PYTHON_BIN}" \
PO_BACKEND_PY="${BACKEND_PYTHON}" \
PO_DATA_DST="${INSTALL_BASE}/data" \
PO_ARG_REPO="${REPO_DIR}" \
PO_ARG_PDF="${DEFAULT_PDF_DIR}" \
PO_ARG_HTML="${DEFAULT_HTML_DIR}" \
"${PYTHON_BIN}" -B - <<'PY'
import json
import os
from pathlib import Path
import tempfile

cfg_path = Path(os.environ["PO_CFG_PATH"])
if cfg_path.is_file() and cfg_path.read_text(encoding="utf-8").strip():
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
else:
    data = {}

if not isinstance(data, dict):
    raise SystemExit("claude_desktop_config.json top-level must be a JSON object")

servers = data.setdefault("mcpServers", {})
if not isinstance(servers, dict):
    raise SystemExit("claude_desktop_config.json `mcpServers` must be an object")

existing = servers.get("paper-orchestra-studio")
existing_env = dict(existing.get("env") or {}) if isinstance(existing, dict) and isinstance(existing.get("env"), dict) else {}

env = {
    **existing_env,
    "PAPER_ORCHESTRA_PLUGIN_DATA": os.environ["PO_DATA_DST"],
}
if os.environ.get("PO_ARG_REPO"):
    env["PAPER_ORCHESTRA_REPO"] = os.environ["PO_ARG_REPO"]
if os.environ.get("PO_BACKEND_PY"):
    env["PAPER_ORCHESTRA_PYTHON"] = os.environ["PO_BACKEND_PY"]
if os.environ.get("PO_ARG_PDF"):
    env["PO_DEFAULT_PDF_DIR"] = os.environ["PO_ARG_PDF"]
if os.environ.get("PO_ARG_HTML"):
    env["PO_DEFAULT_HTML_DIR"] = os.environ["PO_ARG_HTML"]

servers["paper-orchestra-studio"] = {
    "command": os.environ["PO_SERVER_PY"],
    "args": [os.environ["PO_SERVER_DST"]],
    "env": env,
}

fd, tmp = tempfile.mkstemp(dir=str(cfg_path.parent), prefix=".claude_cfg_", suffix=".tmp")
os.close(fd)
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.replace(tmp, cfg_path)
print(f"Updated Claude Desktop config -> {cfg_path}")
PY
