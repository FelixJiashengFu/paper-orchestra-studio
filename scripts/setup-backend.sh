#!/usr/bin/env bash
# Install the PaperOrchestra Python backend for PaperOrchestra Studio (macOS / Linux).
#
#   1. Finds Python 3.11+.
#   2. Copies the "paper-orchestra" folder next to this script to ~/paper-orchestra
#      (Studio looks there by default). An existing install is updated in place;
#      its .venv, .env and providers.json are kept.
#   3. Creates paper-orchestra/.venv and installs requirements.txt.
#   4. Checks for a TeX distribution (latexmk, pdflatex, xelatex).
#
# Usage:
#   bash setup-backend.sh                    # install to ~/paper-orchestra
#   bash setup-backend.sh --target DIR       # install somewhere else
#   bash setup-backend.sh --in-place         # use the folder where it is
#   bash setup-backend.sh --python /path/to/python3.12
set -euo pipefail

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m[ok]\033[0m %s\n' "$1"; }
warn() { printf '    \033[33m[!]\033[0m  %s\n' "$1"; }
fail() { printf '\n\033[31mError: %s\033[0m\n' "$1" >&2; exit 1; }

TARGET=""
IN_PLACE=0
PYTHON=""
while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:?--target needs a folder}"; shift 2 ;;
    --in-place) IN_PLACE=1; shift ;;
    --python) PYTHON="${2:?--python needs a path}"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done

echo "=== PaperOrchestra backend setup ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE=""
for c in "$SCRIPT_DIR/paper-orchestra" "$SCRIPT_DIR/../paper-orchestra"; do
  if [ -f "$c/paper_writing_cli.py" ]; then SOURCE="$(cd "$c" && pwd)"; break; fi
done
[ -n "$SOURCE" ] || fail "could not find the paper-orchestra folder next to this script."
ok "Backend source: $SOURCE"

if [ "$IN_PLACE" = 1 ]; then
  DEST="$SOURCE"
else
  DEST="${TARGET:-$HOME/paper-orchestra}"
  mkdir -p "$DEST"
  DEST="$(cd "$DEST" && pwd)"
fi

# ------------------------------------------------------------------ python
step "Looking for Python 3.11 or newer"
check_python() {
  "$1" -c 'import sys; print("OK" if sys.version_info >= (3, 11) else "OLD")' 2>/dev/null | grep -q '^OK$'
}
PY=""
if [ -n "$PYTHON" ]; then
  check_python "$PYTHON" || fail "$PYTHON is not Python 3.11 or newer."
  PY="$PYTHON"
else
  for c in python3.13 python3.12 python3.11 python3 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if command -v "$c" >/dev/null 2>&1 && check_python "$c"; then PY="$(command -v "$c")"; break; fi
  done
fi
if [ -z "$PY" ]; then
  fail "Python 3.11 or newer was not found.
  macOS:  brew install python@3.12   (or https://www.python.org/downloads/)
  Linux:  install python3.12 and python3.12-venv with your package manager
  then run this script again."
fi
ok "Python: $PY ($("$PY" --version 2>&1))"

# ------------------------------------------------------------------ copy
if [ "$DEST" != "$SOURCE" ]; then
  step "Copying the backend to $DEST"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude '.venv' --exclude '__pycache__' --exclude '.env' --exclude 'providers.json' "$SOURCE/" "$DEST/"
  else
    (cd "$SOURCE" && tar cf - --exclude .venv --exclude __pycache__ --exclude .env --exclude providers.json .) | (cd "$DEST" && tar xf -)
  fi
  ok "Files copied"
fi

# ------------------------------------------------------------------ venv
VENV_PY="$DEST/.venv/bin/python"
if [ -x "$VENV_PY" ]; then
  step "Reusing the existing virtual environment"
else
  step "Creating the virtual environment (.venv)"
  "$PY" -m venv "$DEST/.venv" || fail "creating the virtual environment failed (on Debian/Ubuntu install python3-venv)."
fi
ok "$VENV_PY"

step "Installing Python packages (this can take a few minutes)"
"$VENV_PY" -m pip install --disable-pip-version-check --upgrade pip || fail "upgrading pip failed."
"$VENV_PY" -m pip install --disable-pip-version-check --prefer-binary -r "$DEST/requirements.txt" \
  || fail "installing requirements.txt failed (see the pip output above)."
ok "Packages installed"

if "$VENV_PY" "$DEST/paper_writing_cli.py" --help >/dev/null 2>&1; then
  ok "paper_writing_cli.py starts"
else
  warn "paper_writing_cli.py --help did not exit cleanly"
fi

# ------------------------------------------------------------------ TeX
step "Checking TeX (needed to compile PDFs)"
# MacTeX puts its tools here; GUI apps and fresh shells may not have it on PATH yet.
[ -d /Library/TeX/texbin ] && PATH="$PATH:/Library/TeX/texbin"
missing=""
for t in latexmk pdflatex xelatex bibtex; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t  $(command -v "$t")"; else missing="$missing $t"; fi
done
if [ -n "$missing" ]; then
  warn "Not found:$missing"
  warn "macOS: install MacTeX (brew install --cask mactex-no-gui) or BasicTeX."
  warn "Linux: install texlive-full, or texlive-latex-extra + texlive-xetex + latexmk."
  warn "Studio still runs without TeX, but cannot build PDFs."
fi

# ------------------------------------------------------------------ done
echo
printf '\033[32mBackend ready.\033[0m\n'
echo "  Backend folder: $DEST"
echo "  Python:         $VENV_PY"
echo
if [ "$DEST" = "$(cd "$HOME" && pwd)/paper-orchestra" ]; then
  echo "Open PaperOrchestra Studio. On first start it finds this backend by itself."
  echo "If Studio was opened before, set both paths above in Studio > 环境与算力 (Environment)."
else
  echo "In PaperOrchestra Studio > 环境与算力 (Environment), set the two paths above."
fi
echo "Add your model API keys in Studio > 环境与算力 (Environment) as well."
