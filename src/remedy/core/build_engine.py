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

from remedy.core.build_oracle import coerce_text_arg
from remedy.core.relpath import norm_rel

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
    # Game dev (Godot / engines) — build loop, not chat
    r"godot|gdscript|platformer|roguelike|game\s+loop|sprite\s*sheet|tileset|"
    r"level\s+design|vertical\s+slice|"
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

# File/compiler cues for looks_like_build_request. Word-boundary extensions
# only — substring ".c" used to match ".com", "test" used to match "interesting".
_BUILD_FILE_CUE_RE = re.compile(
    r"(?i)(?:\.(?:py|tsx?|jsx?|rs|go|cpp|h|gd)\b|\.c\b|\bgcc\b|\bcompile\b|\bclang\b)"
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
    {
        "bash_exec",
        "shell_exec",
        "job_run",
        "host_run",
        "mission_verify",
        "mission_update",
    }
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
    r"\buv\s+run\s+pytest\b|"
    # Game engines: headless runs / parse checks / exports are verification.
    r"\bgodot[\w.-]*(?:\.exe)?\b[^\n]*--(?:headless|check-only|export-(?:release|debug|pack)|import)\b|"
    r"\bluac\s+-p\b|\bbusted\b|"
    r"\bgut(?:_cmdln)?\b|\bgdunit4?\b|"
    r"\bnpm\s+run\s+(?:test|build)\b"
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

# Prose-only writes: the one class that leaves a green verify valid.
_DOC_ONLY_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".adoc", ".log", ".remedy-build")

# Source extensions that always invalidate green verify (fast path).
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
        # Game engines
        ".gd",
        ".tscn",
        ".tres",
        ".gdshader",
        ".gdextension",
        ".lua",
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
    return bool(_SHIP_GOAL_RE.search(coerce_text_arg(goal)))


# Generic continuation = the message IS essentially just "continue" / "keep
# going" (optionally "finish it"), NOT "finish <specific thing>". Anchored to the
# whole message so a real goal ("finish the API") stays goal-keyed.
_CONTINUATION_RE = re.compile(
    r"(?i)^\s*(continue|keep\s+going|carry\s+on|go\s+on|go\s+ahead|resume|"
    r"pick\s+up(\s+where.*)?|proceed|finish\s+(it|up|this)|finish|keep\s+at\s+it|"
    r"more|next|go|do\s+it|do\s+them|do\s+that|do\s+this|"
    r"yes\s+do\s+it)\s*[.!…]*\s*$"
)


def _is_generic_continuation(goal: str) -> bool:
    """True for "continue" / "keep going" / empty — resume the project's active
    build regardless of its stored goal. A specific goal stays goal-keyed so it
    can't inherit a sibling goal's green watermark.
    """
    g = coerce_text_arg(goal)
    if not g:
        return True
    return bool(_CONTINUATION_RE.match(g))


#: A *different* chat session may resume this project's active build only when
#: the message is a bare "continue"-style continuation AND the build was touched
#: this recently. Anything older or more specific starts its own build.
CROSS_SESSION_RESUME_WINDOW_S = 30 * 60.0


def ledger_resume_allowed(
    led: Any,
    *,
    session_id: str,
    generic_continuation: bool,
    now: float | None = None,
) -> bool:
    """May *this* session pick up the ledger entry's phase / paths / error set?

    Same session → always. Another session → only a generic continuation
    ("continue" / "proceed" / "next") within ``CROSS_SESSION_RESUME_WINDOW_S``
    of the ledger's last update. A fresh chat asking a new question never
    inherits a sibling's implement/repair phase.
    """
    if led is None:
        return False
    led_sid = str(getattr(led, "session_id", "") or "").strip()
    sid = str(session_id or "").strip()
    if not sid or not led_sid:
        # Anonymous runtime (CLI / unit fakes) or legacy ledger with no stamp:
        # there is no sibling session to isolate from — keep old behaviour.
        return True
    if led_sid == sid:
        return True
    if not generic_continuation:
        return False
    with suppress(Exception):
        from remedy.core.session_continuity import is_recently_stopped

        # Owner Stopped the ledger's session — sibling tabs must not pick up
        # that drive via a bare "continue".
        if is_recently_stopped(led_sid):
            return False
    ts = float(getattr(led, "updated_ts", 0.0) or 0.0)
    if ts <= 0:
        return False
    t = time.time() if now is None else float(now)
    return (t - ts) <= CROSS_SESSION_RESUME_WINDOW_S


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
    low = p.lower()
    if any(low.endswith(suf) for suf in _SOURCE_WRITE_SUFFIXES):
        return True
    # Manifests, configs and scripts (package.json, pyproject.toml, Makefile,
    # *.sh) change what a suite does; only pure prose leaves green intact.
    if low.endswith(_DOC_ONLY_SUFFIXES):
        return False
    return bool(low.strip())


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
    #: Chat session that started / continued this build. A sibling tab (or a
    #: fresh chat after a long build) must never see this state as *its* build.
    session_id: str = ""
    # Unverified mutation set (paths written since last green verify)
    write_set: list[str] = field(default_factory=list)
    required_files: list[str] = field(default_factory=list)
    empty_write_paths: list[str] = field(default_factory=list)
    last_error_vector: dict[str, Any] | None = None
    syntax_ok: bool | None = None
    # Block final user-facing "done" until green (machine gate)
    require_green_to_finish: bool = True
    # Read-only review/analysis: deliver findings — no file_write / verify needed.
    # Flips to a full build (require_green_to_finish=True) the instant a write lands.
    read_only: bool = False
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
    #: Open checklist rows that are product work, not "npm test green".
    #: While this is > 0, auto-verify must not hijack the turn into fixing
    #: the existing suite before the feature exists.
    open_feature_todo_count: int = 0
    #: Owner asked for the whole job (or we inherited that from the ledger).
    #: Green tests are a checkpoint — tools stay on until the goal is actually done.
    drive_to_done: bool = False
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
    last_mutation_score: dict[str, Any] | None = None
    last_gate_tower: dict[str, Any] | None = None
    last_mutant_kill: dict[str, Any] | None = None
    repair_queue: list[Any] | dict[str, Any] | None = None
    _seed_message: dict[str, Any] | None = None

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
            key = norm_rel(raw)
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
        """After green verify: ship if required, else stay in implement when
        the owner's job is not actually finished, else done."""
        if self.missing_required_files():
            self.phase = "implement"
            return
        if self.ship_required and not self.ship_complete():
            self.phase = "ship"
            return
        if int(self.open_todo_count or 0) > 0 or bool(self.drive_to_done):
            self.phase = "implement"
            return
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
            "open_feature_todo_count": self.open_feature_todo_count,
            "drive_to_done": self.drive_to_done,
        }


def looks_like_build_request(message: str) -> bool:
    """True when the user message is a *task* (research → plan → build default)."""
    msg = coerce_text_arg(message)
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
    # Path / compiler / write cues only — substring "test" used to match
    # "interesting", and ".c" matched ".com".
    if len(msg) <= 24:
        return False
    return bool(
        _BUILD_FILE_CUE_RE.search(msg)
        or "need you to" in low
        or "file_write" in low
        or "src/" in low
    )


# Alias — product language is research → plan → build (tasks, not only “builds”)
looks_like_task_request = looks_like_build_request


def begin_build_turn(
    runtime: Any,
    message: str,
    *,
    force: bool = False,
) -> BuildTurnState | None:
    """Start machine supervision for task work (research → plan → build)."""
    message = coerce_text_arg(message)
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
    finish_everything = False
    with suppress(Exception):
        from remedy.core.react_open_work import message_asks_to_finish_everything

        finish_everything = message_asks_to_finish_everything(message)
    prior_drive = False
    prior_active = False
    with suppress(Exception):
        prev = get_build_state(runtime)
        if prev is not None and getattr(prev, "active", False):
            prior_active = True
            prior_drive = bool(getattr(prev, "drive_to_done", False))
    # A bare "keep going"/"proceed" is a continuation, not a new job — only
    # resume an already-active drive. Stronger phrasing ("do all of those
    # things", "until none remain") starts a finish-everything build even
    # without a prior turn.
    continuation = _is_generic_continuation(message)
    strong_finish = finish_everything and not continuation
    wants = force or looks_like_build_request(message) or away or strong_finish
    if not wants and continuation and (prior_drive or prior_active):
        wants = True
    if not force:
        with suppress(Exception):
            from remedy.core.react_policy import is_chat_only_message

            if is_chat_only_message(message):
                return None
    implement_from_review = False
    if not wants:
        # Still enable light supervision if open mission/tasks look like build
        with suppress(Exception):
            brief = getattr(runtime, "_session_brief", None)
            intent = str(getattr(brief, "intent", "") or "")
            if _BUILD_RE.search(intent):
                wants = True
    if not wants:
        # "fix issues 1-10" after a review: those findings are the Build list.
        with suppress(Exception):
            from remedy.core.build_todos import has_open_review_finding_todos
            from remedy.core.intent_policy import _CHANGE_VERB_RE

            if _CHANGE_VERB_RE.search(message) and has_open_review_finding_todos(
                runtime
            ):
                wants = True
                implement_from_review = True
    if not wants:
        # Leftover open Build still drives the host this turn (no Ask pause).
        enable_build_host_drive(runtime)
        return None
    # Tiny muscle: soft supervision only (higher explore tolerance)
    goal_txt = coerce_text_arg(message)[:300]
    if force and prior_active:
        # build_drive / build_tdd / build_resume call with force=True on every
        # hop. Replacing the live state here would zero every cap (auto-verify
        # cycles, machine injects, one-shot nudges, write_set) mid-turn, so a
        # model that re-drives each hop could loop past the walls that exist
        # to stop it. Keep the state; only refresh what the caller may change.
        with suppress(Exception):
            prev = get_build_state(runtime)
            if prev is not None and getattr(prev, "active", False):
                if goal_txt:
                    prev.goal = goal_txt
                if finish_everything:
                    prev.drive_to_done = True
                if implement_from_review:
                    prev.read_only = False
                    prev.require_green_to_finish = True
                return prev
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
    # Read-only review/analysis: supervise the research, but never push writes or
    # demand a green verify to finish. A write later flips it into a full build.
    read_only = False
    with suppress(Exception):
        from remedy.core.intent_policy import looks_like_readonly_request

        read_only = looks_like_readonly_request(goal_txt) and not looks_like_ship_goal(
            goal_txt
        )
    if implement_from_review:
        read_only = False
    st = BuildTurnState(
        active=True,
        phase="implement" if implement_from_review else "scout",
        goal=goal_txt,
        muscle_tier=muscle.label,
        max_serial_explore=explore_cap,
        read_only=read_only,
        require_green_to_finish=not read_only,
        # Full suites (npm test / pytest) wait for a real slice. C one-file
        # still verifies on the first write via should_auto_verify(has_c).
        require_verify_after_writes=2,
        ship_required=looks_like_ship_goal(goal_txt),
        away_mode=away,
        drive_to_done=bool(finish_everything or (prior_drive and not read_only)),
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
    with suppress(Exception):
        from remedy.core.turn_context import turn_session_id

        st.session_id = str(turn_session_id(runtime) or "").strip()
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
        # Load THIS turn's goal entry — never the project's active other-goal
        # build (that copied a sibling's green watermark / verify command). But a
        # generic "continue" / "keep going" means "resume whatever I was doing",
        # so fall back to the project's active build (goal=None) in that case.
        _resume_goal = None if _is_generic_continuation(st.goal) else (st.goal or None)
        led = (
            load_ledger(st.project_path or None, home=home, goal=_resume_goal)
            if _bound
            else None
        )
        # A specific goal with no exact ledger entry falls back to the project's
        # active build, so a reworded / refined goal ("Create index.html" →
        # "Create index.html landing page") and "continue the fix" still resume.
        # The green watermark is NOT carried on this cross-goal fallback, so a
        # *different* goal can never inherit a sibling's "verified" state.
        _cross_goal = False
        if led is None and _bound and _resume_goal is not None:
            with suppress(Exception):
                led = load_ledger(st.project_path or None, home=home, goal=None)
                _cross_goal = led is not None
        # Session scoping: another chat's build is resumed only by a bare
        # "continue" shortly after it was touched. A new question in a fresh
        # tab keeps the project's verify command but starts at scout.
        if led is not None and not ledger_resume_allowed(
            led,
            session_id=st.session_id,
            generic_continuation=_resume_goal is None,
        ):
            if led.verify_command and not st.verify_command:
                st.verify_command = led.verify_command
                st.oracle_ok = True
            led = None
        if led is not None:
            # Always carry oracle / verify command (same project → same tests).
            if led.verify_command and not st.verify_command:
                st.verify_command = led.verify_command
                st.oracle_ok = True
            # Green watermark only for the same goal / generic continuation —
            # never let a cross-goal fallback inherit "verified".
            if led.last_verify_ok is True and not _cross_goal:
                st.last_verify_ok = True
                st.auto_verify_ran = True
                st.write_steps_at_last_green = max(
                    int(st.write_steps_at_last_green or 0),
                    int(led.write_steps or 0),
                )
                if led.last_verify_summary:
                    st.last_verify_summary = led.last_verify_summary
            if not _cross_goal and getattr(led, "drive_to_done", False):
                st.drive_to_done = True
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
        from remedy.core.build_todos import (
            load_todos,
            open_feature_todo_count,
            open_todo_count,
        )

        _items = load_todos(runtime)
        st.open_todo_count = open_todo_count(_items)
        st.open_feature_todo_count = open_feature_todo_count(_items)
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
    # Body coordination: announce this muscle's presence so sibling sessions see
    # it, and the write path can block cross-session overwrites.
    with suppress(Exception):
        from remedy.core import coordination as _coord
        from remedy.core.llm_binding import get_llm_binding
        from remedy.core.turn_context import turn_session_id

        _home = getattr(getattr(runtime, "config", None), "home_dir", None)
        _b = get_llm_binding(runtime)
        _muscle = "/".join(
            x for x in ((_b.provider or "").strip(), (_b.model or "").strip()) if x
        )
        _coord.register(
            str(turn_session_id(runtime) or ""),
            muscle=_muscle,
            project_path=st.project_path,
            goal=st.goal,
            phase=st.phase,
            home=_home,
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


def _coworkers_block() -> str:
    """Body awareness: name the other live muscles + held files, or ''."""
    with suppress(Exception):
        from remedy.core import coordination as _coord
        from remedy.core.turn_context import current_session_id

        sid = str(current_session_id() or "")
        note = _coord.coworkers_note(sid)
        if note:
            return (
                f"\n[Body coordination] {note} "
                "You are one muscle of the same Remedy: do NOT edit files a "
                "sibling session is holding (the write tool refuses them). "
                "Split the work — pick files/areas the others are not touching, "
                "or wait for their hold to free."
            )
    return ""


def _frontier_muscle(state: BuildTurnState) -> bool:
    return str(getattr(state, "muscle_tier", "") or "").strip().lower() == "frontier"


def build_protocol_block(state: BuildTurnState) -> str:
    """Hard system block injected at turn start for build supervision.

    Frontier muscle already knows how to build. A labeled RESEARCH→PLAN→BUILD
    syllabus makes Grok recite the process in thinking instead of doing the
    next file. Local muscle still gets the teaching loop. The machine schedule
    (todos, force-implement, verify gate) is unchanged either way.
    """
    if getattr(state, "read_only", False) and int(getattr(state, "write_steps", 0) or 0) == 0:
        if _frontier_muscle(state):
            return (
                f"[Review] {state.goal or '(user request)'}\n"
                "Scout once (batch reads), deliver findings, stop. "
                "No file_write unless they asked to change something."
                f"{_coworkers_block()}"
            )
        return (
            "[Read-only review — RESEARCH → SYNTHESIZE → DELIVER]\n"
            f"Goal: {state.goal or '(user request)'}\n"
            "1) RESEARCH (scout): batch file_read/list_dir/repo_search in ONE step "
            "(4–12). One good sweep is enough — do not re-scout for marginal detail.\n"
            "2) SYNTHESIZE: strengths, risks, concrete file:line findings, ranked.\n"
            "3) DELIVER the written review. DONE = findings delivered. A read-only "
            "review needs NO file_write and NO verify signal — do not loop for 'a few "
            "more details', and do not claim you must build. Only edit if the user "
            "explicitly asked you to fix or change something (that switches this into "
            "a full build with the green gate)."
            f"{_coworkers_block()}"
        )
    if (
        not getattr(state, "read_only", False)
        and str(getattr(state, "phase", "") or "") == "implement"
        and int(getattr(state, "open_todo_count", 0) or 0) > 0
    ):
        return (
            "[Task loop — IMPLEMENT the open Build list · hard rule]\n"
            f"Goal: {state.goal or '(user request)'}\n"
            "The numbered review findings are already on the Build checklist. "
            "Implement them with file_write / file_edit. Read only the files those "
            "items name. Do **not** start a whole-tree review as this hop. "
            "Do **not** claim Issues 1–10 fixed while checklist rows are still open. "
            "The full test suite waits until those product items exist."
            f"{_coworkers_block()}"
        )
    if _frontier_muscle(state):
        open_n = int(getattr(state, "open_todo_count", 0) or 0)
        bits = [f"[Build] {state.goal or '(user request)'}"]
        if state.verify_command:
            bits.append(
                f"Tests after the product work exists: `{state.verify_command}`"
            )
        if open_n > 0:
            bits.append(
                f"{open_n} checklist items open — do the next one with tools, "
                "then the next. Don't stop to report."
            )
        else:
            bits.append(
                "Use tools until the goal is actually done. Don't stop to report "
                "after one hop."
            )
        bits.append(
            "Don't run the full test suite (`npm test` / `pytest`) until the "
            "current product items exist. Green tests are a checkpoint, not the finish."
        )
        if state.ship_required:
            bits.append(
                "After green: git_status → git_push → gh_release if asked. "
                "Don't rewrite green code to re-test."
            )
        bits.append("You drive this PC. Jail and auth stay.")
        return "\n".join(bits) + _coworkers_block()
    oracle = (
        f"Oracle (run AFTER product Build items are done): `{state.verify_command}`"
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
        "changes. Do **not** run the project test suite (`npm test` / `pytest`) "
        "until the current product checklist items are actually built — a red "
        "suite mid-slice is noise, not the job. Static HTML: files on disk is "
        "verify — do not run pytest. To show the page: host_run "
        "`python -m http.server` in the project folder, then computer_navigate "
        "http://127.0.0.1:8000/ ."
        f"{ship}\n"
        "YOU DRIVE THIS PC this turn (Ask is skipped; jail/auth/Plan stay). "
        "Do not call help_list or goal_add. Do not ask permission. "
        "Use file_read / file_write / host_run / bash_exec now. "
        "Machine loop: todo_write a short checklist covering the WHOLE goal, "
        "then implement item by item. Green tests are a checkpoint **after** "
        "the slice exists — do not stop to report or to chase the suite after "
        "one hop while checklist items remain. "
        "The machine will force implement after scout. It will not auto-run "
        "the existing suite while product Build items are still open. "
        "Never monologue a plan without tool_calls. Never explore one file per step. "
        f"Serial explore cap before forced implement: {state.max_serial_explore}."
        f"{_coworkers_block()}"
    )


def _tool_name(tc: dict[str, Any]) -> str:
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    return str((fn or {}).get("name") or tc.get("name") or "").strip().lower()


def _args_obj(tc: dict[str, Any]) -> dict[str, Any]:
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    raw = tc.get("arguments") or tc.get("args") or (fn or {}).get("arguments") or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        with suppress(Exception):
            import json

            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
    return {}


def _args_path(tc: dict[str, Any]) -> str:
    obj = _args_obj(tc)
    for k in ("path", "file", "filepath", "target", "workdir"):
        if obj.get(k):
            return str(obj[k])[:240]
    argv = obj.get("argv")
    if isinstance(argv, (list, tuple)) and argv:
        return " ".join(str(a) for a in argv)[:120]
    cmd = str(obj.get("command") or "")
    if cmd:
        return cmd[:120]
    return ""


def _tool_command_blob(tc: dict[str, Any]) -> str:
    """Command text for verify classification — argv list becomes ``npm test``."""
    obj = _args_obj(tc)
    argv = obj.get("argv")
    if isinstance(argv, (list, tuple)) and argv:
        return " ".join(str(a) for a in argv).lower()
    cmd = str(obj.get("command") or "")
    if cmd:
        return cmd.lower()
    raw = str((tc.get("function") or {}).get("arguments") or "")
    return (raw + " " + _args_path(tc)).lower()


def observe_tool_batch(
    state: BuildTurnState,
    tool_calls: list[dict[str, Any]] | None,
    tool_messages: list[dict[str, Any]] | None = None,
    runtime: Any = None,
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
        elif n in ("bash_exec", "shell_exec", "job_run", "host_run"):
            blob = _tool_command_blob(tc)
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
        # A read-only review that starts writing is now a real build — restore the
        # green gate so the change is verified before "done".
        if state.read_only:
            state.read_only = False
            state.require_green_to_finish = True
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
                with suppress(Exception):
                    from remedy.memory.harness.hot_writes import record_hot_write

                    record_hot_write(runtime, p, tool=_tool_name(tc))
                if _is_source_path(p):
                    source_write_this_batch = True
                # Successful write of a previously empty path
                state.empty_write_paths = [
                    e for e in state.empty_write_paths if e.replace("\\", "/") != p.replace("\\", "/")
                ]

    with suppress(Exception):
        from remedy.core.build_todos import sync_todos_with_build

        # Pass runtime so save_todos can queue @@todos for the live Build list.
        # runtime=None used to update disk and leave the on-screen checklist stuck.
        sync_todos_with_build(runtime, state)

    # Body coordination heartbeat — every tool wave keeps this muscle's beacon
    # fresh so a live build never loses its claims mid-work. Mid-turn the true
    # per-turn LLM binding is set, so this also self-corrects the muscle label
    # (register() at turn start may have seen only the runtime default).
    with suppress(Exception):
        from remedy.core import coordination as _coord
        from remedy.core.llm_binding import get_llm_binding
        from remedy.core.turn_context import current_session_id

        _sid = str(current_session_id() or "")
        if _sid:
            _muscle = ""
            with suppress(Exception):
                _b = get_llm_binding(None)
                # Only trust the label when a real per-turn binding is set
                # (the no-context fallback has provider but an empty model).
                if (_b.model or "").strip():
                    _muscle = "/".join(
                        x
                        for x in ((_b.provider or "").strip(), (_b.model or "").strip())
                        if x
                    )
            _coord.heartbeat(_sid, phase=state.phase, muscle=_muscle or None)

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
        if "verify_deferred" in low or "verify_cached" in low:
            # Cached/deferred suite is not a new red or a fresh green.
            if "verify_cached" in low and "exit_code=0" in low:
                saw_green = True
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


def _has_c_toolchain() -> bool:
    """True when a C/C++ compiler is on PATH (gcc / clang / cc / MSVC cl)."""
    import shutil

    return any(shutil.which(x) for x in ("gcc", "clang", "cc", "cl"))


def _wrote_c_source(state: BuildTurnState) -> bool:
    writes = [str(w).lower() for w in (getattr(state, "write_set", None) or [])]
    return any(w.endswith((".c", ".cpp", ".cc", ".cxx")) for w in writes)


def next_machine_nudge(state: BuildTurnState) -> dict[str, str] | None:
    """Return a hard user-role inject if the machine schedule is violated."""
    if not state.active:
        return None

    # C/C++ source written but no compiler on this PC → say so once, in plain
    # language, and stop the futile gcc-retry loop. Only fires when the machine
    # genuinely cannot compile, so environments with a toolchain are unaffected.
    if (
        "no_c_toolchain" not in state.nudges_emitted
        and _wrote_c_source(state)
        and not _has_c_toolchain()
    ):
        state.nudges_emitted.append("no_c_toolchain")
        state.require_green_to_finish = False  # can't compile here — don't trap
        return {
            "role": "user",
            "content": (
                "[Build engine · NEEDS A COMPILER] The C/C++ source is saved, but "
                "there is no compiler (gcc/clang) on this PC's PATH — it cannot be "
                "built or run here. Do NOT keep retrying the compile. Tell the user "
                "plainly and simply: the code is written and where it lives, and to "
                "build/run it they need a C compiler installed (on Windows: "
                "`winget install -e --id GnuWin32.Make` won't do it — install "
                "MinGW-w64 or MSYS2 gcc, or use `winget install BrechtSanders."
                "WinLibs.POSIX.UCRT`, then reopen the terminal). Then stop — do not "
                "claim the program ran."
            ),
        }

    # Read-only review with no writes yet: never push writes or verify. The model
    # delivers findings and finishes. A write flips read_only off (see
    # observe_tool_batch), after which the normal build nudges apply.
    if getattr(state, "read_only", False) and int(state.write_steps or 0) == 0:
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

    # Writes without verify (model path — auto-verify may also run).
    # Skip while product checklist items remain — the suite is not the job yet.
    _feature_open = int(getattr(state, "open_feature_todo_count", 0) or 0)
    if (
        state.write_steps >= state.require_verify_after_writes
        and state.verify_steps == 0
        and not state.auto_verify_ran
        and "force_verify" not in state.nudges_emitted
        and (_wrote_c_source(state) or _feature_open <= 0)
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
    # Legacy tests / pre-map runtimes stamp a single slot.
    st = m.get(key) if m is not None else getattr(runtime, "_build_turn", None)
    if not isinstance(st, BuildTurnState):
        return None
    return st if build_state_owned_by(st, key) else None


def build_state_owned_by(state: Any, session_id: str | None) -> bool:
    """True when *state* was started by *session_id* (or either is unstamped)."""
    if state is None:
        return False
    st_sid = str(getattr(state, "session_id", "") or "").strip()
    sid = str(session_id or "").strip()
    if sid == "_anon":
        sid = ""
    if not st_sid or not sid:
        return True
    return st_sid == sid


def deactivate_build_for_session(runtime: Any, session_id: str | None) -> bool:
    """Clear in-memory ``active`` for *session_id* (Stop rebound from another tab).

    Same-session ``continue`` can still resume from the on-disk ledger.
    """
    sid = str(session_id or "").strip()
    if not sid or runtime is None:
        return False
    changed = False
    m = _build_turns_map(runtime)
    if m is not None:
        st = m.get(sid)
        if isinstance(st, BuildTurnState) and bool(getattr(st, "active", False)):
            st.active = False
            changed = True
    legacy = getattr(runtime, "_build_turn", None)
    if (
        isinstance(legacy, BuildTurnState)
        and build_state_owned_by(legacy, sid)
        and bool(getattr(legacy, "active", False))
    ):
        legacy.active = False
        changed = True
    return changed


def should_force_tools_for_build(runtime: Any, message: str) -> bool:
    """True when L1 must not strip tools (build request or active build turn)."""
    message = coerce_text_arg(message)
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
    """Open checklist, unfinished ship, or a finish-everything drive.

    Do not surrender at the reopen cap while the owner's job is still open.
    """
    if state is None or not getattr(state, "active", False):
        return False
    if bool(getattr(state, "drive_to_done", False)):
        return True
    return int(getattr(state, "open_todo_count", 0) or 0) > 0 or (
        bool(getattr(state, "ship_required", False)) and not state.ship_complete()
    )


def build_progress_score(state: BuildTurnState | None) -> int:
    """Monotonic 'is the build advancing' score — writes/verifies/repairs/ship/green.

    The loop's stall guard uses this: an open drive keeps overriding the step
    caps only while this score is still climbing, so a progressing build is
    never capped.
    """
    if state is None or not getattr(state, "active", False):
        return 0
    s = (
        int(getattr(state, "write_steps", 0) or 0)
        + int(getattr(state, "verify_steps", 0) or 0)
        + int(getattr(state, "repair_steps", 0) or 0)
    )
    if getattr(state, "last_verify_ok", None) is True:
        s += 1
    if getattr(state, "ship_pushed", False):
        s += 3
    if getattr(state, "ship_released", False):
        s += 3
    return s


def open_drive_should_continue(
    state: BuildTurnState | None,
    *,
    steps_since_progress: int,
    patience: int = 60,
) -> bool:
    """Open todos / unfinished ship may override the loop step caps — but only
    while the build is still advancing.

    No write/verify/ship/green progress for ``patience`` steps → return False so
    the loop stops honestly instead of running to the hard 10k step wall. A build
    that keeps making progress resets the counter and is never capped, so this
    removes the runaway without weakening a legitimate long build.
    """
    if not build_has_open_drive(state):
        return False
    return int(steps_since_progress) <= int(patience)


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
    open_n = int(getattr(state, "open_todo_count", 0) or 0)
    # Checklist already started (or finish-everything drive) — "say go" / Done
    # with open rows is not a finished build. Session 765c ended "**Done**"
    # with four pending Build-list items.
    if open_n > 0:
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
        return False
    # Wrote code or failed verify → cannot finish without green
    if state.write_steps > 0 and state.last_verify_ok is not True:
        return True
    if state.source_writes_pending() and state.last_verify_ok is not True:
        return True
    return state.syntax_ok is False


def build_blocks_done_summary(state: BuildTurnState | None) -> bool:
    """Keep-agency path: refuse a 'Done' summary while required work remains.

    Unlike ``build_blocks_final_answer`` this does **not** trap a write that
    has not verified yet — tools are still on, so she can keep going. It
    catches the session-765c failure: '**Done**' with an open Build list
    (or missing files / unfinished ship).
    """
    if state is None or not state.active:
        return False
    if not state.require_green_to_finish:
        return False
    if state.empty_write_paths or (
        hasattr(state, "missing_required_files") and state.missing_required_files()
    ):
        return True
    if int(getattr(state, "open_todo_count", 0) or 0) > 0:
        return True
    return bool(state.ship_required and not state.ship_complete())


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
    if int(getattr(state, "open_todo_count", 0) or 0) > 0:
        if int(state.write_steps or 0) == 0:
            return {
                "role": "user",
                "content": (
                    "[Build engine · TODO GATE · refuse scout-only DONE]\n"
                    f"{state.open_todo_count} checklist item(s) still pending. "
                    "Do not ask the owner to say go. Implement the first open "
                    "item now (file_write / file_edit), todo_write it in_progress, "
                    "then the next. Tools stay on."
                ),
            }
        return {
            "role": "user",
            "content": (
                "[Build engine · TODO GATE · refuse DONE]\n"
                f"{state.open_todo_count} checklist item(s) still pending. "
                "todo_write the one you just finished as completed, put the "
                "next in_progress, and keep building. Do not claim finished "
                "with an open Build list."
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
    cap: int | None = None,
    consume: bool = True,
) -> bool:
    """Bound stacked machine nudges so one batch cannot flood the turn.

    Peek with ``consume=False`` so a no-op (auto-drive declined) does not
    burn a slot and starve later repair/observe. A finish-everything drive
    or open checklist gets a higher cap so keep-going turns are not muted
    after four machine cards.
    """
    if state is None:
        return False
    if cap is None:
        cap = (
            12
            if (
                bool(getattr(state, "drive_to_done", False))
                or int(getattr(state, "open_todo_count", 0) or 0) > 0
            )
            else 4
        )
    n = int(getattr(state, "machine_injects", 0) or 0)
    if n >= cap:
        return False
    if consume:
        state.machine_injects = n + 1
    return True


def keep_agency_after_green(
    state: BuildTurnState | None,
    user_message: str = "",
    *,
    run_already_done: bool = False,
) -> bool:
    """True when green verify must NOT strip tools.

    Tests passing is a checkpoint. Tools come off only when nothing remains:
    no open todos, no unfinished ship, no finish-everything drive, no play/
    visual follow-up. Session 765c: one file_edit → npm test green →
    ``GREEN · stop building`` → "**Still open:** …" nine hops in a row.
    """
    # An explicitly requested run outranks the build state, and is checked
    # before it: "create fib.py, then run it and tell me its output" wrote the
    # file, the engine's own smoke test went green, tools were stripped, and
    # the turn ended with the run never performed — on every model tested.
    # The engine's verify passing says nothing about the owner's second step.
    if not run_already_done:
        with suppress(Exception):
            from remedy.core.local_agent_optimize import request_wants_execution

            if request_wants_execution(user_message):
                return True

    if state is None or not state.active:
        return False
    if state.ship_required and not state.ship_complete():
        return True
    if int(getattr(state, "open_todo_count", 0) or 0) > 0:
        return True
    if bool(getattr(state, "drive_to_done", False)):
        return True
    if state.empty_write_paths or (
        hasattr(state, "missing_required_files") and state.missing_required_files()
    ):
        return True
    blob = f"{state.goal or ''} {user_message or ''}"
    with suppress(Exception):
        from remedy.core.react_open_work import message_asks_to_finish_everything

        if message_asks_to_finish_everything(blob):
            return True
    with suppress(Exception):
        from remedy.core.companion_observe import write_set_looks_visual

        if write_set_looks_visual(list(state.write_set or []), state.goal or ""):
            if not getattr(state, "visual_observe_ran", False):
                return True
    return bool(_PLAY_AFTER_GREEN_RE.search(blob))


def green_continue_message(state: BuildTurnState, *, command: str = "") -> dict[str, str]:
    """After machine green: short summary OR continue ship/play if required."""
    cmd = command or state.verify_command or ""
    if keep_agency_after_green(state):
        if not (state.ship_required and not state.ship_complete()):
            open_n = int(getattr(state, "open_todo_count", 0) or 0)
            if open_n > 0 or bool(getattr(state, "drive_to_done", False)):
                extra = (
                    f"{open_n} checklist item(s) still open. "
                    if open_n > 0
                    else "The owner's goal is not finished. "
                )
                return {
                    "role": "user",
                    "content": (
                        "[Build engine · GREEN · keep going]\n"
                        f"Machine verify passed: `{cmd}`.\n"
                        "That is a checkpoint, not the finish line. "
                        f"{extra}"
                        "Do **not** rewrite working tests. Do **not** stop to report. "
                        "todo_write the item you just finished as completed, then "
                        "the next open item as in_progress, so the Build list on "
                        "screen moves. Then implement, verify, next — until the "
                        "owner's goal is actually done.\n"
                        "Tools stay on."
                    ),
                }
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
