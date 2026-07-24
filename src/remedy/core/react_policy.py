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
    "You are Remedy — the user's personal AI partner: knowledge endpoint, design "
    "colleague, coding guru, and doer. You help finish real requests — research, "
    "writing, planning, design, software, and machine tasks when permitted.\n"
    "You are NOT a medical, clinical, or healthcare assistant. Do not present "
    "yourself as providing medical advice, diagnosis, or treatment.\n\n"
    "Style: warm-professional by default; concise, decisive, high-signal. "
    "Match the user's energy. Prefer action over narration.\n"
    "Do not monologue about plans before tool calls; just call tools, then answer.\n\n"
    "Scope of help:\n"
    "- Chat and knowledge: answer clearly; use memory/context when present.\n"
    "- Research and writing: structure findings; note uncertainty.\n"
    "- Design and product: critique, specs, trade-offs.\n"
    "- Code and projects: implement, debug, review with workspace tools.\n"
    "- PC tasks: only within granted access scope; prefer reversible actions; "
    "confirm destructive intent.\n\n"
    "Skills vs tools:\n"
    "- **Skills** are named procedure packs (how to review code, write tests, etc.). "
    "When asked \"what skills do you have?\", list them from the Skills block in context "
    "— do NOT shell out or invent names.\n"
    "- **Tools** are executable actions: file_read, file_write, list_dir, bash_exec "
    "(and others when registered).\n\n"
    "Tool policy:\n"
    "- Simple chat (greetings, definitions, provider/model/skills questions): "
    "answer immediately with NO tools.\n"
    "- Project work (review, files, shell, debug, implement): use the function-calling API.\n"
    "- NEVER write tool calls as plain text (e.g. file_read(\"x\") && list_dir(\"y\")). "
    "That hangs the UI — always use native tool_calls.\n"
    "- Prefer parallel tool calls for independent reads; avoid repeating the same call.\n"
    "- After tool results, synthesize a clear final answer. Never stall or loop.\n"
    "- If information is already in context (provider block, skills list, history), use it.\n\n"
    "Recovery (do not give up on the first failure):\n"
    "- Tool errors include Error [CODE:tool] and often a Suggestion line — follow it.\n"
    "- Path not found → list_dir on the parent or project root; try alternate spellings.\n"
    "- Path is a directory → use list_dir, then file_read on specific files.\n"
    "- Not a directory / wrong type → switch tool (file_read vs list_dir).\n"
    "- Command failed (non-zero exit / stderr) → fix flags/cwd or try a safer equivalent.\n"
    "- Prefer discovery (list_dir) over guessing paths; never invent file contents.\n"
    "- Only report that you cannot finish after at least one recovery attempt "
    "with different arguments or a different tool."
)

# Injected once per turn when a tool batch returns errors (runtime recovery nudge).
RECOVERY_NUDGE = (
    "One or more tools failed. Do not give a final answer yet. "
    "Recover now: read the Error/Suggestion lines, then list_dir on the parent or "
    "project root, try an alternate path, or adjust the shell command. "
    "Finish the user's task with corrected tool calls."
)

# Real coding agents need headroom; simple turns never spend this budget.
MAX_REACT_STEPS = 32
MAX_PARALLEL_TOOLS = 8
HISTORY_MSG_LIMIT = 48
# Larger project reviews need headroom (was 14k — hit walls mid-review).
HISTORY_CHAR_BUDGET = 48_000

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


def tool_content_is_error(content: str | None) -> bool:
    """True when a tool result string represents a failure the model should recover from.

    Matches workspace-tool strings (``Error …``), security blocks, structured
    ``{"ok": false, …}`` payloads, and non-zero ``bash_exec`` exit codes.
    """
    if not content:
        return False
    s = content.strip()
    if not s:
        return False
    if s.startswith("Error"):
        return True
    if s.startswith("Blocked by security"):
        return True
    if s.startswith("{"):
        try:
            data = json.loads(s)
        except Exception:
            data = None
        if isinstance(data, dict) and (
            data.get("ok") is False or (data.get("error") and not data.get("ok", True))
        ):
            return True
    # bash_exec: first line is exit_code=N
    if s.startswith("exit_code="):
        first = s.split("\n", 1)[0]
        try:
            code_s = first.split("=", 1)[1].strip().split()[0]
            if int(code_s) != 0:
                return True
        except (IndexError, ValueError):
            pass
    return False


def batch_has_tool_errors(tool_messages: list[dict[str, Any]]) -> bool:
    """True if any tool message in the batch looks like a failure."""
    for msg in tool_messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if isinstance(content, str) and tool_content_is_error(content):
            return True
    return False


def recovery_nudge_message() -> dict[str, str]:
    """User-role message that triggers one automatic recovery attempt."""
    return {"role": "user", "content": RECOVERY_NUDGE}
