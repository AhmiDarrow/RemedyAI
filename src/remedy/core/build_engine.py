"""Machine-native build engine — the organism builds like a linker, not a chat.

Thesis
------
Capable providers (Grok/Claude/GPT) are **muscle**. This module is the **machine
scheduler** around them:

  scout → implement → verify → repair → done

It does not replace ReAct. It *supervises* it: tracks tool effects, kills
explore thrash, demands verification after writes, and injects hard protocol
blocks the model cannot ignore without tool_calls.

This is how a technological organism ships software: short verified hops,
falsification loops, no monologue theater.
"""

from __future__ import annotations

import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

_BUILD_RE = re.compile(
    r"(?i)\b("
    r"implement|build|ship|scaffold|"
    r"create\s+(an?\s+)?(app|api|service|module|package|feature|cli|library|tool|file|program|"
    r"simple\s+\w+|c\s+program|\w+\s+program)|"
    r"write\s+(a\s+)?(script|module|service|test|app|file|program)|"
    r"add\s+(a\s+)?(feature|endpoint|command|test)|"
    r"fix\s+(the\s+)?(bug|build|tests?|ci|error)|"
    r"refactor|wire\s+up|make\s+(it|me)|develop|"
    r"end[- ]to[- ]end|from\s+scratch|green\s+tests|pytest|npm\s+test|"
    # Broader task verbs — default research → plan → build loop
    r"review|audit|investigate|research|debug|set\s*up|setup|"
    r"migrate|upgrade|replace|prototype|design\s+(the\s+)?(system|api|feature)|"
    r"calculator|todo\s+app|cli\b|"
    # Simple C / compile tasks (partner e2e)
    r"compile|gcc|clang|\.c\b|hello\.c|main\.c"
    r")\b"
)

# Source mutations only — shell is classified as verify when it looks like tests
_WRITE_TOOLS = frozenset(
    {
        "file_write",
        "file_edit",
        "file_edit_batch",
    }
)
_EXPLORE_TOOLS = frozenset(
    {"file_read", "list_dir", "repo_search", "memory_search", "soul_recall"}
)
_VERIFY_TOOLS = frozenset(
    {"bash_exec", "shell_exec", "job_run", "mission_verify", "mission_update"}
)
_SHIP_TOOLS = frozenset(
    {
        "git_status",
        "git_push",
        "gh_release",
        "ship_status",
        "ship_push",
        "ship_release",
    }
)
_VERIFY_HINT = re.compile(
    r"(?i)\b(pytest|npm\s+test|cargo\s+test|go\s+test|unittest|vitest|jest|"
    r"exit_code=0|passed|FAILED|ERROR|tests?\s+passed|ok\s+\d+\s+passed)\b"
)
_SHIP_CMD_HINT = re.compile(
    r"(?i)\b(git\s+push|gh\s+release|gh\s+pr\s+create|git\s+tag\b)\b"
)

PHASES = ("scout", "implement", "verify", "repair", "ship", "done")

# Source extensions that invalidate green verify (not docs/yml alone)
_SOURCE_WRITE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".cs",
        ".cpp",
        ".cc",
        ".c",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".swift",
        ".vue",
        ".svelte",
    }
)

_SHIP_GOAL_RE = re.compile(
    r"(?is)\b("
    r"push|release|ship|publish|tag\b|github|gh\s+release|"
    r"open\s+a?\s*pr|pull\s+request|deploy\s+to\s+(?:prod|github)"
    r")\b"
)


def looks_like_ship_goal(goal: str) -> bool:
    return bool(_SHIP_GOAL_RE.search(goal or ""))


def _is_filesystem_path(path: str) -> bool:
    """True for real paths; false for shell command blobs in ledger noise."""
    p = (path or "").strip()
    if not p or len(p) > 400:
        return False
    # Shell multi-command blobs
    if "\n" in p or " && " in p or "||" in p:
        return False
    low = p.lower()
    if low.startswith(
        (
            "git ",
            "gh ",
            "pytest",
            "python ",
            "python -c",
            "npm ",
            "npx ",
            "node ",
            "wc ",
            "select-string",
            "get-content",
            "get-childitem",
            "rg ",
            "find ",
            "cat ",
            "ls ",
            "dir ",
        )
    ):
        return False
    # Shell verb + path arg (Select-String -Path src/...)
    if re.match(
        r"(?i)^(select-string|get-content|findstr|rg|grep|wc|type)\b",
        p,
    ):
        return False
    # Too many spaces → almost always a command, not a path
    if p.count(" ") >= 2 and not re.match(r"^[A-Za-z]:[\\/]", p):
        return False
    # Path-like: has slash/backslash or extension or drive letter
    if re.match(r"^[A-Za-z]:[\\/]", p):
        return True
    if "/" in p or "\\" in p:
        return True
    if re.search(r"\.[A-Za-z0-9]{1,8}$", p):
        return True
    return False


def _is_source_path(path: str) -> bool:
    p = (path or "").strip().lower().replace("\\", "/")
    if not _is_filesystem_path(p):
        return False
    # Ignore tmp / probe scripts
    if "/.remedy-build/" in p or p.startswith("_") or "/_dump" in p:
        return False
    for suf in _SOURCE_WRITE_SUFFIXES:
        if p.endswith(suf):
            return True
    return False


@dataclass
class BuildTurnState:
    """Per-stream build supervision state (turn-local + ledger-backed)."""

    active: bool = False
    phase: str = "scout"
    goal: str = ""
    started_ts: float = field(default_factory=time.time)
    explore_steps: int = 0
    write_steps: int = 0
    verify_steps: int = 0
    repair_steps: int = 0
    paths_touched: list[str] = field(default_factory=list)
    shell_log: list[str] = field(default_factory=list)  # commands, not paths
    last_verify_ok: bool | None = None
    last_verify_summary: str = ""
    serial_explore_streak: int = 0
    nudges_emitted: list[str] = field(default_factory=list)
    muscle_tier: str = ""
    # Caps (tighter on frontier muscle)
    max_serial_explore: int = 3
    require_verify_after_writes: int = 2
    # Oracle-first
    verify_command: str = ""
    oracle_ok: bool | None = None  # True if command discovered
    auto_verify_ran: bool = False
    project_path: str = ""
    resumed: bool = False
    # Unverified mutation set (paths written since last green verify)
    write_set: list[str] = field(default_factory=list)
    last_error_vector: dict[str, Any] | None = None
    syntax_ok: bool | None = None
    # Block final user-facing "done" until green (machine gate)
    require_green_to_finish: bool = True
    # Convergence / mission binding
    mission_id: str = ""
    auto_verify_cycles: int = 0
    max_auto_verify_cycles: int = 6
    last_scoped_command: str = ""
    oracle_seeded: bool = False
    # Auto-verify cooldown: only re-run after *source* writes past this watermark
    write_steps_at_last_green: int = 0
    # Ship phase (push / gh release)
    ship_required: bool = False
    ship_pushed: bool = False
    ship_released: bool = False
    ship_url: str = ""
    ship_release_url: str = ""
    wasted_auth_probes: int = 0
    last_ship_report: dict[str, Any] = field(default_factory=dict)

    def touch_path(self, path: str) -> None:
        p = (path or "").strip()
        if not p:
            return
        if not _is_filesystem_path(p):
            # Keep shell commands in shell_log only
            if p not in self.shell_log:
                self.shell_log.append(p[:300])
                if len(self.shell_log) > 30:
                    self.shell_log = self.shell_log[-30:]
            return
        if p not in self.paths_touched:
            self.paths_touched.append(p)
            if len(self.paths_touched) > 40:
                self.paths_touched = self.paths_touched[-40:]

    def mark_write(self, path: str) -> None:
        self.touch_path(path)
        p = (path or "").strip()
        if not p or not _is_filesystem_path(p):
            return
        if p not in self.write_set:
            self.write_set.append(p)
            if len(self.write_set) > 40:
                self.write_set = self.write_set[-40:]

    def clear_write_set_on_green(self) -> None:
        if self.last_verify_ok is True:
            self.write_set = []
            self.write_steps_at_last_green = int(self.write_steps or 0)
            self.auto_verify_ran = True
            self.advance_after_green()

    def source_writes_pending(self) -> list[str]:
        return [p for p in (self.write_set or []) if _is_source_path(p)]

    def has_source_writes_since_green(self) -> bool:
        """True when source paths were written after last green watermark."""
        pending = self.source_writes_pending()
        if pending:
            return True
        # Fallback: write_steps grew after green even if path filter missed
        return int(self.write_steps or 0) > int(self.write_steps_at_last_green or 0) and bool(
            self.write_set
        )

    def ship_complete(self) -> bool:
        """Ship goal satisfied: push done; release only if goal asked for it."""
        if not self.ship_required:
            return True
        if not self.ship_pushed:
            return False
        goal = (self.goal or "").lower()
        needs_release = bool(
            re.search(r"\b(release|publish|tag\b|gh\s+release)\b", goal)
        )
        if needs_release and not self.ship_released:
            return False
        return True

    def advance_after_green(self) -> None:
        """After green verify: ship phase if required, else done."""
        if self.ship_required and not self.ship_complete():
            self.phase = "ship"
        else:
            self.phase = "done"

    def ship_report(self) -> dict[str, Any]:
        """End-of-turn observability snapshot for UI/stream."""
        rep = {
            "phase": self.phase,
            "verify_ok": self.last_verify_ok,
            "verify_command": self.verify_command or "",
            "ship_required": self.ship_required,
            "ship_pushed": self.ship_pushed,
            "ship_released": self.ship_released,
            "ship_url": self.ship_url or "",
            "ship_release_url": self.ship_release_url or "",
            "paths": list(self.paths_touched[-12:]),
            "write_set": list(self.write_set[-8:]),
            "goal": (self.goal or "")[:160],
            "wasted_auth_probes": int(self.wasted_auth_probes or 0),
        }
        self.last_ship_report = dict(rep)
        return rep

    def public(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "phase": self.phase,
            "goal": self.goal[:200],
            "explore_steps": self.explore_steps,
            "write_steps": self.write_steps,
            "verify_steps": self.verify_steps,
            "paths": self.paths_touched[-12:],
            "write_set": self.write_set[-12:],
            "last_verify_ok": self.last_verify_ok,
            "serial_explore_streak": self.serial_explore_streak,
            "muscle_tier": self.muscle_tier,
            "verify_command": self.verify_command,
            "oracle_ok": self.oracle_ok,
            "auto_verify_ran": self.auto_verify_ran,
            "resumed": self.resumed,
            "syntax_ok": self.syntax_ok,
            "ship_required": self.ship_required,
            "ship_pushed": self.ship_pushed,
            "ship_released": self.ship_released,
            "ship_url": self.ship_url,
            "ship_release_url": self.ship_release_url,
            "write_steps_at_last_green": self.write_steps_at_last_green,
        }


def looks_like_build_request(message: str) -> bool:
    """True when the user message is a *task* (research → plan → build default)."""
    msg = (message or "").strip()
    if not msg:
        return False
    # Pure social / meta — never force the build engine
    low = msg.lower().rstrip("!.?")
    if low in {
        "hi", "hey", "hello", "thanks", "thank you", "ok", "okay", "yes", "no",
        "yep", "nope", "cool", "bye", "good morning", "good night",
    }:
        return False
    if _BUILD_RE.search(msg):
        return True
    # Long multi-line specs are almost always build work
    if len(msg) > 280 and ("```" in msg or msg.count("\n") >= 4):
        return True
    # Imperative short tasks with path/file cues
    if len(msg) > 24 and any(
        x in low
        for x in (
            ".py",
            ".ts",
            ".js",
            ".rs",
            ".go",
            ".c",
            ".cpp",
            ".h",
            "src/",
            "test",
            "please ",
            "need you to",
            "can you",
            "could you",
            "gcc",
            "compile",
            "file_write",
            "program",
        )
    ):
        return True
    return False


# Alias — product language is research → plan → build (tasks, not only “builds”)
looks_like_task_request = looks_like_build_request


def begin_build_turn(
    runtime: Any,
    message: str,
    *,
    force: bool = False,
) -> BuildTurnState | None:
    """Start machine supervision for task work (research → plan → build)."""
    from remedy.core.muscle_profile import muscle_from_runtime

    muscle = muscle_from_runtime(runtime)
    wants = force or looks_like_build_request(message)
    if not wants:
        # Still enable light supervision if open mission/tasks look like build
        with suppress(Exception):
            brief = getattr(runtime, "_session_brief", None)
            intent = str(getattr(brief, "intent", "") or "")
            if _BUILD_RE.search(intent):
                wants = True
    if not wants:
        return None
    # Tiny muscle: soft supervision only (higher explore tolerance)
    goal_txt = (message or "").strip()[:300]
    st = BuildTurnState(
        active=True,
        phase="scout",
        goal=goal_txt,
        muscle_tier=muscle.label,
        max_serial_explore=2 if muscle.is_frontier else (3 if muscle.is_capable else 5),
        # Partner: always auto-verify after the first write (C hello.c must compile
        # immediately; waiting for 2 writes left simple 1-file tasks unverified).
        require_verify_after_writes=1,
        ship_required=looks_like_ship_goal(goal_txt),
    )
    # Oracle-first: discover verify command up front
    with suppress(Exception):
        from remedy.core.build_oracle import discover_verify_command

        st.verify_command = discover_verify_command(runtime)
        st.oracle_ok = bool(st.verify_command)
    with suppress(Exception):
        st.project_path = str(runtime.effective_project_path() or "")
    # Resume mid-ship from disk ledger
    with suppress(Exception):
        from remedy.core.build_ledger import load_ledger

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        led = load_ledger(st.project_path or None, home=home)
        if led is not None:
            # Always carry oracle + green watermark (even when phase=done)
            if led.verify_command and not st.verify_command:
                st.verify_command = led.verify_command
                st.oracle_ok = True
            if led.last_verify_ok is True:
                st.last_verify_ok = True
                st.auto_verify_ran = True
                st.write_steps_at_last_green = max(
                    int(st.write_steps_at_last_green or 0),
                    int(led.write_steps or 0),
                )
                if led.last_verify_summary:
                    st.last_verify_summary = led.last_verify_summary
        if led is not None and (
            led.phase not in ("done",)
            or (led.last_verify_ok is not True and led.write_steps > 0)
        ):
            st.resumed = True
            if led.phase and led.phase != "done":
                st.phase = led.phase
            if led.verify_command and not st.verify_command:
                st.verify_command = led.verify_command
                st.oracle_ok = True
            # Paths only (drop shell noise from older ledgers)
            st.paths_touched = [
                p for p in list(led.paths_touched or []) if _is_filesystem_path(str(p))
            ][-40:]
            # Do NOT import historical write_steps into a fresh turn — that
            # re-triggers auto-verify thrash on "continue"/"proceed" after green.
            if led.last_verify_ok is not True:
                st.write_steps = max(st.write_steps, int(led.write_steps or 0))
                st.verify_steps = max(st.verify_steps, int(led.verify_steps or 0))
            if led.goal and (not st.goal or len(st.goal) < 8):
                st.goal = led.goal[:300]
            if led.last_verify_ok is False:
                st.last_verify_ok = False
                st.phase = "repair"
            if led.last_verify_summary:
                st.last_verify_summary = led.last_verify_summary
    with suppress(Exception):
        runtime._build_turn = st
    # Durable mission bound to this build (goal + verify stickiness)
    with suppress(Exception):
        from remedy.core.build_mission import ensure_build_mission
        from remedy.core.turn_context import turn_session_id

        ensure_build_mission(
            runtime, st, session_id=str(turn_session_id(runtime) or "")
        )
    # Persist start
    with suppress(Exception):
        from remedy.core.build_ledger import merge_turn_into_ledger
        from remedy.core.turn_context import turn_session_id

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        merge_turn_into_ledger(
            st,
            project_path=st.project_path,
            session_id=str(turn_session_id(runtime) or ""),
            home=home,
        )
    return st


def build_protocol_block(state: BuildTurnState) -> str:
    """Hard system block injected at turn start for build supervision."""
    oracle = (
        f"Oracle verify_command=`{state.verify_command}`"
        if state.verify_command
        else "Oracle: NO verify command yet (fail closed until tests exist)"
    )
    resume = " [RESUMED from build ledger]" if state.resumed else ""
    ship = ""
    if state.ship_required:
        ship = (
            "\n4) SHIP (required for this goal): after green verify use "
            "git_status → git_push → gh_release (if release/tag requested). "
            "Do not rewrite green source just to re-run pytest. "
            "Temp helper scripts → `.remedy-build/tmp/` only. "
            "Prefer run_python_file over python -c blobs."
        )
    return (
        "[Task loop — RESEARCH → PLAN → BUILD · hard rule]"
        f"{resume}\n"
        f"Goal: {state.goal or '(user request)'}\n"
        f"{oracle}\n"
        "Default schedule (do not skip to monologue):\n"
        "1) RESEARCH (scout): batch file_read/list_dir/repo_search/memory_search "
        "in ONE step (4–12). Gather facts before inventing.\n"
        "2) PLAN: short checklist via open tasks / mission_start — machine-side, "
        "not a long essay unless the user asked plan-only.\n"
        "3) BUILD (implement): file_write for new files, file_edit multi-hunk for "
        "changes; then VERIFY (bash_exec / job_run kind=verify / mission_verify); "
        "REPAIR until green → DONE."
        f"{ship}\n"
        "Machine path: build_tdd / build_compile_spec → hops → build_gate_tower → "
        "build_mutant_score. Never claim shipped without a verify signal. "
        "Never monologue a plan without tool_calls. "
        "Never explore one file per step. "
        f"Serial explore cap before forced implement: {state.max_serial_explore}. "
        f"Writes before auto-verify: {state.require_verify_after_writes}."
    )


def _tool_name(tc: dict[str, Any]) -> str:
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    return str((fn or {}).get("name") or tc.get("name") or "").strip().lower()


def _args_path(tc: dict[str, Any]) -> str:
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    raw = (fn or {}).get("arguments") or "{}"
    try:
        import json

        obj = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if isinstance(obj, dict):
            for k in ("path", "file", "filepath", "target", "workdir"):
                if obj.get(k):
                    return str(obj[k])[:240]
            cmd = str(obj.get("command") or "")
            if cmd:
                return cmd[:120]
    except Exception:
        pass
    return ""


def observe_tool_batch(
    state: BuildTurnState,
    tool_calls: list[dict[str, Any]] | None,
    tool_messages: list[dict[str, Any]] | None = None,
) -> None:
    """Update phase counters from a completed tool batch."""
    if not state.active:
        return
    tcs = [t for t in (tool_calls or []) if isinstance(t, dict)]
    names = [_tool_name(t) for t in tcs]
    if not names:
        return

    only_explore = bool(names) and all(n in _EXPLORE_TOOLS for n in names)
    any_write = any(n in _WRITE_TOOLS for n in names)
    any_ship_tool = any(n in _SHIP_TOOLS for n in names)
    # bash_exec/job_run count as verify when args look like tests
    any_verify = False
    any_ship_cmd = False
    for tc in tcs:
        n = _tool_name(tc)
        if n in ("mission_verify",):
            any_verify = True
        elif n in ("bash_exec", "shell_exec", "job_run"):
            blob = (
                _args_path(tc)
                + " "
                + str((tc.get("function") or {}).get("arguments") or "")
            ).lower()
            if any(
                k in blob
                for k in (
                    "pytest",
                    "npm test",
                    "cargo test",
                    "go test",
                    "kind=verify",
                    'kind": "verify',
                    "unittest",
                    "vitest",
                    "jest",
                )
            ):
                any_verify = True
            elif n == "job_run" and "verify" in blob:
                any_verify = True
            if _SHIP_CMD_HINT.search(blob):
                any_ship_cmd = True
        elif n in _SHIP_TOOLS:
            any_ship_cmd = True

    if only_explore and len(names) == 1:
        state.serial_explore_streak += 1
        state.explore_steps += 1
    elif only_explore:
        state.serial_explore_streak = 0
        state.explore_steps += 1
    else:
        state.serial_explore_streak = 0

    source_write_this_batch = False
    if any_write:
        state.write_steps += 1
        if state.phase in ("scout",):
            state.phase = "implement"
    if any_verify:
        state.verify_steps += 1
        state.phase = "verify"

    for tc in tcs:
        p = _args_path(tc)
        if p:
            state.touch_path(p)
            if _tool_name(tc) in _WRITE_TOOLS:
                state.mark_write(p)
                if _is_source_path(p):
                    source_write_this_batch = True

    # Only *source* mutations invalidate green (docs/tmp/yml thrash is ignored)
    if source_write_this_batch and state.auto_verify_ran and state.last_verify_ok is True:
        state.auto_verify_ran = False
        state.last_verify_ok = None

    # Infer ship outcomes from dedicated tools / command results
    for msg in tool_messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        content = str(msg.get("content") or "")
        if not content:
            continue
        low = content[:1200].lower()
        # Auth thrash detection
        if any(
            x in low
            for x in (
                "authentication failed",
                "could not read username",
                "permission denied (publickey)",
                "gh auth login",
                "not logged into",
            )
        ):
            state.wasted_auth_probes += 1
        if any_ship_tool or any_ship_cmd:
            if "pushed" in low or "everything up-to-date" in low or re.search(
                r"\bto\s+github\.com[:/]", low
            ):
                if "error" not in low[:80] and "denied" not in low:
                    state.ship_pushed = True
                    state.phase = "ship"
            if "release" in low and (
                "created" in low or "https://github.com/" in low or "url:" in low
            ):
                if "error" not in low[:80]:
                    state.ship_released = True
                    m = re.search(r"https://github\.com/[^\s\)\"']+", content)
                    if m:
                        state.ship_release_url = m.group(0)[:300]
            if "git_push ok" in low or "ship_pushed=true" in low:
                state.ship_pushed = True
            if "gh_release ok" in low or "ship_released=true" in low:
                state.ship_released = True
            url_m = re.search(r"(?i)(?:remote|push|url)[=:\s]+(https?://\S+)", content)
            if url_m and not state.ship_url:
                state.ship_url = url_m.group(1)[:300]
            if state.ship_complete() and state.last_verify_ok is True:
                state.phase = "done"

    # Infer verify outcome from tool results
    for msg in tool_messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        content = str(msg.get("content") or "")
        if not content:
            continue
        low = content[:800].lower()
        if "exit_code=0" in low or re.search(r"\bpassed\b", low) and "failed" not in low:
            # Don't treat ship tool chatter as verify green
            if any_ship_tool and not any_verify and "passed" not in low:
                continue
            state.last_verify_ok = True
            state.last_verify_summary = content[:2000]
            state.clear_write_set_on_green()
        elif (
            "exit_code=" in low
            and "exit_code=0" not in low
            or "FAILED" in content
            or "Error" in content[:40]
        ):
            if any_verify or any_write:
                state.last_verify_ok = False
                state.phase = "repair"
                state.repair_steps += 1
                state.last_verify_summary = content[:2000]
        if _VERIFY_HINT.search(content):
            if "fail" in low or "error" in low:
                state.last_verify_ok = False
                if state.phase not in ("done", "ship"):
                    state.phase = "repair"
            elif "pass" in low or "exit_code=0" in low:
                state.last_verify_ok = True
                state.clear_write_set_on_green()


def next_machine_nudge(state: BuildTurnState) -> dict[str, str] | None:
    """Return a hard user-role inject if the machine schedule is violated."""
    if not state.active:
        return None

    # Explore thrash → force implement
    if (
        state.serial_explore_streak >= state.max_serial_explore
        and state.write_steps == 0
        and "force_implement" not in state.nudges_emitted
    ):
        state.nudges_emitted.append("force_implement")
        state.phase = "implement"
        return {
            "role": "user",
            "content": (
                "[Build engine · FORCE IMPLEMENT] Serial explore streak exceeded. "
                "STOP single-file scouting. In the NEXT step emit tool_calls that "
                "CHANGE the tree: file_write and/or file_edit (multi-hunk). "
                "Batch remaining reads only if needed for the edit. No plan monologue."
            ),
        }

    # Oracle missing after writes — fail closed
    if (
        state.write_steps >= 1
        and not state.verify_command
        and state.oracle_ok is False
        and "oracle_missing" not in state.nudges_emitted
    ):
        state.nudges_emitted.append("oracle_missing")
        from remedy.core.build_oracle import oracle_missing_nudge

        return oracle_missing_nudge(state)

    # Writes without verify (model path — auto-verify may also run)
    if (
        state.write_steps >= state.require_verify_after_writes
        and state.verify_steps == 0
        and not state.auto_verify_ran
        and "force_verify" not in state.nudges_emitted
    ):
        state.nudges_emitted.append("force_verify")
        state.phase = "verify"
        vhint = (
            f" Prefer: `{state.verify_command}`."
            if state.verify_command
            else " Discover or create a test command first."
        )
        return {
            "role": "user",
            "content": (
                "[Build engine · FORCE VERIFY] Code was written but not verified. "
                "Run tests now: bash_exec / job_run kind=verify / mission_verify."
                f"{vhint} Do not claim done until exit_code=0."
            ),
        }

    # Repair loop
    if (
        state.last_verify_ok is False
        and state.repair_steps >= 1
        and "force_repair" not in state.nudges_emitted
    ):
        state.nudges_emitted.append("force_repair")
        return {
            "role": "user",
            "content": (
                "[Build engine · REPAIR] Verify failed. Read the error (path:line), "
                "file_edit the failing units, re-run the SAME verify command. "
                "Do not expand scope. Do not summarize failure as success."
            ),
        }

    # Too much explore even after multi-batch scouting with no writes
    if (
        state.explore_steps >= state.max_serial_explore + 2
        and state.write_steps == 0
        and "force_implement2" not in state.nudges_emitted
    ):
        state.nudges_emitted.append("force_implement2")
        return {
            "role": "user",
            "content": (
                "[Build engine · IMPLEMENT NOW] Enough context. Write the code. "
                "file_write / file_edit in this step. Verification follows."
            ),
        }

    return None


def monologue_block_nudge(state: BuildTurnState | None) -> dict[str, str] | None:
    """When build is active and model monologued without tools.

    Allow up to 3 blocks — local 7B often re-essays after a single nudge.
    """
    if state is None or not state.active:
        return None
    n = sum(1 for x in state.nudges_emitted if str(x).startswith("monologue_block"))
    if n >= 3:
        return None
    state.nudges_emitted.append(f"monologue_block{n + 1}" if n else "monologue_block")
    return {
        "role": "user",
        "content": (
            "[Task loop · NO MONOLOGUE] Stop writing plans in chat. "
            "Emit native tool_calls **now** (list_dir / file_read batch, then "
            "file_write / file_edit). Do not answer with only a RESEARCH/PLAN/BUILD essay."
        ),
    }


def get_build_state(runtime: Any) -> BuildTurnState | None:
    st = getattr(runtime, "_build_turn", None)
    return st if isinstance(st, BuildTurnState) else None


def should_force_tools_for_build(runtime: Any, message: str) -> bool:
    """True when L1 must not strip tools (build request or active build turn)."""
    st = get_build_state(runtime)
    if st is not None and st.active:
        return True
    if looks_like_build_request(message):
        return True
    with suppress(Exception):
        from remedy.core.muscle_profile import muscle_from_runtime

        m = muscle_from_runtime(runtime)
        if m.builder_contract and looks_like_build_request(message):
            return True
    return False


def build_blocks_final_answer(state: BuildTurnState | None) -> bool:
    """True when machine must refuse a done/final answer (no green verify yet)."""
    if state is None or not state.active:
        return False
    if not state.require_green_to_finish:
        return False
    # Never started writing — monologue block handles; allow chat abandon
    if state.write_steps == 0 and state.verify_steps == 0 and not state.ship_required:
        return False
    # C/C++ sources still in write_set → must compile+run, not stop on chat
    writes = [str(w).lower() for w in (state.write_set or [])]
    has_c = any(w.endswith((".c", ".cpp", ".cc", ".cxx")) for w in writes)
    vcmd = str(state.verify_command or "").lower()
    if has_c and (
        state.last_verify_ok is not True
        or not vcmd
        or "pytest" in vcmd  # never accept python smoke as C green
        or ("gcc" not in vcmd and "clang" not in vcmd)
    ):
        return True
    # Ship goals: green tests alone are not DONE
    if state.ship_required and state.last_verify_ok is True and not state.ship_complete():
        return True
    if state.last_verify_ok is True and not state.write_set and state.ship_complete():
        return False
    # Wrote code or failed verify → cannot finish without green
    if state.write_steps > 0 and state.last_verify_ok is not True:
        return True
    if state.source_writes_pending() and state.last_verify_ok is not True:
        return True
    if state.syntax_ok is False:
        return True
    return False


def unfinished_green_gate_message(state: BuildTurnState) -> dict[str, str]:
    """Injected when model tries to finalize without green verify / ship."""
    ws = ", ".join(state.write_set[-8:]) if state.write_set else "(write set empty)"
    writes = [str(w).lower() for w in (state.write_set or [])]
    has_c = any(w.endswith((".c", ".cpp", ".cc", ".cxx")) for w in writes)
    # Ship incomplete after green
    if state.ship_required and state.last_verify_ok is True and not state.ship_complete():
        return {
            "role": "user",
            "content": (
                "[Build engine · SHIP GATE · refuse DONE]\n"
                "Verify is green but the goal requires ship (push/release).\n"
                f"ship_pushed={state.ship_pushed} ship_released={state.ship_released}\n"
                "Next tools only (do NOT re-run pytest unless you changed source):\n"
                "1) git_status  2) git_push  3) gh_release if tag/release was requested\n"
                "Refactor-only: do not rewrite green code to thrash auto-verify."
            ),
        }
    if has_c:
        cmd = state.verify_command or "gcc -o hello.exe hello.c && hello.exe"
        extra = (
            "This is a **C** task: bash_exec the gcc compile, then run the exe. "
            "Do not stop after file_write alone. Do not use pytest."
        )
    else:
        cmd = state.verify_command or "pytest -q / npm test"
        extra = "If verify is impossible, create a minimal test first (oracle-first)."
    return {
        "role": "user",
        "content": (
            "[Build engine · GREEN GATE · refuse DONE]\n"
            "You attempted a final answer without a green verify. Machine blocks that.\n"
            f"write_set: {ws}\n"
            f"last_verify_ok={state.last_verify_ok} phase={state.phase}\n"
            f"Run `{cmd}` (or job_run kind=verify) until exit_code=0, then summarize.\n"
            f"{extra}"
        ),
    }


def green_continue_message(state: BuildTurnState, *, command: str = "") -> dict[str, str]:
    """After machine green: short summary OR continue ship if required."""
    cmd = command or state.verify_command or ""
    if state.ship_required and not state.ship_complete():
        return {
            "role": "user",
            "content": (
                "[Build engine · GREEN · continue SHIP]\n"
                f"Machine verify passed: `{cmd}`.\n"
                "Do **not** re-run tests. Do **not** rewrite source.\n"
                "Ship now with tools:\n"
                "• git_status (confirm branch/dirty)\n"
                "• git_push (origin)\n"
                "• gh_release if the goal asked for a release/tag\n"
                "Temp scripts only under `.remedy-build/tmp/`. "
                "Prefer run_python_file for one-off helpers."
            ),
        }
    return {
        "role": "user",
        "content": (
            "[Build engine · GREEN · stop building]\n"
            f"Machine verify passed: `{cmd}`.\n"
            "Reply with a **short** user summary only (≤6 lines):\n"
            "• files written\n"
            "• verify command + result\n"
            "• anything still open\n"
            "Do **not** call tools. Do **not** repeat the summary. "
            "Do **not** claim work you did not do — the machine ran verify."
        ),
    }


def format_ship_report_line(state: BuildTurnState | None) -> str:
    """Stream observability line: @@ship_report:{json}"""
    if state is None:
        return ""
    import json

    rep = state.ship_report()
    try:
        body = json.dumps(rep, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        body = str(rep)
    return f"@@ship_report:{body}\n"


def frontier_continue_inject(
    runtime: Any,
    message: str,
) -> dict[str, str] | None:
    """Brief + ledger inject for frontier 'continue' turns (no local harness thrash)."""
    from remedy.core.local_agent_optimize import (
        is_frontier_binding,
        message_wants_continue_work,
    )

    if not message_wants_continue_work(message):
        return None
    prov = str(getattr(runtime, "_llm_provider", "") or "")
    model = str(getattr(runtime, "_llm_model", "") or "")
    base = str(getattr(runtime, "_llm_base_url", "") or "")
    with suppress(Exception):
        bind = getattr(runtime, "_llm_binding", None)
        if bind is not None:
            prov = str(getattr(bind, "provider", None) or prov)
            model = str(getattr(bind, "model", None) or model)
            base = str(getattr(bind, "base_url", None) or base)
    if not is_frontier_binding(prov, model, base):
        return None
    parts: list[str] = [
        "[Frontier continue — brief + ledger; tools over monologue]",
    ]
    with suppress(Exception):
        brief = getattr(runtime, "_session_brief", None)
        if brief is not None:
            intent = str(getattr(brief, "intent", "") or "").strip()
            if intent:
                parts.append(f"Intent: {intent[:220]}")
            for label, attr in (
                ("Open", "open_tasks"),
                ("Next", "next_steps"),
            ):
                items = [
                    str(t).strip()
                    for t in (getattr(brief, attr, None) or [])
                    if str(t).strip()
                ][:6]
                if items:
                    parts.append(f"{label}: " + "; ".join(x[:100] for x in items))
    with suppress(Exception):
        from remedy.core.build_ledger import resume_hint

        proj = str(runtime.effective_project_path() or "")
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        hint = resume_hint(proj or None, home=home)
        if hint:
            parts.append(hint)
    st = get_build_state(runtime)
    if st is not None and st.active:
        parts.append(
            f"Live build: phase={st.phase} verify_ok={st.last_verify_ok} "
            f"ship_required={st.ship_required} ship_pushed={st.ship_pushed} "
            f"ship_released={st.ship_released}"
        )
        if st.ship_required and not st.ship_complete():
            parts.append(
                "Priority: finish ship (git_push / gh_release) — tests already green."
            )
    if len(parts) <= 1:
        return None
    return {"role": "user", "content": "\n".join(parts)}
