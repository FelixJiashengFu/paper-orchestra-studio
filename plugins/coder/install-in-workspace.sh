#!/usr/bin/env bash
# Install PaperOrchestra Studio for the coding agents inside a Coder (coder.com) workspace.
#
# Works for any workspace image: it clones (or updates) this repository and registers the
# plugin with whichever supported agents are installed there (Claude Code, Codex).
# Safe to run on every workspace start; it never deletes anything.
#
#   PO_STUDIO_DIR   install location          (default: $HOME/paper-orchestra-studio)
#   PO_STUDIO_REF   branch or tag to check out (default: main)
#   PO_STUDIO_REPO  git URL                    (default: the public GitHub repository)
set -euo pipefail

PO_STUDIO_DIR="${PO_STUDIO_DIR:-$HOME/paper-orchestra-studio}"
PO_STUDIO_REF="${PO_STUDIO_REF:-main}"
PO_STUDIO_REPO="${PO_STUDIO_REPO:-https://github.com/FelixJiashengFu/paper-orchestra-studio.git}"

log() { printf '[paper-orchestra] %s\n' "$*"; }

command -v git >/dev/null 2>&1 || { log "git is required"; exit 1; }
PYTHON_BIN="$(command -v python3 || command -v python || true)"
[ -n "$PYTHON_BIN" ] || { log "python3 is required"; exit 1; }

if [ -d "$PO_STUDIO_DIR/.git" ]; then
  log "updating $PO_STUDIO_DIR ($PO_STUDIO_REF)"
  git -C "$PO_STUDIO_DIR" fetch --depth 1 origin "$PO_STUDIO_REF"
  git -C "$PO_STUDIO_DIR" merge --ff-only FETCH_HEAD || log "local changes present; keeping the current checkout"
else
  log "cloning into $PO_STUDIO_DIR ($PO_STUDIO_REF)"
  git clone --depth 1 --branch "$PO_STUDIO_REF" "$PO_STUDIO_REPO" "$PO_STUDIO_DIR"
fi

SERVER="$PO_STUDIO_DIR/plugins/shared/mcp_server/paper_orchestra_mcp.py"
"$PYTHON_BIN" "$SERVER" --self-check >/dev/null
log "MCP server self-check passed"

if command -v claude >/dev/null 2>&1; then
  claude plugin marketplace add "$PO_STUDIO_DIR" >/dev/null 2>&1 || true
  if claude plugin install paper-orchestra-studio@paper-orchestra-studio >/dev/null 2>&1; then
    log "Claude Code: plugin installed (skills, auditor agent, both MCP servers)"
  else
    log "Claude Code: plugin install did not complete; run 'claude plugin install paper-orchestra-studio@paper-orchestra-studio' in a session"
  fi
else
  log "Claude Code not found; skipped"
fi

if command -v codex >/dev/null 2>&1; then
  if codex plugin marketplace add "$PO_STUDIO_DIR" >/dev/null 2>&1; then
    log "Codex: marketplace added; enable 'paper-orchestra-studio' in the plugin list"
  else
    log "Codex: could not add the marketplace; run 'codex plugin marketplace add $PO_STUDIO_DIR'"
  fi
else
  log "Codex not found; skipped"
fi

log "done. Check parity with: $PYTHON_BIN $PO_STUDIO_DIR/plugins/scripts/verify_platforms.py"
