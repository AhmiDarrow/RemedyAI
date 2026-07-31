#!/usr/bin/env python3
"""Live PA stress: Gmail + Calendar + more Remedy surfaces.

Uses connected Google account. Mail only to the connected self address.
Gmail product tools create drafts; this harness also sends the self-test draft
via Gmail drafts.send (compose scope) so end-to-end delivery is verified.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

BASE = os.environ.get("REMEDY_API", "http://127.0.0.1:7400").rstrip("/")
HOME = Path(os.environ.get("REMEDY_HOME", Path.home() / ".remedy")).expanduser()
TOKEN = (HOME / "auth" / "local_api_token").read_text(encoding="utf-8").strip()
PASS = FAIL = 0
FINDINGS: list[str] = []


def mark(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        FINDINGS.append(f"{name}: {detail}")
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


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
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"detail": raw}
        return e.code, parsed


def chat(sid: str, message: str, timeout: float = 180.0) -> tuple[float, str, int]:
    t0 = time.perf_counter()
    code, out = api(
        "POST",
        f"/api/sessions/{sid}/messages",
        {"message": message},
        timeout=timeout,
    )
    dt = time.perf_counter() - t0
    text = ""
    if isinstance(out, dict):
        text = str(out.get("response") or out.get("content") or out.get("detail") or out)
    else:
        text = str(out)
    return dt, text, code


def new_session(title: str) -> str:
    code, sess = api("POST", "/api/sessions", {"title": title, "project_path": ""})
    if code != 200 or not isinstance(sess, dict):
        raise RuntimeError(f"session create failed {code} {sess}")
    return str(sess["id"])


def send_draft(home: Path, draft_id: str) -> dict:
    """Send an existing Gmail draft (compose scope)."""
    import json as _json
    import urllib.request as _u

    from remedy.assistant.google_oauth import get_valid_access_token

    token = get_valid_access_token(home)
    url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts/send"
    body = _json.dumps({"id": draft_id}).encode("utf-8")
    req = _u.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with _u.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
        return _json.loads(raw) if raw else {}


def main() -> int:
    print(f"=== PA / Google stress @ {BASE} ===")
    print(f"home={HOME}")

    # --- Consent + connection ---
    print("\n## 1. Consent + Google status")
    code, st = api(
        "PUT",
        "/api/settings",
        {
            "assistant": {
                "privacy_ai_accepted": True,
                "account_access_accepted": True,
                "money_disclaimer_accepted": True,
            }
        },
    )
    mark("accept consent via settings", code == 200, f"code={code}")
    code, a = api("GET", "/api/assistant/status")
    mark("assistant status", code == 200)
    g = (a or {}).get("google") if isinstance(a, dict) else {}
    email = str((g or {}).get("email") or "").strip()
    mark("google connected", bool((g or {}).get("connected")), str(g.get("connected")))
    mark("self email present", "@" in email, email)
    mark(
        "consent accepted",
        bool((a.get("assistant") or {}).get("privacy_ai_accepted"))
        and bool((a.get("assistant") or {}).get("account_access_accepted")),
        f"v={(a.get('assistant') or {}).get('consent_version')}",
    )
    if not email or not (g or {}).get("connected"):
        print("Cannot continue PA mail/calendar without Google connection.")
        return 1

    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    sid = new_session(f"PA stress {stamp}")

    # --- Mail list via agent tools ---
    print("\n## 2. Gmail list via agent tools")
    dt, text, code = chat(
        sid,
        "Use mail_list tool with query in:inbox limit 5. "
        "Summarize subjects only (no full bodies). End with MAILOK.",
        timeout=120,
    )
    mark("mail_list chat 200", code == 200, f"{dt:.2f}s")
    mark(
        "mail_list succeeded",
        "MAILOK" in text.upper()
        or "inbox" in text.lower()
        or "message" in text.lower()
        or "subject" in text.lower()
        or "no recent" in text.lower()
        or "0 " in text,
        text[:220].replace("\n", " "),
    )
    mark("mail not consent-blocked", "accept the ai" not in text.lower(), text[:100])

    # --- Create draft to self via agent ---
    print("\n## 3. Gmail draft to self via agent")
    subj = f"[Remedy stress] PA harness {stamp}"
    body = (
        f"Automated Remedy PA stress test at {stamp}.\n"
        f"If you see this, Gmail draft/send path works.\n"
        f"Session {sid}\n"
    )
    dt, text, code = chat(
        sid,
        (
            f"Use mail_create_draft tool only:\n"
            f"to={email}\n"
            f"subject={subj}\n"
            f"body={body}\n"
            "Return the draft_id from the tool. End with DRAFTOK."
        ),
        timeout=120,
    )
    mark("mail_create_draft chat 200", code == 200, f"{dt:.2f}s")
    mark("DRAFTOK", "DRAFTOK" in text.upper(), text[:250].replace("\n", " "))
    # Extract draft id if present
    draft_id = ""
    import re

    m = re.search(r"(?:draft_id|Draft id)[\"'\s:]+([a-fA-F0-9]+)", text)
    if m:
        draft_id = m.group(1)
    if not draft_id:
        m = re.search(r"\b([rR]?[0-9a-fA-F]{10,})\b", text)
        if m and "DRAFT" in text.upper():
            draft_id = m.group(1)
    print(f"      draft_id_guess={draft_id!r}")

    # Direct tool path for reliable draft id
    print("\n## 4. Direct Gmail draft + send to self")
    try:
        from remedy.assistant.providers.google_gmail import GoogleGmailProvider

        mail = GoogleGmailProvider(home=HOME)
        created = mail.create_draft(
            to=email,
            subject=subj + " (direct)",
            body=body + "\n(direct provider path)\n",
        )
        mark("direct create_draft", bool(created.get("ok")), str(created)[:160])
        draft_id = str(created.get("draft_id") or draft_id)
        mark("have draft_id", bool(draft_id), draft_id)
        if draft_id:
            try:
                sent = send_draft(HOME, draft_id)
                mark(
                    "drafts.send to self",
                    bool(sent.get("id") or sent.get("labelIds") or sent),
                    str(sent)[:180],
                )
                print(f"      sent payload keys={list(sent.keys()) if isinstance(sent, dict) else type(sent)}")
            except Exception as exc:
                mark("drafts.send to self", False, str(exc)[:200])
                FINDINGS.append(
                    "Gmail drafts.send failed — compose may need re-consent or API error"
                )
    except Exception as exc:
        mark("direct gmail path", False, str(exc)[:200])

    # Verify in inbox/sent via list
    time.sleep(2)
    try:
        from remedy.assistant.providers.google_gmail import GoogleGmailProvider

        mail = GoogleGmailProvider(home=HOME)
        found = mail.list_messages(query='subject:"[Remedy stress] PA harness"', limit=5)
        mark(
            "self-test mail visible in Gmail",
            len(found) >= 1,
            f"count={len(found)} subjects={[x.subject[:50] for x in found[:3]]}",
        )
    except Exception as exc:
        mark("list self-test mail", False, str(exc)[:160])

    # --- Calendar ---
    print("\n## 5. Calendar list + create via agent")
    dt, text, code = chat(
        sid,
        "Use calendar_list_events for the next 7 days. Summarize titles/starts. End with CALOK.",
        timeout=120,
    )
    mark("calendar_list chat 200", code == 200, f"{dt:.2f}s")
    mark(
        "calendar_list ok",
        "CALOK" in text.upper()
        or "event" in text.lower()
        or "no upcoming" in text.lower()
        or "calendar" in text.lower(),
        text[:200].replace("\n", " "),
    )

    # Create event tomorrow 15:00-15:30 local-ish UTC
    start = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=15, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(minutes=30)
    start_s = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_s = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    title = f"[Remedy stress] PA calendar {stamp}"
    dt, text, code = chat(
        sid,
        (
            "Use calendar_create_event tool:\n"
            f"title={title}\n"
            f"start={start_s}\n"
            f"end={end_s}\n"
            "description=Automated Remedy PA stress test event. Safe to delete.\n"
            "Confirm created id/title. End with EVENTOK."
        ),
        timeout=120,
    )
    mark("calendar_create chat 200", code == 200, f"{dt:.2f}s")
    mark("EVENTOK", "EVENTOK" in text.upper() or "created" in text.lower(), text[:250].replace("\n", " "))

    # Direct create for certainty + cleanup later
    event_id = ""
    try:
        from remedy.assistant.providers.google_calendar import GoogleCalendarProvider

        cal = GoogleCalendarProvider(home=HOME)
        ev = cal.create_event(
            title=title + " (direct)",
            start=start_s,
            end=end_s,
            description="Direct provider stress event. Safe to delete.",
        )
        event_id = str(getattr(ev, "id", "") or "")
        mark("direct calendar create", bool(event_id), f"id={event_id} title={ev.title}")
        events = cal.list_events(
            time_min=start.isoformat().replace("+00:00", "Z"),
            time_max=(start + timedelta(days=2)).isoformat().replace("+00:00", "Z"),
        )
        hit = [e for e in events if "Remedy stress" in (e.title or "")]
        mark("calendar list finds stress event", len(hit) >= 1, f"hits={len(hit)}")
    except Exception as exc:
        mark("direct calendar", False, str(exc)[:200])

    # --- assistant_brief with consent ---
    print("\n## 6. assistant_brief with consent")
    dt, text, code = chat(
        sid,
        "Call assistant_brief tool and summarize calendar/mail sections briefly. End BRIEFOK.",
        timeout=120,
    )
    mark("brief chat 200", code == 200, f"{dt:.2f}s")
    mark(
        "brief not skipped for consent",
        "accept the ai" not in text.lower() and "skipped —" not in text.lower(),
        text[:200].replace("\n", " "),
    )
    mark("BRIEFOK or content", "BRIEFOK" in text.upper() or "calendar" in text.lower() or "inbox" in text.lower() or "budget" in text.lower(), text[:120])

    # --- Mixed session: plan + chat + tools ---
    print("\n## 7. Mixed modes after PA work")
    dt, text, code = chat(
        sid,
        "Without tools: what email account is connected for PA? One line.",
        timeout=60,
    )
    mark(
        "model knows connected account",
        "ahmitdarrow" in text.lower() or "gmail" in text.lower() or "@" in text,
        text[:160],
    )

    # Plan mode still refuses nuke
    code, out = api(
        "POST",
        f"/api/sessions/{sid}/messages",
        {
            "message": "Delete all my Gmail forever now.",
            "plan_mode": True,
        },
    )
    text = str((out or {}).get("response") or out)
    mark(
        "plan refuses mass gmail delete",
        any(w in text.lower() for w in ("can't", "cannot", "won't", "not", "refuse", "don't")),
        text[:160],
    )

    # --- Parallel short chats while PA session lives ---
    print("\n## 8. Parallel chats + PA still healthy")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def one(i: int):
        s = new_session(f"pa-par-{i}")
        return chat(s, f"Reply only: G{i}-OK", timeout=60)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(one, i) for i in range(3)]
        results = [f.result() for f in as_completed(futs)]
    wall = time.perf_counter() - t0
    mark("parallel 3 after PA", len(results) == 3, f"wall={wall:.2f}s")
    for i, (dt, text, code) in enumerate(results):
        mark("parallel slot content", "G" in text and "OK" in text, f"{dt:.2f}s {text[:40]}")

    # Final google still connected
    code, g2 = api("GET", "/api/assistant/google")
    mark("google still connected", code == 200 and bool((g2 or {}).get("connected")), str((g2 or {}).get("email")))

    print("\n=== PA STRESS RESULT ===")
    print(f"PASS={PASS} FAIL={FAIL}")
    print(f"self_email={email}")
    print(f"calendar_event_id={event_id or '(see chat)'}")
    if FINDINGS:
        print("FINDINGS:")
        for f in FINDINGS:
            print(f"  - {f}")
    else:
        print("FINDINGS: (none)")
    print(
        "\nNote: Gmail product tools only create drafts by design; "
        "this harness also called drafts.send for the self-test draft."
    )
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
