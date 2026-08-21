"""Spawn gate for full-suite verify — model host_run and machine auto-verify.

Session 765c: 52× ``host_run(['npm','test'])`` never counted as verify, so the
machine kept asking and the model kept spawning a 30–60s Vitest. This gate
does **not** ban tests: scoped file tests always run, red always re-runs, a
new source write always re-runs, and “run the tests” from the owner always
runs.
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Any

_FULL_SUITE_RE = re.compile(
    r"(?ix)^\s*(?:npx\s+|npm\s+exec\s+|uv\s+run\s+|python\s+-m\s+)?"
    r"(?:"
    r"npm\s+(?:run\s+)?test(?:\s+--\s+--run)?"
    r"|npx\s+vitest(?:\s+run)?"
    r"|vitest(?:\s+run)?"
    r"|pytest(?:\s+-q)?"
    r"|cargo\s+test"
    r"|go\s+test(?:\s+\./\.\.\.)?"
    r")\s*$"
)

_OWNER_RUN_TESTS_RE = re.compile(
    r"(?i)\b((?:re-?)?run(?:\s+the)?\s+tests?|npm\s+test|pytest|run\s+the\s+suite)\b"
)

VERIFY_CACHED_PREFIX = "VERIFY_CACHED"
VERIFY_DEFERRED_PREFIX = "VERIFY_DEFERRED"


def join_argv(argv: Any) -> str:
    if isinstance(argv, (list, tuple)):
        return " ".join(str(a) for a in argv if str(a).strip()).strip()
    return str(argv or "").strip()


def is_full_suite_verify(command: str | None = None, *, argv: Any = None) -> bool:
    """True for a project-wide test command with no file/nodeid extra."""
    blob = join_argv(argv) if argv else str(command or "")
    blob = re.sub(r"\s+", " ", blob).strip()
    if not blob:
        return False
    # A path-like extra means TDD / scoped — always allow.
    if re.search(r"(?i)(\.test\.|\.spec\.|_test\.|tests?[/\\]|\s+\S+\.(py|ts|tsx|js)\b)", blob):
        return False
    return bool(_FULL_SUITE_RE.search(blob))


def owner_asked_to_run_tests(state: Any) -> bool:
    goal = str(getattr(state, "goal", "") or "")
    return bool(_OWNER_RUN_TESTS_RE.search(goal))


def maybe_short_circuit_verify(
    runtime: Any = None,
    *,
    command: str | None = None,
    argv: Any = None,
) -> str | None:
    """Return a tool result to emit instead of spawning, or None to run."""
    if not is_full_suite_verify(command, argv=argv):
        return None
    state = None
    with suppress(Exception):
        from remedy.core.build_engine import get_build_state

        state = get_build_state(runtime)
    if state is None or not getattr(state, "active", False):
        return None
    if owner_asked_to_run_tests(state):
        return None
    last_ok = getattr(state, "last_verify_ok", None)
    pending = []
    with suppress(Exception):
        pending = list(state.source_writes_pending() or [])
    feature_open = int(getattr(state, "open_feature_todo_count", 0) or 0)
    # Red suite: always spawn (repair).
    if last_ok is False:
        return None
    # Product items still open and we are not in a known-red repair.
    if feature_open > 0 and last_ok is not False:
        n = feature_open
        return (
            f"{VERIFY_DEFERRED_PREFIX} reason=feature_items_open count={n}\n"
            "Not a test failure. Finish the current Build item with file_write / "
            "file_edit. The full suite runs after that slice exists.\n"
            "exit_code=0"
        )
    # Green + no new source writes: replay, do not spawn.
    if last_ok is True and not pending:
        summary = str(getattr(state, "last_verify_summary", "") or "").strip()
        return (
            f"{VERIFY_CACHED_PREFIX} last_verify_ok=true source_write_set_unchanged\n"
            "exit_code=0\n"
            + (summary[:1500] if summary else "(previous suite was green; no new source writes)")
        )
    return None
