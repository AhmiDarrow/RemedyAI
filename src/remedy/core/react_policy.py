"""ReAct tool policy and pseudo-tool recovery helpers.

Extracted from agent.py so the stream loop stays readable and these pieces
can be unit-tested in isolation.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import uuid4

from remedy.interfaces.config import persona_system_addendum

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are Remedy — a fast, self-improving coding agent.\n\n"
    "Style: concise, decisive, high-signal. Prefer action over narration.\n"
    "Do not monologue about plans before tool calls; just call tools, then answer.\n\n"
    "Skills vs tools:\n"
    "- **Skills** are named procedure packs (how to review code, write tests, etc.). "
    "When asked \"what skills do you have?\", list them from the Skills block in context "
    "— do NOT shell out or invent names.\n"
    "- **Tools** are executable actions: file_read, file_write, list_dir, bash_exec.\n\n"
    "Tool policy (OpenCode-smooth):\n"
    "- Simple chat (greetings, definitions, provider/model/skills questions): "
    "answer immediately with NO tools.\n"
    "- Project work (review, files, shell, debug, implement): use the function-calling API.\n"
    "- NEVER write tool calls as plain text (e.g. file_read(\"x\") && list_dir(\"y\")). "
    "That hangs the UI — always use native tool_calls.\n"
    "- Prefer parallel tool calls for independent reads; avoid repeating the same call.\n"
    "- After tool results, synthesize a clear final answer. Never stall or loop.\n"
    "- If information is already in context (provider block, skills list, history), use it."
)

# Real coding agents need headroom; simple turns never spend this budget.
MAX_REACT_STEPS = 24
MAX_PARALLEL_TOOLS = 8
HISTORY_MSG_LIMIT = 30
HISTORY_CHAR_BUDGET = 14_000

# Messages that look like they need filesystem / shell tools.
_TOOL_HINT_RE = re.compile(
    r"\b("
    r"read|write|edit|create|delete|list|ls|cat|open|save|"
    r"file|files|folder|directory|path|workspace|codebase|repo|repository|project|"
    r"review|analyze|analyse|explore|overview|inspect|structure|architecture|"
    r"run|execute|shell|bash|command|terminal|install|build|test|"
    r"implement|refactor|debug|fix|bug|error|stack|trace|"
    r"git|commit|diff|branch|src/|\\.[a-z]{1,5}\b"
    r")\b|"
    r"(?:[A-Za-z]:)?[\\/][\w.\\/ -]+",
    re.IGNORECASE,
)

# Meta questions that must be answered from context (no shell / file thrash).
_META_NO_TOOLS_RE = re.compile(
    r"\b(what skills|which skills|list skills|your skills|"
    r"what tools|which tools|list tools|your tools|"
    r"what can you do|who are you|what are you)\b",
    re.IGNORECASE,
)

# Models sometimes emit tool syntax as plain text instead of function-calls.
_PSEUDO_TOOL_RE = re.compile(
    r"\b(file_read|file_write|list_dir|bash_exec)\s*\(",
    re.IGNORECASE,
)


def message_wants_tools(message: str) -> bool:
    """Return False for chit-chat / simple Qs so models answer in one shot."""
    msg = (message or "").strip()
    if not msg:
        return False
    if _META_NO_TOOLS_RE.search(msg):
        return False
    if _TOOL_HINT_RE.search(msg):
        return True
    if len(msg) <= 160:
        return False
    return True


# Back-compat alias used by older tests / imports.
_message_wants_tools = message_wants_tools


def looks_like_pseudo_tools(text: str) -> bool:
    """True when the model faked tool calls in natural language."""
    if not text:
        return False
    if _PSEUDO_TOOL_RE.search(text):
        return True
    if "&&" in text and re.search(r"\w+\(\s*[\"']", text):
        return True
    return False


_looks_like_pseudo_tools = looks_like_pseudo_tools


def parse_pseudo_tool_calls(text: str) -> list[dict[str, Any]]:
    """Best-effort parse of text-faked tools into OpenAI-style tool_call dicts.

    Models sometimes emit file_read("x") as plain text instead of native tool
    calls. We recover them here and log aggressively so system prompts can be
    tuned from real telemetry rather than growing more recovery heuristics.
    """
    if not text:
        return []
    pat = re.compile(
        r"\b(file_read|file_write|list_dir|bash_exec)\s*\(\s*[\"']([^\"']+)[\"']"
        r"(?:\s*,\s*[\"']([\s\S]*?)[\"'])?\s*\)",
        re.IGNORECASE,
    )
    out: list[dict[str, Any]] = []
    for i, m in enumerate(pat.finditer(text)):
        name = m.group(1).lower()
        arg0 = m.group(2)
        arg1 = m.group(3)
        if name == "file_read":
            args: dict[str, Any] = {"path": arg0}
        elif name == "list_dir":
            args = {"path": arg0}
        elif name == "bash_exec":
            args = {"command": arg0}
        elif name == "file_write":
            args = {"path": arg0, "content": arg1 or ""}
        else:
            continue
        out.append(
            {
                "id": f"pseudo_{i}_{uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args),
                },
            }
        )
        if len(out) >= MAX_PARALLEL_TOOLS:
            break
    if out:
        logger.warning(
            "pseudo_tool_recovery count=%s names=%s preview=%r",
            len(out),
            [c["function"]["name"] for c in out],
            (text or "")[:200],
        )
    return out


_parse_pseudo_tool_calls = parse_pseudo_tool_calls


def tool_call_fingerprint(tc: dict[str, Any]) -> str:
    fn = tc.get("function") or {}
    name = (fn.get("name") or "").strip()
    raw = fn.get("arguments") or "{}"
    if not isinstance(raw, str):
        try:
            raw = json.dumps(raw, sort_keys=True, default=str)
        except Exception:
            raw = str(raw)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
        raw = json.dumps(parsed, sort_keys=True, default=str)
    except Exception:
        raw = raw.strip()
    return f"{name}::{raw}"


_tool_call_fingerprint = tool_call_fingerprint


def build_system_prompt(persona: str | None = None) -> str:
    base = _DEFAULT_SYSTEM_PROMPT
    addendum = persona_system_addendum(persona)
    if addendum:
        return f"{base}\n\n{addendum}"
    return base


_build_system_prompt = build_system_prompt
