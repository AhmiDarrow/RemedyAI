"""Talk-to-Remedy MCP tools: ask, delegate, task status, share context.

These sit alongside the existing skill-export tools on `remedy mcp serve`.
Transports: stdio and Streamable HTTP (MCP revision 2026-07-28, backward
compatible with 2024-11-05 / 2025-03-26).
"""

from __future__ import annotations

import json
from typing import Any

SUPPORTED_PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-11-25",
    "2026-07-28",
)
LATEST_PROTOCOL_VERSION = "2026-07-28"

TALK_TOOLS: list[dict[str, Any]] = [
    {
        "name": "remedy_ask",
        "description": (
            "Ask Remedy a question in plain English. She answers as a peer "
            "agent, not as a tool she is running. Money, send, and delete "
            "still need the owner's approval."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question or request, in plain language.",
                },
                "peer_id": {
                    "type": "string",
                    "description": "Optional contact id from Remedy's peer registry.",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "remedy_delegate",
        "description": (
            "Delegate a task to Remedy. Returns a task id you can poll with "
            "remedy_task_status. Irreversible work waits for the owner."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "What you want Remedy to do, in plain language.",
                },
                "peer_id": {"type": "string"},
                "context": {
                    "type": "string",
                    "description": "Optional extra context (plain text or JSON).",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "remedy_task_status",
        "description": "Look up the status, artifacts, and latest message of a delegated task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task id returned by remedy_delegate or A2A."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "remedy_share_context",
        "description": (
            "Share context Remedy may use for an ongoing or future task. "
            "This is not a grant of new permissions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "Plain text or JSON context to share."},
                "task_id": {"type": "string", "description": "Optional existing task to attach this to."},
                "peer_id": {"type": "string"},
            },
            "required": ["context"],
        },
    },
]


def negotiate_version(requested: str | None) -> str:
    if requested and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


def call_talk_tool(runtime: Any, name: str, args: dict[str, Any], *, source: str = "http") -> str:
    if name == "remedy_ask":
        result = runtime.ask(str(args.get("question") or ""), peer_id=str(args.get("peer_id") or "") or None, source=source)
        return _render(result)
    if name == "remedy_delegate":
        result = runtime.delegate(
            str(args.get("task") or ""),
            peer_id=str(args.get("peer_id") or "") or None,
            context=str(args.get("context") or "") or None,
            source=source,
        )
        return _render(result)
    if name == "remedy_task_status":
        result = runtime.task_status(str(args.get("task_id") or ""))
        return _render(result)
    if name == "remedy_share_context":
        result = runtime.share_context(
            str(args.get("context") or ""),
            peer_id=str(args.get("peer_id") or "") or None,
            task_id=str(args.get("task_id") or "") or None,
            source=source,
        )
        return _render(result)
    raise KeyError(name)


def _render(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, indent=2)
