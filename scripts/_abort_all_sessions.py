#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_local_token import resolve_local_api_token

BASE = "http://127.0.0.1:7400"
TOKEN = resolve_local_api_token(home=Path.home() / ".remedy", base=BASE)


def req(method: str, path: str, body=None, timeout: float = 20.0):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if body is not None:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:240]


code, body = req("GET", "/api/sessions")
sessions = body.get("sessions") if isinstance(body, dict) else body
if isinstance(sessions, dict):
    sessions = sessions.get("sessions") or []
print("list", code, "n=", len(sessions) if isinstance(sessions, list) else sessions)
n = 0
if isinstance(sessions, list):
    for s in sessions:
        if isinstance(s, dict) and s.get("id"):
            c, b = req("POST", f"/api/sessions/{s['id']}/abort", {})
            print("abort", s["id"][:8], c, str(b)[:80])
            n += 1
print("aborted", n)
