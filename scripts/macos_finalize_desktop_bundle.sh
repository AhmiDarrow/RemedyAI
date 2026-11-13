#!/usr/bin/env bash
# After `tauri build` on macOS: place libremedy_core.dylib next to the
# sidecar (Contents/MacOS), ad-hoc codesign, and refresh the updater tarball.
set -euo pipefail

usage() {
  echo "usage: $0 --app <Remedy Desktop.app> --dylib <libremedy_core.dylib> [--tar <out.app.tar.gz>]" >&2
  exit 2
}

APP=""
DYLIB=""
TAR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app) APP="${2:-}"; shift 2 ;;
    --dylib) DYLIB="${2:-}"; shift 2 ;;
    --tar) TAR="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "${APP}" && -d "${APP}" && -n "${DYLIB}" && -f "${DYLIB}" ]] || usage
APP="$(cd "$(dirname "${APP}")" && pwd)/$(basename "${APP}")"
DYLIB="$(cd "$(dirname "${DYLIB}")" && pwd)/$(basename "${DYLIB}")"
if [[ -n "${TAR}" ]]; then
  mkdir -p "$(dirname "${TAR}")"
  TAR="$(cd "$(dirname "${TAR}")" && pwd)/$(basename "${TAR}")"
fi

MACOS="${APP}/Contents/MacOS"
RESOURCES="${APP}/Contents/Resources"
mkdir -p "${MACOS}" "${RESOURCES}"
cp -f "${DYLIB}" "${MACOS}/libremedy_core.dylib"
cp -f "${DYLIB}" "${RESOURCES}/libremedy_core.dylib"
chmod a+r "${MACOS}/libremedy_core.dylib" "${RESOURCES}/libremedy_core.dylib"

# Unsigned / ad-hoc (no Apple Developer identity).
codesign --force --deep --sign - "${APP}"

if [[ -n "${TAR}" ]]; then
  mkdir -p "$(dirname "${TAR}")"
  TMP=$(mktemp -d)
  trap 'rm -rf "${TMP}"' EXIT
  # Pack the .app as the sole top-level entry, same shape Tauri emits.
  APP_NAME="$(basename "${APP}")"
  PARENT="$(cd "$(dirname "${APP}")" && pwd)"
  tar -C "${PARENT}" -czf "${TMP}/bundle.app.tar.gz" "${APP_NAME}"
  mv -f "${TMP}/bundle.app.tar.gz" "${TAR}"
  if [[ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
    # Refresh the minisign sidecar so latest.json matches the rewritten tarball.
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    DESKTOP_DIR="$(cd "${SCRIPT_DIR}/../desktop" && pwd)"
    if ! command -v npx >/dev/null 2>&1; then
      echo "::error::TAURI_SIGNING_PRIVATE_KEY is set but npx is not available to sign ${TAR}" >&2
      exit 1
    fi
    (cd "${DESKTOP_DIR}" && npx tauri signer sign "${TAR}")
  fi
fi
