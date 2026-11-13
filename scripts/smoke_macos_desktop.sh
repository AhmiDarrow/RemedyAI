#!/usr/bin/env bash
# Mount an unsigned Remedy Desktop .dmg, confirm the bundle and sidecar, then
# start remedy-runtime long enough for /api/ping and stop it. Does not need
# a GUI session.
set -euo pipefail

DMG="${1:-}"
if [[ -z "${DMG}" || ! -f "${DMG}" ]]; then
  echo "usage: $0 <Remedy.Desktop_*.dmg>" >&2
  exit 2
fi

MNT="$(mktemp -d /tmp/remedy-dmg-XXXXXX)"
HOME_DIR="$(mktemp -d /tmp/remedy-smoke-home-XXXXXX)"
cleanup() {
  if [[ -n "${SIDECAR_PID:-}" ]] && kill -0 "${SIDECAR_PID}" 2>/dev/null; then
    kill "${SIDECAR_PID}" 2>/dev/null || true
    wait "${SIDECAR_PID}" 2>/dev/null || true
  fi
  hdiutil detach "${MNT}" -quiet -force 2>/dev/null || true
  rm -rf "${MNT}" "${HOME_DIR}"
}
trap cleanup EXIT

# licenseFile in tauri.conf.json is baked in as a DMG software license
# agreement. A non-interactive attach otherwise prints the SLA and exits
# "attach canceled". Feed one "Y", and temporarily drop pipefail so the
# writer's SIGPIPE after a successful attach cannot fail the script.
set +o pipefail
echo Y | hdiutil attach -nobrowse -readonly -mountpoint "${MNT}" "${DMG}"
attach_rc=$?
set -o pipefail
if [[ "${attach_rc}" -ne 0 ]]; then
  echo "hdiutil attach failed (exit ${attach_rc})" >&2
  exit 1
fi
APP="$(find "${MNT}" -maxdepth 2 -name '*.app' -type d | head -1)"
if [[ -z "${APP}" || ! -d "${APP}" ]]; then
  echo "no .app in ${DMG}" >&2
  exit 1
fi
echo "Mounted app: ${APP}"

# Sidecar may be named remedy-runtime or remedy-runtime-<triple>.
SIDECAR=""
for cand in \
  "${APP}/Contents/MacOS/remedy-runtime" \
  "${APP}/Contents/MacOS/remedy-runtime-aarch64-apple-darwin" \
  "${APP}/Contents/MacOS/remedy-runtime-x86_64-apple-darwin"
do
  if [[ -f "${cand}" ]]; then
    SIDECAR="${cand}"
    break
  fi
done
if [[ -z "${SIDECAR}" ]]; then
  echo "missing remedy-runtime sidecar under ${APP}/Contents/MacOS" >&2
  ls -la "${APP}/Contents/MacOS" >&2 || true
  exit 1
fi
chmod +x "${SIDECAR}" || true

DYLIB=""
for cand in \
  "${APP}/Contents/MacOS/libremedy_core.dylib" \
  "${APP}/Contents/Resources/libremedy_core.dylib"
do
  if [[ -f "${cand}" ]]; then
    DYLIB="${cand}"
    break
  fi
done
if [[ -z "${DYLIB}" ]]; then
  echo "missing libremedy_core.dylib in MacOS or Resources" >&2
  exit 1
fi
echo "sidecar=${SIDECAR}"
echo "dylib=${DYLIB}"

codesign -dv "${APP}" 2>&1 | tee /tmp/remedy-codesign.txt || true
if ! grep -qiE 'Signature=(adhoc|adhoc-|AdHoc)|Identifier=' /tmp/remedy-codesign.txt; then
  echo "warning: codesign -dv did not report an identity; continuing" >&2
fi

export REMEDY_HOME="${HOME_DIR}"
# Avoid clashing with a developer instance on 7400.
PORT="${REMEDY_SMOKE_PORT:-17401}"
export REMEDY_API_PORT="${PORT}"

"${SIDECAR}" --serve --listen "127.0.0.1:${PORT}" >"${HOME_DIR}/sidecar.log" 2>&1 &
SIDECAR_PID=$!

ok=0
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/ping" >/tmp/remedy-ping.json 2>/dev/null; then
    ok=1
    break
  fi
  if ! kill -0 "${SIDECAR_PID}" 2>/dev/null; then
    echo "sidecar exited early" >&2
    cat "${HOME_DIR}/sidecar.log" >&2 || true
    exit 1
  fi
  sleep 0.5
done
if [[ "${ok}" -ne 1 ]]; then
  echo "/api/ping did not respond" >&2
  cat "${HOME_DIR}/sidecar.log" >&2 || true
  exit 1
fi
echo "ping: $(cat /tmp/remedy-ping.json)"
kill "${SIDECAR_PID}" 2>/dev/null || true
wait "${SIDECAR_PID}" 2>/dev/null || true
SIDECAR_PID=""
echo "macOS desktop smoke ok"
