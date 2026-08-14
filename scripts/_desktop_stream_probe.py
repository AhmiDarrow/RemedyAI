#!/usr/bin/env python3
"""Hit the same SSE path the desktop UI uses."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_local_token import resolve_local_api_token

BASE = "http://127.0.0.1:7400"
TOKEN = resolve_local_api_token(home=Path.home() / ".remedy", base=BASE)


def req_json(method: str, path: str, body=None, timeout: float = 20.0):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if body is not None:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode()
        return resp.status, json.loads(raw) if raw else {}


def stream_chat(sid: str, message: str, timeout: float = 45.0) -> tuple[float, str, list[str]]:
    data = json.dumps({"message": message}).encode()
    h = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    r = urllib.request.Request(
        f"{BASE}/api/sessions/{sid}/messages/stream",
        data=data,
        headers=h,
        method="POST",
    )
    t0 = time.time()
    first = None
    events: list[str] = []
    text_parts: list[str] = []
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        buf = b""
        while True:
            chunk = resp.read(256)
            if not chunk:
                break
            if first is None:
                first = time.time() - t0
            buf += chunk
            while b"\n\n" in buf:
                raw, buf = buf.split(b"\n\n", 1)
                line = raw.decode("utf-8", errors="replace")
                events.append(line[:160].replace("\n", " | "))
                for ln in line.splitlines():
                    if ln.startswith("data:"):
                        payload = ln[5:].strip()
                        if payload.startswith("{") or payload.startswith("["):
                            try:
                                obj = json.loads(payload)
                            except json.JSONDecodeError:
                                text_parts.append(payload)
                                continue
                            if isinstance(obj, dict):
                                if obj.get("type") in ("token", "text", "delta"):
                                    text_parts.append(str(obj.get("text") or obj.get("content") or ""))
                                elif isinstance(obj.get("content"), str):
                                    text_parts.append(obj["content"])
                        elif payload and payload != "[DONE]":
                            text_parts.append(payload)
            if time.time() - t0 > timeout:
                break
    dt = time.time() - t0
    return (first if first is not None else dt), "".join(text_parts), events


def main() -> int:
    print("health", end=" ")
    try:
        urllib.request.urlopen(BASE + "/health", timeout=5)
        print("200")
    except Exception as e:
        print("FAIL", e)
    code, sess = req_json("POST", "/api/sessions", {"title": "desktop-stream-probe"})
    sid = sess.get("id") or sess.get("session_id")
    print("sid", sid, "create", code)
    for msg in ("Reply only STILLALIVE", "Turn 0: say only T0OK"):
        t0 = time.time()
        try:
            first, text, events = stream_chat(sid, msg, timeout=40)
            print(
                f"STREAM {msg!r} first={first:.2f}s total={time.time()-t0:.1f}s "
                f"text={text[:160]!r} events={len(events)}"
            )
            for ev in events[:6]:
                print("   ev", ev[:140])
        except Exception as e:
            print(f"STREAM FAIL {msg!r} after {time.time()-t0:.1f}s {e}")
            try:
                req_json("POST", f"/api/sessions/{sid}/abort", {})
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
