"""One-shot: upload Tauri updater signing key to GitHub Actions secrets.

Reads private key from ~/.tauri/remedy.key (or TAURI_KEY_PATH).
Uses GH_TOKEN / GITHUB_TOKEN or git credential for auth.
Does not print secret values.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from nacl import encoding, public
except ImportError:
    print("Installing PyNaCl...", file=sys.stderr)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pynacl", "-q"])
    from nacl import encoding, public

OWNER = "AhmiDarrow"
REPO = "RemedyAI"


def git_credential_token() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return None


def github_token() -> str:
    for env in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_PAT"):
        val = os.environ.get(env, "").strip()
        if val:
            return val
    tok = git_credential_token()
    if tok:
        return tok
    raise SystemExit("No GitHub token found (set GH_TOKEN or use git credential manager)")


def api(token: str, method: str, url: str, data: dict | None = None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "remedy-signing-setup",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"GitHub API {method} {url} failed: {e.code} {err}") from e


def encrypt(public_key_b64: str, secret_value: str) -> str:
    pk = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(sealed).decode("utf-8")


def main() -> None:
    key_path = Path(os.environ.get("TAURI_KEY_PATH", Path.home() / ".tauri" / "remedy.key"))
    if not key_path.is_file():
        raise SystemExit(f"Private key not found: {key_path}")
    private_key = key_path.read_text(encoding="utf-8").strip()
    if not private_key:
        raise SystemExit("Private key file is empty")

    # Empty password matches `tauri signer generate -p ""`
    password = os.environ.get("TAURI_SIGNING_PRIVATE_KEY_PASSWORD", "")

    token = github_token()
    meta = api(
        token,
        "GET",
        f"https://api.github.com/repos/{OWNER}/{REPO}/actions/secrets/public-key",
    )
    key_id = meta["key_id"]
    pub = meta["key"]

    secrets = {
        "TAURI_SIGNING_PRIVATE_KEY": private_key,
        "TAURI_SIGNING_PRIVATE_KEY_PASSWORD": password,
    }
    for name, value in secrets.items():
        encrypted = encrypt(pub, value)
        api(
            token,
            "PUT",
            f"https://api.github.com/repos/{OWNER}/{REPO}/actions/secrets/{name}",
            {"encrypted_value": encrypted, "key_id": key_id},
        )
        print(f"Set secret: {name} (chars={len(value)})")

    listed = api(token, "GET", f"https://api.github.com/repos/{OWNER}/{REPO}/actions/secrets")
    names = sorted(s["name"] for s in listed.get("secrets", []))
    print("Repo action secrets:", ", ".join(names))
    print("Done. Public key belongs in tauri.conf.json plugins.updater.pubkey")


if __name__ == "__main__":
    main()
