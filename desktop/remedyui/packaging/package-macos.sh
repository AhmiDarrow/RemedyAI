#!/usr/bin/env bash
# Builds Remedy Desktop.app, a first-install DMG, and the updater .app.tar.gz.
# The tarball is the sole payload the in-app updater installs (gzip, .app at the
# top level). DMG is first install only. Ad-hoc codesign (--sign -). Does not
# tag or publish. Does not copy libremedy_core.dylib; the RemedyUI shell does
# not load it.
set -euo pipefail

JOBS="${JOBS:-4}"
SKIP_BUILD=0
BINARY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --binary) BINARY="${2:-}"; shift 2 ;;
    --jobs) JOBS="${2:-4}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
renice -n 10 -p $$ >/dev/null 2>&1 || true

VER="$(sed -n 's/^version = "\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)".*/\1/p' Cargo.toml | head -n 1)"
if [[ -z "${VER}" ]]; then
  echo "Could not read the package version from Cargo.toml" >&2
  exit 1
fi

case "$(uname -m)" in
  arm64|aarch64) ARCH="aarch64" ;;
  x86_64) ARCH="x64" ;;
  *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac

if [[ "${SKIP_BUILD}" -eq 0 ]]; then
  nice -n 10 cargo build --release --jobs "${JOBS}" --locked --manifest-path "${ROOT}/Cargo.toml"
fi
if [[ -z "${BINARY}" ]]; then
  BINARY="${ROOT}/target/release/remedy-desktop"
fi
if [[ ! -f "${BINARY}" ]]; then
  echo "Binary not found: ${BINARY}" >&2
  exit 1
fi

OUT="${ROOT}/target/packaging"
STAGE="${OUT}/macos-stage"
rm -rf "${STAGE}"
APP="${STAGE}/Remedy Desktop.app"
mkdir -p "${APP}/Contents/MacOS" "${APP}/Contents/Resources"
install -m 0755 "${BINARY}" "${APP}/Contents/MacOS/Remedy Desktop"
ln -sf "Remedy Desktop" "${APP}/Contents/MacOS/remedy-desktop"
cp -f "${ROOT}/assets/icon.png" "${APP}/Contents/Resources/icon.png"

cat > "${APP}/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Remedy Desktop</string>
  <key>CFBundleDisplayName</key><string>Remedy Desktop</string>
  <key>CFBundleIdentifier</key><string>com.remedy.desktop</string>
  <key>CFBundleVersion</key><string>${VER}</string>
  <key>CFBundleShortVersionString</key><string>${VER}</string>
  <key>CFBundleExecutable</key><string>Remedy Desktop</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>icon.png</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

codesign --force --deep --sign - "${APP}"

DMG="${OUT}/Remedy.Desktop_${VER}_${ARCH}.dmg"
TAR="${OUT}/Remedy.Desktop_${VER}_${ARCH}.app.tar.gz"
rm -f "${DMG}" "${TAR}"
hdiutil create -volname "Remedy Desktop" -srcfolder "${APP}" -ov -format UDZO "${DMG}"

# Sole top-level entry, same shape the 0.70.5 updater extracts.
tar -C "${STAGE}" -czf "${TAR}" "Remedy Desktop.app"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "${DIR}/sign-minisign.sh" ]]; then
  bash "${DIR}/sign-minisign.sh" "${TAR}"
  bash "${DIR}/sign-minisign.sh" "${DMG}"
fi

echo "${DMG}"
echo "${TAR}"
echo "Not published. The .app.tar.gz is the darwin-${ARCH} update payload under runtime-v${VER}."
