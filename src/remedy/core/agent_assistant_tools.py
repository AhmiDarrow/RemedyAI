"""Register personal-assistant tools (accounts status, budget/debt/bills, brief stub)."""

from __future__ import annotations

import contextlib
import json
from typing import Any

from remedy.assistant.disclaimer import MONEY_DISCLAIMER_FULL, MONEY_DISCLAIMER_SHORT
from remedy.assistant.store import get_assistant_store


def register_assistant_tools(runtime: Any) -> None:
    """Always-on PA organization tools; calendar/mail OAuth comes later."""

    home = None
    with contextlib.suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
    store = get_assistant_store(home)

    async def assistant_accounts() -> str:
        """Status of personal-assistant linked accounts and prefs (no secrets)."""
        return json.dumps(store.public_status(), indent=2)

    async def assistant_brief(hint: str = "") -> str:
        """On-demand brief from goals + local budget/bills (calendar/mail when linked)."""
        from remedy.assistant.privacy import (
            BRIEF_SNIPPET_MAX,
            clip,
            consent_ok,
            redact_secrets,
            sanitize_mail_list_item,
        )

        prefs = store.get_prefs()
        parts: list[str] = ["# Assistant brief", ""]
        # Local budget/goals never need Google consent; mail/calendar do.
        google_ok, google_reason = consent_ok(home)
        if prefs.brief.include_calendar:
            if not google_ok:
                parts.append("## Calendar")
                parts.append(f"(Skipped — {google_reason})")
                parts.append("")
            else:
                try:
                    from datetime import UTC, datetime, timedelta


                    cal = _calendar_provider()
                    if cal is not None:
                        now = datetime.now(UTC)
                        events = cal.list_events(
                            time_min=now.isoformat().replace("+00:00", "Z"),
                            time_max=(now + timedelta(days=7)).isoformat().replace(
                                "+00:00", "Z"
                            ),
                        )
                        parts.append("## Calendar (next 7 days)")
                        if not events:
                            parts.append("No upcoming events on primary calendar.")
                        else:
                            for ev in events[:15]:
                                title = clip(str(getattr(ev, "title", "") or ""), 80)
                                start = clip(str(getattr(ev, "start", "") or ""), 40)
                                parts.append(f"- {start}: {title}")
                        parts.append("")
                    else:
                        parts.append("## Calendar")
                        parts.append(
                            "Not connected — Settings → Personal assistant → Connect Google (Gmail)."
                        )
                        parts.append("")
                except Exception as exc:
                    parts.append("## Calendar")
                    parts.append(f"(Could not load calendar: {exc})")
                    parts.append("")
        if prefs.brief.include_mail:
            _brief_mail = _mail_provider()
            _imap_mail = (
                _brief_mail is not None
                and getattr(_brief_mail, "provider_id", "") == "imap"
            )
            if not google_ok and not _imap_mail:
                parts.append("## Mail")
                parts.append(f"(Skipped — {google_reason})")
                parts.append("")
            else:
                try:
                    mail = _brief_mail
                    if mail is not None:
                        msgs = mail.list_messages(query="in:inbox", limit=8)
                        parts.append("## Inbox (recent)")
                        if not msgs:
                            parts.append("No recent inbox messages.")
                        else:
                            for m in msgs:
                                row = sanitize_mail_list_item(
                                    redact_secrets(
                                        {
                                            "id": getattr(m, "id", "") or "",
                                            "subject": getattr(m, "subject", "") or "",
                                            "from": getattr(m, "from_addr", "") or "",
                                            "snippet": getattr(m, "snippet", "") or "",
                                            "date": getattr(m, "date", "") or "",
                                            "thread_id": getattr(m, "thread_id", "") or "",
                                        }
                                    )
                                )
                                parts.append(
                                    f"- {clip(str(row.get('from') or '?'), 40)}: "
                                    f"{clip(str(row.get('subject') or ''), 60)} — "
                                    f"{clip(str(row.get('snippet') or ''), BRIEF_SNIPPET_MAX)}"
                                )
                        parts.append("")
                    else:
                        parts.append("## Mail")
                        parts.append(
                            "Not connected — Settings → Personal assistant → Connect Google (Gmail)."
                        )
                        parts.append("")
                except Exception as exc:
                    parts.append("## Mail")
                    parts.append(f"(Could not load mail: {exc})")
                    parts.append("")
        if prefs.brief.include_budget:
            st = store.budget_status()
            parts.append("## Budget")
            parts.append(st.get("message") or "No budget")
            for row in (st.get("categories") or [])[:12]:
                parts.append(
                    f"- {row['category']}: spent {row['spent']} / planned {row['planned']} "
                    f"(remaining {row['remaining']})"
                )
            parts.append("")
        bills = store.list_bills()
        if bills:
            parts.append("## Bills")
            for b in bills[:15]:
                parts.append(
                    f"- {b.name}: ${b.amount:.2f} {b.cadence} next={b.next_due or '?'}"
                )
            parts.append("")
        debts = store.list_debts()
        if debts:
            parts.append("## Debts (user-entered)")
            for d in debts[:15]:
                parts.append(
                    f"- {d.name}: balance ${d.balance:.2f}, APR {d.apr_pct}%, min ${d.min_payment:.2f}"
                )
            parts.append("")
        # Goals from runtime if available
        with contextlib.suppress(Exception):
            tasks = list(runtime.list_tasks() or [])
            open_g = [
                t
                for t in tasks
                if "goal" in (getattr(t, "tags", None) or [])
                and str(getattr(t, "status", "")).lower()
                not in ("completed", "taskstatus.completed")
            ]
            if open_g:
                parts.append("## Open goals")
                for t in open_g[:12]:
                    parts.append(f"- {getattr(t, 'title', t)}")
                parts.append("")
        parts.append("## Linked accounts")
        accts = store.accounts_public()
        if not accts:
            parts.append(
                "None connected yet. Connect Google (Gmail) in Settings → Personal assistant. "
                "Microsoft/Yahoo next."
            )
        else:
            for a in accts:
                parts.append(f"- {a.get('provider')}: {a.get('status')} {a.get('email')}")
        parts.append("")
        parts.append(f"_Note: {MONEY_DISCLAIMER_SHORT}_")
        if hint:
            parts.append(f"\n(User hint: {hint[:200]})")
        return "\n".join(parts)

    async def calendar_list_events(days: int = 7, time_min: str = "", time_max: str = "") -> str:
        """List primary Google Calendar events (requires Connect Google)."""
        from datetime import UTC, datetime, timedelta

        from remedy.assistant.privacy import consent_ok

        cal = _calendar_provider()
        if cal is not None and getattr(cal, "provider_id", "") != "caldav":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if cal is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Google Calendar not connected. Settings → Personal assistant → Connect Google.",
                },
                indent=2,
            )
        now = datetime.now(UTC)
        tmin = (time_min or "").strip() or now.isoformat().replace("+00:00", "Z")
        if (time_max or "").strip():
            tmax = time_max.strip()
        else:
            d = max(1, min(int(days or 7), 31))
            tmax = (now + timedelta(days=d)).isoformat().replace("+00:00", "Z")
        try:
            events = cal.list_events(time_min=tmin, time_max=tmax)
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        return json.dumps(
            {
                "ok": True,
                "time_min": tmin,
                "time_max": tmax,
                "count": len(events),
                "events": [
                    {
                        "id": e.id,
                        "title": e.title,
                        "start": e.start,
                        "end": e.end,
                        "location": e.location,
                        "description": (e.description or "")[:400],
                    }
                    for e in events
                ],
                "message": f"{len(events)} event(s) on primary calendar",
            },
            indent=2,
        )

    async def calendar_create_event(
        title: str = "",
        start: str = "",
        end: str = "",
        description: str = "",
    ) -> str:
        """Create an event on primary Google Calendar (ISO start/end or YYYY-MM-DD all-day)."""
        from remedy.assistant.privacy import consent_ok

        cal = _calendar_provider()
        if cal is not None and getattr(cal, "provider_id", "") != "caldav":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if cal is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Google Calendar not connected. Settings → Personal assistant → Connect Google.",
                },
                indent=2,
            )
        if not (title or "").strip() or not (start or "").strip() or not (end or "").strip():
            return json.dumps(
                {
                    "ok": False,
                    "message": "Need title, start, and end (ISO datetime or YYYY-MM-DD for all-day).",
                },
                indent=2,
            )
        try:
            ev = cal.create_event(
                title=title.strip(),
                start=start.strip(),
                end=end.strip(),
                description=description or "",
            )
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        return json.dumps(
            {
                "ok": True,
                "event": {
                    "id": ev.id,
                    "title": ev.title,
                    "start": ev.start,
                    "end": ev.end,
                },
                "message": f"Created: {ev.title} @ {ev.start}",
            },
            indent=2,
        )

    async def mail_connect(address: str = "", app_password: str = "") -> str:
        """Connect a mailbox with an app password (no cloud project needed).

        Works with Gmail / Outlook / Yahoo / Fastmail / iCloud once 2-step
        verification is on. Verifies IMAP + SMTP before saving.
        """
        from remedy.assistant.providers.imap_smtp import (
            ImapSmtpMailProvider,
            MailAccount,
            preset_for,
            save_mail_credentials,
        )

        addr = (address or "").strip()
        pwd = (app_password or "").replace(" ", "")
        if not addr or not pwd:
            p = preset_for(addr) if addr else {}
            url = p.get("app_password_url") or ""
            return json.dumps(
                {
                    "ok": False,
                    "message": (
                        "I need the email address and a 16-character app password "
                        "(not the normal account password)."
                        + (f" Generate one at {url}" if url else "")
                    ),
                },
                indent=2,
            )
        # Verify before storing so a typo never gets saved as "connected".
        acct = MailAccount.from_address(addr, pwd)
        if not acct.is_ready():
            return json.dumps(
                {
                    "ok": False,
                    "message": (
                        f"I don't know the mail servers for {addr.split('@')[-1]}. "
                        "Supported without setup: Gmail, Outlook/Hotmail, Yahoo, "
                        "Fastmail, iCloud."
                    ),
                },
                indent=2,
            )
        try:
            ImapSmtpMailProvider(acct).verify()
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        saved = save_mail_credentials(addr, pwd, home=home)
        # Connecting a mailbox with explicit credentials IS the consent for
        # account access — record it rather than sending the owner to a second
        # toggle. What it means is stated plainly in the reply.
        with contextlib.suppress(Exception):
            store.patch_prefs(privacy_ai_accepted=True, account_access_accepted=True)
        saved["privacy"] = (
            "The app password is stored in Remedy's local encrypted secret store "
            "and is never sent to the model. Mail you ask me to read or answer "
            "does get sent to your chosen AI provider for that turn."
        )
        return json.dumps(saved, indent=2)

    async def mail_disconnect() -> str:
        """Forget the connected mailbox and its app password.

        Connecting used to be a one-way door: there was no tool, route, or CLI
        path back out, so the only way to unlink a mailbox was deleting keys by
        hand.
        """
        from remedy.assistant.providers.imap_smtp import clear_mail_credentials

        return json.dumps(clear_mail_credentials(home=home), indent=2)

    async def mail_status() -> str:
        """Which mailbox is connected and how (app password vs Google OAuth)."""
        from remedy.assistant.providers.imap_smtp import load_mail_account, preset_for

        acct = load_mail_account(home)
        google = False
        with contextlib.suppress(Exception):
            from remedy.assistant.google_oauth import load_tokens

            google = bool(load_tokens(home).connected)
        if acct is not None:
            p = preset_for(acct.address)
            return json.dumps(
                {
                    "ok": True,
                    "connected": True,
                    "method": "app_password",
                    "address": acct.address,
                    "provider": p.get("label") or "mail",
                    "google_oauth": google,
                    "message": f"Connected as {acct.address} over IMAP/SMTP",
                },
                indent=2,
            )
        return json.dumps(
            {
                "ok": True,
                "connected": bool(google),
                "method": "google_oauth" if google else "",
                "google_oauth": google,
                "message": (
                    "Google OAuth connected"
                    if google
                    else "No mailbox connected. Use mail_connect with an app password."
                ),
            },
            indent=2,
        )

    def _mail_provider():
        """Prefer the app-password mailbox; fall back to Google OAuth."""
        with contextlib.suppress(Exception):
            from remedy.assistant.providers.imap_smtp import get_imap_mail

            m = get_imap_mail(home)
            if m is not None:
                return m
        with contextlib.suppress(Exception):
            from remedy.assistant.providers.google_gmail import get_google_gmail

            return get_google_gmail(home)
        return None

    def _calendar_provider():
        """Prefer CalDAV (app password, no cloud project); fall back to OAuth."""
        with contextlib.suppress(Exception):
            from remedy.assistant.providers.caldav import get_caldav_calendar

            c = get_caldav_calendar(home)
            if c is not None:
                return c
        with contextlib.suppress(Exception):
            from remedy.assistant.providers.google_calendar import get_google_calendar

            return get_google_calendar(home)
        return None

    async def calendar_update_event(
        event_id: str = "",
        title: str = "",
        start: str = "",
        end: str = "",
        description: str = "",
        location: str = "",
    ) -> str:
        """Reschedule or edit an existing calendar event (only what you pass)."""
        from remedy.assistant.privacy import consent_ok

        cal = _calendar_provider()
        if cal is not None and getattr(cal, "provider_id", "") != "caldav":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if cal is None:
            return json.dumps(
                {"ok": False, "message": "Google Calendar not connected."}, indent=2
            )
        if not (event_id or "").strip():
            return json.dumps(
                {"ok": False, "message": "event_id required (from calendar_list_events)."},
                indent=2,
            )
        fn = getattr(cal, "update_event", None)
        if fn is None:
            return json.dumps(
                {"ok": False, "message": "This calendar provider cannot update events."},
                indent=2,
            )
        try:
            ev = fn(
                event_id.strip(),
                title=title,
                start=start,
                end=end,
                description=description,
                location=location,
            )
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        return json.dumps(
            {
                "ok": True,
                "event": {"id": ev.id, "title": ev.title, "start": ev.start, "end": ev.end},
                "message": f"Updated: {ev.title} @ {ev.start}",
            },
            indent=2,
        )

    async def calendar_cancel_event(event_id: str = "") -> str:
        """Cancel (delete) an event on the primary calendar. Asks first in Ask mode."""
        from remedy.assistant.privacy import consent_ok
        from remedy.core.approvals import APPROVALS
        from remedy.core.turn_context import turn_session_id

        cal = _calendar_provider()
        if cal is not None and getattr(cal, "provider_id", "") != "caldav":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if cal is None:
            return json.dumps(
                {"ok": False, "message": "Google Calendar not connected."}, indent=2
            )
        eid = (event_id or "").strip()
        if not eid:
            return json.dumps(
                {"ok": False, "message": "event_id required (from calendar_list_events)."},
                indent=2,
            )
        # Deleting someone's appointment is not reversible from here.
        label = eid
        with contextlib.suppress(Exception):
            ev0 = cal.get_event(eid)
            label = f"{ev0.title} @ {ev0.start}"
        summary = f"calendar_cancel_event {label[:100]}"
        ask_reason = APPROVALS.needs_ask(summary, tool_name="calendar_cancel_event")
        sid = turn_session_id(runtime)
        if ask_reason and not APPROVALS.is_approved(
            "calendar_cancel_event", summary, session_id=sid
        ):
            item = APPROVALS.create(
                tool_name="calendar_cancel_event",
                command=summary,
                reason=ask_reason,
                session_id=sid,
            )
            return (
                f"APPROVAL_REQUIRED id={item.id}\n"
                f"reason={ask_reason}\n"
                f"event={label}\n"
                "Do not invent success. Tell the user this needs approval "
                f"(or /approve {item.id}), then retry."
            )
        fn = getattr(cal, "delete_event", None)
        if fn is None:
            return json.dumps(
                {"ok": False, "message": "This calendar provider cannot delete events."},
                indent=2,
            )
        try:
            fn(eid)
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        return json.dumps(
            {"ok": True, "event_id": eid, "message": f"Cancelled: {label}"}, indent=2
        )

    async def mail_reply(
        message_id: str = "",
        body: str = "",
        reply_all: bool = False,
    ) -> str:
        """Reply IN THREAD to a message (keeps the conversation together)."""
        from remedy.assistant.privacy import consent_ok, redact_secrets
        from remedy.core.approvals import APPROVALS
        from remedy.core.turn_context import turn_session_id

        mail = _mail_provider()
        if mail is not None and getattr(mail, "provider_id", "") != "imap":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if mail is None:
            return json.dumps({"ok": False, "message": "Gmail not connected."}, indent=2)
        mid = (message_id or "").strip()
        if not mid:
            return json.dumps(
                {"ok": False, "message": "message_id required (from mail_list)."}, indent=2
            )
        # Sending on the owner's behalf always respects the approval gate.
        summary = f"mail_reply to message={mid} chars={len(body or '')}"
        ask_reason = APPROVALS.needs_ask(summary, tool_name="mail_reply")
        sid = turn_session_id(runtime)
        if ask_reason and not APPROVALS.is_approved("mail_reply", summary, session_id=sid):
            item = APPROVALS.create(
                tool_name="mail_reply",
                command=summary,
                reason=ask_reason,
                session_id=sid,
            )
            return (
                f"APPROVAL_REQUIRED id={item.id}\n"
                f"reason={ask_reason}\n"
                f"message_id={mid}\n"
                "Do not invent success. Tell the user this needs approval "
                f"(or /approve {item.id}), then retry mail_reply."
            )
        fn = getattr(mail, "reply_to_message", None)
        if fn is None:
            return json.dumps(
                {"ok": False, "message": "This mail provider cannot reply in thread."},
                indent=2,
            )
        try:
            result = fn(mid, body=body or "", reply_all=bool(reply_all))
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        safe = {
            "ok": True,
            "message_id": result.get("message_id"),
            "thread_id": result.get("thread_id"),
            "in_reply_to": result.get("in_reply_to"),
            "to": result.get("to"),
            "subject": result.get("subject"),
            "message": result.get("message"),
            "privacy": "Sent from your mailbox; body not re-sent to the model.",
        }
        return json.dumps(redact_secrets(safe), indent=2)

    async def mail_archive(message_id: str = "") -> str:
        """Archive a message (out of the inbox, still searchable)."""
        from remedy.assistant.privacy import consent_ok

        mail = _mail_provider()
        if mail is not None and getattr(mail, "provider_id", "") != "imap":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if mail is None:
            return json.dumps({"ok": False, "message": "Gmail not connected."}, indent=2)
        mid = (message_id or "").strip()
        if not mid:
            return json.dumps({"ok": False, "message": "message_id required."}, indent=2)
        fn = getattr(mail, "archive_message", None)
        if fn is None:
            return json.dumps(
                {"ok": False, "message": "This mail provider cannot archive."}, indent=2
            )
        try:
            return json.dumps(fn(mid), indent=2)
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)

    async def mail_mark_read(message_id: str = "", read: bool = True) -> str:
        """Mark a message read (or unread with read=false)."""
        from remedy.assistant.privacy import consent_ok

        mail = _mail_provider()
        if mail is not None and getattr(mail, "provider_id", "") != "imap":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if mail is None:
            return json.dumps({"ok": False, "message": "Gmail not connected."}, indent=2)
        mid = (message_id or "").strip()
        if not mid:
            return json.dumps({"ok": False, "message": "message_id required."}, indent=2)
        fn = getattr(mail, "mark_read", None)
        if fn is None:
            return json.dumps(
                {"ok": False, "message": "This mail provider cannot change read state."},
                indent=2,
            )
        try:
            return json.dumps(fn(mid, read=bool(read)), indent=2)
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)

    async def mail_list(query: str = "in:inbox", limit: int = 15) -> str:
        """List the owner's mail — IMAP app password or Google OAuth."""
        from remedy.assistant.privacy import (
            consent_ok,
            redact_secrets,
            sanitize_mail_list_item,
        )

        mail = _mail_provider()
        if mail is not None and getattr(mail, "provider_id", "") != "imap":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if mail is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": (
                        "Gmail not connected. Settings → Personal assistant → "
                        "Google (Gmail) → Connect."
                    ),
                },
                indent=2,
            )
        try:
            msgs = mail.list_messages(
                query=(query or "in:inbox").strip() or "in:inbox",
                # Clamped both ends: min() alone let a negative limit through
                # to the provider, which is not a smaller page but a nonsense
                # one.
                limit=max(1, min(int(limit or 15), 25)),
            )
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        payload = {
            "ok": True,
            "count": len(msgs),
            "query": query or "in:inbox",
            "messages": [
                sanitize_mail_list_item(
                    {
                        "id": m.id,
                        "subject": m.subject,
                        "from": m.from_addr,
                        "snippet": m.snippet,
                        "date": m.date,
                        "thread_id": m.thread_id,
                    }
                )
                for m in msgs
            ],
            "message": f"{len(msgs)} message(s)",
            "privacy": "Tokens stay local; this list may be sent to your AI provider.",
        }
        return json.dumps(redact_secrets(payload), indent=2)

    async def mail_get(message_id: str = "") -> str:
        """Read one message by id (body snippet / plain text)."""
        from remedy.assistant.privacy import (
            consent_ok,
            redact_secrets,
            sanitize_mail_body,
        )

        mail = _mail_provider()
        if mail is not None and getattr(mail, "provider_id", "") != "imap":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if mail is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Gmail not connected. Settings → Personal assistant → Connect.",
                },
                indent=2,
            )
        if not (message_id or "").strip():
            return json.dumps(
                {"ok": False, "message": "message_id required (from mail_list)."},
                indent=2,
            )
        try:
            m = mail.get_message(message_id.strip())
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        payload = {
            "ok": True,
            "message": {
                "id": m.id,
                "subject": m.subject,
                "from": m.from_addr,
                "date": m.date,
                "body": sanitize_mail_body(m.snippet),
                "thread_id": m.thread_id,
            },
            "privacy": "Body truncated; tokens never sent to the model.",
        }
        return json.dumps(redact_secrets(payload), indent=2)

    async def mail_create_draft(
        to: str = "",
        subject: str = "",
        body: str = "",
    ) -> str:
        """Create a draft (does not send)."""
        from remedy.assistant.privacy import consent_ok, redact_secrets

        mail = _mail_provider()
        if mail is not None and getattr(mail, "provider_id", "") != "imap":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if mail is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Gmail not connected. Settings → Personal assistant → Connect.",
                },
                indent=2,
            )
        if not (to or "").strip():
            return json.dumps({"ok": False, "message": "to address required"}, indent=2)
        try:
            result = mail.create_draft(
                to=to.strip(),
                subject=(subject or "").strip(),
                body=body or "",
            )
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        # Never echo full draft body back to the model path beyond confirmation
        safe = {
            "ok": True,
            "draft_id": result.get("draft_id"),
            "message_id": result.get("message_id"),
            "to": result.get("to"),
            "subject": result.get("subject"),
            "message": result.get("message"),
            "privacy": "Draft stored at Google; body not re-sent to the model.",
        }
        return json.dumps(redact_secrets(safe), indent=2)

    async def mail_send(
        to: str = "",
        subject: str = "",
        body: str = "",
    ) -> str:
        """Send a message now, over IMAP/SMTP or Gmail.

        High-impact: in Ask approval mode the user must confirm before send.
        Auto mode (owner power) skips the prompt on trusted scopes.
        """
        from remedy.assistant.privacy import consent_ok, redact_secrets
        from remedy.core.approvals import APPROVALS

        mail = _mail_provider()
        if mail is not None and getattr(mail, "provider_id", "") != "imap":
            ok, reason = consent_ok(home)
            if not ok:
                return json.dumps({"ok": False, "message": reason}, indent=2)
        if mail is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Gmail not connected. Settings → Personal assistant → Connect.",
                },
                indent=2,
            )
        if not (to or "").strip():
            return json.dumps({"ok": False, "message": "to address required"}, indent=2)
        to_addr = to.strip()
        subj = (subject or "").strip()
        # Partner trust: never silent-send in Ask mode.
        summary = f"mail_send to={to_addr} subject={subj[:80]}"
        ask_reason = APPROVALS.needs_ask(summary, tool_name="mail_send")
        from remedy.core.turn_context import turn_session_id

        sid = turn_session_id(runtime)
        if ask_reason and not APPROVALS.is_approved(
            "mail_send", summary, session_id=sid
        ):
            item = APPROVALS.create(
                tool_name="mail_send",
                command=summary,
                reason=ask_reason,
                session_id=sid,
            )
            return (
                f"APPROVAL_REQUIRED id={item.id}\n"
                f"reason={ask_reason}\n"
                f"to={to_addr}\n"
                f"subject={subj[:120]}\n"
                "Do not invent success. Tell the user this needs approval in the UI "
                f"(or /approve {item.id}). After they approve, retry mail_send with "
                "the same arguments."
            )
        send_fn = getattr(mail, "send_message", None)
        if send_fn is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "This mail provider does not support send; use mail_create_draft.",
                },
                indent=2,
            )
        try:
            result = send_fn(
                to=to_addr,
                subject=subj,
                body=body or "",
            )
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        safe = {
            "ok": True,
            "message_id": result.get("message_id"),
            "thread_id": result.get("thread_id"),
            "to": result.get("to"),
            "subject": result.get("subject"),
            "message": result.get("message"),
            "privacy": "Message sent via Gmail API; body not re-sent to the model.",
        }
        return json.dumps(redact_secrets(safe), indent=2)

    async def budget_get() -> str:
        st = store.budget_status()
        return json.dumps(st, indent=2)

    async def budget_set(
        label: str = "",
        income_planned: float = 0.0,
        categories_json: str = "",
    ) -> str:
        """Set budget period. categories_json: [{\"name\":\"groceries\",\"planned\":400}, ...]"""
        from datetime import UTC, datetime

        lab = (label or "").strip() or datetime.now(UTC).strftime("%Y-%m")
        cats: list[dict[str, Any]] = []
        raw = (categories_json or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    cats = [c for c in parsed if isinstance(c, dict)]
            except json.JSONDecodeError:
                return "categories_json must be a JSON list of {name, planned}"
        # Accept disclaimer on first money tool use
        store.patch_prefs(money_disclaimer_accepted=True)
        period = store.set_budget(
            label=lab, income_planned=float(income_planned or 0), categories=cats
        )
        return json.dumps(
            {
                "ok": True,
                "label": period.label,
                "categories": [c.to_dict() for c in period.categories],
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"Budget set for {period.label}",
            },
            indent=2,
        )

    async def budget_tx_add(
        amount: float = 0.0,
        category: str = "misc",
        note: str = "",
        kind: str = "expense",
        date: str = "",
    ) -> str:
        store.patch_prefs(money_disclaimer_accepted=True)
        tx = store.add_tx(
            amount=float(amount),
            category=category or "misc",
            note=note,
            kind=kind or "expense",
            date=date,
        )
        return json.dumps(
            {
                "ok": True,
                "tx": tx.to_dict(),
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"Logged {tx.kind} ${tx.amount:.2f} → {tx.category}",
            },
            indent=2,
        )

    async def budget_status() -> str:
        return json.dumps(store.budget_status(), indent=2)

    async def debt_list() -> str:
        debts = [d.to_dict() for d in store.list_debts()]
        return json.dumps(
            {
                "ok": True,
                "debts": debts,
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"{len(debts)} debt(s) on file",
            },
            indent=2,
        )

    async def debt_upsert(
        name: str = "",
        balance: float = 0.0,
        apr_pct: float = 0.0,
        min_payment: float = 0.0,
        due_day: int = 0,
        note: str = "",
        id: str = "",
    ) -> str:
        store.patch_prefs(money_disclaimer_accepted=True)
        d = store.upsert_debt(
            name=name,
            balance=balance,
            apr_pct=apr_pct,
            min_payment=min_payment,
            due_day=due_day,
            note=note,
            id=id,
        )
        return json.dumps(
            {
                "ok": True,
                "debt": d.to_dict(),
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"Saved debt {d.name}",
            },
            indent=2,
        )

    async def debt_scenario(
        name: str = "",
        debt_id: str = "",
        extra_payment: float = 0.0,
    ) -> str:
        store.patch_prefs(money_disclaimer_accepted=True)
        return json.dumps(
            store.debt_scenario(
                name=name, debt_id=debt_id, extra_payment=float(extra_payment or 0)
            ),
            indent=2,
        )

    async def bill_list() -> str:
        bills = [b.to_dict() for b in store.list_bills()]
        return json.dumps(
            {
                "ok": True,
                "bills": bills,
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"{len(bills)} bill(s)",
            },
            indent=2,
        )

    async def bill_upsert(
        name: str = "",
        amount: float = 0.0,
        cadence: str = "monthly",
        next_due: str = "",
        note: str = "",
        id: str = "",
    ) -> str:
        store.patch_prefs(money_disclaimer_accepted=True)
        b = store.upsert_bill(
            name=name,
            amount=amount,
            cadence=cadence,
            next_due=next_due,
            note=note,
            id=id,
        )
        return json.dumps(
            {
                "ok": True,
                "bill": b.to_dict(),
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"Saved bill {b.name}",
            },
            indent=2,
        )

    async def money_disclaimer() -> str:
        return MONEY_DISCLAIMER_FULL

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "assistant_accounts",
        "Personal assistant status: linked accounts (planned/connected), budget/debt counts, brief prefs.",
        assistant_accounts,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "assistant_brief",
        "On-demand brief: budget/bills/debts + open goals. Calendar/mail when accounts linked.",
        assistant_brief,
        {
            "type": "object",
            "properties": {
                "hint": {"type": "string", "description": "Optional focus"},
            },
        },
    )
    reg.register_builtin_handler(
        "budget_get",
        "Get current budget period and category remaining (organization tool, not advice).",
        budget_get,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "budget_set",
        "Set/replace budget for a month label. categories_json: JSON list of {name, planned}.",
        budget_set,
        {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "e.g. 2026-07"},
                "income_planned": {"type": "number"},
                "categories_json": {
                    "type": "string",
                    "description": '[{"name":"groceries","planned":400}]',
                },
            },
        },
    )
    reg.register_builtin_handler(
        "budget_tx_add",
        "Log an expense or income against the budget (user-entered amounts).",
        budget_tx_add,
        {
            "type": "object",
            "properties": {
                "amount": {"type": "number"},
                "category": {"type": "string"},
                "note": {"type": "string"},
                "kind": {"type": "string", "description": "expense | income"},
                "date": {"type": "string", "description": "YYYY-MM-DD optional"},
            },
            "required": ["amount"],
        },
    )
    reg.register_builtin_handler(
        "budget_status",
        "Spent vs planned by category (organization only).",
        budget_status,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "debt_list",
        "List user-entered debts (balances/APRs you stored — not credit pulls).",
        debt_list,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "debt_upsert",
        "Add/update a debt the user reports (name, balance, apr_pct, min_payment, due_day).",
        debt_upsert,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "balance": {"type": "number"},
                "apr_pct": {"type": "number"},
                "min_payment": {"type": "number"},
                "due_day": {"type": "integer"},
                "note": {"type": "string"},
                "id": {"type": "string"},
            },
            "required": ["name"],
        },
    )
    reg.register_builtin_handler(
        "debt_scenario",
        "Illustrative payoff months if paying min+extra (NOT financial advice).",
        debt_scenario,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "debt_id": {"type": "string"},
                "extra_payment": {"type": "number"},
            },
        },
    )
    reg.register_builtin_handler(
        "bill_list",
        "List bills/due dates the user logged.",
        bill_list,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "bill_upsert",
        "Add/update a bill (name, amount, cadence monthly|weekly|yearly, next_due YYYY-MM-DD).",
        bill_upsert,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "amount": {"type": "number"},
                "cadence": {"type": "string"},
                "next_due": {"type": "string"},
                "note": {"type": "string"},
                "id": {"type": "string"},
            },
            "required": ["name"],
        },
    )
    reg.register_builtin_handler(
        "money_disclaimer",
        "Show the full budget/debt tools disclaimer (organization, not advice).",
        money_disclaimer,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "calendar_list_events",
        "List the owner's calendar events. Works over CalDAV with the mailbox app "
        "password (Gmail / iCloud / Fastmail) or Google OAuth — whichever is "
        "connected. days default 7.",
        calendar_list_events,
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Lookahead days (1–31), default 7"},
                "time_min": {"type": "string", "description": "Optional ISO start"},
                "time_max": {"type": "string", "description": "Optional ISO end"},
            },
        },
    )
    reg.register_builtin_handler(
        "calendar_create_event",
        "Create an event on the owner's calendar. Works over CalDAV with the mailbox "
        "app password or Google OAuth — whichever is connected. Official API, "
        "never a browser login.",
        calendar_create_event,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {
                    "type": "string",
                    "description": "ISO datetime or YYYY-MM-DD (all-day)",
                },
                "end": {
                    "type": "string",
                    "description": "ISO datetime or YYYY-MM-DD (all-day exclusive end)",
                },
                "description": {"type": "string"},
            },
            "required": ["title", "start", "end"],
        },
    )
    reg.register_builtin_handler(
        "mail_connect",
        "Connect the owner's mailbox with an APP PASSWORD (no Google Cloud project "
        "needed). Works for Gmail / Outlook / Yahoo / Fastmail / iCloud once 2-step "
        "verification is on. Verifies IMAP+SMTP before saving. Ask the owner to "
        "generate the 16-character app password — never their normal password.",
        mail_connect,
        {
            "type": "object",
            "properties": {
                "address": {"type": "string", "description": "Full email address"},
                "app_password": {
                    "type": "string",
                    "description": "16-character app password from the mail provider",
                },
            },
            "required": ["address", "app_password"],
        },
    )
    reg.register_builtin_handler(
        "mail_disconnect",
        "Disconnect the owner's mailbox: forget the stored app password and drop "
        "the linked account. Use when they ask to unlink, or before connecting a "
        "different address.",
        mail_disconnect,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "mail_status",
        "Which mailbox is connected and by which method (app password or Google OAuth).",
        mail_status,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "calendar_update_event",
        "Reschedule or edit an existing event (event_id from calendar_list_events). "
        "Only the fields you pass change — use this to move a time, not create a duplicate.",
        calendar_update_event,
        {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "title": {"type": "string"},
                "start": {"type": "string", "description": "New ISO start or YYYY-MM-DD"},
                "end": {"type": "string", "description": "New ISO end or YYYY-MM-DD"},
                "description": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["event_id"],
        },
    )
    reg.register_builtin_handler(
        "calendar_cancel_event",
        "Cancel/delete a calendar event (event_id from calendar_list_events). "
        "Not reversible from here — asks the owner first in Ask mode.",
        calendar_cancel_event,
        {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    )
    reg.register_builtin_handler(
        "mail_reply",
        "Reply IN THREAD to a message (message_id from mail_list). Keeps the "
        "conversation together — prefer this over mail_send when answering mail.",
        mail_reply,
        {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "body": {"type": "string"},
                "reply_all": {"type": "boolean", "description": "Include original Cc"},
            },
            "required": ["message_id", "body"],
        },
    )
    reg.register_builtin_handler(
        "mail_archive",
        "Archive a message — out of the inbox, still searchable.",
        mail_archive,
        {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
        },
    )
    reg.register_builtin_handler(
        "mail_mark_read",
        "Mark a message read (read=false to mark unread).",
        mail_mark_read,
        {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "read": {"type": "boolean"},
            },
            "required": ["message_id"],
        },
    )
    reg.register_builtin_handler(
        "mail_list",
        "List the owner's mail (query e.g. in:inbox, from:x). Works over IMAP with an "
        "app password (Gmail / Outlook / Yahoo / Fastmail / iCloud) or Google "
        "OAuth — whichever is connected. mail_connect links one.",
        mail_list,
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query (default in:inbox)",
                },
                "limit": {"type": "integer", "description": "Max messages 1–50"},
            },
        },
    )
    reg.register_builtin_handler(
        "mail_get",
        "Read one message by id from mail_list (plain text / snippet). Same mailbox "
        "mail_list used — IMAP app password or Google OAuth.",
        mail_get,
        {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
            },
            "required": ["message_id"],
        },
    )
    reg.register_builtin_handler(
        "mail_create_draft",
        "Create a draft (does not send). Works with an IMAP app password or Google "
        "OAuth — whichever mailbox is connected.",
        mail_create_draft,
        {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to"],
        },
    )
    reg.register_builtin_handler(
        "mail_send",
        "Send a message now (not a draft), over IMAP/SMTP or Google OAuth. "
        "Use only when the user explicitly asks to send.",
        mail_send,
        {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to"],
        },
    )
