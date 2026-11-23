#!/usr/bin/env bash
# Builds Remedy.Desktop_{version}_amd64.AppImage and _amd64.deb.
# The AppImage is the in-app update payload. A .deb install cannot replace itself;
# the app points at the GitHub releases page instead.
# Does not tag or publish.
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

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "${DIR}/check.py"

command -v dpkg-deb >/dev/null 2>&1 || { echo "dpkg-deb is required" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }

VER="$(sed -n 's/^version = "\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)".*/\1/p' Cargo.toml | head -n 1)"
if [[ -z "${VER}" ]]; then
  echo "Could not read the package version from Cargo.toml" >&2
  exit 1
fi

if [[ "${SKIP_BUILD}" -eq 0 ]]; then
  nice -n 10 cargo build --release --jobs "${JOBS}" --locked --manifest-path "${ROOT}/Cargo.toml"
fi

if [[ -z "${BINARY}" ]]; then
  # Honor CARGO_TARGET_DIR. CI leaves it unset and builds into the crate's target/.
  TARGET_ROOT="${CARGO_TARGET_DIR:-${ROOT}/target}"
  BINARY="${TARGET_ROOT}/release/remedy-desktop"
fi
if [[ ! -f "${BINARY}" ]]; then
  echo "Binary not found: ${BINARY}" >&2
  exit 1
fi
MAGIC="$(od -An -t x1 -N 4 "${BINARY}" | tr -d ' \n')"
if [[ "${MAGIC}" != "7f454c46" ]]; then
  echo "Binary is not an ELF executable: ${BINARY}" >&2
  exit 1
fi
MACHINE="$(od -An -t x1 -j 18 -N 2 "${BINARY}" | tr -d ' \n')"
if [[ "${MACHINE}" != "3e00" ]]; then
  echo "Binary is not x86_64 (e_machine ${MACHINE}). The feed's linux asset is amd64." >&2
  exit 1
fi

OUT="${ROOT}/target/packaging"
rm -rf "${OUT}/AppDir" "${OUT}/deb"
mkdir -p "${OUT}/AppDir" "${OUT}/deb/DEBIAN" "${OUT}/deb/usr/bin" \
  "${OUT}/deb/usr/share/applications" "${OUT}/deb/usr/share/icons/hicolor/256x256/apps"

REPO="$(cd "${ROOT}/../.." && pwd)"
RUNTIME="${OUT}/remedy-runtime"
if [[ "${SKIP_BUILD}" -eq 0 || ! -f "${RUNTIME}" ]]; then
  command -v go >/dev/null 2>&1 || { echo "go is required to build remedy-runtime" >&2; exit 1; }
  ( cd "${REPO}/native/go" && go build -trimpath -ldflags "-s -w" -o "${RUNTIME}" ./cmd/remedy-runtime )
fi
if [[ ! -f "${RUNTIME}" ]]; then
  echo "Runtime not found: ${RUNTIME}" >&2
  exit 1
fi

install -m 0755 "${BINARY}" "${OUT}/AppDir/remedy-desktop"
install -m 0755 "${RUNTIME}" "${OUT}/AppDir/remedy-runtime"
install -m 0755 "${BINARY}" "${OUT}/deb/usr/bin/remedy-desktop"
install -m 0755 "${RUNTIME}" "${OUT}/deb/usr/bin/remedy-runtime"
cp -f "${ROOT}/assets/icon.png" "${OUT}/AppDir/remedy-desktop.png"
cp -f "${ROOT}/assets/icon.png" "${OUT}/deb/usr/share/icons/hicolor/256x256/apps/remedy-desktop.png"

cat > "${OUT}/AppDir/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/remedy-desktop" "$@"
EOF
chmod 0755 "${OUT}/AppDir/AppRun"

cat > "${OUT}/AppDir/Remedy Desktop.desktop" <<EOF
[Desktop Entry]
Name=Remedy Desktop
Exec=remedy-desktop
Icon=remedy-desktop
Type=Application
Categories=Utility;
StartupWMClass=Remedy Desktop
X-AppImage-Version=${VER}
EOF
cp -f "${OUT}/AppDir/Remedy Desktop.desktop" "${OUT}/deb/usr/share/applications/com.remedy.desktop.desktop"

SIZE_KB="$(du -sk "${OUT}/deb/usr" | awk '{print $1}')"
SHLIBS="libc6 (>= 2.35)"
if command -v dpkg-shlibdeps >/dev/null 2>&1; then
  LINE="$(dpkg-shlibdeps -O "${BINARY}" 2>/dev/null | sed -n 's/^shlibs:Depends=//p' | head -n 1 || true)"
  if [[ -n "${LINE}" ]]; then
    SHLIBS="${LINE}"
  fi
fi
cat > "${OUT}/deb/DEBIAN/control" <<EOF
Package: remedy-desktop
Version: ${VER}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Remedy <ahmitdarrow@gmail.com>
Installed-Size: ${SIZE_KB}
Depends: ${SHLIBS}
Description: Remedy Desktop
 Remedy Desktop (${VER}), identifier com.remedy.desktop.
 User data stays in ~/.remedy and is not removed by install or upgrade.
EOF

DEB="${OUT}/Remedy.Desktop_${VER}_amd64.deb"
dpkg-deb --build "${OUT}/deb" "${DEB}"

APPIMAGE="${OUT}/Remedy.Desktop_${VER}_amd64.AppImage"
TOOL=""
if command -v appimagetool >/dev/null 2>&1; then
  TOOL="appimagetool"
else
  TOOL="${OUT}/appimagetool-x86_64.AppImage"
  if [[ ! -x "${TOOL}" ]]; then
    curl -fsSL -o "${TOOL}" \
      "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod 0755 "${TOOL}"
  fi
fi
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "${TOOL}" "${OUT}/AppDir" "${APPIMAGE}"
chmod 0755 "${APPIMAGE}"

bash "${DIR}/sign-minisign.sh" "${APPIMAGE}"
bash "${DIR}/sign-minisign.sh" "${DEB}"

echo "${APPIMAGE}"
echo "${DEB}"
echo "Not published. The AppImage is the linux-x86_64 update payload."
