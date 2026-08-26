"""Auto-optimize Remedy for local/RMB models so real work can complete.

Local 7B hosts fail when given cloud-sized system prompts + 80 tools + "high"
thinking monologue. This module is the product answer: **detect local muscle
and automatically reshape the turn** so coding jobs (create a calculator,
write a file, fix a bug) actually run tools and finish.

Wired into system assembly, provider build_body, and stream recovery.
"""

from __future__ import annotations

import re
from typing import Any

# User wants code/files on disk — not a tutorial monologue.
_IMPLEMENT_RE = re.compile(
    r"(?is)\b("
    r"create|build|implement|write|make|scaffold|generate|code|app|program|"
    r"script|calculator|todo|cli|gui|server|fix|debug|refactor|patch|add|"
    r"file|project|feature|function|class|module|test|"
    r"launch|serve|preview|localhost|site|webpage"
    r")\b"
)


# "keep going" / resume — same product bar as implement (must use tools).
_CONTINUE_RE = re.compile(
    r"(?is)^\s*("
    r"keep\s+going|continue|resume|carry\s+on|go\s+on|proceed|"
    r"finish\s+(?:it|this|the\s+(?:task|job|build|app))|"
    r"don'?t\s+stop|do\s+it|ship\s+it|make\s+progress|"
    r"try\s+again|retry|pick\s+up(?:\s+where)?|"
    r"next\s+step|keep\s+(?:building|working|coding)"
    r")\b"
)

# Intent monologue without tools: "I'll build X… Let me start by laying out…"
_INTENT_MONOLOGUE_RE = re.compile(
    r"(?is)("
    r"i(?:'ll| will)\s+build\b|"
    r"let\s+me\s+start\s+by\b|"
    r"laying\s+out\s+the\s+architecture\b|"
    r"creating\s+the\s+core\s+project\s+structure\b|"
    r"i(?:'ll| will)\s+(?:begin|start)\s+(?:by|with)\b|"
    r"cross[- ]platform\b.*\b(packaging|viewer|editor)\b|"
    r"here(?:'s| is)\s+(?:the\s+)?(?:plan|architecture)\b"
    r")"
)

_LOCAL_CONTRACT = """[Local agent mode — auto-optimized]
You run on a fixed on-device window. Tasks use RESEARCH → PLAN → BUILD **via tools only**.
1. Call tools immediately. First step must include tool_calls (not markdown).
2. RESEARCH = list_dir / file_read / repo_search (small batches).
3. PLAN = short mental checklist — never a long RESEARCH/PLAN/BUILD essay in chat.
4. BUILD = file_write / file_edit / host_run / bash_exec. Code on disk, not in chat.
5. **Illegal:** tutorial monologue, "pip install …" guides, full source in ``` fences
   instead of file_write, "Step 1/2/3" how-to without tools.
6. Prefer absolute paths under the **project workspace**. Desktop is often PATH_DENIED.
7. After tools succeed, one short user-facing summary. No DSML/XML tool dumps as text.
"""

_CREATE_NUDGE = (
    "[Local create · tools required] Your next reply MUST use native function calls. "
    "Prefer **file_write(path, content)** with a complete working program under the "
    "project workspace, then verify (bash_exec py_compile / run). "
    "Do **not** paste full source only in chat. Do **not** write RESEARCH/PLAN/BUILD "
    "sections as the answer. Desktop is often PATH_DENIED under project scope."
)


def is_local_binding(
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> bool:
    try:
        from remedy.nanoswarm.token_nanobot import is_local_model
        from remedy.runtime.rmb.mode import is_rmb_provider

        if is_rmb_provider(provider, base_url):
            return True
        return bool(
            is_local_model(provider, model, base_url=base_url)
        )
    except Exception:
        p = (provider or "").strip().lower()
        return p in ("rmb", "ollama", "llamacpp", "local")


def needs_agent_harness(
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> bool:
    """True when Remedy must *drive* the model (local/RMB muscle).

    Product split (2026-08-09):
    - **Local / RMB / Ollama / llama.cpp** → harness ON
      mid-turn fit, force tool_choice, monologue breakers, write-first packs,
      bootstrap, tight context. Small models cannot be trusted to self-steer.
    - **Frontier / hosted** (Grok, Claude, OpenAI, …) → harness OFF (light rails)
      full context, keep tools, no monologue thrash, no force-required spam.
      Trust the model to complete the task; only safety + recovery rails.

    Alias of :func:`is_local_binding` so call sites read as policy, not plumbing.
    """
    return is_local_binding(provider, model, base_url)


def is_frontier_binding(
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> bool:
    """True for hosted frontier chat — let them be smart."""
    return not needs_agent_harness(provider, model, base_url)


def message_wants_continue_work(message: str | None) -> bool:
    """True for short resume phrases: keep going / continue / finish it."""
    t = (message or "").strip()
    if not t or len(t) > 220:
        # Long messages use implement keywords instead
        return bool(_CONTINUE_RE.search(t[:220])) if t else False
    return bool(_CONTINUE_RE.search(t))


def message_wants_implement(message: str | None) -> bool:
    """True when the user wants code/files on disk *or* is resuming that work.

    Partner rule: "keep going" on a build session is implement intent.
    """
    t = (message or "").strip()
    if len(t) < 2:
        return False
    if message_wants_continue_work(t):
        return True
    return bool(_IMPLEMENT_RE.search(t))


def history_suggests_unfinished_build(history: list[dict[str, Any]] | None) -> bool:
    """Recent turn already used tools or promised build work → continue = build."""
    if not history:
        return False
    # Look at last ~12 messages
    recent = history[-12:]
    for m in recent:
        if not isinstance(m, dict):
            continue
        if m.get("tool_calls"):
            return True
        role = str(m.get("role") or "").lower()
        if role == "tool":
            return True
        content = m.get("content")
        if not isinstance(content, str):
            continue
        low = content.lower()
        if role == "assistant" and (
            _INTENT_MONOLOGUE_RE.search(content)
            or "file_write" in low
            or "bash_exec" in low
            or "i'll build" in low
            or "let me start" in low
        ):
            return True
        if role == "tool" or "name 'suppress'" in low or "error" in low[:80]:
            if any(
                k in low
                for k in ("bash_exec", "file_write", "file_edit", "suppress", "failed")
            ):
                return True
    return False


def message_wants_build_work(
    message: str | None,
    history: list[dict[str, Any]] | None = None,
) -> bool:
    """Implement keywords, continue phrases, or resume of unfinished build."""
    if message_wants_implement(message):
        return True
    # Bare "ok" / "yes" after a build monologue still means keep building
    t = (message or "").strip().lower()
    if t in ("ok", "okay", "yes", "yep", "yeah", "do it", "go", "sure") and history_suggests_unfinished_build(
        history
    ):
        return True
    # Short follow-ups during a build session
    return bool(history_suggests_unfinished_build(history) and len(t) < 80)


def looks_like_intent_monologue(text: str | None) -> bool:
    """True when the model promises to build/architect without tool_calls."""
    t = (text or "").strip()
    if len(t) < 40:
        return False
    return bool(_INTENT_MONOLOGUE_RE.search(t))


def monologue_fingerprint(text: str | None) -> str:
    """Normalize monologue text for loop detection (ignore whitespace noise)."""
    import re as _re

    t = (text or "").strip().lower()
    t = _re.sub(r"\s+", " ", t)
    # Collapse repeated identical sentences (model stutter in one blob)
    parts = [p.strip() for p in _re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    if len(parts) >= 2:
        # If first sentence repeats, keep once
        uniq: list[str] = []
        for p in parts:
            if not uniq or p != uniq[-1]:
                uniq.append(p)
        t = " ".join(uniq)
    return t[:400]


def text_has_internal_repetition(text: str | None) -> bool:
    """True when the same sentence appears 2+ times in one reply (loop stutter)."""
    t = (text or "").strip()
    if len(t) < 80:
        return False
    import re as _re

    # Include concatenated "Foo.Foo" (no space) — session 765c tuner stutter.
    parts = [
        _re.sub(r"[;:]+", ".", p.strip().lower())
        for p in _re.split(r"(?<=[.!?])(?:\s+|(?=[A-Z]))", t)
        if len(p.strip()) > 40
    ]
    if len(parts) < 2:
        # Also catch paragraph repeats without punctuation
        chunks = [c.strip().lower() for c in t.split("\n\n") if len(c.strip()) > 50]
        if len(chunks) >= 2 and chunks[0] == chunks[1]:
            return True
    else:
        seen: set[str] = set()
        for p in parts:
            if p in seen:
                return True
            seen.add(p)
    compact = _re.sub(r"\s+", " ", t)
    compact = _re.sub(r"[;:]+", ".", compact).lower()
    if len(compact) >= 160:
        window = compact[:80]
        if compact.count(window) >= 2:
            return True
    return False


def project_listing_snapshot(project_path: str | None, *, max_entries: int = 40) -> str:
    """Real list_dir-style text for inject when model only monologues 'explore'."""
    from pathlib import Path as _P

    root = _P(project_path or "").expanduser()
    if not root.is_dir():
        return f"(project path not found: {project_path})"
    lines: list[str] = [f"Project root: {root}"]
    try:
        entries = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        return f"(cannot list {root}: {e})"
    n = 0
    for p in entries:
        if p.name.startswith("."):
            continue
        kind = "dir" if p.is_dir() else "file"
        lines.append(f"  {kind} {p.name}")
        n += 1
        if n >= max_entries:
            lines.append("  …")
            break
    # One level into src/ if present
    src = root / "src"
    if src.is_dir():
        lines.append("src/:")
        try:
            for p in sorted(src.rglob("*"))[:30]:
                if p.is_file():
                    try:
                        rel = p.relative_to(root).as_posix()
                    except ValueError:
                        rel = str(p)
                    lines.append(f"  file {rel}")
        except OSError:
            pass
    return "\n".join(lines)


def local_system_contract() -> str:
    return _LOCAL_CONTRACT


def local_create_nudge() -> str:
    return _CREATE_NUDGE


# Tutorial / RPB essay without tools — the #1 local 7B failure mode (export 2026-08-08).
_RPB_HEADING_RE = re.compile(
    r"(?im)^\s*(?:#{1,4}\s*)?(?:\d+\.\s*)?(?:\*\*)?(research|plan|build)\b"
)
_TUTORIAL_MARKERS = (
    "pip install",
    "necessary libraries",
    "verify the build",
    "we have created",
    "we have outlined",
    "step 1:",
    "step 2:",
    "step 3:",
    "short checklist",
    "to create a standalone",
    "follow these steps",
    "installers for windows",
    "create installers",
)


def looks_like_tutorial_monologue(text: str | None) -> bool:
    """True when the model wrote a how-to / RPB essay instead of tool_calls.

    Session export: create PDF viewer → multi-turn RESEARCH/PLAN/BUILD markdown
    with fenced code and pip install, zero file_write. Must never be accepted.

    Also: short intent monologues ("I'll build X… laying out architecture")
    that leave the partner with zero files on disk (screenshot 2026-08-08).
    """
    t = (text or "").strip()
    if not t:
        return False
    # Intent-only promise (can be short) — still not work
    if looks_like_intent_monologue(t) and len(t) < 900 and "```" not in t:
        # Pure promise + architecture talk, no fenced work product
        return True
    if len(t) < 180:
        return False
    low = t.lower()
    headings = {m.group(1).lower() for m in _RPB_HEADING_RE.finditer(t)}
    if len(headings) >= 2:
        return True
    fences = t.count("```")
    marker_hits = sum(1 for m in _TUTORIAL_MARKERS if m in low)
    if fences >= 2 and marker_hits >= 1:
        return True
    if fences >= 4 and len(t) > 700:
        return True
    # Long implement dump with code but no admission of having used tools
    if (
        fences >= 2
        and len(t) > 500
        and any(k in low for k in ("create", "implement", "build", "viewer", "editor", "app"))
        and not any(
            k in low
            for k in (
                "i wrote",
                "i created the file",
                "file_write",
                "wrote to",
                "saved to",
                "created `",
            )
        )
    ):
        return True
    return bool(marker_hits >= 2 and len(t) > 400)


def continue_build_nudge(*, project_path: str | None = None) -> dict[str, str]:
    """Hard user message after intent monologue on keep-going / build turns."""
    root = (project_path or "").strip().replace("\\", "/")
    example = f'{root.rstrip("/")}/README.md' if root else "PROJECT_ROOT/README.md"
    return {
        "role": "user",
        "content": (
            "[Partner · CONTINUE BUILD — tools required] You promised work but made "
            "**zero** tool_calls. That is not allowed. Your next message MUST be native "
            "function calls only (no architecture essay):\n"
            f"1) list_dir on the project root (or file_write `{example}` if empty)\n"
            "2) file_write / file_edit for real source files under the workspace\n"
            "3) host_run / bash_exec only after files exist (verify, not plan)\n"
            "Illegal: 'I'll build…', 'let me start by laying out…', RESEARCH/PLAN markdown.\n"
            "Start **now** with tool_calls."
        ),
    }


def tutorial_monologue_nudge(*, project_path: str | None = None) -> dict[str, str]:
    """Hard user message: stop essay, emit file_write now."""
    root = (project_path or "").strip().replace("\\", "/")
    example = f'{root.rstrip("/")}/app.py' if root else "PROJECT_ROOT/app.py"
    return {
        "role": "user",
        "content": (
            "[Local performance · REJECT monologue] That answer was a tutorial essay, "
            "not work. **Do not** write RESEARCH/PLAN/BUILD sections or paste full "
            "source in chat. Emit native tool_calls **now**:\n"
            f"1) file_write(path=\"{example}\", content=...complete working code...)\n"
            "2) bash_exec to verify (e.g. python -m py_compile …)\n"
            "Paths must be under the project workspace. One short summary only after tools."
        ),
    }


def slim_system_for_local(
    system_prompt: str,
    context: str,
    *,
    provider: str = "",
    model: str = "",
    base_url: str = "",
    max_steps: int = 256,
    user_message: str = "",
    window: int | None = None,
) -> str:
    """Build a tight system block that still carries workspace + local contract.

    Head/context caps scale with the physical n_ctx (autofit 16–32k can keep
    more workspace than the old hard 6k-char trim).
    """
    # Trivia / math / world-knowledge: tiny prompt. The agent contract
    # ("call tools immediately") makes reasoners and base models ramble.
    try:
        from remedy.core.react_policy import is_pure_trivia_message

        if is_pure_trivia_message(user_message):
            return (
                f"You are a local assistant on this PC (model: {model or 'local'}).\n"
                "Answer in one short sentence. No tools. No essays. No chain-of-thought."
            )
    except Exception:
        pass

    win = int(window or 0)
    if win < 2048:
        try:
            from remedy.core.endless_context import resolve_local_window

            win = int(
                resolve_local_window(
                    provider=provider, model=model, base_url=base_url
                )
                or 8192
            )
        except Exception:
            win = 8192
    if win >= 24_576:
        head_cap, ctx_cap = 1400, 18_000
    elif win >= 16_384:
        head_cap, ctx_cap = 1100, 12_000
    elif win >= 12_288:
        head_cap, ctx_cap = 900, 8_000
    else:
        head_cap, ctx_cap = 700, 6_000

    # Keep identity line if present (first ~400 chars of product system)
    head = (system_prompt or "").strip()
    if len(head) > head_cap + 200:
        # Drop the long tool-policy essay for local — contract replaces it
        first = head.split("\n\n", 1)[0]
        head = first[:head_cap] + (
            "\nStyle: concise, tool-first. Prefer action over narration."
        )

    runtime_info = (
        f"Provider: {provider} · model: {model}\n"
        f"Mode: local fixed-window agent · n_ctx={win} · run until done "
        f"(safety ceiling {max_steps} steps).\n"
        "Answer provider/model questions from this line — no tools needed."
    )
    parts = [head, runtime_info, local_system_contract()]
    ctx = (context or "").strip()
    if ctx:
        if len(ctx) > ctx_cap:
            keep = max(2000, ctx_cap - 500)
            ctx = ctx[:keep] + "\n…[context trimmed for local window]"
        parts.append(ctx)
    if message_wants_implement(user_message):
        parts.append(local_create_nudge())
    return "\n\n".join(p for p in parts if p and str(p).strip())


# First implement steps: write/read only — bash_exec too often skips the write.
WRITE_FIRST_TOOLS: tuple[str, ...] = (
    "file_write",
    "file_edit",
    "file_edit_batch",
    "file_read",
    "list_dir",
    "repo_search",
)

_OPERATE_HOST_RE = re.compile(
    r"(?is)\b(launch|serve|preview|localhost|http\.server|npm\s+start|"
    r"start\s+(the\s+)?(dev\s+)?server|open\s+(the\s+)?(site|page|preview))\b"
)

# Remedy injects its own bracket-tagged turns ("[Partner · … tools required]",
# "[Task loop — PLAN]", "[Local create · tools required]"). They are harness
# scaffolding, not something the owner typed.
_HARNESS_INJECT_RE = re.compile(
    r"""(?ix)^\s*\[\s*(
        partner | task\s+loop | local\s+(create|agent) | evidence\s+delta |
        build | auto\s+verify | machine\s+verify | local\s+create\s+contract
    )\b[^\]]*\]"""
)


_INJECT_CONSTANTS = (
    "RECOVERY_NUDGE",
    "EMPTY_WRITE_NUDGE",
    "SPEED_BATCH_NUDGE",
    "APPROVAL_NUDGE",
    "EMPTY_SEARCH_NUDGE",
    "AGENCY_REARM_NUDGE",
    "UNFINISHED_WORK_NUDGE",
)
_inject_prefixes_cache: tuple[str, ...] | None = None


def _known_inject_prefixes() -> tuple[str, ...]:
    """Opening text of the nudges Remedy injects as *user* turns.

    Not every inject carries a ``[Bracketed tag]``. ``RECOVERY_NUDGE`` starts
    "One or more tools failed. Do not give a final answer yet…" — plain prose,
    indistinguishable from owner input by shape alone. Matching the real
    constants keeps this exact instead of guessing at phrasing.
    """
    global _inject_prefixes_cache
    if _inject_prefixes_cache is not None:
        return _inject_prefixes_cache
    found: list[str] = []
    try:
        from remedy.core import react_policy as _rp

        for attr in _INJECT_CONSTANTS:
            val = getattr(_rp, attr, "")
            if isinstance(val, str) and len(val.strip()) >= 24:
                found.append(val.strip()[:48].lower())
    except Exception:
        found = []
    _inject_prefixes_cache = tuple(found)
    return _inject_prefixes_cache


def is_harness_inject(text: str | None) -> bool:
    """True for a turn Remedy wrote to steer itself, not owner input."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if _HARNESS_INJECT_RE.match(raw):
        return True
    low = raw[:48].lower()
    return any(low.startswith(p[: len(low)]) or p.startswith(low) for p in _known_inject_prefixes())


def last_real_user_message(messages: list[dict[str, Any]] | None) -> str:
    """The owner's most recent turn, skipping Remedy's own injected nudges.

    Classifying the inject instead of the request is actively harmful: the
    nudges quote tool names ("Reply with native tool_calls only (file_read /
    file_write …)"), which ``looks_like_injected_tool_markup`` reads as pasted
    tool markup — i.e. trivia. The body optimizer then strips every tool from
    the very step whose inject demanded a tool call, and a local model
    deadlocks: no tools, so no tool call, so another nudge.
    """
    fallback = ""
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        text = content if isinstance(content, str) else str(content or "")
        if not text.strip():
            continue
        if is_harness_inject(text):
            fallback = fallback or text
            continue
        return text
    # Every user turn was an inject (rare): better to over-arm than to strip.
    return fallback


_WRITE_TOOL_NAMES = frozenset(
    {"file_write", "file_edit", "file_edit_batch", "apply_patch"}
)

_EXEC_TOOL_NAMES = frozenset({"bash_exec", "host_run", "run_python_file"})

# "…then run it", "…and tell me the output", "…prove it works": an explicit
# second step the owner asked for, after the writing is done.
_RUN_REQUEST_RE = re.compile(
    r"(?is)\b("
    r"then\s+run|run\s+it|run\s+that|and\s+run\b|execute\s+it|"
    r"run\s+the\s+(?:file|script|program|app|test|tests)|"
    r"tell\s+me\s+(?:its|the)\s+output|show\s+me\s+(?:its|the)\s+output|"
    r"report\s+(?:its|the)\s+output|prove\s+it\s+works|demonstrate\s+it|"
    r"run\s+it\s+again|confirm\s+it\s+(?:works|passes)"
    r")\b"
)


def request_wants_execution(user_message: str | None) -> bool:
    """True when the owner explicitly asked for the code to be *run*."""
    return bool(_RUN_REQUEST_RE.search(str(user_message or "")))


def execution_already_ran(history: list[dict[str, Any]] | None) -> bool:
    """True once an execution tool has actually been called this turn."""
    for m in reversed(history or []):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            if str((tc.get("function") or {}).get("name") or "") in _EXEC_TOOL_NAMES:
                return True
    return False


def write_already_attempted(history: list[dict[str, Any]] | None) -> bool:
    """True once the model has actually called a write tool this turn."""
    for m in reversed(history or []):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            name = str((tc.get("function") or {}).get("name") or "")
            if name in _WRITE_TOOL_NAMES:
                return True
    return False


def filter_tools_write_first(
    tools: list[dict[str, Any]] | None,
    *,
    user_message: str = "",
    step_index: int = 0,
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    """On early implement steps, drop bash/mission so the model must file_write.

    The point is *write before you run a shell*, not *wait two steps*. Gating on
    ``step_index`` alone meant a model that wrote a file on step 0 still had no
    ``bash_exec`` / ``host_run`` / ``run_python_file`` on step 1 — so "create
    fib.py, run it, tell me the output" ended after the write, every time, on
    every model tested (local and cloud alike). Once a write has happened the
    objective is met and execution tools come back immediately.
    """
    if not tools or step_index > 1:
        return tools
    if not message_wants_implement(user_message):
        return tools
    if write_already_attempted(history):
        return tools
    allow = set(WRITE_FIRST_TOOLS)
    if _OPERATE_HOST_RE.search(user_message or ""):
        allow.update({"host_run", "bash_exec", "computer_navigate"})
    out: list[dict[str, Any]] = []
    for t in tools:
        fn = t.get("function") if isinstance(t, dict) else None
        name = str((fn or {}).get("name") or "")
        if name in allow:
            out.append(t)
    return out or tools


def force_tool_choice_required(
    *,
    provider: str | None,
    model: str | None,
    base_url: str | None,
    tools: list | None,
    user_message: str | None = None,
    step_index: int = 0,
    history: list[dict[str, Any]] | None = None,
) -> bool:
    """True when llama.cpp should get tool_choice=required (not monologue)."""
    if not tools:
        return False
    if not is_local_binding(provider, model, base_url):
        return False
    msg = user_message or ""
    build = message_wants_build_work(msg, history)
    # Keep-going / implement: force tools for the whole build turn
    # (monologue loops often happen after step 8 when this used to drop)
    if build and step_index <= 64:
        return True
    # Anytime tools are armed and step is early, bias required for RMB
    if step_index == 0 and (provider or "").lower() in ("rmb", "llamacpp", "ollama"):
        # Only force if message looks like work (not pure greeting)
        low = msg.strip().lower()
        if low and low not in ("hi", "hey", "hello", "thanks", "thank you"):
            if build or message_wants_implement(msg) or len(low) > 40:
                return True
    return False


def tools_include_writes(tools: list | None) -> bool:
    """True when file_write / file_edit / batch are in the armed tool list."""
    if not tools:
        return False
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        name = str((fn or {}).get("name") or t.get("name") or "").strip().lower()
        if name in ("file_write", "file_edit", "file_edit_batch"):
            return True
    return False


def local_completion_cap(
    window: int,
    *,
    tools_present: bool,
    force_tools: bool,
    write_tools: bool = False,
) -> int:
    """n_predict budget for local hosts.

    Partner rule: must be large enough for real ``file_write`` / ``file_edit``
    JSON. Late build steps still need headroom even when force_tools is off.
    """
    win = max(2048, int(window or 8192))
    # Write tools always get the high band (edit/write bodies are huge as JSON)
    if write_tools or (force_tools and tools_present):
        # 32k ctx → 12k; hard floor 4k so mid-stream file_edit does not die
        return max(4096, min(12288, win // 3))
    if tools_present:
        # Was 6k at 32k ctx — R1/Qwen3 fill that with hidden thinking (minutes).
        return max(768, min(2048, win // 12))
    return max(256, min(768, win // 24))


def apply_local_body_optimize(
    body: dict[str, Any],
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    user_message: str | None = None,
    step_index: int = 0,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mutate/return chat body optimized for local hosts."""
    if not isinstance(body, dict):
        return body
    if not is_local_binding(provider, model, base_url):
        return body
    out = dict(body)
    tools = out.get("tools") if isinstance(out.get("tools"), list) else None
    force = force_tool_choice_required(
        provider=provider,
        model=model,
        base_url=base_url,
        tools=tools,
        user_message=user_message,
        step_index=step_index,
        history=history,
    )
    writes = tools_include_writes(tools)
    # Late-step builds still need tool_choice when write tools are armed
    if writes and message_wants_build_work(user_message or "", history):
        force = True
    try:
        from remedy.core.endless_context import resolve_local_window

        win = resolve_local_window(
            provider=provider, model=model, base_url=base_url
        )
    except Exception:
        win = 8192
    # Sticky bump after TOOL_ARGS_TRUNCATED (set by tool batch)
    sticky = 0
    try:
        sticky = int(out.get("_remedy_write_budget") or 0)
    except (TypeError, ValueError):
        sticky = 0
    # After machine green verify: force tiny summary budget, no tools
    green_summary = False
    try:
        for m in reversed(history or []):
            if not isinstance(m, dict):
                continue
            c = str(m.get("content") or "")
            if (
                "GREEN · stop building" in c
                or "Machine verify passed" in c
                or "AUTO VERIFY · GREEN" in c
                or "Verify is GREEN" in c
            ):
                green_summary = True
                break
            # only scan recent tail
            if m.get("role") == "assistant" and len(c) > 400:
                break
    except Exception:
        green_summary = False
    # Also honor body already tool-less after green strip upstream
    if not green_summary and not out.get("tools") and not tools:
        try:
            for m in reversed((history or [])[-6:]):
                if isinstance(m, dict) and "GREEN" in str(m.get("content") or ""):
                    green_summary = True
                    break
        except Exception:
            pass
    # A green build verify is not the same as the owner's request being done.
    # "Create fib.py, then run it and tell me its output" writes the file, the
    # engine's own smoke test passes, and GREEN stripped every tool and ordered
    # a summary — so the model was *forbidden* from running what it had just
    # written. The turn ended with the second half of the request unperformed,
    # identically on every model tested. Hold the tools until the run happens.
    if (
        green_summary
        and request_wants_execution(user_message)
        and not execution_already_ran(history)
    ):
        green_summary = False

    if green_summary:
        tools = None
        out.pop("tools", None)
        out.pop("tool_choice", None)
        force = False
        writes = False

    trivia = False
    try:
        from remedy.core.react_policy import is_pure_trivia_message

        trivia = is_pure_trivia_message(user_message or "")
    except Exception:
        trivia = False
    if trivia:
        tools = None
        out.pop("tools", None)
        out.pop("tool_choice", None)
        force = False
        writes = False

    cap = local_completion_cap(
        win,
        tools_present=bool(tools) and not trivia,
        force_tools=force and not trivia,
        write_tools=writes and not trivia,
    )
    if trivia:
        cap = min(cap, 256)
    if green_summary:
        cap = min(cap, 512)
    if sticky > cap and not green_summary:
        cap = sticky
    try:
        cur = int(out.get("max_tokens") or cap)
        # Never *shrink* a write-tool budget below the write floor
        if writes and not green_summary:
            out["max_tokens"] = max(cur, cap)
        else:
            out["max_tokens"] = min(cur, cap) if cur > 0 else cap
            if not green_summary:
                out["max_tokens"] = max(int(out["max_tokens"]), min(cap, 2048))
            else:
                out["max_tokens"] = min(int(out["max_tokens"]), 512)
    except (TypeError, ValueError):
        out["max_tokens"] = cap
    out.pop("_remedy_write_budget", None)
    if tools:
        out["temperature"] = 0.05 if force else min(float(out.get("temperature") or 0.2), 0.15)
        # Non-stream JSON first: llama.cpp is flaky streaming with tool_choice=required.
        # On implement turns, force tools — "auto" lets 7B dump tutorial monologues.
        out["stream"] = False
        out["tool_choice"] = "required" if force else "auto"
    elif green_summary:
        out["stream"] = False
        out["temperature"] = 0.1
    # Local models: never send cloud thinking knobs. Qwen3/R1 otherwise
    # spend thousands of tokens in <think> before "1 + 1" → 2. Request
    # body covers hosts that ignore argv; unknown field is ignored.
    out.pop("reasoning_effort", None)
    out.pop("thinking", None)
    prev_kw = out.get("chat_template_kwargs")
    merged_kw: dict[str, Any] = dict(prev_kw) if isinstance(prev_kw, dict) else {}
    merged_kw.setdefault("enable_thinking", False)
    out["chat_template_kwargs"] = merged_kw
    out.setdefault("reasoning_budget", 0)
    return out


# Allow spaces in Windows paths (e.g. "Remedy Projects")
_PATH_RE = re.compile(
    r"(?i)((?:[A-Za-z]:[\\/]|[\\/])"
    r"(?:[^\"'\n|*?<>]+\.)"
    r"(?:py|js|ts|tsx|jsx|rs|go|java|cs|cpp|c|h|md|txt|json|html|css)\b)"
)


def extract_create_path(message: str | None) -> str | None:
    """Best-effort path the user wants created/written."""
    m = _PATH_RE.search(message or "")
    if not m:
        return None
    return m.group(1).strip().strip("`'\" ").replace("\\", "/")


def simple_python_calculator_source() -> str:
    return (
        '"""Simple calculator — generated by Remedy local agent bootstrap."""\n\n'
        "def add(a, b):\n"
        "    return a + b\n\n"
        "def sub(a, b):\n"
        "    return a - b\n\n"
        "def mul(a, b):\n"
        "    return a * b\n\n"
        "def div(a, b):\n"
        "    if b == 0:\n"
        "        raise ZeroDivisionError('division by zero')\n"
        "    return a / b\n\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    print(add(2, 3))\n"
    )


def _extract_print_phrase(message: str) -> str | None:
    """Pull `prints exactly: X` / `print \"X\"` from a simple create request."""
    import re

    m = re.search(
        r"(?is)prints?\s+(?:exactly\s+)?[:\"]\s*([^\n\"']+)",
        message or "",
    )
    if m:
        return m.group(1).strip().strip("`'\" ")
    m = re.search(r'(?is)print\(\s*["\']([^"\']+)["\']\s*\)', message or "")
    if m:
        return m.group(1).strip()
    return None


def _extract_simple_filename(message: str) -> str | None:
    """hello.py / main.c style basename from the user message."""
    import re

    m = re.search(
        r"(?i)\b([A-Za-z_][\w\-]*\.(?:py|c|cpp|rs|go|js|ts))\b",
        message or "",
    )
    if m:
        return m.group(1)
    return None


async def maybe_bootstrap_local_create(
    runtime: Any,
    message: str,
) -> str | None:
    """Deterministic finish for *narrow* local create asks only.

    Partner rule (2026-08-08): do **not** short-circuit the ReAct loop for
    general \"write hello.py\" / PDF apps — that wrote the wrong file
    (always calculator.py) and skipped tools. Only bootstrap when the user
    clearly wants a **calculator**, or a one-file print program with an
    explicit filename + print phrase.
    """
    if not message_wants_implement(message):
        return None
    try:
        from remedy.core.turn_context import current_plan_mode

        if current_plan_mode(runtime):
            return None
    except Exception:
        pass
    try:
        from remedy.core.llm_binding import get_llm_binding

        b = get_llm_binding(runtime)
        if not is_local_binding(b.provider, b.model, b.base_url):
            return None
    except Exception:
        return None

    low = (message or "").lower()
    wants_calc = any(
        k in low for k in ("calculator", "calc app", "add/sub", "tkinter calculator")
    )
    print_phrase = _extract_print_phrase(message or "")
    fname = _extract_simple_filename(message or "")
    # One-file print program: "write hello.py that prints exactly: hello partner"
    wants_hello = bool(
        print_phrase
        and fname
        and fname.endswith(".py")
        and any(k in low for k in ("write", "create", "make", "build"))
        and "pdf" not in low
        and "remedypdf" not in low
    )
    if not wants_calc and not wants_hello:
        return None

    path = extract_create_path(message)
    if not path:
        try:
            root = str(runtime.effective_project_path() or "").replace("\\", "/")
            if root and wants_calc:
                path = f"{root.rstrip('/')}/calculator.py"
            elif root and fname:
                path = f"{root.rstrip('/')}/{fname}"
        except Exception:
            path = None
    if not path:
        return None

    ensure_local_power_approvals()
    if wants_calc:
        content = simple_python_calculator_source()
        label = "working Python calculator (add/sub/mul/div)"
    else:
        phrase = print_phrase or "hello partner"
        # Escape for Python string
        safe = phrase.replace("\\", "\\\\").replace('"', '\\"')
        content = (
            f'"""Generated by Remedy — simple print program."""\n\n'
            f"def main() -> None:\n"
            f'    print("{safe}")\n\n'
            f'if __name__ == "__main__":\n'
            f"    main()\n"
        )
        label = f'Python program that prints "{phrase}"'

    try:
        from pathlib import Path as _P

        resolved = runtime.resolve_tool_path(path, for_write=True)
        resolved = _P(resolved)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
    except Exception as exc:
        return (
            f"I tried to create `{path}` automatically (local agent bootstrap) but "
            f"hit: {exc}. I'll continue with tools."
        )

    compile_note = ""
    run_note = ""
    try:
        import py_compile

        py_compile.compile(str(resolved), doraise=True)
        compile_note = " Syntax check (py_compile) passed."
    except Exception as exc:
        compile_note = f" (py_compile warning: {exc})"
    if wants_hello and print_phrase:
        try:
            import subprocess

            from remedy.core.build_python import python_cmd_for_subprocess

            py = python_cmd_for_subprocess(resolved.parent)
            if not py:
                raise RuntimeError("no real Python")
            r = subprocess.run(
                [*py, str(resolved)],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(resolved.parent),
            )
            out = (r.stdout or "").strip()
            if print_phrase in out:
                run_note = f"\nVerified run output: `{out}`"
        except Exception:
            pass

    return (
        f"Created `{resolved.as_posix()}` — {label}.{compile_note}{run_note}\n\n"
        f"Run: `python \"{resolved}\"`\n"
    )


def ensure_local_power_approvals() -> None:
    """Skip the Ask click for this local/RMB turn only — never persist Settings.

    Sibling cloud tabs stay in Ask. Process-global ``APPROVALS.mode`` and
    ``config.toml`` are left untouched.
    """
    try:
        from remedy.core.turn_context import set_turn_skip_ask

        set_turn_skip_ask(True)
    except Exception:
        pass


def inject_local_messages(
    messages: list[dict[str, Any]],
    *,
    user_message: str = "",
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    project_path: str | None = None,
) -> list[dict[str, Any]]:
    """Ensure local contract + create nudge exist once in the message list."""
    if not is_local_binding(provider, model, base_url):
        return messages
    out = list(messages)
    blob = "\n".join(
        str(m.get("content") or "")
        for m in out
        if isinstance(m, dict) and m.get("role") == "system"
    )
    if "[Local agent mode" not in blob:
        # Prepend contract after first system if present
        insert_at = 0
        for i, m in enumerate(out):
            if isinstance(m, dict) and m.get("role") == "system":
                insert_at = i + 1
                break
        out.insert(
            insert_at,
            {"role": "system", "content": local_system_contract()},
        )
    wants_build = message_wants_implement(user_message) or message_wants_continue_work(
        user_message
    )
    if wants_build and "[Local create contract]" not in blob:
        # Insert just before last user message
        idx = len(out)
        for i in range(len(out) - 1, -1, -1):
            if isinstance(out[i], dict) and out[i].get("role") == "user":
                idx = i
                break
        nudge = local_create_nudge()
        if project_path and str(project_path).strip():
            root = str(project_path).strip().replace("\\", "/")
            nudge += (
                f"\nDefault write root for this session: `{root}/`. "
                f"Example: file_write(path=\"{root}/calculator.py\", content=...)."
            )
        out.insert(idx, {"role": "system", "content": nudge})
    return out
