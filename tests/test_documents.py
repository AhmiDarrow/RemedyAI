"""Document intake — classification, field extraction, and proposals.

A wrong amount or date here would become a wrong bill or a missed deadline, so
the extraction is deterministic and tested hard. Nothing in this module may
execute an action: it proposes, the owner confirms.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from remedy.core import documents as D

NOW = datetime(2026, 8, 17, 10, 0, 0)

POWER_BILL = """City Power & Light
Account Number: 4471-99823
Statement date: August 1, 2026

Amount due: $184.53
Payment due by 09/05/2026

Questions? Call (417) 555-0142 or email billing@citypower.example
"""

APPOINTMENT = """Springfield Family Dental
Your appointment is scheduled for September 3, 2026 at 2:30 PM.
Please arrive 15 minutes early.
To reschedule call 417-555-8890.
"""

RENEWAL = """Missouri Department of Revenue
NOTICE — ACTION REQUIRED
Your vehicle registration expires 10/31/2026.
Renew by that date to avoid a late fee.
"""

PRESCRIPTION = """Walgreens Pharmacy
Prescription refill ready
Rx 88213-A  Metformin 500mg tablets
Take one tablet twice daily.
"""


# --- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    "text,kind",
    [
        (POWER_BILL, "bill"),
        (APPOINTMENT, "appointment"),
        (RENEWAL, "notice"),
        (PRESCRIPTION, "prescription"),
    ],
)
def test_classify(text: str, kind: str) -> None:
    got, conf = D.classify_document(text)
    assert got == kind
    assert conf > 0


def test_classify_empty_and_unknown() -> None:
    assert D.classify_document("") == ("other", 0.0)
    assert D.classify_document("just some words")[0] == "other"


# --- amounts ----------------------------------------------------------------


def test_find_amounts_parses_and_flags_due() -> None:
    hits = D.find_amounts(POWER_BILL)
    assert [h.value for h in hits] == [184.53]
    assert hits[0].currency == "USD"
    assert hits[0].is_due is True  # "Amount due:" in context


def test_find_amounts_handles_thousands_and_symbols() -> None:
    vals = {h.value for h in D.find_amounts("Total $1,234.56 and £99.00 and 45.00 USD")}
    assert vals == {1234.56, 99.00, 45.00}


def test_find_amounts_ignores_bare_numbers() -> None:
    assert D.find_amounts("Account 4471 99823 ref 12") == []


# --- dates ------------------------------------------------------------------


def test_bill_due_date_and_role() -> None:
    dates = D.find_dates(POWER_BILL, now=NOW)
    due = [d for d in dates if d.role == "due"]
    assert due and due[0].iso == "2026-09-05"


def test_statement_date_marked_issued() -> None:
    issued = [d for d in D.find_dates(POWER_BILL, now=NOW) if d.role == "issued"]
    assert issued and issued[0].iso == "2026-08-01"


def test_appointment_date_role() -> None:
    hits = [d for d in D.find_dates(APPOINTMENT, now=NOW) if d.role == "appointment"]
    assert hits and hits[0].iso == "2026-09-03"


def test_expiry_role() -> None:
    hits = [d for d in D.find_dates(RENEWAL, now=NOW) if d.role == "expires"]
    assert hits and hits[0].iso == "2026-10-31"


@pytest.mark.parametrize(
    "raw,iso",
    [
        ("2026-09-15", "2026-09-15"),
        ("September 15, 2026", "2026-09-15"),
        ("Sep 15 2026", "2026-09-15"),
        ("15 September 2026", "2026-09-15"),
        ("09/15/2026", "2026-09-15"),
        ("9/15/26", "2026-09-15"),
    ],
)
def test_date_formats(raw: str, iso: str) -> None:
    hits = D.find_dates(f"due {raw}", now=NOW)
    assert any(h.iso == iso for h in hits), f"{raw} -> {[h.iso for h in hits]}"


def test_impossible_date_is_dropped() -> None:
    assert D.find_dates("due 02/30/2026", now=NOW) == []


def test_missing_year_defaults_to_current() -> None:
    hits = D.find_dates("appointment on March 4", now=NOW)
    assert hits and hits[0].iso.startswith("2026-03-04")


# --- contacts / accounts ----------------------------------------------------


def test_contacts_and_account_number() -> None:
    facts = D.extract_facts(POWER_BILL, now=NOW)
    assert "billing@citypower.example" in facts.emails
    assert any("555-0142" in p or "5550142" in p.replace(" ", "") for p in facts.phones)
    assert any("4471" in a for a in facts.accounts)
    assert facts.sender.startswith("City Power")


def test_unreadable_flag() -> None:
    assert D.extract_facts("Amount due: $[unreadable]").unreadable is True
    assert D.extract_facts("Amount due: $5.00").unreadable is False


# --- proposals --------------------------------------------------------------


def test_bill_proposes_tracking_and_early_reminder() -> None:
    out = D.intake(POWER_BILL, now=NOW)
    tools = [p["tool"] for p in out["proposals"]]
    assert "bill_upsert" in tools
    assert "remind_me" in tools
    bill = next(p for p in out["proposals"] if p["tool"] == "bill_upsert")
    assert bill["args"]["amount"] == 184.53
    assert bill["args"]["next_due"] == "2026-09-05"
    rem = next(p for p in out["proposals"] if p["tool"] == "remind_me")
    # reminder lands BEFORE the due date, which is the whole point
    assert rem["args"]["when"] < "2026-09-05"
    assert rem["args"]["when"].startswith("2026-09-02")
    assert rem["args"]["importance"] == "high"


def test_appointment_proposes_calendar_and_night_before() -> None:
    out = D.intake(APPOINTMENT, now=NOW)
    tools = [p["tool"] for p in out["proposals"]]
    assert "calendar_create_event" in tools
    ev = next(p for p in out["proposals"] if p["tool"] == "calendar_create_event")
    assert ev["args"]["start"] == "2026-09-03"
    rem = next(p for p in out["proposals"] if p["tool"] == "remind_me")
    assert rem["args"]["when"].startswith("2026-09-02")


def test_expiry_proposes_renewal_reminder() -> None:
    out = D.intake(RENEWAL, now=NOW)
    rem = [p for p in out["proposals"] if p["tool"] == "remind_me"]
    assert rem, "a deadline with no reminder is the failure mode this exists to stop"
    assert "renew" in rem[0]["args"]["text"].lower()
    assert rem[0]["args"]["when"] < "2026-10-31"


def test_prescription_without_a_date_still_proposes_a_check_in() -> None:
    out = D.intake(PRESCRIPTION, now=NOW)
    tools = [p["tool"] for p in out["proposals"]]
    assert "remind_me" in tools


def test_past_dates_are_not_proposed() -> None:
    old = "Invoice\nAmount due: $20.00\nPayment due by 01/05/2020"
    out = D.intake(old, now=NOW)
    assert not [p for p in out["proposals"] if p["tool"] == "remind_me"]


def test_every_proposal_carries_evidence_and_confirmation_flag() -> None:
    out = D.intake(POWER_BILL, now=NOW)
    assert out["needs_confirmation"] is True
    for p in out["proposals"]:
        assert "why" in p and "evidence" in p
        assert p["args"]  # never an empty action


def test_unreadable_document_warns_in_the_note() -> None:
    out = D.intake("Amount due: $[unreadable]\nPayment due by 09/05/2026", now=NOW)
    assert "unreadable" in out["note"].lower()


def test_intake_on_empty_text_is_safe() -> None:
    out = D.intake("", now=NOW)
    assert out["ok"] is True
    assert out["proposals"] == []


# --- file reading -----------------------------------------------------------


def test_read_text_file(tmp_path) -> None:
    p = tmp_path / "letter.txt"
    p.write_text(POWER_BILL, encoding="utf-8")
    out = D.read_document_text(p)
    assert out["ok"] is True and out["source"] == "text"
    assert "184.53" in out["text"]


def test_read_missing_file() -> None:
    out = D.read_document_text("nope-does-not-exist.txt")
    assert out["ok"] is False and "No file" in out["error"]


def test_read_pdf_explains_itself(tmp_path) -> None:
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 stub")
    out = D.read_document_text(p)
    assert out["ok"] is False
    assert "screenshot" in out["error"].lower()


def test_read_unknown_type(tmp_path) -> None:
    p = tmp_path / "thing.xyz"
    p.write_text("x", encoding="utf-8")
    assert D.read_document_text(p)["ok"] is False


# --- vision prompt wiring ---------------------------------------------------


def test_document_vision_prompt_registered() -> None:
    from remedy.core.computer.vision_observe import focus_question_for_kind

    doc = focus_question_for_kind("document")
    assert "real-world document" in doc
    assert "[unreadable]" in doc
    # other kinds unchanged
    assert "Click targets" in focus_question_for_kind("cua")
    assert "Hierarchy" in focus_question_for_kind("design")


def test_document_tools_registered() -> None:
    from types import SimpleNamespace

    from remedy.core.agent_document_tools import register_document_tools
    from remedy.skills.tool_registry import ToolRegistry

    reg = ToolRegistry()
    rt = SimpleNamespace(tool_registry=reg, config=SimpleNamespace(home_dir=None))
    register_document_tools(rt)
    names = {t.name for t in reg.tools}
    assert {"document_read", "document_intake"} <= names
