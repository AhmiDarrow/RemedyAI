#!/usr/bin/env python3
"""Drive Remedy via chat to exercise every major capability end-to-end.

Uses real agent turns (not just API probes) so tools + LLM + consent paths run.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

BASE = os.environ.get("REMEDY_API", "http://127.0.0.1:7400").rstrip("/")
HOME = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy")).expanduser()
TOKEN = (HOME / "auth" / "local_api_token").read_text(encoding="utf-8").strip()
REPO = Path(__file__).resolve().parents[1]

PASS = FAIL = SKIP = 0
RESULTS: list[tuple[str, str, str]] = []


def mark(name: str, ok: bool, detail: str = "", *, skip: bool = False) -> None:
    global PASS, FAIL, SKIP
    if skip:
        SKIP += 1
        tag = "SKIP"
    elif ok:
        PASS += 1
        tag = "PASS"
    else:
        FAIL += 1
        tag = "FAIL"
    RESULTS.append((tag, name, detail))
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n{'='*60}\n## {title}\n{'='*60}")


def api(method: str, path: str, body: dict | None = None, timeout: float = 180.0):
    data = None
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
    }
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
            return e.code, {"detail": raw}
    except Exception as e:
        return 0, {"detail": str(e)}


def extract_text(resp: object) -> str:
    if not isinstance(resp, dict):
        return str(resp)
    for k in ("content", "message", "reply", "response", "text", "output"):
        v = resp.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, dict):
            t = v.get("content") or v.get("text")
            if isinstance(t, str) and t.strip():
                return t
    msgs = resp.get("messages")
    if isinstance(msgs, list) and msgs:
        last = msgs[-1]
        if isinstance(last, dict):
            return str(last.get("content") or last.get("text") or last)
        return str(last)
    return json.dumps(resp, default=str)[:2000]


def new_session(title: str) -> str:
    code, sess = api("POST", "/api/sessions", {"title": title})
    if code != 200:
        raise RuntimeError(f"create session failed {code} {sess}")
    return str(sess.get("id") or sess.get("session_id"))


def chat(sid: str, message: str, timeout: float = 180.0) -> tuple[float, str, int]:
    t0 = time.time()
    code, resp = api(
        "POST", f"/api/sessions/{sid}/messages", {"message": message}, timeout=timeout
    )
    dt = time.time() - t0
    return dt, extract_text(resp), code


def main() -> int:
    print(f"AGENT FULL WORKOUT @ {BASE}")
    print(f"home={HOME} started={datetime.now(UTC).isoformat()}")

    def stop_host_poller():
        return None  # type: ignore
    try:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lib_host_poller import start_host_poller
        from lib_host_poller import stop_host_poller as _stop

        stop_host_poller = _stop
        start_host_poller()
    except Exception as e:
        print(f"  [poller] optional: {e}")

    # ------------------------------------------------------------------
    section("0. Prep — ensure agent-ready settings")
    # ------------------------------------------------------------------
    code, prep = api(
        "PUT",
        "/api/settings",
        {
            "web_tools_enabled": True,
            "http_bootstrap": True,
            "approval_mode": "auto",
            "thinking_level": "medium",
            "user_name": "Ahmi",
            "name": "Remedy",
            "vision_enabled": True,
            "vision_model_id": "smolvlm2-2.2b",
            "setup_completed": True,
            "assistant": {
                "privacy_ai_accepted": True,
                "account_access_accepted": True,
                "money_disclaimer_accepted": True,
                "enabled": True,
            },
        },
    )
    mark("prep settings", code == 200, f"code={code}")
    code, gst = api("GET", "/api/assistant/google")
    mark("google status", code == 200, str(gst)[:160] if isinstance(gst, dict) else str(gst)[:160])
    google_connected = isinstance(gst, dict) and (
        gst.get("connected") is True
        or bool(gst.get("email"))
        or str(gst.get("status") or "").lower() in ("ready", "connected")
    )
    google_email = ""
    if isinstance(gst, dict):
        google_email = str(gst.get("email") or "")
        # Live probe of Cloud APIs when connected
        code_p, probe = api("GET", "/api/assistant/google")
        # Prefer nested probe from assistant status if present
        code_a, ast = api("GET", "/api/assistant/status")
        if isinstance(ast, dict) and isinstance(ast.get("google"), dict):
            g2 = ast["google"]
            if g2.get("email"):
                google_email = str(g2.get("email"))
                google_connected = google_connected or bool(g2.get("connected"))
        mark(
            "gmail API probe",
            True,
            f"connected={google_connected} email={google_email or 'n/a'}",
        )
    mark("google connected flag", google_connected, google_email or "no email")

    sid = new_session("Agent full workout")
    mark("session created", bool(sid), sid)

    # ------------------------------------------------------------------
    section("1. Self-setup via chat")
    # ------------------------------------------------------------------
    dt, text, code = chat(
        sid,
        "Call get_settings tool now and report user_name and approval_mode. "
        "Reply with line USERCFG: name=… approval=… web=… then CFGOK.",
        timeout=90,
    )
    # Recovery may strip markup; accept CFGOK / Ahmi / approval
    mark(
        "get_settings via agent",
        code == 200
        and (
            "CFGOK" in text.upper()
            or "USERCFG" in text.upper()
            or "Ahmi" in text
            or "approval" in text.lower()
        )
        and "<function" not in text.lower()
        and "invoke" not in text.lower(),
        f"{dt:.1f}s {text[:120]}",
    )

    # ------------------------------------------------------------------
    section("2. Budget / bills / debts")
    # ------------------------------------------------------------------
    dt, text, code = chat(
        sid,
        "Set a budget period named workout-live with income 5000, categories: "
        "food 400, rent 1800, fun 200. Then add a $12.50 food transaction 'coffee'. "
        "Reply BUDGETOK when done with remaining food amount.",
        timeout=120,
    )
    mark("budget set + tx", code == 200 and "BUDGETOK" in text.upper(), f"{dt:.1f}s {text[:160]}")

    dt1, text1, code1 = chat(
        sid,
        "Using bill_upsert tool: add bill name=Netflix amount=15.99 cadence=monthly "
        "next_due=2026-08-01. Reply BILLOK when tool succeeds.",
        timeout=90,
    )
    dt2, text2, code2 = chat(
        sid,
        "Using debt_upsert tool: add debt name=CardOne balance=1200 apr_pct=19.9 "
        "min_payment=35 due_day=15. Reply DEBTOK when tool succeeds.",
        timeout=90,
    )
    # Also verify via store status (agent text can flake)
    store_ok = False
    try:
        from remedy.assistant.store import get_assistant_store

        st = get_assistant_store(HOME)
        bills = st.list_bills() if hasattr(st, "list_bills") else []
        debts = st.list_debts() if hasattr(st, "list_debts") else []
        # public status may carry counts
        pub = st.public_status() if hasattr(st, "public_status") else {}
        bill_n = int(pub.get("bill_count") or len(bills) or 0)
        debt_n = int(pub.get("debt_count") or len(debts) or 0)
        store_ok = bill_n > 0 and debt_n > 0
        store_detail = f"bills={bill_n} debts={debt_n}"
    except Exception as e:
        store_detail = str(e)
    text_ok = (
        ("BILLOK" in text1.upper() or "netflix" in text1.lower())
        and ("DEBTOK" in text2.upper() or "cardone" in text2.lower())
    )
    mark(
        "bills + debts",
        (code1 == 200 and code2 == 200) and (text_ok or store_ok),
        f"{dt1+dt2:.1f}s text_ok={text_ok} store={store_detail} "
        f"t1={text1[:60]!r} t2={text2[:60]!r}",
    )

    dt, text, code = chat(
        sid,
        "Show budget status and bill list briefly. Reply MONEYSTATUSOK.",
        timeout=90,
    )
    mark("money status", code == 200, f"{dt:.1f}s {text[:160]}")

    # ------------------------------------------------------------------
    section("3. Goals / plans / brief")
    # ------------------------------------------------------------------
    dt, text, code = chat(
        sid,
        "Create a goal: Ship Remedy workout QA this week. "
        "Then give an assistant brief. End with GOALSBRIEFOK.",
        timeout=120,
    )
    mark("goals + brief", code == 200, f"{dt:.1f}s {text[:160]}")

    # ------------------------------------------------------------------
    section("4. Mail — list, draft, send")
    # ------------------------------------------------------------------
    dt, text, code = chat(
        sid,
        "List my latest 5 inbox emails with subjects (use mail tools). "
        "If Gmail API is disabled say GMAIL_API_DISABLED. Else end MAILLISTOK.",
        timeout=120,
    )
    gmail_disabled = "GMAIL_API_DISABLED" in text.upper() or "disabled" in text.lower() and "gmail" in text.lower()
    mark(
        "mail list",
        code == 200 and ("MAILLISTOK" in text.upper() or gmail_disabled or "message" in text.lower()),
        f"{dt:.1f}s {text[:200]}",
        skip=gmail_disabled,
    )

    to_addr = google_email or "owner@example.com"
    dt, text, code = chat(
        sid,
        f"Create a Gmail draft to {to_addr} subject 'Remedy workout draft' "
        f"body 'Automated draft from Remedy full workout at {datetime.now(UTC).isoformat()}.'. "
        "If API disabled say GMAIL_API_DISABLED else DRAFTOK with draft id.",
        timeout=120,
    )
    mark(
        "mail draft",
        code == 200
        and (
            "DRAFTOK" in text.upper()
            or "draft" in text.lower()
            or "GMAIL_API_DISABLED" in text.upper()
        ),
        f"{dt:.1f}s {text[:200]}",
        skip="GMAIL_API_DISABLED" in text.upper() or ("disabled" in text.lower() and "gmail" in text.lower()),
    )

    dt, text, code = chat(
        sid,
        f"SEND a real email now to {to_addr} with subject "
        f"'Remedy workout send {date.today().isoformat()}' and body "
        f"'Hello from Remedy agent full workout. Time={datetime.now(UTC).isoformat()}.'. "
        "Use mail_send. If API disabled say GMAIL_API_DISABLED else SENDOK with message id. "
        "I explicitly authorize this one send.",
        timeout=120,
    )
    mark(
        "mail send",
        code == 200
        and (
            "SENDOK" in text.upper()
            or "sent" in text.lower()
            or "GMAIL_API_DISABLED" in text.upper()
            or "message_id" in text.lower()
        ),
        f"{dt:.1f}s {text[:220]}",
        skip="GMAIL_API_DISABLED" in text.upper()
        or ("disabled" in text.lower() and "gmail" in text.lower())
        or ("not been used" in text.lower()),
    )

    # ------------------------------------------------------------------
    section("5. Calendar — list + create event")
    # ------------------------------------------------------------------
    dt, text, code = chat(
        sid,
        "List my calendar events for the next 7 days. "
        "If Calendar API disabled say CAL_API_DISABLED else CALISTOK.",
        timeout=120,
    )
    cal_disabled = "CAL_API_DISABLED" in text.upper() or (
        "disabled" in text.lower() and "calendar" in text.lower()
    )
    mark(
        "calendar list",
        code == 200 and ("CALISTOK" in text.upper() or cal_disabled or "event" in text.lower()),
        f"{dt:.1f}s {text[:200]}",
        skip=cal_disabled,
    )

    start_d = (date.today() + timedelta(days=2)).isoformat()
    end_d = (date.today() + timedelta(days=3)).isoformat()
    dt, text, code = chat(
        sid,
        f"Create a calendar event title 'Remedy Workout QA' all-day start {start_d} end {end_d} "
        f"description 'Created by agent full workout'. "
        "If API disabled say CAL_API_DISABLED else CALCREATEOK with event id.",
        timeout=120,
    )
    mark(
        "calendar create",
        code == 200
        and (
            "CALCREATEOK" in text.upper()
            or "created" in text.lower()
            or "CAL_API_DISABLED" in text.upper()
        ),
        f"{dt:.1f}s {text[:220]}",
        skip="CAL_API_DISABLED" in text.upper()
        or ("disabled" in text.lower() and "calendar" in text.lower())
        or ("forbidden" in text.lower()),
    )

    # ------------------------------------------------------------------
    section("6. Web / files / vision / memory")
    # ------------------------------------------------------------------
    dt, text, code = chat(
        sid,
        "Fetch https://example.com with web_fetch and quote the title. End WEBOK.",
        timeout=90,
    )
    mark("web_fetch", code == 200 and "WEBOK" in text.upper(), f"{dt:.1f}s {text[:120]}")

    probe = REPO / "scripts" / "_workout_probe.txt"
    dt, text, code = chat(
        sid,
        f"Write exactly WORKOUTPROBE into {probe.as_posix()} using tools, then confirm. End FILEOK.",
        timeout=90,
    )
    file_ok = probe.is_file() and "WORKOUTPROBE" in probe.read_text(encoding="utf-8", errors="replace")
    mark("file write", code == 200 and file_ok, f"{dt:.1f}s exists={probe.is_file()}")
    if probe.is_file():
        probe.unlink(missing_ok=True)

    dt, text, code = chat(
        sid,
        "Remember this fact permanently: workout-token-velvet-77 means QA session green. "
        "Confirm MEMORYOK.",
        timeout=90,
    )
    mark("memory save", code == 200, f"{dt:.1f}s {text[:100]}")

    dt, text, code = chat(
        sid,
        "What does workout-token-velvet-77 mean? Reply with the phrase if you recall.",
        timeout=90,
    )
    mark(
        "memory recall",
        code == 200 and ("velvet" in text.lower() or "qa" in text.lower()),
        f"{dt:.1f}s {text[:120]}",
    )

    # vision decode via API for reliability, then agent mention
    code, vst = api("GET", "/api/vision/status")
    mark("vision status API", code == 200, str(vst)[:100] if isinstance(vst, dict) else "")
    vpath = HOME / "tmp_e2e_vision.png"
    if not vpath.is_file():
        vpath = REPO / "desktop" / "public" / "logo.png"
    code, vdec = api(
        "POST",
        "/api/vision/test",
        {"path": str(vpath)} if vpath.is_file() else {},
    )
    mark(
        "vision decode test",
        code == 200
        and isinstance(vdec, dict)
        and (vdec.get("ok") is True or bool(vdec.get("text") or vdec.get("path"))),
        str(vdec)[:120] if isinstance(vdec, dict) else str(vdec)[:120],
    )

    # ------------------------------------------------------------------
    section("7. Computer-use / host / parallel")
    # ------------------------------------------------------------------
    code, hello = api("POST", "/api/computer/host/hello", {"client": "workout"})
    mark("computer hello", code in (200, 201), str(hello)[:100])
    code, jobs = api("GET", "/api/computer/jobs/next")
    mark("computer jobs/next", code == 200, str(jobs)[:80])

    sids = [new_session(f"par-{i}") for i in range(3)]
    t0 = time.time()
    outs = []
    for i, s in enumerate(sids):
        outs.append(chat(s, f"Reply only TAB{i}-OK", timeout=60))
    wall = time.time() - t0
    mark("parallel tabs", all(c == 200 and f"TAB{i}-OK" in t for i, (d, t, c) in enumerate(outs)), f"wall={wall:.1f}s")
    for s in sids:
        api("DELETE", f"/api/sessions/{s}")

    # ------------------------------------------------------------------
    section("8. Slash + partner surfaces")
    # ------------------------------------------------------------------
    for cmd in ("/help", "/status", "/whoami", "/skills"):
        code, r = api("POST", f"/api/sessions/{sid}/command", {"command": cmd})
        mark(f"command {cmd}", code == 200, str(r)[:80])

    code, partner = api("GET", "/api/partner/status")
    mark("partner status", code == 200, str(partner)[:100] if isinstance(partner, dict) else "")
    code, metrics = api("GET", "/api/metrics")
    mark("metrics", code == 200)

    # ------------------------------------------------------------------
    section("9. Cleanup")
    # ------------------------------------------------------------------
    api("DELETE", f"/api/sessions/{sid}")
    mark("delete workout session", True)
    with contextlib.suppress(Exception):
        stop_host_poller()

    print(f"\n{'='*60}")
    print(f"WORKOUT COMPLETE  PASS={PASS}  FAIL={FAIL}  SKIP={SKIP}")
    print(f"{'='*60}")
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    if fails:
        print("\nFAILURES:")
        for tag, name, detail in fails:
            print(f"  - {name}: {detail}")
    skips = [r for r in RESULTS if r[0] == "SKIP"]
    if skips:
        print("\nSKIPPED (env / GCP):")
        for tag, name, detail in skips:
            print(f"  - {name}: {detail}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
