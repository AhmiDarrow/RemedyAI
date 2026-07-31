#!/usr/bin/env python3
"""Minimal Desktop-host poller for local API testing (no Tauri required).

Claims computer jobs and completes navigates/clicks with stub success so
``host_connected`` stays true and agent computer tools exercise the full path.

Usage:
  set REMEDY_API=http://127.0.0.1:7400
  python scripts/computer_host_poller.py
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("REMEDY_API", "http://127.0.0.1:7400").rstrip("/")
HOME = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy")).expanduser()
TOKEN_PATH = HOME / "auth" / "local_api_token"


def _load_token() -> str:
    """Host/jobs/ui require Bearer — load plain or DPAPI-sealed local_api_token."""
    try:
        import sys

        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / "src"))
        sys.path.insert(0, str(root / "scripts"))
        from lib_local_token import resolve_local_api_token

        return resolve_local_api_token(home=HOME, base=BASE)
    except Exception:
        pass
    if not TOKEN_PATH.is_file():
        return ""
    raw = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if raw and not raw.lstrip().startswith("{"):
        return raw
    return ""


# Host routes require Bearer (S-AUTH-04 hardening)
TOKEN = _load_token()


def api(method: str, path: str, body: dict | None = None, timeout: float = 15.0):
    data = None
    headers = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, headers=headers, method=method.upper()
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, {"detail": raw[:200]}
    except Exception as e:
        return 0, {"detail": str(e)}


def main() -> int:
    print(f"computer host poller → {BASE}", flush=True)
    api("POST", "/api/computer/host/hello", {"client": "poller-sim", "bounds": {
        "x": 100, "y": 100, "width": 800, "height": 600, "scale": 1.0
    }})
    n = 0
    while True:
        # Real poller mark
        code, ui = api("GET", "/api/computer/ui/command?take=0")
        cmd = (ui or {}).get("command") if isinstance(ui, dict) else None
        if cmd and isinstance(cmd, dict):
            job_id = cmd.get("job_id")
            action = cmd.get("action") or cmd.get("job_action") or "navigate"
            url = cmd.get("url") or ""
            print(f"  ui_command {action} job={job_id} url={url[:60]}", flush=True)
            if job_id:
                api(
                    "POST",
                    f"/api/computer/jobs/{job_id}/complete",
                    {
                        "ok": True,
                        "result": {
                            "ok": True,
                            "action": action,
                            "url": url,
                            "message": f"poller-sim completed {action}",
                            "stub": True,
                        },
                    },
                )
                api("POST", "/api/computer/ui/command/ack", {"job_id": job_id})
            else:
                api("POST", "/api/computer/ui/command/ack", {})

        code, nxt = api("GET", "/api/computer/jobs/next")
        job = (nxt or {}).get("job") if isinstance(nxt, dict) else None
        if job and isinstance(job, dict) and job.get("id"):
            jid = job["id"]
            act = job.get("action") or "unknown"
            payload = job.get("payload") or {}
            print(f"  claim {act} id={jid}", flush=True)
            api(
                "POST",
                f"/api/computer/jobs/{jid}/complete",
                {
                    "ok": True,
                    "result": {
                        "ok": True,
                        "action": act,
                        "payload": payload,
                        "message": f"poller-sim completed {act}",
                        "stub": True,
                        "url": payload.get("url") if isinstance(payload, dict) else None,
                    },
                },
            )
            n += 1
        else:
            time.sleep(0.25)
        if n and n % 20 == 0:
            print(f"  completed {n} jobs", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("poller stop")
        raise SystemExit(0)
