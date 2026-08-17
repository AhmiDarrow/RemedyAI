"""Document intake — a photo of real-world paper becomes things Remedy can do.

Phase 4 of the real-life work. Life admin arrives as paper and PDFs: bills,
appointment cards, renewal notices, prescriptions. Reading them is a burden,
and for someone who can't easily read fine print it is a barrier. This turns
"here's a letter" into "here's what it is, what it wants, and shall I handle it".

Design rule: **propose, never silently act.** A misread amount or date that
quietly became a payment reminder or a calendar entry would be worse than no
help at all. Extraction is deterministic and testable; the model reasons over
the text; the owner confirms before anything is written anywhere.

The dates/amounts here are *candidates* — every proposal carries the exact
snippet it came from so the owner can check it at a glance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# --- kinds ------------------------------------------------------------------

KINDS = (
    "bill",
    "appointment",
    "prescription",
    "notice",
    "receipt",
    "statement",
    "other",
)

_KIND_SIGNALS: dict[str, tuple[tuple[str, int], ...]] = {
    "bill": (
        ("amount due", 4), ("total due", 4), ("pay by", 4), ("payment due", 4),
        ("minimum payment", 3), ("invoice", 3), ("balance due", 4),
        ("autopay", 2), ("billing period", 2), ("account number", 1),
    ),
    "appointment": (
        ("appointment", 4), ("your visit", 3), ("please arrive", 3),
        ("scheduled for", 3), ("confirm your", 2), ("clinic", 2),
        ("check-in", 2), ("reschedule", 2),
    ),
    "prescription": (
        ("prescription", 4), ("refill", 4), ("pharmacy", 3), ("rx ", 3),
        ("tablet", 2), ("dosage", 2), ("take ", 1),
    ),
    "notice": (
        ("final notice", 5), ("action required", 4), ("expires", 3),
        ("renewal", 3), ("must be renewed", 4), ("notice", 2),
        ("failure to", 3), ("deadline", 3),
    ),
    "receipt": (
        ("receipt", 4), ("thank you for your purchase", 4), ("total paid", 4),
        ("order #", 3), ("order number", 3), ("refund", 2),
    ),
    "statement": (
        ("statement", 3), ("closing balance", 3), ("transactions", 2),
        ("period ending", 2), ("previous balance", 3),
    ),
}

# --- money ------------------------------------------------------------------

_AMOUNT_RE = re.compile(
    r"(?<![\w.])(?:(?P<sym>[$£€])\s?(?P<v1>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)"
    r"|(?P<v2>\d{1,3}(?:,\d{3})*\.\d{2})\s?(?P<cur>USD|EUR|GBP))(?![\w])"
)
_DUE_AMOUNT_HINT = re.compile(
    r"(?i)\b(amount due|total due|balance due|payment due|minimum payment|total|pay this amount)\b"
)

# --- dates ------------------------------------------------------------------

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

_DATE_PATTERNS = (
    # 2026-09-15
    re.compile(r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"),
    # September 15, 2026  /  Sep 15 2026
    re.compile(rf"(?i)\b(?P<mon>{_MONTH_ALT})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s*(?P<y>\d{{4}})?\b"),
    # 15 September 2026
    re.compile(rf"(?i)\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<mon>{_MONTH_ALT})\.?,?\s*(?P<y>\d{{4}})?\b"),
    # 09/15/2026 or 9/15/26  (US order — see note in parse)
    re.compile(r"\b(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/(?P<y>\d{2,4}))?\b"),
)

_DUE_HINT = re.compile(
    r"(?i)\b(due|pay by|payable by|by the|no later than|deadline|last day)\b"
)
_APPT_HINT = re.compile(
    r"(?i)\b(appointment|visit|scheduled|arrive|check[- ]in|see you|consultation)\b"
)
_EXPIRY_HINT = re.compile(r"(?i)\b(expires?|expiration|valid (?:through|until)|renew by)\b")
_ISSUED_HINT = re.compile(r"(?i)\b(issued|statement date|invoice date|printed|as of)\b")

_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
_URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>\"')]+")
_ACCOUNT_RE = re.compile(
    r"(?i)\b(?:account|acct|invoice|reference|ref|policy|member|order)\s*(?:no\.?|number|#)?\s*[:#]?\s*"
    r"([A-Z0-9][A-Z0-9-]{3,})"
)


@dataclass
class DateHit:
    raw: str
    iso: str
    role: str = "date"  # due | appointment | expires | issued | date
    context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"raw": self.raw, "iso": self.iso, "role": self.role, "context": self.context}


@dataclass
class AmountHit:
    raw: str
    value: float
    currency: str = "USD"
    context: str = ""
    is_due: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "value": self.value,
            "currency": self.currency,
            "context": self.context,
            "is_due": self.is_due,
        }


@dataclass
class DocumentFacts:
    kind: str = "other"
    confidence: float = 0.0
    dates: list[DateHit] = field(default_factory=list)
    amounts: list[AmountHit] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    sender: str = ""
    unreadable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "confidence": round(self.confidence, 2),
            "dates": [d.to_dict() for d in self.dates],
            "amounts": [a.to_dict() for a in self.amounts],
            "phones": self.phones,
            "emails": self.emails,
            "urls": self.urls,
            "accounts": self.accounts,
            "sender": self.sender,
            "unreadable": self.unreadable,
        }


# --- extraction -------------------------------------------------------------


def _context_of(text: str, start: int, end: int, width: int = 48) -> str:
    a = max(0, start - width)
    b = min(len(text), end + width)
    return " ".join(text[a:b].split())


def classify_document(text: str) -> tuple[str, float]:
    """Best-guess kind + a 0..1 confidence from keyword weight."""
    low = (text or "").lower()
    if not low.strip():
        return "other", 0.0
    scores: dict[str, int] = {}
    for kind, signals in _KIND_SIGNALS.items():
        total = sum(weight for phrase, weight in signals if phrase in low)
        if total:
            scores[kind] = total
    if not scores:
        return "other", 0.0
    best = max(scores, key=lambda k: scores[k])
    top = scores[best]
    runner = max((v for k, v in scores.items() if k != best), default=0)
    # Confidence rises with weight and with the margin over the runner-up.
    conf = min(1.0, top / 10.0) * (0.6 + 0.4 * (1.0 if runner == 0 else min(1.0, (top - runner) / top)))
    return best, round(min(conf, 1.0), 2)


def _safe_date(y: int, m: int, d: int) -> str:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return ""


def find_dates(text: str, *, now: datetime | None = None) -> list[DateHit]:
    """Dates with the role they play (due / appointment / expires / issued).

    Ambiguous numeric dates are read US-style (MM/DD) — stated plainly so a
    caller can flag it. A 2-digit year maps into the current century.
    """
    base = now or datetime.now()
    out: list[DateHit] = []
    seen: set[tuple[str, int]] = set()
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text or ""):
            gd = m.groupdict()
            iso = ""
            if "mon" in gd and gd.get("mon"):
                mon = _MONTHS.get(gd["mon"].lower().rstrip("."), 0)
                day = int(gd.get("d") or 0)
                year = int(gd["y"]) if gd.get("y") else base.year
                iso = _safe_date(year, mon, day)
            elif gd.get("y") and gd.get("m") and gd.get("d") and len(gd["y"]) == 4:
                iso = _safe_date(int(gd["y"]), int(gd["m"]), int(gd["d"]))
            elif gd.get("m") and gd.get("d"):
                year_raw = gd.get("y")
                if year_raw and len(year_raw) == 2:
                    year = 2000 + int(year_raw)
                elif year_raw:
                    year = int(year_raw)
                else:
                    year = base.year
                iso = _safe_date(year, int(gd["m"]), int(gd["d"]))
            if not iso:
                continue
            key = (iso, m.start())
            if key in seen:
                continue
            seen.add(key)
            ctx = _context_of(text, m.start(), m.end())
            # A document labels a date BEFORE it ("Payment due by 09/05",
            # "Statement date: August 1"). Judging on a symmetric window let a
            # later "Amount due" line mislabel the statement date, so weigh the
            # preceding text first and only glance a little way past.
            before = " ".join(text[max(0, m.start() - 44) : m.start()].split())
            after = " ".join(text[m.end() : m.end() + 14].split())
            role = "date"
            for probe in (before, f"{before} {after}"):
                if _DUE_HINT.search(probe):
                    role = "due"
                elif _APPT_HINT.search(probe):
                    role = "appointment"
                elif _EXPIRY_HINT.search(probe):
                    role = "expires"
                elif _ISSUED_HINT.search(probe):
                    role = "issued"
                if role != "date":
                    break
            out.append(DateHit(raw=m.group(0), iso=iso, role=role, context=ctx))
    # De-dupe identical iso+role, keep first mention.
    uniq: list[DateHit] = []
    taken: set[tuple[str, str]] = set()
    for hit in out:
        k = (hit.iso, hit.role)
        if k not in taken:
            taken.add(k)
            uniq.append(hit)
    uniq.sort(key=lambda h: h.iso)
    return uniq


def find_amounts(text: str) -> list[AmountHit]:
    out: list[AmountHit] = []
    for m in _AMOUNT_RE.finditer(text or ""):
        raw = m.group(0)
        num = m.group("v1") or m.group("v2") or ""
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        sym = m.group("sym") or ""
        cur = m.group("cur") or {"$": "USD", "£": "GBP", "€": "EUR"}.get(sym, "USD")
        ctx = _context_of(text, m.start(), m.end())
        out.append(
            AmountHit(
                raw=raw,
                value=value,
                currency=cur,
                context=ctx,
                is_due=bool(_DUE_AMOUNT_HINT.search(ctx)),
            )
        )
    return out


def _first_line_sender(text: str) -> str:
    """Letterheads put the sender at the top — use the first substantive line."""
    for line in (text or "").splitlines():
        s = line.strip()
        if len(s) < 3:
            continue
        low = s.lower()
        if low.startswith(("###", "transcription", "key facts", "uncertain")):
            continue
        if _DATE_PATTERNS[0].search(s) or _AMOUNT_RE.search(s):
            continue
        return s[:80]
    return ""


def extract_facts(text: str, *, now: datetime | None = None) -> DocumentFacts:
    """Everything deterministic we can pull from the document's text."""
    body = text or ""
    kind, conf = classify_document(body)
    facts = DocumentFacts(
        kind=kind,
        confidence=conf,
        dates=find_dates(body, now=now),
        amounts=find_amounts(body),
        phones=sorted({p.strip() for p in _PHONE_RE.findall(body)})[:6],
        emails=sorted(set(_EMAIL_RE.findall(body)))[:6],
        urls=sorted(set(_URL_RE.findall(body)))[:6],
        accounts=sorted({a for a in _ACCOUNT_RE.findall(body)})[:6],
        sender=_first_line_sender(body),
        unreadable="[unreadable]" in body.lower(),
    )
    return facts


# --- proposals --------------------------------------------------------------

# How far ahead of a deadline a reminder is actually useful.
LEAD_DAYS = {"bill": 3, "notice": 5, "prescription": 3, "appointment": 1, "other": 2}


def _lead_reminder(iso: str, days: int, *, hour: int = 9) -> str | None:
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return None
    lead = d - timedelta(days=max(0, days))
    return f"{lead.isoformat()}T{hour:02d}:00:00"


def propose_actions(
    facts: DocumentFacts, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Concrete, confirmable next steps — each carries the evidence it came from.

    Nothing here executes; the owner says yes first.
    """
    base = now or datetime.now()
    today = base.date()
    out: list[dict[str, Any]] = []

    def future(iso: str) -> bool:
        try:
            return date.fromisoformat(iso) >= today
        except ValueError:
            return False

    due = [d for d in facts.dates if d.role == "due" and future(d.iso)]
    appts = [d for d in facts.dates if d.role == "appointment" and future(d.iso)]
    expiry = [d for d in facts.dates if d.role == "expires" and future(d.iso)]
    due_amounts = [a for a in facts.amounts if a.is_due] or facts.amounts
    top_amount = max(due_amounts, key=lambda a: a.value) if due_amounts else None
    who = facts.sender or "this document"

    # A bill: track it and warn before it is late.
    if facts.kind == "bill" and due:
        d0 = due[0]
        label = f"{who} bill"
        if top_amount:
            label += f" ${top_amount.value:,.2f}"
        out.append(
            {
                "tool": "bill_upsert",
                "args": {
                    "name": who[:60],
                    "amount": top_amount.value if top_amount else 0.0,
                    "cadence": "monthly",
                    "next_due": d0.iso,
                },
                "why": f"Track this bill (due {d0.iso})",
                "evidence": d0.context,
            }
        )
        when = _lead_reminder(d0.iso, LEAD_DAYS["bill"])
        if when:
            out.append(
                {
                    "tool": "remind_me",
                    "args": {
                        "text": f"Pay {label} — due {d0.iso}",
                        "when": when,
                        "importance": "high",
                    },
                    "why": f"Remind {LEAD_DAYS['bill']} days before it is due",
                    "evidence": d0.context,
                }
            )

    # An appointment: put it in the calendar and nudge the day before.
    if appts:
        a0 = appts[0]
        title = f"{who}" if facts.kind == "appointment" else f"{who} appointment"
        out.append(
            {
                "tool": "calendar_create_event",
                "args": {
                    "title": title[:80],
                    "start": a0.iso,
                    "end": a0.iso,
                    "description": a0.context[:200],
                },
                "why": f"Add the appointment on {a0.iso}",
                "evidence": a0.context,
            }
        )
        when = _lead_reminder(a0.iso, LEAD_DAYS["appointment"], hour=18)
        if when:
            out.append(
                {
                    "tool": "remind_me",
                    "args": {"text": f"{title} tomorrow ({a0.iso})", "when": when},
                    "why": "Remind the evening before",
                    "evidence": a0.context,
                }
            )

    # Anything with a deadline that is not a bill: don't let it lapse.
    if expiry:
        e0 = expiry[0]
        when = _lead_reminder(e0.iso, LEAD_DAYS.get(facts.kind, 5))
        if when:
            out.append(
                {
                    "tool": "remind_me",
                    "args": {
                        "text": f"{who} expires {e0.iso} — renew it",
                        "when": when,
                        "importance": "high",
                    },
                    "why": f"Warn before it expires on {e0.iso}",
                    "evidence": e0.context,
                }
            )

    # A prescription with no explicit date still deserves a nudge to refill.
    if facts.kind == "prescription" and not (due or expiry):
        out.append(
            {
                "tool": "remind_me",
                "args": {
                    "text": f"Refill prescription from {who}",
                    "when": (today + timedelta(days=21)).isoformat() + "T09:00:00",
                },
                "why": "No refill date printed — a 3-week check-in is a safe default",
                "evidence": "",
            }
        )

    # A deadline this document asks the owner to act on, with a way to reply.
    if facts.emails and facts.kind in ("notice", "bill", "appointment"):
        out.append(
            {
                "tool": "mail_create_draft",
                "args": {"to": facts.emails[0], "subject": f"Re: {who}"[:80], "body": ""},
                "why": f"Draft a reply to {facts.emails[0]} (nothing is sent without you)",
                "evidence": "",
            }
        )
    return out


def intake(text: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Facts + proposals for a document's text. Pure — no I/O, no side effects."""
    facts = extract_facts(text, now=now)
    proposals = propose_actions(facts, now=now)
    return {
        "ok": True,
        "facts": facts.to_dict(),
        "proposals": proposals,
        "needs_confirmation": True,
        "note": (
            "These are proposals read off the document — check the amounts and "
            "dates against the evidence before I act on any of them."
            + (
                " Parts of this document were unreadable, so something may be missing."
                if facts.unreadable
                else ""
            )
        ),
    }


# --- getting text out of a file ---------------------------------------------

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".log", ".eml"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def read_document_text(
    path: str | Path, *, runtime: Any = None, hint: str = ""
) -> dict[str, Any]:
    """Text out of a document file.

    Plain text is read directly. Images go through the visual decoder with the
    document prompt, and are queued for the chat model's own vision when it has
    it (a frontier model reads a photographed letter far better than the small
    local decoder).
    """
    p = Path(path)
    out: dict[str, Any] = {"ok": False, "text": "", "source": "", "path": str(p)}
    if not p.is_file():
        out["error"] = f"No file at {p}"
        return out
    suffix = p.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        try:
            out["text"] = p.read_text(encoding="utf-8", errors="replace")[:40000]
            out["ok"] = True
            out["source"] = "text"
        except OSError as exc:
            out["error"] = str(exc)
        return out
    if suffix in IMAGE_SUFFIXES:
        from remedy.core.computer.vision_observe import (
            decode_screenshot_brief,
            focus_question_for_kind,
            queue_native_screenshot,
        )

        queued = False
        try:
            queue_native_screenshot(runtime, p, kind="document")
            queued = True
        except Exception:
            queued = False
        decoded = decode_screenshot_brief(
            p, extra_question=focus_question_for_kind("document", hint), timeout_s=45.0
        )
        out["queued_for_chat_vision"] = queued
        if decoded.get("ok") and decoded.get("text"):
            out["ok"] = True
            out["text"] = str(decoded["text"])
            out["source"] = "local_vision"
        else:
            out["source"] = "chat_vision" if queued else ""
            out["ok"] = queued
            out["error"] = "" if queued else str(decoded.get("error") or "no decoder")
            if queued:
                out["text"] = ""
                out["note"] = (
                    "The local decoder could not read it; the image is queued for "
                    "your chat model's own vision on the next step."
                )
        return out
    if suffix == ".pdf":
        out["error"] = (
            "PDFs aren't read directly yet. Open it and send a screenshot, or "
            "export the page as PNG/JPG and I'll read that."
        )
        return out
    out["error"] = f"I can't read {suffix or 'that file type'} yet."
    return out
