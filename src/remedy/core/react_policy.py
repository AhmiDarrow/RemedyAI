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
    "- **Tools** are executable actions: file_read, file_write, list_dir, bash_exec, "
    "local_discover, comfyui (and others when registered).\n\n"
    "Tool policy:\n"
    "- Simple chat (greetings, definitions, provider/model/skills questions): "
    "answer immediately with NO tools.\n"
    "- Project work (review, files, shell, debug, implement): use the function-calling API.\n"
    "- Local apps/services (ComfyUI, Ollama, skill deps): use **local_discover** "
    "(scan / one) or the dedicated tool (e.g. comfyui). "
    "NEVER thrash list_dir on C:\\ or / or run where/dir /s to find installs — "
    "discovery is built-in and portable for any machine.\n"
    "- ComfyUI images: **comfyui** action=status|locate|generate; paste markdown images "
    "from the tool result into your final answer.\n"
    "- NEVER write tool calls as plain text (e.g. file_read(\"x\") && list_dir(\"y\")). "
    "That hangs the UI — always use native tool_calls.\n"
    "- NEVER emit DSML/XML tool markup (tool_calls, invoke, invoke_parameter) as chat text.\n"
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
    r"git|commit|diff|branch|src/|\\.[a-z]{1,5}\b|"
    r"comfyui|comfy|txt2img|img2img|portrait|nebula|spacey|"
    r"generate(\s+an?)?\s+image|image\s+generation|render(\s+an?)?\s+image|"
    r"make\s+(me\s+)?(an?\s+)?(image|picture|photo)|"
    r"draw\s+(me\s+)?|illustrat|picture\s+of|photo\s+of|"
    r"show\s+(it|me|the\s+image)|embed(\s+it)?|display(\s+it)?"
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
    r"\b(file_read|file_write|list_dir|bash_exec|comfyui|local_discover)\s*\(",
    re.IGNORECASE,
)
# Leaked "tool markup" (DSML / XML-ish tool_calls) that must never show as chat text.
# DeepSeek-class models often emit fullwidth pipes: ｜DSML｜tool_calls
_DSML_TOOL_RE = re.compile(
    r"(tool[_\s-]?calls|function[_\s-]?calls|DSML|"
    r"</?invoke\b|invoke_parameter|invoke_step|"
    r"</?parameter\b|name\s*=\s*[\"'](?:file_read|bash_exec|comfyui|list_dir))",
    re.IGNORECASE,
)
_DSML_INVOKE_RE = re.compile(
    r"""name\s*=\s*["'](file_read|file_write|list_dir|bash_exec|comfyui|local_discover)["']""",
    re.IGNORECASE,
)
# Supports both <parameter name="path">val</parameter> and unclosed ...name="path"...>val
# Also: parameter name="action" string="true">status
_DSML_PARAM_RE = re.compile(
    r"""(?:invoke_parameter|parameter)\s+[^>\n]*\bname\s*=\s*["'](\w+)["'][^>\n]*>\s*([^<\n｜|]*)""",
    re.IGNORECASE | re.DOTALL,
)
# Fullwidth / markup noise that should never reach the chat bubble.
_DSML_NOISE_RE = re.compile(
    r"[|｜]{1,2}\s*DSML\s*[|｜]{1,2}|[|｜]{2}",
    re.IGNORECASE,
)
_COMFY_HUNT_RE = re.compile(
    r"comfyui|comfy.?ui|\\\\ComfyUI|/ComfyUI|8188",
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
    """True when the model faked tool calls in natural language or DSML markup."""
    if not text:
        return False
    if _PSEUDO_TOOL_RE.search(text):
        return True
    if _DSML_NOISE_RE.search(text) and re.search(
        r"tool|invoke|parameter|comfyui|bash_exec|list_dir", text, re.I
    ):
        return True
    # Any leaked tool_calls / invoke markup counts — even without a clean name=.
    if _DSML_TOOL_RE.search(text) and (
        _DSML_INVOKE_RE.search(text)
        or re.search(
            r"\b(list_dir|bash_exec|file_read|comfyui|local_discover)\b", text, re.I
        )
    ):
        return True
    if "&&" in text and re.search(r"\w+\(\s*[\"']", text):
        return True
    return False


_looks_like_pseudo_tools = looks_like_pseudo_tools


def strip_tool_markup(text: str) -> str:
    """Remove DSML / fake tool-call markup so it never stays in the user bubble."""
    if not text:
        return ""
    t = text
    # Strip DSML wrapper tokens
    t = _DSML_NOISE_RE.sub(" ", t)
    # Remove tool_calls … blocks (greedy enough for multi-invoke dumps)
    t = re.sub(
        r"(?is)(?:tool[_\s-]?calls|function[_\s-]?calls)\b.*?(?=(?:\n{2,}|\Z))",
        " ",
        t,
    )
    t = re.sub(r"(?is)</?(?:invoke|parameter|invoke_parameter|invoke_step)[^>]*>", " ", t)
    t = re.sub(
        r"""(?is)name\s*=\s*["'](?:file_read|file_write|list_dir|bash_exec|comfyui|local_discover)["'][^<\n]*""",
        " ",
        t,
    )
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


_strip_tool_markup = strip_tool_markup


def _rewrite_bash_to_comfyui(command: str) -> dict[str, Any] | None:
    """If bash is clearly a ComfyUI health/locate probe, map to comfyui tool."""
    cmd = (command or "").strip().lower()
    if not cmd:
        return None
    # Disk hunts for ComfyUI (where/dir/Get-ChildItem) → locate, not recursive bash
    if re.search(r"\b(where|dir|get-childitem|findstr|find)\b", cmd) and _COMFY_HUNT_RE.search(
        cmd
    ):
        return {"action": "locate"}
    if "8188" in cmd or "comfyui" in cmd or "comfy" in cmd:
        if any(
            x in cmd
            for x in (
                "curl",
                "wget",
                "invoke-webrequest",
                "system_stats",
                "http://",
                "https://",
            )
        ):
            return {"action": "status"}
        if re.search(r"\b(where|dir|ls|find)\b", cmd):
            return {"action": "locate"}
    return None


def _is_comfy_hunt_text(text: str) -> bool:
    """True when the model is thrashing the FS looking for ComfyUI."""
    if not text or not _COMFY_HUNT_RE.search(text):
        return False
    # Tool spam listing C:\…\ComfyUI or where/dir searches
    if re.search(r"\b(list_dir|bash_exec|where|dir\s+/s)\b", text, re.I):
        return True
    if re.search(r"[A-Za-z]:\\[^\n]*ComfyUI", text, re.I):
        return True
    return False


def _parse_dsml_tool_calls(text: str) -> list[dict[str, Any]]:
    """Recover OpenAI tool_calls from leaked DSML/XML-style tool markup.

    Example failure modes (shown as chat text):
      tool_calls invoke name="bash_exec" ... curl ...8188
      tool_calls invoke name="list_dir" ... relative_path ... C:\\...\\ComfyUI
      ｜DSML｜tool_calls invoke name="comfyui" parameter name="action">status
    """
    if not text:
        return []
    # Normalize fullwidth junk so matching is reliable
    norm = (
        text.replace("｜", "|")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )
    if not (
        _DSML_TOOL_RE.search(norm)
        or "tool_calls" in norm.lower()
        or "dsml" in norm.lower()
        or _DSML_INVOKE_RE.search(norm)
    ):
        return []
    text = norm

    # Collapse entire "find ComfyUI on disk" spam → one comfyui call.
    if _is_comfy_hunt_text(text):
        # Prefer status if it also looks like an HTTP check; else locate.
        action = "status" if re.search(r"curl|8188|system_stats|http", text, re.I) else "locate"
        # Pure path hunting without HTTP → locate (returns start hints)
        if re.search(r"\b(where|dir\s+/s|list_dir)\b", text, re.I) and not re.search(
            r"curl|system_stats", text, re.I
        ):
            action = "locate"
        # Explicit comfyui action=status in the dump wins
        if re.search(r"""name\s*=\s*["']action["'][^>]*>\s*status""", text, re.I):
            action = "status"
        if re.search(r"""name\s*=\s*["']action["'][^>]*>\s*generate""", text, re.I):
            action = "generate"
        return [
            {
                "id": f"dsml_comfy_{uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": "comfyui",
                    "arguments": json.dumps({"action": action}),
                },
            }
        ]

    out: list[dict[str, Any]] = []
    # Split on invoke boundaries when possible
    chunks = re.split(r"(?i)(?:invoke\b|tool_call\b|tool_calls\b)", text)
    for i, chunk in enumerate(chunks):
        m = _DSML_INVOKE_RE.search(chunk)
        if not m:
            m = re.search(
                r"""["'](file_read|file_write|list_dir|bash_exec|comfyui)["']""",
                chunk,
                re.IGNORECASE,
            )
            if not m:
                if "bash_exec" in chunk.lower() and (
                    "curl" in chunk.lower() or "8188" in chunk or "comfy" in chunk.lower()
                ):
                    name = "bash_exec"
                elif "list_dir" in chunk.lower() and _COMFY_HUNT_RE.search(chunk):
                    name = "list_dir"
                else:
                    continue
            else:
                name = m.group(1).lower()
        else:
            name = m.group(1).lower()

        params: dict[str, str] = {}
        for pm in _DSML_PARAM_RE.finditer(chunk):
            params[pm.group(1).lower()] = pm.group(2).strip()
        if not params:
            code_m = re.search(
                r"""(?:code|command|path|relative_path|prompt|action)\s*[:=]\s*["']?([^"'<\n]+)""",
                chunk,
                re.IGNORECASE,
            )
            if code_m:
                key = "command" if name == "bash_exec" else "path"
                if "prompt" in chunk.lower():
                    key = "prompt"
                if "action" in chunk.lower():
                    key = "action"
                params[key] = code_m.group(1).strip()

        # Normalize aliases models invent
        if "relative_path" in params and "path" not in params:
            params["path"] = params["relative_path"]
        if "directory" in params and "path" not in params:
            params["path"] = params["directory"]

        args: dict[str, Any]
        if name == "bash_exec":
            command = (
                params.get("command")
                or params.get("code")
                or params.get("cmd")
                or ""
            )
            rewritten = _rewrite_bash_to_comfyui(command)
            if rewritten:
                name = "comfyui"
                args = rewritten
            else:
                args = {"command": command}
        elif name == "comfyui":
            args = {"action": params.get("action") or "status"}
            if params.get("prompt"):
                args["prompt"] = params["prompt"]
        elif name == "file_read":
            args = {"path": params.get("path") or params.get("file") or ""}
        elif name == "list_dir":
            path = params.get("path") or params.get("directory") or "."
            # list_dir of ComfyUI install paths is never useful — use comfyui
            if _COMFY_HUNT_RE.search(path) or _COMFY_HUNT_RE.search(chunk):
                name = "comfyui"
                args = {"action": "locate"}
            else:
                args = {"path": path}
        elif name == "file_write":
            args = {
                "path": params.get("path") or "",
                "content": params.get("content") or params.get("code") or "",
            }
        else:
            continue

        if name == "bash_exec" and not args.get("command"):
            continue
        if name in ("file_read", "file_write") and not args.get("path"):
            continue

        out.append(
            {
                "id": f"dsml_{i}_{uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args),
                },
            }
        )
        if len(out) >= MAX_PARALLEL_TOOLS:
            break
    return out


def parse_pseudo_tool_calls(text: str) -> list[dict[str, Any]]:
    """Best-effort parse of text-faked tools into OpenAI-style tool_call dicts.

    Models sometimes emit file_read("x") as plain text instead of native tool
    calls, or dump DSML/XML tool markup into the chat bubble. We recover them
    here so the ReAct loop can still execute.
    """
    if not text:
        return []
    out: list[dict[str, Any]] = []

    # 1) DSML / XML-ish dumps first (the ComfyUI failure mode)
    out.extend(_parse_dsml_tool_calls(text))

    # 2) Classic function-call-as-text
    pat = re.compile(
        r"\b(file_read|file_write|list_dir|bash_exec|comfyui)\s*\(\s*"
        r"(?:[\"']([^\"']*)[\"']|(action|prompt|path|command)\s*=\s*[\"']([^\"']*)[\"'])"
        r"(?:\s*,\s*(?:[\"']([^\"']*)[\"']|(\w+)\s*=\s*[\"']([^\"']*)[\"']))?\s*\)",
        re.IGNORECASE,
    )
    # Simpler reliable pattern for positional forms
    pat_simple = re.compile(
        r"\b(file_read|file_write|list_dir|bash_exec)\s*\(\s*[\"']([^\"']+)[\"']"
        r"(?:\s*,\s*[\"']([\s\S]*?)[\"'])?\s*\)",
        re.IGNORECASE,
    )
    for i, m in enumerate(pat_simple.finditer(text)):
        name = m.group(1).lower()
        arg0 = m.group(2)
        arg1 = m.group(3)
        if name == "file_read":
            args: dict[str, Any] = {"path": arg0}
        elif name == "list_dir":
            args = {"path": arg0}
        elif name == "bash_exec":
            rewritten = _rewrite_bash_to_comfyui(arg0)
            if rewritten:
                name = "comfyui"
                args = rewritten
            else:
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

    # comfyui(action="status") / comfyui(action="generate", prompt="...")
    for i, m in enumerate(
        re.finditer(
            r"""\bcomfyui\s*\(\s*action\s*=\s*["'](\w+)["']"""
            r"""(?:\s*,\s*prompt\s*=\s*["']([^"']*)["'])?\s*\)""",
            text,
            re.IGNORECASE,
        )
    ):
        args = {"action": m.group(1).lower()}
        if m.group(2):
            args["prompt"] = m.group(2)
        out.append(
            {
                "id": f"pseudo_comfy_{i}_{uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": "comfyui",
                    "arguments": json.dumps(args),
                },
            }
        )

    # De-dupe by name+arguments
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for tc in out:
        fn = tc.get("function") or {}
        key = f"{fn.get('name')}::{fn.get('arguments')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(tc)
        if len(unique) >= MAX_PARALLEL_TOOLS:
            break

    if unique:
        logger.warning(
            "pseudo_tool_recovery count=%s names=%s preview=%r",
            len(unique),
            [c["function"]["name"] for c in unique],
            (text or "")[:200],
        )
    return unique


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
