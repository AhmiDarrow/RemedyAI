"""Document intake tools — real-world paper becomes things Remedy can do.

``document_intake`` is the one to reach for: read a photo/scan, work out what
it is, and come back with concrete proposals (track this bill, remind before
it's due, put the appointment in the calendar). Nothing is written anywhere
until the owner says yes.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from remedy.core import documents as D


def register_document_tools(runtime: Any) -> None:
    def _resolve(path: str):
        """Jail the path like any other file read."""
        with contextlib.suppress(Exception):
            return runtime.resolve_tool_path(path)
        from pathlib import Path

        return Path(path)

    async def document_read(path: str = "", hint: str = "") -> str:
        """Get the text out of a document (photo, scan, or text file)."""
        if not str(path or "").strip():
            return json.dumps({"ok": False, "message": "path required"}, indent=2)
        target = _resolve(path)
        out = D.read_document_text(target, runtime=runtime, hint=hint)
        if out.get("ok") and out.get("text"):
            out["text"] = str(out["text"])[:20000]
        return json.dumps(out, indent=2)

    async def document_intake(path: str = "", text: str = "", hint: str = "") -> str:
        """Read a document and propose what to do about it.

        Pass ``path`` for a file, or ``text`` when you already have the wording
        (e.g. you read the photo yourself with your own vision).
        """
        body = str(text or "")
        source = "given_text"
        read_meta: dict[str, Any] = {}
        if not body.strip():
            if not str(path or "").strip():
                return json.dumps(
                    {"ok": False, "message": "Pass a file path or the document text."},
                    indent=2,
                )
            read_meta = D.read_document_text(_resolve(path), runtime=runtime, hint=hint)
            body = str(read_meta.get("text") or "")
            source = str(read_meta.get("source") or "")
            if not body.strip():
                # Image queued for the chat model's own eyes — say so plainly
                # instead of pretending there is nothing there.
                return json.dumps(
                    {
                        "ok": bool(read_meta.get("ok")),
                        "facts": {},
                        "proposals": [],
                        "source": source,
                        "message": (
                            read_meta.get("note")
                            or read_meta.get("error")
                            or "No readable text yet."
                        ),
                        "next": (
                            "Look at the image yourself on the next step, then call "
                            "document_intake again with text= set to what you read."
                        ),
                    },
                    indent=2,
                )
        result = D.intake(body)
        result["source"] = source
        if read_meta.get("queued_for_chat_vision"):
            result["also_queued_for_your_vision"] = True
        # Keep the transcript out of the payload — the model already has it when
        # it passed text=, and a long bill body doubles the turn for nothing.
        result["text_chars"] = len(body)
        return json.dumps(result, indent=2)

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "document_read",
        "Read the text out of a document file (photo/scan of a letter, bill, "
        "notice; or a .txt/.md). Images use the visual decoder and are also "
        "queued for your own vision when you have it.",
        document_read,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path or attachment path"},
                "hint": {"type": "string", "description": "What the owner wants from it"},
            },
            "required": ["path"],
        },
    )
    reg.register_builtin_handler(
        "document_intake",
        "Turn a real-world document into proposed actions: classify it (bill / "
        "appointment / notice / prescription / receipt), pull out amounts, "
        "dates, account and contact details, and propose concrete next steps "
        "(track the bill, remind before it is due, add the appointment). "
        "PROPOSALS ONLY — confirm the amounts and dates with the owner before "
        "running any of them. Pass text= if you read the image yourself.",
        document_intake,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Document file to read"},
                "text": {
                    "type": "string",
                    "description": "Document wording, if you already have it",
                },
                "hint": {"type": "string"},
            },
        },
    )
