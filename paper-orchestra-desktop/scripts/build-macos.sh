#!/usr/bin/env bash
set -euo pipefail

# Build PaperOrchestra Studio for macOS (.app + .dmg)
# Usage:
#   ./scripts/build-macos.sh              # builds for current Mac architecture (Apple Silicon or Intel)
#   ./scripts/build-macos.sh universal    # builds Universal Binary (arm64 + x86_64)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_DIR}"

echo "=== PaperOrchestra Studio macOS Builder ==="

if ! xcode-select -p >/dev/null 2>&1; then
  echo "Error: Xcode Command Line Tools not found. Run: xcode-select --install"
  exit 1
fi

for cmd in rustc cargo node npm; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "Error: '${cmd}' is not installed."
    exit 1
  fi
done

TARGET_FLAG=""
if [[ "${1:-}" == "universal" ]]; then
  echo "Ensuring Rust targets aarch64-apple-darwin and x86_64-apple-darwin..."
  rustup target add aarch64-apple-darwin x86_64-apple-darwin
  TARGET_FLAG="--target universal-apple-darwin"
fi

echo "Installing npm dependencies..."
npm install

echo "Running Rust unit tests..."
(cd src-tauri && cargo test)

# The .dmg step lays out its window by scripting Finder, which needs an
# "Automation" permission and a logged-in screen; without them it fails or
# hangs after the .app is already built. So build only the .app by default.
# PO_MAC_DMG=1 also makes the .dmg, without the Finder scripting (CI=true).
if [[ "${PO_MAC_DMG:-}" == "1" ]]; then
  echo "Building Tauri macOS bundle (.app and .dmg)..."
  CI=true npx tauri build ${TARGET_FLAG} --bundles app,dmg
else
  echo "Building Tauri macOS bundle (.app)..."
  npx tauri build ${TARGET_FLAG} --bundles app
fi

echo ""
echo "Build complete! The app is in:"
echo "  ${PROJECT_DIR}/src-tauri/target/**/release/bundle/macos/"
if [[ "${PO_MAC_DMG:-}" == "1" ]]; then
  echo "  ${PROJECT_DIR}/src-tauri/target/**/release/bundle/dmg/"
fi
echo "Install it with:"
echo "  ditto \"<that folder>/PaperOrchestra Studio.app\" \"/Applications/PaperOrchestra Studio.app\""
