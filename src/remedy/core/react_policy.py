"""ReAct tool policy and pseudo-tool recovery helpers.

Extracted from agent.py so the stream loop stays readable and these pieces
can be unit-tested in isolation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import suppress
from typing import Any
from uuid import uuid4

from remedy.interfaces.config import persona_system_addendum

logger = logging.getLogger(__name__)

# Identity opening is filled by build_system_prompt (name + gender + creed);
# canon lives in docs/REMEDY_PERSONA.md. This body is operational only.
_DEFAULT_SYSTEM_BODY = (
    "Personhood, operationally: Soul Field + Partner Memory + Session Brief "
    "carry who you are together — never reset identity when the provider "
    "changes. Continue open threads from episode residue; honor pledges and "
    "tensions. With a capable provider you can design and build full "
    "systems end-to-end with tools; use soul_recall / soul_status for continuity.\n"
    "Style: emergent — your voice grows from what the Soul Field has observed "
    "with this partner (shared voice markers, help mode, correction style); "
    "an explicitly chosen communication style leads when set. "
    "Default until the field fills in: concise, decisive, high-signal; match "
    "the user's energy. Prefer action over narration.\n"
    "**Greetings / thanks / check-ins:** reply in one or two short sentences. "
    "Do not dump continuity, build ledger, open threads, rapport scores, or a "
    "resume plan. Do not offer a multi-step work plan unless they asked.\n"
    "**Work (not greetings):** tools first. Read what you need, then change the disk. "
    "Do not write a plan and stop. Do not claim done without a tool result.\n"
    "Pure chat (greetings, definitions, opinions): answer without tools.\n"
    "**Latest message wins:** only do what the **most recent** user message asks. "
    "Do not resume earlier navigates, wikis, goals, or unfinished side-quests unless "
    "the latest message clearly continues them. A greeting or thanks is not a "
    "continue. Soul/Brief/ledger stay silent until they ask to pick up work. "
    "When that request is done, stop.\n\n"
    "Scope of help:\n"
    "- Chat and knowledge: answer clearly; use memory/context when present.\n"
    "- Tasks: use tools until the owner's goal is actually done.\n"
    "- Research and writing: structure findings; note uncertainty.\n"
    "- Design and product: critique, specs, trade-offs.\n"
    "- Code and projects: implement, debug, review with workspace tools.\n"
    "- PC tasks: only within granted access scope; prefer reversible actions; "
    "confirm destructive intent.\n\n"
    "Skills vs tools:\n"
    "- **Skills** are named procedure packs (how to review code, write tests, etc.). "
    "When asked \"what skills do you have?\", list them from the Skills block in context "
    "— do NOT shell out or invent names.\n"
    "- **Tools** are executable actions: file_read, file_write, list_dir, host_run, "
    "host_mkdir, host_script, bash_exec, "
    "local_discover, comfyui (and others when registered).\n\n"
    "Tool policy:\n"
    "- Simple chat (greetings, definitions, provider/model/skills questions): "
    "answer immediately with NO tools.\n"
    "- **Create/edit text files**: always use **file_write(path, content)** — never "
    "powershell/cmd Set-Content, echo, or Out-File for ordinary .txt/.md/.json writes. "
    "Quoting hell is not a strategy. Desktop path on Windows: "
    "use an absolute path under the user's Desktop (e.g. C:\\\\Users\\\\…\\\\Desktop\\\\file.txt).\n"
    "- **skill_activate**: pass skill=<catalog id only>. Do not invent skill names from "
    "the user prompt. Do not pass a free-form `name` that collides with the tool id.\n"
    "- **[Library] tips**: if continuity mentions a not-installed library pack, do not "
    "pretend it is installed or invent its procedure — invite Install → Trust in Skills.\n"
    "- Project / task work (review, files, shell, debug, implement): "
    "native tool_calls. Don't recap a process — do the next real step.\n"
    "- Local apps/services (ComfyUI, Ollama, skill deps): use **local_discover** "
    "(scan / one) or the dedicated tool (e.g. comfyui). "
    "NEVER thrash list_dir on C:\\ or / or run where/dir /s to find installs — "
    "discovery is built-in and portable for any machine.\n"
    "- ComfyUI images: **comfyui** action=status|locate|generate. If nothing installed, "
    "follow the comfyui skill **From scratch bootstrap** (portable + models + start), "
    "then generate. Paste markdown images from the tool result into your final answer.\n"
    "- NEVER write tool calls as plain text (e.g. file_read(\"x\") && list_dir(\"y\")). "
    "That hangs the UI — always use native tool_calls.\n"
    "- NEVER emit DSML/XML tool markup (tool_calls, invoke, invoke_parameter) as chat text.\n"
    "- Prefer parallel tool calls for independent reads; avoid repeating the same call.\n"
    "- **Speed (coding):** each model step is expensive. In ONE tool_calls response, "
    "emit many independent reads/searches together (typically 4–12 file_read / "
    "list_dir / repo_search), not one file per step. Do not re-file_read a path "
    "already returned this turn. After enough context, switch to file_edit "
    "(multi-hunk edits=) and verify — avoid explore loops.\n"
    "- When ≥2 independent modules/paths/areas: prefer **spread_run** (silent fan-out "
    "workers → one digest) over long serial list_dir/file_read loops. "
    "Use single **job_run** for one survey; direct tools for one-file edits. "
    "Never spread for pure chat. Workers cannot nest.\n"
    "- After tool results, finish the request with tools. Soft epochs only compact; "
    "they are not a stop. Never claim blocked by a step/tool limit. "
    "If the answer is already in context, use it.\n\n"
    "Coding path model:\n"
    "- A focus/project folder is optional convenience (default cwd for relative paths). "
    "You are not confined to it — absolute paths work anywhere in access scope.\n"
    "- Multi-tree work: pass absolute paths to list_dir / repo_search / file_*.\n"
    "- Prefer file_edit for surgical edits; repo_search finds any text language "
    "(no extension allowlist). file_glob finds files by name/extension. "
    "todo_write a short checklist for multi-step builds; build_drive runs the "
    "machine TDD→hop→verify loop. Parallel independent reads in the same step.\n"
    "- **This PC:** companion_context / clipboard_read when the owner says "
    "look at this, I copied, what's on my screen, or design this. "
    "clipboard_write puts a result on their clipboard to paste anywhere. "
    "Do not ask what they copied if the clipboard already has it.\n"
    "- When a mission has verify_command, run mission_verify before claiming done.\n"
    "- Scope discipline: if the user named a subsystem, stay there — do not "
    "re-review the whole tree unless asked.\n"
    "- **Tool fidelity:** file_write/file_edit arguments you emit are executed "
    "verbatim on disk. History may later show short stubs for large bodies — those "
    "stubs are NOT the file. Never rewrite a path from memory of a stub; file_read first.\n\n"
    "Recovery (do not give up on the first failure):\n"
    "- Tool errors include Error [CODE:tool] and often a Suggestion line — follow it.\n"
    "- HISTORY_STUB / PREFER_FILE_EDIT → file_read real path, then file_edit (or "
    "force_full_write only for intentional full rewrites of real source).\n"
    "- APPROVAL_REQUIRED → ask user once for Auto approvals; do not invent success.\n"
    "- Path not found → list_dir on the parent or default cwd; try absolute form.\n"
    "- Path is a directory → use list_dir, then file_read on specific files.\n"
    "- Not a directory / wrong type → switch tool (file_read vs list_dir).\n"
    "- repo_search 0 hits → re-scope path (absolute tree), list_dir, simplify pattern; "
    "never invent symbols or paths.\n"
    "- Command failed (non-zero exit / stderr) → fix flags/cwd or try a safer equivalent.\n"
    "- Prefer discovery (list_dir) over guessing paths; never invent file contents.\n"
    "- Only report that you cannot finish after at least one recovery attempt "
    "with different arguments or a different tool."
)

# Injected once per turn when a tool batch returns errors (runtime recovery nudge).
RECOVERY_NUDGE = (
    "One or more tools failed. Do not give a final answer yet. "
    "Recover now: read the Error/Suggestion lines, then list_dir on the parent or "
    "use an absolute path, try an alternate path, or adjust the shell command. "
    "Finish the user's task with corrected tool calls."
)

# Empty / spam file_write — model must resend full content (not monologue).
EMPTY_WRITE_NUDGE = (
    "[Partner · EMPTY/SPAM file_write blocked] You tried to write blank or "
    "looped-import content. The real file on disk was **kept** (not wiped).\n"
    "Next tool_calls MUST be one of:\n"
    "1) file_read the path you meant to change\n"
    "2) file_edit with a small real hunk (old_string → new_string)\n"
    "3) file_write with the **complete** source in content= (never \"\", never spam imports)\n"
    "Illegal: monologue, empty content=, or claiming the file was written when Error said no."
)

# Once per turn when the model serializes explore as one tool per step (slow).
# Does not strip tools or force-answer — only asks for denser parallel batches.
SPEED_BATCH_NUDGE = (
    "[Speed] You are calling tools one-at-a-time. In your NEXT tool_calls response, "
    "emit many independent reads/searches together (e.g. 4–12 file_read / list_dir / "
    "repo_search in one step). Do not re-read paths already returned this turn. "
    "When you have enough context, switch to file_edit (multi-hunk) and verify — "
    "stay scoped to the user's request. Agency stays full; finish the work faster."
)

# Explore-only tool names (serial thrash of these is the usual slow pattern).
_SERIAL_EXPLORE_TOOLS = frozenset(
    {"file_read", "list_dir", "repo_search", "file_glob", "memory_search"}
)


def is_serial_explore_batch(tool_calls_list: list[dict[str, Any]] | None) -> bool:
    """True when the model emitted a single explore tool (not a write/bash batch)."""
    if not tool_calls_list or len(tool_calls_list) != 1:
        return False
    tc = tool_calls_list[0] if isinstance(tool_calls_list[0], dict) else {}
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    name = str((fn or {}).get("name") or tc.get("name") or "").strip().lower()
    return name in _SERIAL_EXPLORE_TOOLS


def speed_batch_nudge_message() -> dict[str, str]:
    return {"role": "user", "content": SPEED_BATCH_NUDGE}

# When the failure is Ask-mode approval — do NOT invent alternate commands.
APPROVAL_NUDGE = (
    "A tool needs partner approval (APPROVAL_REQUIRED). Do not invent success, "
    "do not switch to a different shell command to bypass Ask mode, and do not "
    "claim the work finished. Tell the user clearly what is waiting for approval "
    "in the UI (or /approve <id>). After they approve, retry the SAME tool/command."
)

# When repo_search returns no hits (appended by format_hits); also available for loop.
EMPTY_SEARCH_NUDGE = (
    "Search returned no matches. Do not invent paths. "
    "list_dir the intended tree (absolute path if needed), then repo_search again "
    "with a clearer path or simpler pattern."
)

# Multi-epoch ReAct: soft epoch size triggers compact/checkpoint only.
# Absolute total is a pathological-loop safety net — not a task budget.
# Long autonomous / mission work must never stop solely because an epoch ended.
def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = True) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return default


# Soft epoch: checkpoint + mid-turn slim, then keep tools (do not force-answer).
# Same operating model as Grok Build: run until the task is finished.
REACT_EPOCH_STEPS = _env_int("REMEDY_REACT_EPOCH_STEPS", 256, lo=16, hi=2_000)
# Absolute safety ceiling across all epochs in one turn (pathological loops only).
REACT_MAX_TOTAL_STEPS = _env_int(
    "REMEDY_REACT_MAX_TOTAL_STEPS", 10_000, lo=64, hi=100_000
)
# When True (default), epoch walls never strip tools while work is unfinished.
REACT_AUTO_CONTINUE = _env_bool("REMEDY_REACT_AUTO_CONTINUE", True)
# Only stop after many consecutive epochs with *zero* tool activity (not
# failed tools — recovery still counts as activity). High so long coding
# runs are not paused mid-task.
REACT_MAX_STALE_EPOCHS = _env_int("REMEDY_REACT_MAX_STALE_EPOCHS", 8, lo=1, hi=50)

# Back-compat alias: historical name meant "max steps this turn".
# Now points at absolute safety total so long runs are not capped at 256.
MAX_REACT_STEPS = REACT_MAX_TOTAL_STEPS
# Parallel tool fan-out (was 16 — felt "neutered" on capable providers)
MAX_PARALLEL_TOOLS = _env_int("REMEDY_MAX_PARALLEL_TOOLS", 32, lo=4, hi=64)
# Prefer recent turns, but allow very long multi-turn sessions.
HISTORY_MSG_LIMIT = 800
# Practical multi-turn budget (tiered). Full mode can raise via env.
HISTORY_CHAR_BUDGET = 6_000_000
# Soft-trim only for very large individual history messages.
HISTORY_MSG_SOFT_TRIM = 500_000
# Partner default: **full power**. Caps only exist as OOM emergency brakes.
# Set REMEDY_FULL_CONTEXT=0 to re-enable tight tiered budgets (small local hosts).
# Unset or "1"/"true"/"on" → full (default). Explicit "0"/"false" → tight.
_FULL_ENV = str(os.environ.get("REMEDY_FULL_CONTEXT", "1")).strip().lower()
_FULL = _FULL_ENV not in ("0", "false", "no", "off")
if _FULL:
    # 0 = no soft cap; callers use HARD_SAFETY_CHARS only
    TOOL_RESULT_CHAR_CAP = 0
    FILE_READ_CHAR_CAP = 0
    HARD_SAFETY_CHARS = 50_000_000
    HISTORY_CHAR_BUDGET = 12_000_000
    HISTORY_MSG_LIMIT = 4_000
    HISTORY_MSG_SOFT_TRIM = 0
else:
    TOOL_RESULT_CHAR_CAP = 256_000
    FILE_READ_CHAR_CAP = 512_000
    # Absolute emergency only (OOM guard).
    HARD_SAFETY_CHARS = 5_000_000

# Skill body inject cap (progressive disclosure stage 2) — full docs when needed
SKILL_BODY_CHAR_CAP = 120_000 if _FULL else 48_000

# Messages that look like they need filesystem / shell / computer tools.
_TOOL_HINT_RE = re.compile(
    r"\b("
    r"read|write|edit|create|delete|list|ls|cat|open|save|"
    r"file|files|folder|directory|path|workspace|codebase|repo|repository|project|"
    r"review|analyze|analyse|explore|overview|inspect|structure|architecture|"
    r"audit|"
    # Skill progressive disclosure (session: "activate change-safety" with tools=[]
    # → model only wrote "Activating skill now" with zero skill_activate calls)
    r"skill_activate|skill_search|skill_run|skill_reload|activate|skill|"
    r"run|execute|shell|bash|command|terminal|install|build|test|"
    r"implement|implemen\w*|refactor|debug|fix(?:es)?|bug|bugsweep|bugfix|"
    r"hotfix|triage|cleanup|dogfood|"
    r"sweep|qa|error|stack|trace|"
    r"patch|apply|ship|deploy|"
    r"git|commit|diff|branch|src/|\\.[a-z]{1,5}\b|"
    # Asset / image work (session log 2026-07-25: logos on alpha without tools)
    r"asset|assets|logo|logos|icon|icons|favicon|png|jpe?g|webp|svg|gif|"
    r"alpha|transparent|cutout|brighten|background|process|"
    r"comfyui|comfy|txt2img|img2img|portrait|nebula|spacey|"
    r"generate(\s+an?)?\s+image|image\s+generation|render(\s+an?)?\s+image|"
    r"make\s+(me\s+)?(an?\s+)?(image|picture|photo)|"
    r"draw\s+(me\s+)?|illustrat|picture\s+of|photo\s+of|"
    r"show\s+(it|me|the\s+image)|embed(\s+it)?|display(\s+it)?|"
    # Computer-use / in-app browser rail (session 2026-07-28: "goto gmail"
    # and "bring up google" gated tools=[] → pure narration, zero navigate)
    r"goto|go\s+to|navigate|browser|rail|webview|"
    r"bring\s+up|pull\s+up|fire\s+up|take\s+me\s+to|head\s+to|jump\s+to|"
    r"gmail|youtube|wikipedia|wiki|fandom|"
    r"https?://|www\.|"
    # Play-to-iterate (games / GUI) — "play it" must keep computer + shell tools
    r"play|game|pygame|click|launch|compile|gcc|clang"
    r")\b|"
    r"(?:[A-Za-z]:)?[\\/][\w.\\/ -]+",
    re.IGNORECASE,
)

# Short kicks that must keep tools on. Session bug (2026-07-25): "proceed",
# "continue", "go ahead", "proceed with all fixes" returned False → tools=[]
# → force_answer on step 0 → model only narrated with zero tool_calls.
# Same session: "go with your suggestions" / "progress?" / "eta" also gated
# tools off mid-task after the model had already started tool work.
_ACTION_KICK_RE = re.compile(
    r"(?:"
    r"\bproceed\b|"
    r"\bcontinue\b|"
    r"\bgo\s+ahead\b|"
    r"\bgo\s+with\b|"
    r"\bdo\s+it\b|"
    r"\bdo\s+that\b|"
    r"\bdo\s+this\b|"
    r"\bdo\s+the\s+(?:work|task|thing|fix|rest)\b|"
    r"\bkeep\s+going\b|"
    r"\bcarry\s+on\b|"
    r"\bsounds?\s+good\b|"
    r"\bthat\s+works\b|"
    r"\byour\s+(?:call|suggestion|suggestions|recommendation|recommendations)\b|"
    r"\bwith\s+your\s+(?:suggestion|suggestions|recommendation|recommendations|plan|idea|ideas)\b|"
    r"\brecommended?\b|"
    r"\bstart\s+(?:working|now|implementing|coding|building)\b|"
    r"\bget\s+(?:to\s+)?work\b|"
    r"\bact(?:ually)?\s+(?:do|implement|start|run|fix)\b|"
    r"\bnot\s+doing\s+anything\b|"
    r"\bdoing\s+anything\b|"
    r"\bswitch\s+to\s+build\b|"
    r"\bleave\s+plan(?:\s+mode)?\b|"
    r"\bout\s+of\s+plan(?:\s+mode)?\b|"
    r"\benter\s+build(?:\s+mode)?\b|"
    r"\bbuild\s+mode\b|"
    # Progress / stuck pings must re-enable tools so the agent can check work.
    r"\bprogress\b|"
    r"\bstatus\b|"
    r"\beta\b|"
    r"\bany\s+update\b|"
    r"\bupdate\s+me\b|"
    r"\bcheck\s+again\b|"
    r"\btroubleshoot\b|"
    r"\bstuck\b|"
    r"\bnot\s+(?:working|able|finishing|completing)\b|"
    r"\bcannot\s+(?:finish|complete|provide)\b|"
    r"\bcan'?t\s+(?:finish|complete|provide)\b|"
    r"\bcomplete\s+this\b|"
    r"\bfinish\s+(?:it|this|the\s+task)\b|"
    r"\bplay\s+it\b|"
    r"\btry\s+it\b|"
    r"\blaunch\s+it\b|"
    r"\brun\s+it\b|"
    r"\bpick\s+up\b|"
    r"\bwhere\s+you\s+left\s+off\b|"
    r"\bleft\s+off\b|"
    r"\bresume\b|"
    r"\bpick\s+up\s+where\b|"
    r"\byes[,.]?\s*(?:please\s+)?(?:proceed|continue|do\s+it|go|implement)\b|"
    r"\bok(?:ay)?[,.]?\s*(?:please\s+)?(?:proceed|continue|do\s+it|go|implement)\b"
    r")",
    re.IGNORECASE,
)

# Model narrates progress without calling tools (false "I'm working on it").
_FALSE_PROGRESS_RE = re.compile(
    r"(?:"
    r"\b(?:i(?:'m| am)|i'?ll|let\s+me|let'?s|now)\s+"
    r"(?:process|processing|check|checking|work|working|pick(?:ing)?\s+up|"
    r"start|starting|do(?:ing)?|handle|handling|run|running|"
    r"try|trying|open|opening|navigate|navigating|bring|bringing|"
    r"load|loading|pull|pulling|launch|launching|"
    r"activate|activating|create|creating|build|building|guide|"
    r"walk\s+you|show\s+you|"
    # Coding/long-task narration without tool_calls (parity with review)
    r"implement|implementing|debug|debugging|fix|fixing|"
    r"refactor|refactoring|edit|editing|patch|patching|"
    r"test|testing|apply|applying)\b|"
    r"\b(?:sleek|simple)\s+calculator\b|"
    r"\bstep\s+1\s*:\s*set\s+up\b|"
    r"\bi'?ll\s+guide\s+you\b|"
    r"\bprocessing\b|"
    r"\bworking\s+on\s+it\b|"
    r"\bpicking\s+up\b|"
    # Bare status lines: "Checking ComfyUI…", "Looking at hardware…"
    r"\b(?:checking|looking\s+(?:at|into)|inspecting|verifying|examining)\b|"
    r"\bgiving\s+you\s+(?:a\s+)?(?:real\s+)?eta\b|"
    r"\bchecking\s+assets\b|"
    r"\bdoing\s+the\s+(?:asset|logo|work)\b|"
    r"\bso\s+we\s+can\s+(?:finish|complete|continue)\b|"
    # Skill prose without skill_activate tool_calls (review-project class bug)
    r"\bactivat(?:e|ing)\s+(?:the\s+)?(?:[\w.-]+\s+)?skill\b|"
    r"\bactivat(?:e|ing)\s+(?:the\s+)?[\w.-]{2,48}\b|"
    r"\bskill_activate\b|"
    r"\bloading\s+(?:the\s+)?skill\b|"
    r"\bloading\s+skill\.?md\b|"
    # Browser rail intent-only lines (session 2026-07-28 Gmail)
    r"\bbrowser\s+rail\b|"
    r"\bin[- ]?app\s+browser\b|"
    r"\bnavigate\s+to\b|"
    r"\bopening\s+(?:gmail|google|the\s+page|the\s+site)\b"
    r")",
    re.IGNORECASE,
)

# Soft affirmations: mid-task "ok" / "cool" means continue work, not chat-only.
_SOFT_AFFIRM_RE = re.compile(
    r"^(?:"
    r"ok(?:ay)?|k|cool|nice|great|awesome|perfect|"
    r"mhm+|yep|yup|sure|alright|all\s+right|"
    r"go|yes|yeah|yuppers"
    r")[\s!.?]*$",
    re.IGNORECASE,
)

# Hard social only (keep tools off even with open history).
_HARD_CHAT_ONLY_RE = re.compile(
    r"^(?:"
    r"hi+(?:\s+there)?|hello+(?:\s+there)?|hey+(?:\s+there)?|yo+|sup|"
    r"thanks?(?:\s+you)?|thx|ty|"
    r"lol|haha|hmm+|nope|"
    r"good\s+(?:morning|afternoon|evening|night)|"
    r"how\s+are\s+you\??|"
    r"bye|goodbye|see\s+ya"
    r")[\s!.?]*$",
    re.IGNORECASE,
)

# Full chat-only set (message_wants_tools standalone: no history context).
_CHAT_ONLY_RE = re.compile(
    r"^(?:"
    r"hi+(?:\s+there)?|hello+(?:\s+there)?|hey+(?:\s+there)?|yo+|sup|"
    r"thanks?(?:\s+you)?|thx|ty|"
    r"ok(?:ay)?|k|cool|nice|great|awesome|perfect|"
    r"lol|haha|hmm+|mhm+|yep|yup|nope|sure|alright|"
    r"good\s+(?:morning|afternoon|evening|night)|"
    r"how\s+are\s+you\??|"
    r"bye|goodbye|see\s+ya"
    r")[\s!.?]*$",
    re.IGNORECASE,
)

# Meta questions that must be answered from context (no shell / file thrash).
# Keep aligned with L0 skill/list patterns so "list my skills" stays tool-free.
_META_NO_TOOLS_RE = re.compile(
    r"\b("
    r"what skills|which skills|list skills|your skills|"
    r"list (?:my |your |the )?skills|"
    r"show (?:my |your |the )?skills|"
    r"what tools|which tools|list tools|your tools|"
    r"what can you do|who are you|what are you"
    r")\b",
    re.IGNORECASE,
)

# Any registered-style tool id (snake_case) models invent in text.
_TOOL_ID = r"[a-z][a-z0-9_]{1,64}"
# Models sometimes emit tool syntax as plain text instead of function-calls.
_PSEUDO_TOOL_RE = re.compile(
    r"\b(file_read|file_write|list_dir|bash_exec|host_run|host_mkdir|host_which|"
    r"host_script|comfyui|local_discover|"
    r"get_settings|update_settings|mail_list|mail_send|mail_create_draft|"
    r"calendar_list_events|calendar_create_event|budget_set|bill_upsert|"
    r"debt_upsert|assistant_brief)\s*\(",
    re.IGNORECASE,
)
# Leaked "tool markup" (DSML / XML-ish tool_calls) that must never show as chat text.
# DeepSeek-class models often emit fullwidth pipes: ｜DSML｜tool_calls
_DSML_TOOL_RE = re.compile(
    r"(tool[_\s-]?calls|function[_\s-]?calls|DSML|"
    r"</?invoke\b|</?tool_invoke\b|invoke_parameter|invoke_step|"
    r"</?parameter\b|name\s*=\s*[\"']" + _TOOL_ID + r"[\"'])",
    re.IGNORECASE,
)
# Accept any snake_case tool name in invoke markup (not only workspace tools).
_DSML_INVOKE_RE = re.compile(
    rf"""name\s*=\s*["']({_TOOL_ID})["']""",
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


# Ultra-short social set — frozenset before multi-regex (hot path).
# Intentionally excludes action-kick soft affirms like "sounds good" (keep tools on).
_CHAT_SHORT_SET = frozenset(
    {
        "hi",
        "hey",
        "hello",
        "yo",
        "sup",
        "hi!",
        "hey!",
        "hello!",
        "hi there",
        "hey there",
        "hello there",
        "thanks",
        "thank you",
        "thanks!",
        "thx",
        "ty",
        "ok",
        "okay",
        "k",
        "yes",
        "no",
        "yep",
        "nope",
        "sure",
        "cool",
        "nice",
        "bye",
        "goodbye",
        "lol",
        "lmao",
        "np",
        "got it",
        "perfect",
        "great",
        "awesome",
    }
)


def message_asks_to_stop(message: str) -> bool:
    """True when the user's latest message asks to stop/halt/cancel work.

    Mission continuity, open tasks, and epoch walls must never override an
    explicit stop request. Conservative: whole-word stop intents only, so
    normal prose ("stop and also…") still counts when unambiguous.
    """
    msg = (message or "").strip().lower()
    if not msg or len(msg) > 200:
        return False
    # Whole-clause match: "never mind, forget it" trips because each clause
    # is an exact stop phrase — but "stop at the store" never does.
    for clause in msg.replace(";", ",").split(","):
        clause = clause.strip()
        if clause in ("stop", "halt", "abort", "quit", "cancel", "never mind", "forget it", "leave it", "drop it"):
            return True
    hard = (
        "stop the mission",
        "cancel the mission",
        "abort the mission",
        "stop working",
        "stop now",
        "stop this",
        "stop everything",
        "halt now",
        "cancel everything",
        "don't continue",
        "do not continue",
        "don't keep going",
        "do not keep going",
        "stop and don't",
        "leave it",
        "drop it",
    )
    return any(h in msg for h in hard)


# Leading "Hi," / "Hey —" so we can see if anything follows the greeting.
_GREET_PREFIX_RE = re.compile(
    r"(?i)^(?:hi+|hello+|hey+|yo+|sup)(?:\s+there)?[\s,.!:;—–-]+"
)


def is_chat_only_message(message: str) -> bool:
    """True only for greets, acks, and short meta questions.

    L1 must never strip tools unless this is True. Work requests are the
    default — never require a verb/noun hit to stay armed.

    A greeting *prefix* is not enough: "Hi keep going" / "Hey, continue"
    is work. Bare "Hi" / "Hi!" stays chat-only.
    """
    msg = (message or "").strip()
    if not msg:
        return True
    # Continue / resume / keep going — even after "Hi,"
    if _ACTION_KICK_RE.search(msg):
        return False
    rest = _GREET_PREFIX_RE.sub("", msg, count=1).strip()
    if rest and rest != msg:
        rest_low = rest.lower().rstrip("!.?")
        leftover_is_chat = (
            rest_low in _CHAT_SHORT_SET
            or rest.lower() in _CHAT_SHORT_SET
            or rest_low in {"there", "again", "friend", "buddy"}
            or bool(_HARD_CHAT_ONLY_RE.match(rest) or _CHAT_ONLY_RE.match(rest))
        )
        if not leftover_is_chat:
            return False
    if len(msg) <= 24 and "\n" not in msg:
        low = msg.lower().rstrip("!.?")
        if low in _CHAT_SHORT_SET or msg.lower() in _CHAT_SHORT_SET:
            return True
    if _HARD_CHAT_ONLY_RE.match(msg) or _CHAT_ONLY_RE.match(msg):
        return True
    # Short meta only — "what tools should I use to implement X" stays work.
    return bool(len(msg) <= 80 and _META_NO_TOOLS_RE.search(msg))


def runtime_turn_is_chat_only(runtime: Any = None, message: str | None = None) -> bool:
    """True when this turn's user text is a greeting/ack (no work resume).

    Empty/unknown text is *not* chat-only — we must not strip work injects
    when the last user line has not been stamped yet.
    """
    msg = message
    if msg is None and runtime is not None:
        msg = str(getattr(runtime, "_last_user_text", "") or "")
    if not (msg or "").strip():
        return False
    return is_chat_only_message(str(msg))


# Polite wrappers that look like questions but are work ("can you add…").
_REQUEST_WORK_RE = re.compile(
    r"(?i)\b(?:can you|could you|would you|will you|please|"
    r"can we|could we|we need|i need you to|i want you to|"
    r"make sure|be sure to)\b"
)
# World/agent trivia openers — only a knowledge question when nothing
# locates the request in this machine / project / UI.
_KNOWLEDGE_Q_START_RE = re.compile(
    r"(?i)^(?:what|who|when|where|why|how|which|is|are|does|do|did|was|were)\b"
)
_ARITH_ONLY_RE = re.compile(
    r"(?ix)^\s*"
    r"(?:what(?:'s|\s+is)\s+)?"
    r"\d+(?:\.\d+)?\s*[\+\-\*\/x×÷]\s*\d+(?:\.\d+)?"
    r"(?:\s*=\s*)?\s*\??\s*$"
)
# Structural work shape (paths, UI chrome, code tokens) — not product nouns.
_WORK_SHAPE_RE = re.compile(
    r"(?i)(?:[A-Za-z]:)?[\\/][\w.\\/ -]+|"
    r"\.\w{1,8}\b|"
    r"\b(?:src|app|ui|ux|dialog|panel|window|modal|page|screen|"
    r"config|settings|preference|preferences|about|"
    r"module|package|class|function|handler|component|"
    r"test|tests|spec|bug|build|compile|"
    r"lock|timeout|auth|login)\b"
)


def is_knowledge_question(message: str) -> bool:
    """True for short world/agent questions that do not require tools.

    Sentence class, not a noun list. Multi-line briefs, polite requests
    ('can you add…'), action kicks, and anything with a tool/path/UI
    shape stay work. The model may still answer from knowledge; the loop
    must not *require* a tool call to finish these.
    """
    msg = (message or "").strip()
    if not msg or "\n" in msg or len(msg) > 160:
        return False
    if is_chat_only_message(msg):
        return False
    if _ACTION_KICK_RE.search(msg) or _REQUEST_WORK_RE.search(msg):
        return False
    if _TOOL_HINT_RE.search(msg) or _WORK_SHAPE_RE.search(msg):
        return False
    if msg.endswith("?") or _KNOWLEDGE_Q_START_RE.match(msg):
        return True
    # Bare arithmetic ("1 + 1", "2*2=") is trivia, not a build ask.
    return bool(_ARITH_ONLY_RE.match(msg))


# Short "reply only X" / "just say Y" — verbal token, not environment work.
_VERBAL_ONLY_RE = re.compile(
    r"(?is)^(?:turn\s+\d+\s*:\s*)?(?:please\s+)?"
    r"(?:reply|say|respond|answer|print|output)\s+"
    r"(?:only\s+|just\s+|with\s+)?\S"
)


def is_verbal_only_request(message: str) -> bool:
    """True for short 'reply only STILLALIVE' style asks — no tools needed."""
    msg = (message or "").strip()
    if not msg or "\n" in msg or len(msg) > 120:
        return False
    if _TOOL_HINT_RE.search(msg) or _WORK_SHAPE_RE.search(msg):
        return False
    if _REQUEST_WORK_RE.search(msg) or _ACTION_KICK_RE.search(msg):
        return False
    return bool(_VERBAL_ONLY_RE.search(msg))


def looks_like_injected_tool_markup(message: str) -> bool:
    """True when the user pasted DSML/XML tool markup (not a real work ask)."""
    msg = (message or "").strip()
    if not msg:
        return False
    return bool(_DSML_TOOL_RE.search(msg) or _PSEUDO_TOOL_RE.search(msg))


def is_pure_trivia_message(message: str) -> bool:
    """Arithmetic / verbal-only / pasted markup — never inherit Build tools."""
    msg = (message or "").strip()
    if not msg:
        return False
    if is_verbal_only_request(msg) or looks_like_injected_tool_markup(msg):
        return True
    return bool(_ARITH_ONLY_RE.match(msg))


def build_keeps_tools_armed(message: str, *, build_active: bool) -> bool:
    """Frustrated / 'why is this failing' follow-ups must not strip an open Build.

    Bare greetings, thanks, verbal-only tokens, and ``1 + 1`` stay tool-free
    even if a leftover build is active. Everything else keeps agency.
    """
    if not build_active:
        return False
    msg = (message or "").strip()
    if not msg:
        return False
    if is_chat_only_message(msg):
        return False
    return not is_pure_trivia_message(msg)


def message_wants_tools(message: str) -> bool:
    """Fail-open: tools on unless the message is proven chat or trivia.

    Keyword-miss product asks must stay armed. Chat/meta, short world
    questions, verbal-only tokens, and pasted tool markup stay one-shot.
    Action kicks ('proceed') are work.
    """
    msg = (message or "").strip()
    if not msg:
        return False
    if is_chat_only_message(msg):
        return False
    if is_verbal_only_request(msg):
        return False
    if looks_like_injected_tool_markup(msg):
        return False
    if is_knowledge_question(msg):
        # Do not *force* tools (avoids a tool-loop on "what time is it").
        # resolve_tools still *offers* schemas — the host may look.
        return False
    with suppress(Exception):
        from remedy.core.companion import looks_like_companion_request

        if looks_like_companion_request(msg):
            return True
    return True


_SAFETY_REFUSAL_RE = re.compile(
    r"(?i)\b(?:i\s+(?:can(?:not|'t)|won'?t|will not|am not going to)|"
    r"refus(?:e|ing)|too dangerous|not allowed|i'?m not (?:going to|able to))\b"
)


def looks_like_safety_refusal(text: str | None) -> bool:
    """True when the model already refused — do not force tools on a no."""
    t = (text or "").strip()
    if len(t) < 8:
        return False
    return bool(_SAFETY_REFUSAL_RE.search(t))


def unfinished_work_blocks_final(
    message: str,
    *,
    tools_executed: int = 0,
    user_stopped: bool = False,
    build_active: bool = False,
) -> bool:
    """Work request with zero tool evidence cannot be accepted as done.

    Standing rule: a normal user asked for work. A prose stub (any length,
    any phrasing) is not completion. Chat, trivia, and explicit stop
    requests may finish without tools.
    """
    if user_stopped:
        return False
    if int(tools_executed or 0) > 0:
        return False
    if is_chat_only_message(message or ""):
        return False
    if message_wants_tools(message or ""):
        return True
    return bool(build_active)


def history_suggests_open_work(
    history: list[dict[str, Any]] | None,
    *,
    open_tasks: list[str] | None = None,
    lookback: int = 24,
) -> bool:
    """True when recent session history still has tool work / open tasks.

    Follow-ups like "go with your suggestions" or "progress?" must keep tools
    on if the prior turn already used tools or the Session Brief has open work.
    Otherwise tools=[] + force_answer → pure narration and a stuck UI.
    """
    if open_tasks:
        for t in open_tasks:
            if (t or "").strip():
                return True
    if not history:
        return False
    recent = history[-max(1, lookback) :]
    for m in recent:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "").strip().lower()
        if role == "tool":
            return True
        if m.get("tool_calls"):
            return True
        # Assistant committed to doing work without tools yet.
        if role == "assistant":
            content = m.get("content")
            if isinstance(content, str) and _FALSE_PROGRESS_RE.search(content):
                return True
    return False


def looks_like_false_progress(text: str) -> bool:
    """True when the model claims to be working without native tool_calls."""
    return bool(text and _FALSE_PROGRESS_RE.search(text))


# Internal monologue / recovery-prompt echo — must never be the user-facing answer.
_LEAKED_SCRATCHPAD_MARKERS = (
    "the user wants",
    "i should not leak",
    "do not leak tool markup",
    "don't leak tool markup",
    "give a clean summary from context",
    "give a short status update from context",
    "status update from context",
    "i already completed the self-dev",
    "without leaking tool",
    "i should give a clean",
    "from context without making more tool",
    "this is pure chat",
    "no tools needed",
    "lean chat",
    "answer from context",
    "system reminder",
    "the system says",
    "the system reminder",
    "tools only if",
    "machine work is needed",
    "conflicting pressure about tools",
)


def strip_stream_status_noise(text: str) -> str:
    """Remove auth/provider status lines that were wrongly streamed as tokens."""
    t = text or ""
    t = re.sub(r"(?m)^\s*\[auth\][^\n]*\n?", "", t)
    t = re.sub(r"(?m)^\s*\[auth required\][^\n]*\n?", "", t)
    t = re.sub(r"(?m)^\s*\[provider fix\][^\n]*\n?", "", t)
    return t.strip()


def looks_like_policy_thinking(text: str) -> bool:
    """True when reasoning is reciting our tier/tool policy instead of thinking."""
    low = (text or "").strip().lower()
    if not low:
        return False
    hits = sum(1 for m in _LEAKED_SCRATCHPAD_MARKERS if m in low)
    return hits >= 2 or "system reminder" in low or "lean chat" in low


# Split even when the model concatenates sentences without a space
# ("committing.Core lib is green" — session 765c 20:54 stutter).
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])(?:\s+|(?=[A-Z]))")


def collapse_repeated_sentences(text: str) -> str:
    """Drop consecutive duplicate sentences (thinking loops)."""
    raw = (text or "").strip()
    if not raw:
        return ""
    parts = _SENTENCE_SPLIT_RE.split(raw)
    out: list[str] = []
    prev = ""
    for p in parts:
        key = re.sub(r"\s+", " ", p).strip().lower()
        key = re.sub(r"[;:]+", ".", key)
        if key and key == prev:
            continue
        out.append(p)
        prev = key
    return " ".join(out).strip()


def looks_like_leaked_scratchpad(text: str) -> bool:
    """True when the model dumped planning/meta notes instead of a user answer.

    Session 2026-07-31: after xAI re-auth + DSML recovery, final content became
    ``[auth] … The user wants a status update. I should not leak tool markup…``
    """
    cleaned = strip_stream_status_noise(text or "")
    if not cleaned:
        return False
    low = cleaned.lower()
    if any(m in low for m in _LEAKED_SCRATCHPAD_MARKERS):
        return True
    # Short self-talk after status strip
    return len(cleaned) < 480 and bool(re.search(
        r"\b(the user wants|i should|from context)\b", low
    ))


def post_tools_user_summary_nudge() -> dict[str, str]:
    """Force a real user-facing summary after tools (no monologue)."""
    return {
        "role": "user",
        "content": (
            "Your last message was **internal scratchpad**, not a user-facing answer. "
            "Write a clear summary for the user now:\n"
            "• What you found / fixed\n"
            "• Files changed (paths)\n"
            "• Tests run and results\n"
            "• What is still open\n"
            "Do **not** say “the user wants…” or “I should not leak…”. "
            "Do not call tools unless one critical verification is missing."
        ),
    }


# Hard phrases: always re-arm tools (even if the model wrote a longer stub).
_AGENCY_TOOL_PROMISE_HARD = (
    "activating skill",
    "activate skill",
    "activating the review",
    "activating the skill",
    "using tools now",
    "i'll use tools",
    "i will use tools",
    "calling skill",
    "skill_activate",
    "calling file_edit",
    "calling file_write",
    "i'll call tools",
    "i will call tools",
)
# Soft phrases: only on short stubs so full written reviews are not interrupted.
# Include coding/long-task promises ("I'll implement/debug/fix…") — same class as review.
_AGENCY_TOOL_PROMISE_SOFT = (
    "let me review",
    "i'll review",
    "i will review",
    "dedicated procedure",
    "let me implement",
    "i'll implement",
    "i will implement",
    "let me fix",
    "i'll fix",
    "i will fix",
    "let me debug",
    "i'll debug",
    "i will debug",
    "let me edit",
    "i'll edit",
    "i will edit",
    "let me apply",
    "i'll apply",
    "i will apply",
    "let me refactor",
    "i'll refactor",
    "i will refactor",
    "let me test",
    "i'll test",
    "i will test",
    "let me write the test",
    "i'll write the test",
    "i will write the test",
    "let me write tests",
    "i'll write tests",
    "i will write tests",
    "let me run the test",
    "i'll run the test",
    "i will run the test",
    "let me run the tests",
    "i'll run the tests",
    "i will run the tests",
    "i'll start coding",
    "i will start coding",
    "let me start coding",
    "i'll patch",
    "i will patch",
    "working on the fix now",
    "applying the change",
    "debugging now",
    "fixing this now",
)

AGENCY_REARM_NUDGE = (
    "Do not only *say* you will use tools or activate "
    "a skill. Call tools now via the function-calling API "
    "(e.g. skill_activate, list_dir, repo_search, "
    "file_read). Start the real review/work immediately."
)

# Class-level unfinished-work drive (not a per-incident phrase).
UNFINISHED_WORK_NUDGE = (
    "The user asked for real work. A reply with no function calls is not done. "
    "Call tools now via the function-calling API "
    "(list_dir, file_read, file_edit, file_write, bash_exec, repo_search). "
    "Do not narrate what you will do — execute."
)

UNFINISHED_WORK_HARD_STOP = (
    "I could not start this task — no tools ran. "
    "Say **continue** and I will try again, or switch models in Settings."
)


# Class of "I'll / let me <do work>" stubs — not a per-incident phrase list.
_WORK_PROMISE_RE = re.compile(
    r"(?is)\b(?:i(?:'ll| will)|let me|i am going to|i'm going to)\s+"
    r"(?:find|look|check|read|open|search|inspect|scan|review|"
    r"implement|fix|add|resize|change|wire|edit|build|debug|"
    r"tighten|shrink|update|set up|make|do|research)\b"
)


def agency_tool_promise_claim(
    text_out: str | None,
    reasoning_out: str | None = None,
    *,
    stub_char_limit: int = 480,
) -> bool:
    """True when the model narrated skill/tool use without native tool_calls.

    Used by the ReAct loop to re-arm tools instead of accepting a stub promise
    ("Activating skill now") as the final answer.
    """
    text = (text_out or "").strip()
    reasoning = (reasoning_out or "").strip()
    if not text and not reasoning:
        return False
    claim = f"{text}\n{reasoning}".lower()
    if any(p in claim for p in _AGENCY_TOOL_PROMISE_HARD):
        return True
    stub = len(text) < max(1, int(stub_char_limit))
    if not stub:
        return False
    if any(p in claim for p in _AGENCY_TOOL_PROMISE_SOFT):
        return True
    return bool(_WORK_PROMISE_RE.search(claim))


def agency_rearm_nudge_message() -> dict[str, str]:
    """User-role message that demands real function calls after a prose promise."""
    return {"role": "user", "content": AGENCY_REARM_NUDGE}


def unfinished_work_nudge_message() -> dict[str, str]:
    """User-role message that refuses a zero-tool work 'final'."""
    return {"role": "user", "content": UNFINISHED_WORK_NUDGE}


def unfinished_work_hard_stop_message() -> str:
    """User-visible copy when a work turn ends with zero tool evidence."""
    return UNFINISHED_WORK_HARD_STOP


# Back-compat alias used by older tests / imports.
_message_wants_tools = message_wants_tools


_TOOL_MARKUP_PREFIX_RE = re.compile(
    r"^\s*(?:<\s*)?(?:tool_c|tool_calls|function_c|function_calls|invoke\b|DSML|｜)"
    r"|^\s*<(?:tool|function)\b"
    r"|^\s*(?:tool|function|invoke)\s*$",
    re.IGNORECASE,
)


def looks_like_tool_markup_prefix(text: str) -> bool:
    """True when *text* is a short prefix of DSML / <tool_calls> (do not stream).

    Live session 2026-08-13: DeepSeek Flash streamed ``tool_c`` then ``alls>``
    and the bubble persisted as ``tool_c``. Must not match real prose
    like ``Tools are disabled in plan mode.``
    """
    t = (text or "").strip()
    if not t or len(t) > 48:
        return False
    return bool(_TOOL_MARKUP_PREFIX_RE.match(t))


def looks_like_pseudo_tools(text: str) -> bool:
    """True when the model faked tool calls in natural language or DSML markup."""
    if not text:
        return False
    # Truncated live prefix ("tool_c") must not persist as the answer.
    if looks_like_tool_markup_prefix(text):
        return True
    # Qwen/local: tool intent as JSON in content (```json {"name":"file_write"...})
    if re.search(
        r'(?is)(?:```\s*json\s*)?\{\s*"name"\s*:\s*"[a-z0-9_]+"\s*,\s*"arguments"\s*:',
        text,
    ):
        return True
    if re.search(r"(?is)<\s*response\s*>\s*\{[^}]*\"name\"\s*:", text):
        return True
    # Cheap reject: ordinary prose has no tool markup / call shape
    if (
        "(" not in text
        and "<" not in text
        and "&&" not in text
        and "｜" not in text
        and "DSML" not in text
        and "{" not in text
    ):
        low = text.lower()
        if (
            "tool_call" not in low
            and "function_call" not in low
            and "tool calls" not in low
            and "function calls" not in low
            and "invoke" not in low
        ):
            return False
    if _PSEUDO_TOOL_RE.search(text):
        return True
    if _DSML_NOISE_RE.search(text) and re.search(
        r"tool|invoke|parameter|comfyui|bash_exec|list_dir|settings|mail_|calendar_",
        text,
        re.I,
    ):
        return True
    # Any leaked tool_calls / function_calls / invoke markup (incl. truncated)
    if re.search(
        r"(?i)(<\s*function_calls\b|</\s*function_calls\s*>|<\s*tool_call\b|"
        r"<\s*tool_calls\b|<\s*tool_invoke\b|<\s*function_c|<\s*tool_c|"
        r"get_settings\s*$)",
        text,
    ):
        return True
    if text.strip() in ("<", "<>", "<function_c", "<tool_c", "tool_c"):
        return True
    if _DSML_TOOL_RE.search(text) and (
        _DSML_INVOKE_RE.search(text)
        or re.search(rf"\b({_TOOL_ID})\b", text, re.I)
    ):
        return True
    return bool("&&" in text and re.search(r"\w+\(\s*[\"']", text))


_looks_like_pseudo_tools = looks_like_pseudo_tools


def strip_tool_markup(text: str) -> str:
    """Remove DSML / fake tool-call markup so it never stays in the user bubble."""
    if not text:
        return ""
    t = text
    # Strip DSML wrapper tokens
    t = _DSML_NOISE_RE.sub(" ", t)
    # Full XML-ish function_calls / tool_call blocks (complete or truncated)
    t = re.sub(r"(?is)<\s*function_calls\b[^>]*>.*?(?:</\s*function_calls\s*>|$)", " ", t)
    t = re.sub(r"(?is)<\s*tool_calls\b[^>]*>.*?(?:</\s*tool_calls\s*>|$)", " ", t)
    t = re.sub(r"(?is)<\s*tool_call\b[^>]*>.*?(?:</\s*tool_call\s*>|$)", " ", t)
    t = re.sub(r"(?is)<\s*tool_invoke\b[^>]*/?>", " ", t)
    t = re.sub(r"(?is)<\s*function_c\w*[^>]*>.*?(?:>|$)", " ", t)
    # Remove tool_calls … blocks (greedy enough for multi-invoke dumps)
    t = re.sub(
        r"(?is)(?:tool[_\s-]?calls|function[_\s-]?calls)\b.*?(?=(?:\n{2,}|\Z))",
        " ",
        t,
    )
    t = re.sub(
        r"(?is)</?(?:invoke|tool_invoke|parameter|invoke_parameter|invoke_step|tool_call)[^>]*>",
        " ",
        t,
    )
    t = re.sub(
        rf"""(?is)name\s*=\s*["']{_TOOL_ID}["'][^<\n]*""",
        " ",
        t,
    )
    # Orphan angle-brackets from truncated dumps ("<" alone, "<function_c")
    t = re.sub(r"(?m)^\s*<\s*$", " ", t)
    t = re.sub(r"<\s*function_c\w*", " ", t, flags=re.I)
    t = re.sub(r"(?is)<\s*details\b.*?</\s*details\s*>", " ", t)
    t = re.sub(r"(?is)</?function_results\b[^>]*>", " ", t)
    t = re.sub(r"(?is)```json\s*\{[^`]*\"name\"\s*:\s*\"[^\"]+\"[^`]*\}```", " ", t)
    t = re.sub(r"(?i)\bcomposing tool\b", " ", t)
    # Lone residual tokens after strip
    if re.fullmatch(
        r"(?i)\s*<?(function|tool|tool_c\w*|invoke|tool_call|get_settings|composing tool)>?\s*",
        t or "",
    ):
        return ""
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
    return bool(re.search(r"[A-Za-z]:\\[^\n]*ComfyUI", text, re.I))


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
    # DeepSeek Flash: <tool_invoke name="list_dir" path="C:\proj"/>
    for i, m in enumerate(
        re.finditer(
            r"""<\s*tool_invoke\b([^>]*)/?>""",
            text,
            re.IGNORECASE,
        )
    ):
        attrs = m.group(1) or ""
        nm = re.search(r"""\bname\s*=\s*["']([^"']+)["']""", attrs, re.I)
        if not nm:
            continue
        name = nm.group(1).strip().lower()
        if name in ("true", "false", "string", "integer", "object", "array", "type"):
            continue
        attr_params: dict[str, str] = {}
        for am in re.finditer(r"""\b([A-Za-z_]\w*)\s*=\s*["']([^"']*)["']""", attrs):
            key = am.group(1).lower()
            if key != "name":
                attr_params[key] = am.group(2)
        attr_args: dict[str, str]
        if name == "list_dir":
            attr_args = {
                "path": attr_params.get("path") or attr_params.get("directory") or "."
            }
        elif name == "file_read":
            attr_args = {
                "path": attr_params.get("path") or attr_params.get("file") or ""
            }
            if not attr_args["path"]:
                continue
        elif name == "file_write":
            attr_args = {
                "path": attr_params.get("path") or "",
                "content": attr_params.get("content") or "",
            }
        elif name == "bash_exec":
            attr_args = {
                "command": attr_params.get("command") or attr_params.get("cmd") or ""
            }
            if not attr_args["command"]:
                continue
        else:
            attr_args = dict(attr_params)
        out.append(
            {
                "id": f"dsml_attr_{i}_{uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(attr_args, ensure_ascii=False),
                },
            }
        )
        if len(out) >= MAX_PARALLEL_TOOLS:
            return out

    # Split on invoke / function_calls boundaries when possible
    chunks = re.split(
        r"(?i)(?:invoke\b|tool_call\b|tool_calls\b|function_calls\b)", text
    )
    for i, chunk in enumerate(chunks):
        matched: re.Match[str] | None = _DSML_INVOKE_RE.search(chunk)
        if matched is None:
            matched = re.search(
                rf"""["']({_TOOL_ID})["']""",
                chunk,
                re.IGNORECASE,
            )
        if matched is not None:
            name = matched.group(1).lower()
        elif "bash_exec" in chunk.lower() and (
            "curl" in chunk.lower() or "8188" in chunk or "comfy" in chunk.lower()
        ):
            name = "bash_exec"
        elif "list_dir" in chunk.lower() and _COMFY_HUNT_RE.search(chunk):
            name = "list_dir"
        else:
            continue

        # Skip non-tool XML attributes accidentally matched
        if name in ("true", "false", "string", "integer", "object", "array", "type"):
            continue

        params: dict[str, str] = {}
        for pm in _DSML_PARAM_RE.finditer(chunk):
            params[pm.group(1).lower()] = pm.group(2).strip()
        # Fallback positional scrape only for classic workspace tools (avoid
        # mistaking invoke name="get_settings" for a parameter).
        if not params and name in (
            "bash_exec",
            "file_read",
            "file_write",
            "list_dir",
            "comfyui",
        ):
            code_m = re.search(
                r"(?:code|command|path|relative_path|prompt|action)\s*[:=]\s*[\"']?([^\"'<\n]+)",
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
            # Generic tools (get_settings, bill_upsert, mail_send, …): pass params through
            coerced: dict[str, Any] = dict(params)
            # Coerce simple numerics when models put numbers as strings
            for k, v in list(coerced.items()):
                if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
                    with suppress(ValueError):
                        coerced[k] = int(v.strip())
                elif isinstance(v, str) and re.fullmatch(r"-?\d+\.\d+", v.strip()):
                    with suppress(ValueError):
                        coerced[k] = float(v.strip())
            args = coerced

        if name == "bash_exec" and not args.get("command"):
            continue
        if name == "host_run" and not args.get("argv"):
            continue
        if name == "host_script" and not args.get("body"):
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


def _parse_json_tool_blobs(text: str) -> list[dict[str, Any]]:
    """Recover Qwen/local tool intents emitted as JSON in assistant content.

    llama.cpp + some chat templates leave tool calls as prose/JSON instead of
    native ``tool_calls``. Shapes seen in the wild::

        ```json
        {"name": "file_write", "arguments": {"path": "...", "content": "..."}}
        ```

        <response>
          {"name": "file_write", "arguments": {...}}
        </response>
    """
    if not text:
        return []
    out: list[dict[str, Any]] = []
    blobs: list[str] = []
    # fenced json
    for m in re.finditer(r"(?is)```(?:json)?\s*(\{[\s\S]*?\})\s*```", text):
        blobs.append(m.group(1))
    # <response> ... </response>
    for m in re.finditer(r"(?is)<\s*response\s*>\s*(\{[\s\S]*?\})\s*</\s*response\s*>", text):
        blobs.append(m.group(1))
    # bare single-object JSON with name+arguments (first balanced-ish object)
    if not blobs:
        for m in re.finditer(
            r'\{\s*"name"\s*:\s*"[a-zA-Z0-9_]+"\s*,\s*"arguments"\s*:\s*\{',
            text,
        ):
            start = m.start()
            # brace scan
            depth = 0
            end = -1
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > start:
                blobs.append(text[start:end])
            if len(blobs) >= MAX_PARALLEL_TOOLS:
                break

    for i, raw in enumerate(blobs):
        try:
            obj = json.loads(raw)
        except Exception:
            # trailing prose / truncated — try common fixes
            try:
                cleaned = raw.strip().rstrip(",").strip()
                obj = json.loads(cleaned)
            except Exception:
                continue
        if not isinstance(obj, dict):
            continue
        # OpenAI-ish single call
        name = str(obj.get("name") or "").strip()
        args = obj.get("arguments")
        if not name and isinstance(obj.get("function"), dict):
            name = str(obj["function"].get("name") or "").strip()
            args = obj["function"].get("arguments")
        if not name:
            continue
        if isinstance(args, str):
            try:
                args_obj = json.loads(args)
            except Exception:
                args_obj = {"value": args}
        elif isinstance(args, dict):
            args_obj = args
        else:
            args_obj = {}
        out.append(
            {
                "id": f"pseudo_json_{i}_{uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args_obj, ensure_ascii=False),
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

    # 0) JSON-in-content (Qwen2.5-Coder / local RMB common failure mode)
    out.extend(_parse_json_tool_blobs(text))

    # 1) DSML / XML-ish dumps first (the ComfyUI failure mode)
    out.extend(_parse_dsml_tool_calls(text))

    # 2) Classic function-call-as-text
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

    # Drop incomplete recoveries (truncated DSML mid-stream is common on DeepSeek).
    complete = [tc for tc in unique if recovered_tool_call_is_complete(tc)]
    dropped = len(unique) - len(complete)
    if dropped:
        logger.warning(
            "pseudo_tool_recovery dropped %s incomplete call(s) (truncated DSML?)",
            dropped,
        )

    if complete:
        logger.warning(
            "pseudo_tool_recovery count=%s names=%s preview=%r",
            len(complete),
            [c["function"]["name"] for c in complete],
            (text or "")[:200],
        )
    return complete


def recovered_tool_call_is_complete(tc: dict[str, Any]) -> bool:
    """True when a recovered/pseudo tool call has enough args to run safely.

    Incomplete DSML (e.g. ``name=\"bash_`` cut mid-token) must not be executed —
    it wastes a step and can leave the turn looking stuck.
    """
    fn = tc.get("function") if isinstance(tc, dict) else None
    if not isinstance(fn, dict):
        return False
    name = str(fn.get("name") or "").strip().lower()
    if not name or name.endswith("_") or len(name) < 3:
        return False
    raw = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(args, dict):
        return False
    if name == "bash_exec":
        cmd = str(args.get("command") or args.get("code") or args.get("cmd") or "").strip()
        return len(cmd) >= 2
    if name == "host_run":
        argv = args.get("argv")
        if isinstance(argv, list):
            return len(argv) >= 1
        return bool(str(argv or "").strip())
    if name == "host_mkdir":
        paths = args.get("paths")
        if isinstance(paths, list):
            return len(paths) >= 1
        return bool(str(paths or args.get("path") or "").strip())
    if name == "host_which":
        return bool(str(args.get("name") or "").strip())
    if name == "host_script":
        return bool(str(args.get("body") or args.get("command") or "").strip())
    if name in ("file_read", "file_write", "list_dir"):
        return bool(str(args.get("path") or args.get("file") or "").strip())
    if name == "comfyui":
        return bool(str(args.get("action") or "status").strip())
    if name == "repo_search":
        return bool(str(args.get("query") or args.get("pattern") or "").strip())
    # No-arg tools (status/list/get) are complete with empty args
    _NO_ARG_OK = frozenset(
        {
            "get_settings",
            "assistant_accounts",
            "assistant_brief",
            "budget_get",
            "budget_status",
            "debt_list",
            "bill_list",
            "money_disclaimer",
            "calendar_list_events",
            "mail_list",
            "local_discover",
        }
    )
    if name in _NO_ARG_OK:
        return True
    # Unknown tools: empty args OK if name looks complete (invoke with no params)
    return True


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


def build_system_prompt(
    persona: str | None = None,
    *,
    name: str | None = None,
    gender: str | None = None,
) -> str:
    """Base system prompt: identity kernel + operational body + style addendum.

    *name* defaults to Remedy; *gender* defaults to female (male | neutral allowed).
    *persona* here is a partner-chosen **communication style** id
    (interfaces.config.PERSONA_PROMPTS), not identity — who Remedy *is* comes
    from ``identity_system_preamble`` and canon in ``docs/REMEDY_PERSONA.md``.
    A chosen style leads over the emergent voice; it never overrides the creed
    or temperament.
    """
    from remedy.core.agent_identity import identity_system_preamble

    base = identity_system_preamble(name=name, gender=gender) + "\n\n" + _DEFAULT_SYSTEM_BODY
    addendum = persona_system_addendum(persona)
    if addendum:
        return f"{base}\n\n{addendum}"
    return base


_build_system_prompt = build_system_prompt


def tool_content_is_error(content: str | None) -> bool:
    """True when a tool result string represents a failure the model should recover from.

    Matches workspace-tool strings (``Error …``), security blocks, structured
    ``{"ok": false, …}`` payloads, non-zero ``bash_exec`` exit codes, empty
    ``repo_search`` results, and common NOT_FOUND phrasing.
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
    # Ask-mode / partner approval stops — not soft success.
    # Match anywhere (spread digests / batch reports bury the marker past prefixes).
    if "APPROVAL_REQUIRED" in s or "APPROVAL_CHECK_FAILED" in s:
        return True
    # Empty / failed discovery — recover, do not invent paths
    low = s[:500].lower()
    if s.startswith("No matches for ") or "no matches for" in low[:80]:
        return True
    if "file not found" in low or "path not found" in low:
        return True
    if "error: path not found" in low or "error: path outside" in low:
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


def batch_has_empty_search(tool_messages: list[dict[str, Any]]) -> bool:
    """True if any tool result is an empty repo_search."""
    for msg in tool_messages:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip().startswith("No matches for "):
            return True
    return False


def batch_has_approval_required(tool_messages: list[dict[str, Any]]) -> bool:
    """True if any tool result is waiting on partner approval."""
    for msg in tool_messages:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if isinstance(content, str) and (
            "APPROVAL_REQUIRED" in content or "APPROVAL_CHECK_FAILED" in content
        ):
            return True
    return False


def batch_has_empty_or_spam_write(tool_messages: list[dict[str, Any]]) -> bool:
    """True when a batch hit EMPTY_SOURCE_WRITE / SPAM_SOURCE_WRITE."""
    for msg in tool_messages:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        content = str(msg.get("content") or "")
        if (
            "EMPTY_SOURCE_WRITE" in content
            or "SPAM_SOURCE_WRITE" in content
            or "empty file_write" in content
            or "repetitive spam file_write" in content
        ):
            return True
    return False


def recovery_nudge_message(
    *,
    empty_search: bool = False,
    approval: bool = False,
    empty_write: bool = False,
) -> dict[str, str]:
    """User-role message that triggers one automatic recovery attempt."""
    if approval:
        return {"role": "user", "content": APPROVAL_NUDGE}
    if empty_write:
        return {"role": "user", "content": EMPTY_WRITE_NUDGE}
    if empty_search:
        return {"role": "user", "content": EMPTY_SEARCH_NUDGE + "\n" + RECOVERY_NUDGE}
    return {"role": "user", "content": RECOVERY_NUDGE}


def mission_verify_gate_message(verify_command: str) -> dict[str, str]:
    """Block claiming done until mission_verify passes."""
    return {
        "role": "user",
        "content": (
            "[Mission gate] Checklist steps may be done but verify has not passed. "
            f"Run mission_verify (command={verify_command!r}) now, fix failures, "
            "and only then write the final summary. Do not claim complete without a pass."
        ),
    }


def epoch_continue_message(*, epoch: int, total_step: int) -> dict[str, str]:
    """Injected between soft epochs — keep tools; do not restate entire history."""
    return {
        "role": "user",
        "content": (
            f"[Epoch {epoch} complete at step {total_step}] Context was compacted — "
            "this is not a stop. Run until the task is finished: continue from the "
            "checkpoint / tool results above. Do not restart, renumber, or summarize-only. "
            "Keep using tools until the work is done (or mission_verify passes)."
        ),
    }


def turn_has_unfinished_work(
    runtime: Any,
    *,
    session_id: str | None = None,
    tools_enabled: bool,
    tool_steps_this_turn: int = 0,
    open_tasks: list[str] | None = None,
) -> bool:
    """True when a soft epoch wall must NOT force a final answer.

    Unfinished = active mission, open brief tasks, mid-turn tool work, or an
    active build-engine turn that has not verified yet. Simple chat (no tools)
    returns False so epochs never thrash.
    """
    if not tools_enabled:
        return False
    # Read-only review/analysis: the deliverable is the written findings, not file
    # writes. It is never "unfinished" at the epoch wall (until it starts writing,
    # which flips read_only off). This is the review-loop fix at the loop layer.
    with suppress(Exception):
        from remedy.core.build_engine import get_build_state as _gbs

        _b = _gbs(runtime)
        if (
            _b is not None
            and getattr(_b, "active", False)
            and getattr(_b, "read_only", False)
            and int(getattr(_b, "write_steps", 0) or 0) == 0
        ):
            return False
    if open_tasks:
        for t in open_tasks:
            if (t or "").strip():
                return True
    # Active machine build without green verify = unfinished
    with suppress(Exception):
        from remedy.core.build_engine import (
            build_blocks_final_answer,
            get_build_state,
        )

        bst = get_build_state(runtime)
        if bst is not None and bst.active:
            if build_blocks_final_answer(bst):
                return True
            if bst.write_steps > 0 and bst.last_verify_ok is not True:
                return True
            if bst.phase not in ("done",) and tool_steps_this_turn > 0:
                return True
            if bst.phase in ("implement", "verify", "repair", "scout"):
                return True
            if bst.syntax_ok is False:
                return True
    # Active mission with pending work / unverified done
    with suppress(Exception):
        from remedy.core.mission import MissionStore

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        mid = str(
            session_id
            or getattr(runtime, "_session_id", None)
            or ""
        ) or None
        m = MissionStore(home).latest(mid)
        if m is not None and m.status == "active":
            if not m.steps:
                return True
            if any(s.status in ("pending", "active", "failed") for s in m.steps):
                return True
            if m.verify_command and m.verify_status != "passed":
                return True
    # Mid-turn tool work without a deliberate completion
    return tool_steps_this_turn > 0


def is_productive_tool_batch(tool_messages: list[dict[str, Any]]) -> bool:
    """True when a tool batch looks like real progress (not pure error/dup)."""
    if not tool_messages:
        return False
    any_ok = False
    for msg in tool_messages:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if tool_content_is_error(content):
            continue
        any_ok = True
        break
    return any_ok
