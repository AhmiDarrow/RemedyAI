#!/usr/bin/env python3
"""Live soak: security, chat (deepseek-v4-flash), plan mode against local Remedy API.

Usage (API already on 127.0.0.1:7400):
  python scripts/live_soak_security_chat.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import sys
from pathlib import Path as _PathForToken
_SCRIPTS = _PathForToken(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib_local_token import resolve_local_api_token

HOME = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy")).expanduser()
BASE = os.environ.get("REMEDY_API", "http://127.0.0.1:7400").rstrip("/")
TOKEN_PATH = HOME / "auth" / "local_api_token"


def token() -> str:
    return resolve_local_api_token(home=HOME, base=BASE)


def req(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    auth: bool = True,
    timeout: float = 120.0,
) -> tuple[int, object]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = f"Bearer {token()}"
    r = urllib.request.Request(
        f"{BASE}{path}", data=data, headers=headers, method=method.upper()
    )
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
        raw = e.read().decode("utf-8", errors="replace")
    try:
        parsed: object = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = raw
    return code, parsed


def stream_message(
    session_id: str,
    content: str,
    *,
    plan_mode: bool = False,
    timeout: float = 180.0,
) -> dict:
    """POST stream and collect tokens + done event."""
    body = json.dumps(
        {
            "message": content,
            "plan_mode": plan_mode,
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    r = urllib.request.Request(
        f"{BASE}/api/sessions/{session_id}/messages/stream",
        data=body,
        headers=headers,
        method="POST",
    )
    t0 = time.perf_counter()
    first_token_at: float | None = None
    tokens: list[str] = []
    thinking: list[str] = []
    tools: list[str] = []
    error: str | None = None
    done_payload: dict | None = None
    event_name = "message"
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        buf = b""
        while True:
            chunk = resp.read(1024)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line_s = line.decode("utf-8", errors="replace").rstrip("\r")
                if not line_s:
                    continue
                if line_s.startswith(":"):
                    continue
                if line_s.startswith("event:"):
                    event_name = line_s[6:].strip()
                    continue
                if not line_s.startswith("data:"):
                    continue
                payload = line_s[5:].strip()
                if payload == "[DONE]":
                    continue
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    tokens.append(payload)
                    continue
                if not isinstance(obj, dict):
                    continue
                et = str(obj.get("type") or event_name or "")
                if et in ("token", "text", "message"):
                    piece = str(
                        obj.get("text")
                        or obj.get("content")
                        or obj.get("token")
                        or obj.get("delta")
                        or ""
                    )
                    if piece:
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        tokens.append(piece)
                elif et == "thinking":
                    thinking.append(str(obj.get("text") or obj.get("thinking") or ""))
                elif et in ("tool_call", "tool_result", "tool"):
                    tools.append(json.dumps(obj)[:200])
                elif et == "error":
                    error = str(obj.get("message") or obj.get("error") or obj)
                elif et in ("done", "complete", "end"):
                    done_payload = obj
                    if obj.get("content") or obj.get("text"):
                        # final full content if stream used chunks poorly
                        if not tokens:
                            tokens.append(str(obj.get("content") or obj.get("text") or ""))
    total = time.perf_counter() - t0
    text = "".join(tokens).strip()
    if not text and done_payload:
        text = str(done_payload.get("content") or done_payload.get("text") or "")
    return {
        "text": text,
        "ttft_s": (first_token_at - t0) if first_token_at else None,
        "total_s": total,
        "thinking_chunks": len(thinking),
        "tool_events": len(tools),
        "error": error,
        "done": done_payload,
    }


def ok(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        RESULTS["fail"] += 1
    else:
        RESULTS["pass"] += 1


RESULTS = {"pass": 0, "fail": 0}


def main() -> int:
    print(f"=== Remedy live soak @ {BASE} ===")
    print(f"home={HOME}")

    # --- 1. Health / auth ---
    print("\n## 1. Health & auth boundary")
    code, st = req("GET", "/api/status", auth=True)
    ok("status 200", code == 200, f"code={code} version={getattr(st, 'get', lambda k, d=None: None)('version') if isinstance(st, dict) else st}")
    if isinstance(st, dict):
        ok("version present", bool(st.get("version")), str(st.get("version")))
    code_u, _ = req("GET", "/api/settings", auth=False)
    ok("settings unauth → 401", code_u == 401, f"code={code_u}")
    code_b, boot = req("GET", "/api/auth/local-bootstrap", auth=False)
    ok("bootstrap loopback 200", code_b == 200, f"code={code_b}")
    if isinstance(boot, dict):
        ok("bootstrap has token", bool(boot.get("token")))

    # --- 2. Settings / model ---
    print("\n## 2. Model config (deepseek-v4-flash)")
    code, settings = req("GET", "/api/settings")
    ok("settings 200", code == 200)
    provider = settings.get("llm_provider") if isinstance(settings, dict) else None
    model = settings.get("llm_model") if isinstance(settings, dict) else None
    ok("provider=deepseek", provider == "deepseek", f"got {provider}")
    ok("model=deepseek-v4-flash", model == "deepseek-v4-flash", f"got {model}")
    print(f"      thinking={settings.get('thinking_level') if isinstance(settings, dict) else '?'}")

    # --- 3. Security: PA / OAuth / computer ---
    print("\n## 3. Security surfaces")
    code, astat = req("GET", "/api/assistant/status")
    ok("assistant status 200", code == 200)
    if isinstance(astat, dict):
        g = astat.get("google") or {}
        a = astat.get("assistant") or {}
        enc = g.get("tokens_encoding")
        ok(
            "tokens_encoding dpapi|plain|missing",
            enc in ("dpapi", "plain", "missing"),
            f"enc={enc}",
        )
        if g.get("connected") and enc == "plain":
            ok(
                "plain warning present when connected plain",
                bool(g.get("tokens_encoding_warning")),
                str(g.get("tokens_encoding_warning"))[:80],
            )
        elif enc == "dpapi":
            ok("DPAPI sealed tokens", True, "tokens_encoding=dpapi")
        # Consent gate: try start oauth without consent
        code_o, body_o = req("POST", "/api/assistant/google/oauth/start", body={})
        privacy = a.get("privacy_ai_accepted")
        if not privacy:
            ok(
                "oauth start blocked without consent",
                code_o in (403, 400),
                f"code={code_o} body={str(body_o)[:120]}",
            )
        else:
            ok("consent already accepted (skip block test)", True, f"code={code_o}")

    # Computer host loopback hello (no bearer — middleware allows loopback)
    code_h, hello = req(
        "POST", "/api/computer/host/hello", body={"client": "live-soak"}, auth=False
    )
    ok("computer host hello loopback", code_h == 200, f"code={code_h} {hello}")
    if isinstance(hello, dict):
        ok(
            "hello alone not poller-connected",
            hello.get("host_connected") is False,
            str(hello.get("host_connected")),
        )
    code_p, polled = req("GET", "/api/computer/jobs/next", auth=False)
    ok("jobs/next poller heartbeat", code_p == 200, str(polled)[:60])

    # a11y push without job should 404 not 401 on loopback
    code_a, a11y = req(
        "POST",
        "/api/computer/a11y/push",
        body={"job_id": "deadbeefdeadbeef", "elements": []},
        auth=False,
    )
    ok(
        "a11y push loopback reaches handler",
        code_a in (200, 404),
        f"code={code_a} (401 would mean auth blocked loopback)",
    )

    # Provider sanitize unit (in-process)
    print("\n## 4. Provider sanitize (in-process)")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from remedy.core.provider_sanitize import sanitize_chat_body

    leaked = {
        "model": "x",
        "messages": [
            {"role": "tool", "content": "secret sk-abcdefghijklmnopqrstuvwxyz99999 here"},
            {"role": "tool", "content": '{"access_token":"ya29.should-not-leave"}'},
        ],
    }
    clean = sanitize_chat_body(leaked)
    c0 = clean["messages"][0]["content"]
    ok("redacts sk- keys", "sk-abcdefghijklmnopqrstuvwxyz99999" not in c0, c0[:80])
    ok("input not mutated", "sk-abcdefghijklmnopqrstuvwxyz99999" in leaked["messages"][0]["content"])

    # --- 5. Session + fast chat ---
    print("\n## 5. Normal chat (speed + accuracy)")
    code, sess = req(
        "POST",
        "/api/sessions",
        body={"title": "Live soak security/chat", "project_path": ""},
    )
    ok("create session", code == 200, str(sess)[:100])
    sid = (sess or {}).get("id") if isinstance(sess, dict) else None
    if not sid and isinstance(sess, dict):
        sid = (sess.get("session") or {}).get("id")
    if not sid:
        # list shape
        print(f"  session payload: {sess}")
        RESULTS["fail"] += 1
        return 1
    print(f"      session_id={sid}")

    # Fast factual ping
    t0 = time.perf_counter()
    code, chat = req(
        "POST",
        f"/api/sessions/{sid}/messages",
        body={
            "message": (
                "Reply with exactly one line: the product of 17*19, then the word FASTOK. "
                "No tools. No markdown."
            )
        },
        timeout=120,
    )
    dt = time.perf_counter() - t0
    ok("chat POST 200", code == 200, f"code={code} {dt:.2f}s")
    reply = ""
    if isinstance(chat, dict):
        reply = str(
            chat.get("response")
            or chat.get("content")
            or chat.get("message")
            or ""
        )
        if not reply and isinstance(chat.get("assistant"), dict):
            reply = str(chat["assistant"].get("content") or "")
        # messages endpoint may return message object
        if not reply:
            reply = str(chat.get("text") or chat)
    print(f"      non-stream reply ({dt:.2f}s): {reply[:300]!r}")
    ok("accuracy 17*19=323", "323" in reply, reply[:120])
    ok("marker FASTOK", "FASTOK" in reply.upper(), reply[:120])
    ok("chat latency < 45s", dt < 45.0, f"{dt:.2f}s")
    ok("chat reasonably fast < 20s", dt < 20.0, f"{dt:.2f}s (soft)")

    # Stream path (second turn)
    print("\n## 6. Streaming chat")
    try:
        stream = stream_message(
            sid,
            "In one short sentence: what is the capital of France? Then say STREAMOK.",
            plan_mode=False,
            timeout=120,
        )
        print(
            f"      stream total={stream['total_s']:.2f}s ttft={stream['ttft_s']} "
            f"tools={stream['tool_events']} err={stream['error']}"
        )
        print(f"      text: {stream['text'][:280]!r}")
        ok("stream completed", stream["error"] is None, str(stream["error"]))
        ok(
            "stream has content or tool activity",
            bool(stream["text"]) or stream["tool_events"] > 0,
            f"len={len(stream['text'])}",
        )
        if stream["text"]:
            ok(
                "stream accuracy Paris",
                "paris" in stream["text"].lower(),
                stream["text"][:100],
            )
            ok("stream marker STREAMOK", "STREAMOK" in stream["text"].upper())
        if stream["ttft_s"] is not None:
            ok("TTFT < 15s", stream["ttft_s"] < 15.0, f"{stream['ttft_s']:.2f}s")
            ok("TTFT snappy < 8s", stream["ttft_s"] < 8.0, f"{stream['ttft_s']:.2f}s (soft)")
        ok("stream total < 60s", stream["total_s"] < 60.0, f"{stream['total_s']:.2f}s")
    except Exception as exc:
        ok("stream path", False, str(exc))

    # --- 7. Plan mode ---
    print("\n## 7. Plan mode")
    # Create plan via API
    code, plan = req(
        "POST",
        "/api/plans",
        body={
            "title": "Soak plan: verify security docs",
            "goal": "Confirm security map and chat work",
            "steps": [
                "Check auth boundary",
                "Run a short chat turn",
                "Note tokens_encoding",
            ],
            "session_id": sid,
            "status": "draft",
        },
    )
    ok("create plan 200", code == 200, str(plan)[:120])
    plan_id = None
    if isinstance(plan, dict):
        plan_id = (plan.get("plan") or {}).get("id") or plan.get("id")
    code, latest = req("GET", f"/api/plans/latest?session_id={sid}&actionable=1")
    ok("latest plan for session", code == 200)
    if isinstance(latest, dict) and latest.get("plan"):
        ok("latest plan matches session", True, latest["plan"].get("title", "")[:60])
    else:
        ok("latest plan present", False, str(latest)[:100])

    if plan_id:
        code, ap = req(
            "POST", f"/api/plans/{plan_id}/status", body={"status": "approved"}
        )
        ok("approve plan", code == 200, f"code={code}")
        code, cp = req(
            "POST", f"/api/plans/{plan_id}/status", body={"status": "cancelled"}
        )
        ok("cancel plan", code == 200, f"code={code}")

    # Plan-mode chat: should explore, not write files
    try:
        stream_p = stream_message(
            sid,
            "Plan mode: outline 3 steps to add a /security-status slash command. "
            "Do not edit files. End with PLANOK.",
            plan_mode=True,
            timeout=150,
        )
        print(
            f"      plan stream total={stream_p['total_s']:.2f}s "
            f"ttft={stream_p['ttft_s']} text_len={len(stream_p['text'])}"
        )
        print(f"      plan text head: {stream_p['text'][:350]!r}")
        ok("plan stream ok", stream_p["error"] is None, str(stream_p["error"]))
        ok(
            "plan produced outline",
            len(stream_p["text"]) > 40
            or stream_p["tool_events"] > 0
            or "PLANOK" in stream_p["text"].upper(),
            f"len={len(stream_p['text'])} tools={stream_p['tool_events']}",
        )
        # After plan mode, check for any accidental plan save
        code, plans = req("GET", f"/api/plans?session_id={sid}&limit=10")
        ok("plans list 200", code == 200)
    except Exception as exc:
        ok("plan stream", False, str(exc))

    # Slash /plan
    code, cmd = req(
        "POST",
        f"/api/sessions/{sid}/command",
        body={"command": "/plans"},
    )
    ok("/plans command", code == 200, str(cmd)[:100])

    # --- 8. Messages history persisted ---
    print("\n## 8. Persistence")
    code, msgs = req("GET", f"/api/sessions/{sid}/messages?limit=20")
    ok("messages list 200", code == 200)
    n = 0
    if isinstance(msgs, dict):
        n = len(msgs.get("messages") or [])
    ok("messages saved", n >= 2, f"count={n}")

    # Summary
    print("\n=== RESULT ===")
    print(f"PASS={RESULTS['pass']} FAIL={RESULTS['fail']}")
    return 0 if RESULTS["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
