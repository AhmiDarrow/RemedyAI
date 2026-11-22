"""A2A v1.0 Agent Card at /.well-known/agent-card.json."""

from __future__ import annotations

from typing import Any

from remedy import __version__

A2A_PROTOCOL_VERSION = "1.0"
WELL_KNOWN_PATH = "/.well-known/agent-card.json"
# Compat alias used by some pre-0.3.0 clients; we also serve this path.
WELL_KNOWN_LEGACY_PATH = "/.well-known/agent.json"


def public_card(
    *,
    url: str,
    name: str = "Remedy",
    description: str | None = None,
    version: str | None = None,
    streaming: bool = True,
    extended: bool = True,
) -> dict[str, Any]:
    desc = description or (
        "A partner on this computer. She finishes the goal you set and waits "
        "for the owner before money, send, or delete. She talks with other "
        "bots as a peer over A2A and MCP — never as a muscle they run."
    )
    return {
        "name": name,
        "description": desc,
        "version": version or __version__,
        "supportedInterfaces": [
            {
                "url": url,
                "protocolBinding": "JSONRPC",
                "protocolVersion": A2A_PROTOCOL_VERSION,
            }
        ],
        "capabilities": {
            "streaming": streaming,
            "pushNotifications": False,
            "extendedAgentCard": extended,
        },
        "securitySchemes": {
            "bearer": {
                "httpAuthSecurityScheme": {
                    "scheme": "bearer",
                }
            }
        },
        "securityRequirements": [{"bearer": []}],
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": _public_skills(),
        "provider": {
            "organization": "RemedyAI",
            "url": "https://github.com/AhmiDarrow/RemedyAI",
        },
        "documentationUrl": "https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/AGENT_INTEROP.md",
    }


def extended_card(base: dict[str, Any], *, extra_skills: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    card = dict(base)
    skills = list(card.get("skills") or [])
    if extra_skills:
        known = {s.get("id") for s in skills}
        for skill in extra_skills:
            if skill.get("id") not in known:
                skills.append(skill)
    card["skills"] = skills
    card["description"] = (
        str(card.get("description") or "")
        + " Authenticated peers may ask questions, delegate tasks, share "
        "context, and poll task status, subject to per-peer permissions and "
        "the owner's consent policy."
    ).strip()
    return card


def _public_skills() -> list[dict[str, Any]]:
    modes_in = ["text/plain", "application/json"]
    modes_out = ["text/plain", "application/json"]
    return [
        {
            "id": "ask",
            "name": "Ask Remedy",
            "description": "Ask Remedy a question in plain language. She answers as a peer.",
            "tags": ["ask", "chat", "help"],
            "examples": ["What did we decide about the Friday deploy?"],
            "inputModes": modes_in,
            "outputModes": modes_out,
        },
        {
            "id": "delegate",
            "name": "Delegate a task",
            "description": (
                "Hand Remedy a task to track and, when policy allows, work on. "
                "Money, sending messages, and deleting things still need the owner."
            ),
            "tags": ["task", "delegate", "orchestrate"],
            "examples": ["Summarize yesterday's notes and list open questions."],
            "inputModes": modes_in,
            "outputModes": modes_out,
        },
        {
            "id": "share_context",
            "name": "Share context",
            "description": "Share structured context Remedy may use for a task. Not a grant of new permissions.",
            "tags": ["context", "share"],
            "examples": ["Here is the current sprint goal and the three open PRs."],
            "inputModes": modes_in,
            "outputModes": modes_out,
        },
    ]
