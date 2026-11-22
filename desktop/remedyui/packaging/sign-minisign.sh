#!/usr/bin/env bash
# Signs one artifact with the existing minisign secret. See sign-minisign.ps1.
# No secret in the environment: exit 0, file stays unsigned. Never prints the key.
set -euo pipefail

FILE="${1:-}"
if [[ -z "${FILE}" || ! -f "${FILE}" ]]; then
  echo "usage: $0 <file>" >&2
  exit 2
fi

if ! command -v minisign >/dev/null 2>&1; then
  echo "minisign is not on PATH" >&2
  exit 1
fi

TMP=""
cleanup() {
  if [[ -n "${TMP}" && -f "${TMP}" ]]; then
    rm -f "${TMP}"
  fi
}
trap cleanup EXIT

KEY=""
PASS=""
if [[ -n "${REMEDY_UPDATE_KEY:-}" ]]; then
  if [[ ! -f "${REMEDY_UPDATE_KEY}" ]]; then
    echo "REMEDY_UPDATE_KEY is set but the file is missing" >&2
    exit 1
  fi
  KEY="${REMEDY_UPDATE_KEY}"
  PASS="${REMEDY_UPDATE_KEY_PASSWORD:-}"
elif [[ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
  PASS="${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}"
  RAW="${TAURI_SIGNING_PRIVATE_KEY}"
  if [[ -f "${RAW}" ]]; then
    KEY="${RAW}"
  else
    TEXT="${RAW}"
    if [[ "${TEXT}" != *"untrusted comment"* ]]; then
      if ! TEXT="$(printf '%s' "${RAW}" | tr -d '[:space:]' | base64 -d 2>/dev/null)"; then
        echo "unsigned: no usable minisign secret ($(basename "${FILE}"))"
        exit 0
      fi
    fi
    if [[ "${TEXT}" != *"untrusted comment"* ]]; then
      echo "unsigned: no usable minisign secret ($(basename "${FILE}"))"
      exit 0
    fi
    TMP="$(mktemp)"
    printf '%s' "${TEXT}" > "${TMP}"
    KEY="${TMP}"
    unset TEXT RAW
  fi
else
  echo "unsigned: no minisign secret in the environment ($(basename "${FILE}"))"
  exit 0
fi

SIG="${FILE}.sig"
NAME="$(basename "${FILE}")"
set +e
if [[ -z "${PASS}" ]]; then
  minisign -W -S -s "${KEY}" -m "${FILE}" -x "${SIG}" -c "Remedy Desktop update" -t "${NAME}"
  CODE=$?
else
  printf '%s\n' "${PASS}" | minisign -S -s "${KEY}" -m "${FILE}" -x "${SIG}" -c "Remedy Desktop update" -t "${NAME}"
  CODE=$?
fi
set -e
unset PASS
if [[ "${CODE}" -ne 0 || ! -f "${SIG}" ]]; then
  echo "minisign failed (exit ${CODE}). The key was not printed." >&2
  exit 1
fi
echo "signed ${NAME} -> $(basename "${SIG}")"
