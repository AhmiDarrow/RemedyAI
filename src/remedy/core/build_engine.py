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
    r"sweep|bugsweep|bugfix|bug-hunt|hotfix|triage|cleanup|dogfood|"
    r"migrate|upgrade|replace|prototype|design\s+(the\s+)?(system|api|feature)|"
    r"calculator|todo\s+app|cli\b|"
    # Simple C / compile tasks (partner e2e)
    r"compile|gcc|clang|\.c\b|hello\.c|main\.c|"
    r"pygame|play\s+(it|the\s+game)|try\s+it|"
    r"we need (a |an |to )|"
    r"landing\s+page|web\s*page|product\s+page|marketing\s+page|"
    r"launch\s+(the\s+)?(site|server|app|page|preview)|"
    r"serve\s+(it\s+)?locally|start\s+(the\s+)?(dev\s+)?server|"
    r"preview\s+(the\s+)?(site|page)|"
    r"can we (add|resize|change|shrink|tighten)|"
    r"resize|autolock|auto[- ]?lock|"
    r"settings (and |/ )?(about )?(ui|dialog|panel|window)"
    r")\b"
)

# Source mutations only — shell is classified as verify when it looks like tests
_WRITE_TOOLS = frozenset(
    {
        "file_write",
        "file_edit",
        "file_edit_batch",
        "apply_patch",
        "build_unit_hop",
        "build_drive",
        "build_parallel",
    }
)
_EXPLORE_TOOLS = frozenset(
    {
        "file_read",
        "list_dir",
        "repo_search",
        "file_glob",
        "memory_search",
        "soul_recall",
    }
)
_VERIFY_TOOLS = frozenset(
    {"bash_exec", "shell_exec", "job_run", "mission_verify", "mission_update"}
)
_SHIP_TOOLS = frozenset(
    {
        "git_push",
        "gh_release",
        "ship_status",
        "ship_push",
        "ship_release",
    }
)
_VERIFY_HINT = re.compile(
    r"(?i)\b(pytest|npm\s+test|cargo\s+test|go\s+test|unittest|vitest|jest|"
    r"gcc|clang|cl\.exe|rustc|cargo\s+(?:build|run|check)|go\s+(?:build|run)|"
    r"dotnet\s+(?:build|test|run)|npx\s+tsc|tsc\b|"
    r"exit_code=0|passed|FAILED|ERROR|tests?\s+passed|ok\s+\d+\s+passed)\b"
)
_SHIP_CMD_HINT = re.compile(
    r"(?i)\b(git\s+push|gh\s+release|gh\s+pr\s+create|git\s+tag\b)\b"
)

# Real compile/test — not `cat hello.c` / `gcc --version` / filename chatter.
_VERIFY_CMD_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"\bpytest\b|"
    r"\bnpm\s+test\b|"
    r"\bcargo\s+(?:test|build|run|check)\b|"
    r"\bgo\s+(?:test|build|run)\b|"
    r"\bkind\s*=\s*verify\b|"
    r"\bunittest\b|\bvitest\b|\bjest\b|"
    r"\b(?:gcc|g\+\+|clang|clang\+\+)\b(?![^\n]*--version)[^\n]*\.(?:c|cpp|cc|cxx|h)\b|"
    r"\brustc\b[^\n]*\.(?:rs)\b|"
    r"\bcl\.exe\b|"
    r"\bdotnet\s+(?:test|build|run)\b|"
    r"\bpython\s+-m\s+(?:pytest|unittest|py_compile)\b|"
    r"\buv\s+run\s+pytest\b"
    r")"
)
_NOT_VERIFY_CMD_RE = re.compile(
    r"(?ix)^\s*(?:cat|type|get-content|more|less|dir|ls|head|tail)\b"
)


def _blob_is_verify_command(blob: str) -> bool:
    s = blob or ""
    if _NOT_VERIFY_CMD_RE.search(s.strip()):
        return False
    return bool(_VERIFY_CMD_RE.search(s))

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
        ".html",
        ".htm",
        ".css",
    }
)

# Filenames the goal explicitly asked to create (must exist before done).
_NAMED_FILE_RE = re.compile(
    r"(?i)(?<![.\w])((?:[\w.-]+[/\\])*[\w.-]+\.(?:html?|css|js|tsx?|jsx?|py|md|json))\b"
)
_HTML_PAGE_GOAL_RE = re.compile(
    r"(?i)\b(?:landing\s+page|web\s*page|html\s+page|product\s+page|"
    r"marketing\s+page|(?:create|add|build|write)\s+(?:a\s+|an\s+|the\s+)?wiki)\b"
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
    return bool(re.search(r"\.[A-Za-z0-9]{1,8}$", p))


def _is_build_meta_path(path: str) -> bool:
    """Ledger / todos / tmp helpers are not product writes."""
    p = (path or "").strip().lower().replace("\\", "/")
    if not p:
        return False
    norm = p if p.startswith("/") else "/" + p
    if "/.remedy-build/" in norm or norm.endswith("/.remedy-build"):
        return True
    name = p.rsplit("/", 1)[-1]
    return name in {"ledger.json", "todos.json"}


def _is_source_path(path: str) -> bool:
    p = (path or "").strip().lower().replace("\\", "/")
    if not _is_filesystem_path(p):
        return False
    # Ignore tmp / probe scripts / ledger
    if _is_build_meta_path(p) or p.startswith("_") or "/_dump" in p:
        return False
    return any(p.endswith(suf) for suf in _SOURCE_WRITE_SUFFIXES)


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
    required_files: list[str] = field(default_factory=list)
    empty_write_paths: list[str] = field(default_factory=list)
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
    # Machine-owned drive (implement / repair hops without waiting on the model)
    auto_drive_ran: bool = False
    auto_repair_cycles: int = 0
    max_auto_repair_cycles: int = 3
    last_drive: dict[str, Any] = field(default_factory=dict)
    review_fix_ran: bool = False
    visual_observe_ran: bool = False
    away_mode: bool = False
    machine_injects: int = 0
    open_todo_count: int = 0
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
        if _is_build_meta_path(p):
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

    def named_required_files(self) -> list[str]:
        """Filenames the goal / empty-write failures still demand on disk."""
        found: list[str] = []
        seen: set[str] = set()

        def _add(raw: str) -> None:
            key = (raw or "").replace("\\", "/").lstrip("./").strip()
            if not key or key in seen:
                return
            if "." not in key.split("/")[-1]:
                return
            seen.add(key)
            found.append(key)

        for m in _NAMED_FILE_RE.finditer(self.goal or ""):
            _add(m.group(1))
        for p in self.required_files:
            _add(p)
        for p in self.empty_write_paths:
            _add(p)
        return found

    def _write_set_has(self, name: str) -> bool:
        low = name.replace("\\", "/").lower()
        for w in self.write_set or []:
            wl = str(w).replace("\\", "/").lower()
            if wl.endswith("/" + low) or wl.endswith(low) or low in wl:
                return True
        return False

    def missing_required_files(self) -> list[str]:
        """Required goal files that were never successfully written."""
        missing: list[str] = []
        root = None
        raw = (self.project_path or "").strip()
        if raw:
            from pathlib import Path as _P

            with suppress(Exception):
                root = _P(raw)

        def _on_disk(name: str) -> bool:
            if root is None:
                return False
            try:
                cand = root / name
                return cand.is_file() and cand.stat().st_size > 8
            except OSError:
                return False

        for name in self.named_required_files():
            if self._write_set_has(name) or _on_disk(name):
                continue
            missing.append(name)
        if _HTML_PAGE_GOAL_RE.search(self.goal or ""):
            html_ok = any(
                str(w).replace("\\", "/").lower().endswith((".html", ".htm"))
                for w in (self.write_set or [])
            ) or any(n.lower().endswith((".html", ".htm")) and _on_disk(n) for n in self.named_required_files())
            if not html_ok and root is not None:
                with suppress(Exception):
                    html_ok = any(
                        h.is_file() and h.stat().st_size > 0
                        for h in list(root.glob("*.html")) + list(root.glob("*.htm"))
                    )
            if not html_ok:
                named_html = [n for n in self.named_required_files() if n.lower().endswith((".html", ".htm"))]
                if named_html:
                    for n in named_html:
                        if n not in missing:
                            missing.append(n)
                elif not html_ok:
                    missing.append("index.html")
        return missing

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
        return not (needs_release and not self.ship_released)

    def advance_after_green(self) -> None:
        """After green verify: ship phase if required, else done."""
        if self.missing_required_files():
            self.phase = "implement"
            return
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
            "auto_drive_ran": self.auto_drive_ran,
            "auto_repair_cycles": self.auto_repair_cycles,
            "open_todo_count": self.open_todo_count,
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
    return bool(len(msg) > 24 and any(x in low for x in (".py", ".ts", ".js", ".rs", ".go", ".c", ".cpp", ".h", "src/", "test", "please ", "need you to", "can you", "could you", "gcc", "compile", "file_write", "program")))


# Alias — product language is research → plan → build (tasks, not only “builds”)
looks_like_task_request = looks_like_build_request


def begin_build_turn(
    runtime: Any,
    message: str,
    *,
    force: bool = False,
) -> BuildTurnState | None:
    """Start machine supervision for task work (research → plan → build)."""
    if not force:
        try:
            from remedy.core.turn_context import current_plan_mode

            if current_plan_mode(runtime):
                return None
        except Exception:
            pass
    from remedy.core.muscle_profile import muscle_from_runtime

    muscle = muscle_from_runtime(runtime)
    away = False
    with suppress(Exception):
        from remedy.core.away_mode import looks_like_away_request

        away = looks_like_away_request(message)
    wants = force or looks_like_build_request(message) or away
    if not force:
        with suppress(Exception):
            from remedy.core.react_policy import is_chat_only_message

            if is_chat_only_message(message):
                return None
    if not wants:
        # Still enable light supervision if open mission/tasks look like build
        with suppress(Exception):
            brief = getattr(runtime, "_session_brief", None)
            intent = str(getattr(brief, "intent", "") or "")
            if _BUILD_RE.search(intent):
                wants = True
    if not wants:
        # Leftover open Build still drives the host this turn (no Ask pause).
        enable_build_host_drive(runtime)
        return None
    # Tiny muscle: soft supervision only (higher explore tolerance)
    goal_txt = (message or "").strip()[:300]
    html_or_serve = bool(
        _HTML_PAGE_GOAL_RE.search(goal_txt)
        or re.search(
            r"(?i)\b(launch|serve|preview|localhost|index\.html|landing\s+page)\b",
            goal_txt,
        )
    )
    explore_cap = 1 if away else (2 if muscle.is_frontier else (3 if muscle.is_capable else 5))
    if html_or_serve:
        explore_cap = 1
    st = BuildTurnState(
        active=True,
        phase="scout",
        goal=goal_txt,
        muscle_tier=muscle.label,
        max_serial_explore=explore_cap,
        # Partner: always auto-verify after the first write (C hello.c must compile
        # immediately; waiting for 2 writes left simple 1-file tasks unverified).
        require_verify_after_writes=1,
        ship_required=looks_like_ship_goal(goal_txt),
        away_mode=away,
        required_files=[
            m.group(1).replace("\\", "/")
            for m in _NAMED_FILE_RE.finditer(goal_txt)
        ],
    )
    # Oracle-first: discover verify command up front
    with suppress(Exception):
        from remedy.core.build_oracle import discover_verify_command

        st.verify_command = discover_verify_command(runtime)
        st.oracle_ok = bool(st.verify_command)
    with suppress(Exception):
        st.project_path = str(runtime.effective_project_path() or "")
    # Resume mid-ship from disk ledger — only when a real project is bound.
    # Unbound/home-dir runtimes (incl. unit-test fakes) must NOT inherit a
    # stale cross-session ledger: that re-arms auto-verify with a real
    # subprocess (e.g. `pytest -q`) on a bare runtime and hangs the turn.
    _bound = False
    with suppress(Exception):
        from remedy.core.workspace import is_unset_project_path as _unset_pp

        raw = None
        with suppress(Exception):
            from remedy.core.turn_context import current_turn_workspace

            ws = current_turn_workspace()
            if ws is not None:
                raw = getattr(ws, "project_raw", None)
        if raw is None:
            raw = getattr(runtime, "_project_path_raw", None)
        _bound = not _unset_pp(raw)
    with suppress(Exception):
        from remedy.core.build_ledger import load_ledger

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        led = load_ledger(st.project_path or None, home=home) if _bound else None
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
            if led.last_verify_summary:
                st.last_verify_summary = led.last_verify_summary
            timed_out = "timed out" in (led.last_verify_summary or "").lower()
            if led.last_verify_ok is False:
                st.last_verify_ok = False
                if timed_out:
                    # Do not immediately re-run the command that just hung the turn.
                    st.auto_verify_ran = True
                    st.last_scoped_command = ""
                    st.write_steps_at_last_green = int(st.write_steps or 0)
                    st.phase = "implement" if st.missing_required_files() else "verify"
                else:
                    st.phase = "repair"
            # Restore body: last fail + unverified writes so the next wake acts
            if led.last_verify_ok is not True:
                if isinstance(led.last_error_vector, dict) and led.last_error_vector:
                    if not timed_out:
                        st.last_error_vector = dict(led.last_error_vector)
                if led.last_scoped_command and not timed_out:
                    sc = str(led.last_scoped_command)
                    if "--lf" not in sc:
                        st.last_scoped_command = sc
                writes = [
                    p
                    for p in list(led.write_set or [])
                    if _is_filesystem_path(str(p)) and not _is_build_meta_path(str(p))
                ]
                if writes:
                    st.write_set = writes[-40:]
    with suppress(Exception):
        runtime._build_turn = st
    with suppress(Exception):
        key = _session_build_key(runtime)
        m = _build_turns_map(runtime)
        if m is None:
            m = {}
            runtime._build_turns = m
        m[key] = st
        if len(m) > 48:
            for old in list(m.keys())[: len(m) - 48]:
                if old != key:
                    m.pop(old, None)
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
    enable_build_host_drive(runtime, st)
    with suppress(Exception):
        from remedy.core.build_todos import sync_todos_with_build

        sync_todos_with_build(runtime, st)
    return st


def enable_build_host_drive(
    runtime: Any = None,
    state: BuildTurnState | None = None,
) -> None:
    """Active Build drives the host this turn — no Ask pause.

    Write jail and auth-secret blocks stay on. Settings approval_mode is
    not changed. Plan mode / greetings never call this.
    """
    st = state
    if st is None and runtime is not None:
        st = get_build_state(runtime)
    if st is None or not getattr(st, "active", False):
        return
    if str(getattr(st, "phase", "") or "") == "done" and not st.missing_required_files():
        return
    with suppress(Exception):
        from remedy.core.turn_context import set_turn_skip_ask

        set_turn_skip_ask(True, runtime)


def enable_work_host_drive(
    runtime: Any = None,
    message: str = "",
    *,
    plan_mode: bool = False,
    build_state: BuildTurnState | None = None,
) -> None:
    """Any work turn drives the host — no Ask pause.

    Greetings, verbal trivia, Plan mode, and untrusted scope stay Ask.
    Jail and auth-secret blocks stay on. Settings approval_mode is not changed.
    """
    if plan_mode:
        return
    msg = message or ""
    with suppress(Exception):
        from remedy.core.react_policy import is_chat_only_message, is_pure_trivia_message

        if is_chat_only_message(msg) or is_pure_trivia_message(msg):
            return
    enable_build_host_drive(runtime, build_state)
    with suppress(Exception):
        from remedy.core.turn_context import set_turn_skip_ask

        set_turn_skip_ask(True, runtime)


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
        "REPAIR until green → DONE. Static HTML: files on disk is verify — do not "
        "run pytest. To show the page: host_run `python -m http.server` in the "
        "project folder, then computer_navigate http://127.0.0.1:8000/ ."
        f"{ship}\n"
        "YOU DRIVE THIS PC this turn (Ask is skipped; jail/auth/Plan stay). "
        "Do not call help_list or goal_add. Do not ask permission. "
        "Use file_read / file_write / host_run / bash_exec now. "
        "Machine loop: todo_write a short checklist, then write and verify. "
        "The machine will force implement after scout and auto-verify after writes. "
        "Never monologue a plan without tool_calls. Never explore one file per step. "
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
    verify_ids: set[str] = set()
    ship_ids: set[str] = set()
    for tc in tcs:
        n = _tool_name(tc)
        tid = str(tc.get("id") or tc.get("tool_call_id") or "")
        if n in ("mission_verify",):
            any_verify = True
            if tid:
                verify_ids.add(tid)
        elif n in ("bash_exec", "shell_exec", "job_run"):
            blob = (
                _args_path(tc)
                + " "
                + str((tc.get("function") or {}).get("arguments") or "")
            ).lower()
            if _blob_is_verify_command(blob):
                any_verify = True
                if tid:
                    verify_ids.add(tid)
            elif n == "job_run" and "verify" in blob:
                any_verify = True
                if tid:
                    verify_ids.add(tid)
            if _SHIP_CMD_HINT.search(blob):
                any_ship_cmd = True
                if tid:
                    ship_ids.add(tid)
        elif n in _SHIP_TOOLS:
            any_ship_cmd = True
            if tid:
                ship_ids.add(tid)

    if only_explore and len(names) == 1:
        state.serial_explore_streak += 1
        state.explore_steps += 1
    elif only_explore:
        state.serial_explore_streak = 0
        state.explore_steps += 1
    else:
        state.serial_explore_streak = 0

    empty_write_ids: set[str] = set()
    for msg in tool_messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        content = str(msg.get("content") or "")
        if (
            "EMPTY_SOURCE_WRITE" in content
            or "empty file_write" in content
            or "SPAM_SOURCE_WRITE" in content
        ):
            cid = str(msg.get("tool_call_id") or msg.get("id") or "")
            if cid:
                empty_write_ids.add(cid)
    successful_write = False
    for tc in tcs:
        if _tool_name(tc) not in _WRITE_TOOLS:
            continue
        tid = str(tc.get("id") or tc.get("tool_call_id") or "")
        if tid and tid in empty_write_ids:
            p = _args_path(tc)
            if p:
                state.empty_write_paths.append(p)
                if p not in state.required_files:
                    state.required_files.append(p)
            continue
        successful_write = True
    if any_write and not successful_write:
        any_write = False

    source_write_this_batch = False
    if any_write:
        written_paths = [_args_path(tc) for tc in tcs if _tool_name(tc) in _WRITE_TOOLS]
        written_paths = [p for p in written_paths if p]
        if written_paths and all(_is_build_meta_path(p) for p in written_paths):
            any_write = False
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
            tid = str(tc.get("id") or tc.get("tool_call_id") or "")
            if _tool_name(tc) in _WRITE_TOOLS and not (tid and tid in empty_write_ids):
                state.mark_write(p)
                if _is_source_path(p):
                    source_write_this_batch = True
                # Successful write of a previously empty path
                state.empty_write_paths = [
                    e for e in state.empty_write_paths if e.replace("\\", "/") != p.replace("\\", "/")
                ]

    with suppress(Exception):
        from remedy.core.build_todos import sync_todos_with_build

        sync_todos_with_build(None, state)

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
            cid = str(msg.get("tool_call_id") or msg.get("id") or "")
            if ship_ids and (not cid or cid not in ship_ids):
                continue
            fail = any(
                tok in low
                for tok in ("rejected", "error:", "fatal:", "not pushed", "permission denied")
            )
            if fail:
                continue
            if (
                "git_push ok" in low
                or "ship_pushed=true" in low
                or "ship_push ok" in low
                or "everything up-to-date" in low
            ):
                state.ship_pushed = True
                state.phase = "ship"
            if "gh_release ok" in low or "ship_released=true" in low:
                state.ship_released = True
            if "release" in low and ("created" in low or "url:" in low):
                state.ship_released = True
                m = re.search(r"(?i)(?:remote|push|url)[=:\s]+(https?://\S+)", content)
                if m:
                    state.ship_release_url = m.group(1)[:300]
            url_m = re.search(r"(?i)(?:remote|push|url)[=:\s]+(https?://\S+)", content)
            if url_m and not state.ship_url:
                state.ship_url = url_m.group(1)[:300]
            if state.ship_complete() and state.last_verify_ok is True:
                state.phase = "done"

    # Infer verify outcome ONLY from verify-class tool results.
    # file_read of a test file ("5 passed") or bash_exec mkdir (exit_code=0)
    # used to false-green the build and strip tools — that is not a test run.
    if not any_verify:
        return

    saw_red = False
    saw_green = False
    last_summary = ""
    for msg in tool_messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        # Parallel file_read + gcc must not green from the read body.
        cid = str(msg.get("tool_call_id") or msg.get("id") or "")
        if verify_ids and (not cid or cid not in verify_ids):
            continue
        content = str(msg.get("content") or "")
        if not content:
            continue
        low = content[:800].lower()
        if "approval_required" in low or "write_jail" in low:
            continue
        if "started background" in low:
            continue
        last_summary = content[:2000]
        # Official runner line only (ignore "exit_code=0" inside stdout)
        m_exit = re.search(r"(?im)^(?:verify\s+)?exit_code=(\d+)", content)
        if m_exit:
            if m_exit.group(1) == "0":
                saw_green = True
            else:
                saw_red = True
            continue
        passed = re.search(r"\b(\d+)\s+passed\b", low)
        if (
            passed
            and int(passed.group(1)) > 0
            and "failed" not in low
            # pytest can report "3 passed, 2 errors" (collection/fixture
            # errors) with no "failed" — that is not green.
            and not re.search(r"\b\d+\s+error(s)?\b", low)
            and "errors during collection" not in low
        ):
            saw_green = True
        elif _VERIFY_HINT.search(content) and re.search(
            r"\b(fail|failed|error)\b", low
        ):
            saw_red = True
    if saw_red:
        state.last_verify_ok = False
        state.phase = "repair"
        state.repair_steps += 1
        state.last_verify_summary = last_summary
        with suppress(Exception):
            from remedy.core.build_error_vector import parse_verify_output

            cmd = state.verify_command or ""
            vec = parse_verify_output(last_summary, command=cmd, ok=False)
            state.last_error_vector = vec.to_public()
            if vec.repair_command:
                state.last_scoped_command = vec.repair_command
    elif saw_green:
        state.last_verify_ok = True
        state.last_verify_summary = last_summary
        state.clear_write_set_on_green()


def next_machine_nudge(state: BuildTurnState) -> dict[str, str] | None:
    """Return a hard user-role inject if the machine schedule is violated."""
    if not state.active:
        return None

    missing = state.missing_required_files()
    if missing and "force_required_files" not in state.nudges_emitted:
        state.nudges_emitted.append("force_required_files")
        state.phase = "implement"
        listed = ", ".join(missing[:8])
        return {
            "role": "user",
            "content": (
                "[Build engine · FORCE IMPLEMENT] The goal still needs these files "
                f"on disk with real content: {listed}. "
                "file_write each one with the complete source (never empty). "
                "Do not claim done. Verify only after the files exist."
            ),
        }

    # Static page on disk — drive serve before pytest/verify essays
    html_ready = int(state.write_steps or 0) > 0 or any(
        str(w).lower().endswith((".html", ".htm")) for w in (state.write_set or [])
    )
    if not html_ready and (state.project_path or "").strip():
        with suppress(Exception):
            from pathlib import Path as _P

            root = _P(state.project_path)
            html_ready = any(
                h.is_file() and h.stat().st_size > 0
                for h in list(root.glob("*.html")) + list(root.glob("*.htm"))
            )
    if (
        "force_serve" not in state.nudges_emitted
        and html_ready
        and (
            _HTML_PAGE_GOAL_RE.search(state.goal or "")
            or re.search(r"(?i)\b(launch|serve|preview|localhost)\b", state.goal or "")
        )
    ):
        state.nudges_emitted.append("force_serve")
        return {
            "role": "user",
            "content": (
                "[Build engine · DRIVE HOST] The page files exist. "
                "host_run `python -m http.server 8000` with workdir= the project "
                "folder (timeout_seconds=5 is fine — the server stays up). "
                "Then computer_navigate http://127.0.0.1:8000/ . "
                "Do not open .md/.html via start/explorer. Do not pytest."
            ),
        }

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

    # Repair loop — structured ticket beats a vague "read the error"
    if (
        state.last_verify_ok is False
        and state.repair_steps >= 1
        and "force_repair" not in state.nudges_emitted
    ):
        state.nudges_emitted.append("force_repair")
        with suppress(Exception):
            from remedy.core.build_error_vector import (
                ErrorVector,
                parse_verify_output,
                repair_ticket_message,
            )

            vec = None
            if isinstance(state.last_error_vector, dict):
                vec = ErrorVector.from_public(state.last_error_vector)
                if not vec.failing_nodes and not vec.path_lines:
                    vec = None
            if vec is None and state.last_verify_summary:
                vec = parse_verify_output(
                    state.last_verify_summary,
                    command=state.verify_command or "",
                    ok=False,
                )
            if vec is not None and not vec.ok:
                return repair_ticket_message(vec)
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


def _session_build_key(runtime: Any) -> str:
    """Current turn's session id — never another tab's."""
    try:
        from remedy.core.turn_context import turn_session_id

        sid = str(turn_session_id(runtime) or "").strip()
        if sid:
            return sid
    except Exception:
        pass
    return "_anon"


def _build_turns_map(runtime: Any) -> dict[str, BuildTurnState] | None:
    m = getattr(runtime, "_build_turns", None)
    return m if isinstance(m, dict) else None


def get_build_state(runtime: Any) -> BuildTurnState | None:
    """This turn's machine state only (not a sibling session)."""
    if runtime is None:
        return None
    key = _session_build_key(runtime)
    m = _build_turns_map(runtime)
    if m is not None:
        st = m.get(key)
        return st if isinstance(st, BuildTurnState) else None
    # Legacy tests / pre-map runtimes stamp a single slot.
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


def build_has_open_drive(state: BuildTurnState | None) -> bool:
    """Open checklist or unfinished ship — do not surrender at the reopen cap."""
    if state is None or not getattr(state, "active", False):
        return False
    return int(getattr(state, "open_todo_count", 0) or 0) > 0 or (
        bool(getattr(state, "ship_required", False)) and not state.ship_complete()
    )


def green_gate_cap_allows_final(
    state: BuildTurnState | None,
    *,
    reopen_count: int,
    max_reopens: int,
) -> bool:
    """Cap may end a stuck verify loop; open todos/ship keep injecting."""
    if int(reopen_count) < int(max_reopens or 0):
        return False
    return not build_has_open_drive(state)


def build_blocks_final_answer(state: BuildTurnState | None) -> bool:
    """True when machine must refuse a done/final answer (no green verify yet)."""
    if state is None or not state.active:
        return False
    if not state.require_green_to_finish:
        return False
    # Empty writes / named goal files still missing — never claim done.
    if state.empty_write_paths or state.missing_required_files():
        return True
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
        # Still refuse DONE while the machine checklist is open
        return int(getattr(state, "open_todo_count", 0) or 0) > 0
    # Wrote code or failed verify → cannot finish without green
    if state.write_steps > 0 and state.last_verify_ok is not True:
        return True
    if state.source_writes_pending() and state.last_verify_ok is not True:
        return True
    if int(getattr(state, "open_todo_count", 0) or 0) > 0 and state.write_steps > 0:
        return True
    return state.syntax_ok is False


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
    if int(getattr(state, "open_todo_count", 0) or 0) > 0 and state.last_verify_ok is True:
        return {
            "role": "user",
            "content": (
                "[Build engine · TODO GATE · refuse DONE]\n"
                f"{state.open_todo_count} checklist item(s) still pending. "
                "todo_write them completed (or cancelled with a reason), then summarize. "
                "Do not claim finished with open todos."
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


_PLAY_AFTER_GREEN_RE = re.compile(
    r"(?i)\b("
    r"play(\s+it)?|pygame|try\s+it|iterate|"
    r"computer_app|desktop\s+game|video\s+game"
    r")\b"
)


def can_machine_inject(
    state: BuildTurnState | None,
    *,
    cap: int = 4,
    consume: bool = True,
) -> bool:
    """Bound stacked machine nudges so one batch cannot flood the turn.

    Peek with ``consume=False`` so a no-op (auto-drive declined) does not
    burn a slot and starve later repair/observe.
    """
    if state is None:
        return False
    n = int(getattr(state, "machine_injects", 0) or 0)
    if n >= cap:
        return False
    if consume:
        state.machine_injects = n + 1
    return True


def keep_agency_after_green(
    state: BuildTurnState | None,
    user_message: str = "",
) -> bool:
    """True when green verify must NOT strip tools (ship / play / iterate)."""
    if state is None or not state.active:
        return False
    if state.ship_required and not state.ship_complete():
        return True
    if getattr(state, "away_mode", False) and int(getattr(state, "open_todo_count", 0) or 0) > 0:
        return True
    with suppress(Exception):
        from remedy.core.companion_observe import write_set_looks_visual

        if write_set_looks_visual(list(state.write_set or []), state.goal or ""):
            if not getattr(state, "visual_observe_ran", False):
                return True
    blob = f"{state.goal or ''} {user_message or ''}"
    return bool(_PLAY_AFTER_GREEN_RE.search(blob))


def green_continue_message(state: BuildTurnState, *, command: str = "") -> dict[str, str]:
    """After machine green: short summary OR continue ship/play if required."""
    cmd = command or state.verify_command or ""
    if keep_agency_after_green(state):
        if not (state.ship_required and not state.ship_complete()):
            return {
                "role": "user",
                "content": (
                    "[Build engine · GREEN · play/iterate]\n"
                    f"Machine verify passed: `{cmd}`.\n"
                    "Do **not** rewrite working source just to re-verify.\n"
                    "The program is built — **use it**: computer_app the exe "
                    "(or run_python_file / bash_exec background), then "
                    "computer_snapshot target=desktop, play it, file_edit only "
                    "what you observe is wrong, rebuild, repeat.\n"
                    "Tools stay on."
                ),
            }
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
