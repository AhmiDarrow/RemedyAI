"""Document intake — real paper turned into proposals, and nothing more.

Two promises hold this together. First, intake *proposes*: it never books the
appointment or pays the bill, so the payload must stay proposals until the
owner says yes. Second, when a photo cannot be read yet, it must say so and
say what to do next — the failure mode is answering "no proposals" about a
bill that is plainly legible to anyone looking at it, which reads as "nothing
here" instead of "I could not see it".

The extraction itself lives in remedy.core.documents and is tested there; this
is the tool surface around it.
"""

from __future__ import annotations

import json

import pytest

from remedy.core.agent_document_tools import register_document_tools


class Reg:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.schemas: dict[str, dict] = {}
        self.descriptions: dict[str, str] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler
        self.schemas[name] = parameters or {}
        self.descriptions[name] = description or ""


class RT:
    def __init__(self, root) -> None:
        self.tool_registry = Reg()
        self.config = type("C", (), {"home_dir": str(root)})()
        self.root = root
        self.resolved: list[str] = []

    def resolve_tool_path(self, path, *, for_write=False):
        self.resolved.append(str(path))
        target = (self.root / str(path)).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise PermissionError("outside jail")
        return target


BILL = """ACME Water Services
Account number 4471-9920
Amount due: $128.40
Due date: 12 March 2027
Please pay by the due date to avoid a late fee.
"""


@pytest.fixture()
def docs(tmp_path):
    rt = RT(tmp_path)
    register_document_tools(rt)
    return {"rt": rt, "tools": rt.tool_registry.tools, "root": tmp_path}


# --- document_read ----------------------------------------------------------


@pytest.mark.asyncio
async def test_reading_needs_a_path(docs):
    out = json.loads(await docs["tools"]["document_read"]())
    assert out["ok"] is False
    assert "path required" in out["message"]


@pytest.mark.asyncio
async def test_a_text_file_is_read(docs):
    (docs["root"] / "bill.txt").write_text(BILL, encoding="utf-8")
    out = json.loads(await docs["tools"]["document_read"](path="bill.txt"))
    assert out["ok"] is True
    assert "128.40" in out["text"]


@pytest.mark.asyncio
async def test_a_missing_file_is_reported_not_raised(docs):
    out = json.loads(await docs["tools"]["document_read"](path="nope.txt"))
    assert out.get("ok") is not True


@pytest.mark.asyncio
async def test_the_path_goes_through_the_jail(docs):
    """A document read is a file read; it gets the same guard as any other."""
    await docs["tools"]["document_read"](path="bill.txt")
    assert docs["rt"].resolved == ["bill.txt"]


@pytest.mark.asyncio
async def test_an_enormous_document_is_truncated(docs):
    """A 5MB scan must not become a 5MB tool result."""
    (docs["root"] / "big.txt").write_text("x" * 200_000, encoding="utf-8")
    out = json.loads(await docs["tools"]["document_read"](path="big.txt"))
    assert len(out["text"]) <= 20000


# --- document_intake --------------------------------------------------------


@pytest.mark.asyncio
async def test_intake_needs_something_to_work_from(docs):
    out = json.loads(await docs["tools"]["document_intake"]())
    assert out["ok"] is False
    assert "path" in out["message"] and "text" in out["message"]


@pytest.mark.asyncio
async def test_intake_reads_text_given_directly(docs):
    out = json.loads(await docs["tools"]["document_intake"](text=BILL))
    assert out["source"] == "given_text"
    assert out.get("proposals") is not None


@pytest.mark.asyncio
async def test_intake_reads_a_file_when_given_a_path(docs):
    (docs["root"] / "bill.txt").write_text(BILL, encoding="utf-8")
    out = json.loads(await docs["tools"]["document_intake"](path="bill.txt"))
    assert out.get("proposals") is not None
    assert out["source"] != "given_text"


@pytest.mark.asyncio
async def test_given_text_wins_over_a_path(docs):
    """The model read the photo itself; do not go back to the decoder."""
    (docs["root"] / "bill.txt").write_text("something else entirely", encoding="utf-8")
    out = json.loads(
        await docs["tools"]["document_intake"](path="bill.txt", text=BILL)
    )
    assert out["source"] == "given_text"
    assert docs["rt"].resolved == []


@pytest.mark.asyncio
async def test_the_document_body_is_not_echoed_back(docs):
    """It doubles the turn for nothing — the model already sent it."""
    out = json.loads(await docs["tools"]["document_intake"](text=BILL))
    assert "128.40" not in json.dumps(out.get("text", ""))
    assert out["text_chars"] == len(BILL)


@pytest.mark.asyncio
async def test_an_unreadable_image_says_so_and_says_what_to_do_next(docs, monkeypatch):
    """The worst outcome is a confident 'no proposals' about a legible bill."""
    monkeypatch.setattr(
        "remedy.core.documents.read_document_text",
        lambda target, runtime=None, hint="": {
            "ok": True,
            "text": "",
            "source": "image",
            "note": "queued for chat vision",
            "queued_for_chat_vision": True,
        },
    )
    out = json.loads(await docs["tools"]["document_intake"](path="scan.jpg"))
    assert out["proposals"] == []
    assert "queued for chat vision" in out["message"]
    assert "document_intake again with text=" in out["next"]


@pytest.mark.asyncio
async def test_a_read_error_is_surfaced_rather_than_swallowed(docs, monkeypatch):
    monkeypatch.setattr(
        "remedy.core.documents.read_document_text",
        lambda target, runtime=None, hint="": {
            "ok": False,
            "text": "",
            "error": "no decoder installed",
        },
    )
    out = json.loads(await docs["tools"]["document_intake"](path="scan.jpg"))
    assert out["ok"] is False
    assert "no decoder installed" in out["message"]


@pytest.mark.asyncio
async def test_a_queued_image_that_also_read_text_flags_both(docs, monkeypatch):
    monkeypatch.setattr(
        "remedy.core.documents.read_document_text",
        lambda target, runtime=None, hint="": {
            "ok": True,
            "text": BILL,
            "source": "ocr",
            "queued_for_chat_vision": True,
        },
    )
    out = json.loads(await docs["tools"]["document_intake"](path="scan.jpg"))
    assert out["also_queued_for_your_vision"] is True
    assert out.get("proposals") is not None


@pytest.mark.asyncio
async def test_intake_proposes_and_does_not_act(docs):
    """Nothing in the payload may be a completed action."""
    out = json.loads(await docs["tools"]["document_intake"](text=BILL))
    blob = json.dumps(out).lower()
    for done_word in ("paid", "booked", "scheduled the", "created reminder id"):
        assert done_word not in blob


# --- registration -----------------------------------------------------------


def test_both_document_tools_are_registered(docs):
    assert set(docs["tools"]) == {"document_read", "document_intake"}


def test_reading_declares_that_it_needs_a_path(docs):
    assert docs["rt"].tool_registry.schemas["document_read"]["required"] == ["path"]


def test_intake_takes_either_a_path_or_text(docs):
    props = docs["rt"].tool_registry.schemas["document_intake"]["properties"]
    assert {"path", "text"} <= set(props)


def test_the_intake_description_says_proposals_only(docs):
    """The model reads this line before deciding whether to act on the result.

    If it ever stops saying so, the model starts treating a proposal to pay a
    bill as an instruction to pay it.
    """
    desc = docs["rt"].tool_registry.descriptions["document_intake"]
    assert "PROPOSALS ONLY" in desc
    assert "confirm" in desc.lower()
