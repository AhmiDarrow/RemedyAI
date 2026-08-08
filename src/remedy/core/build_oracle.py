"""Oracle-first verify discovery + auto-verify after write waves.

The machine discovers the test command (stack fingerprint) and can run it
without waiting for the model to choose — falsification is not optional.
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Any

# Tools that mutate source (not shell — shell may be verify)
_MUTATE_TOOLS = frozenset({"file_write", "file_edit"})


def discover_verify_command(runtime: Any, *, path: str = "") -> str:
    """Return best-effort verify command for the active project (may be empty)."""
    cmd = ""
    with suppress(Exception):
        from remedy.core.project_fingerprint import fingerprint_path

        root = None
        if (path or "").strip():
            with suppress(Exception):
                root = runtime.resolve_tool_path(path)  # type: ignore[attr-defined]
                if root is not None and root.is_file():
                    root = root.parent
        if root is None:
            with suppress(Exception):
                root = runtime.effective_project_path()
        if root is not None:
            fp = fingerprint_path(root)
            cmd = str(fp.suggest_verify or "").strip()
    if not cmd:
        # Shallow fallbacks from common markers under project
        with suppress(Exception):
            from pathlib import Path

            root = runtime.effective_project_path()
            p = Path(root)
            if (p / "pyproject.toml").exists() or (p / "pytest.ini").exists() or (
                p / "tests"
            ).is_dir():
                cmd = "pytest -q"
            elif (p / "package.json").exists():
                cmd = "npm test"
            elif (p / "Cargo.toml").exists():
                cmd = "cargo test"
            elif (p / "go.mod").exists():
                cmd = "go test ./..."
    return cmd


def oracle_missing_nudge(state: Any) -> dict[str, str]:
    """Fail-closed: no verify command — model must add tests or set command."""
    return {
        "role": "user",
        "content": (
            "[Build engine · ORACLE MISSING · fail closed]\n"
            "No verify command discovered for this project (no pytest/npm/cargo/go "
            "fingerprint). You cannot claim DONE.\n"
            "Next step MUST be one of:\n"
            "1) file_write a minimal test + bash_exec the runner, or\n"
            "2) bash_exec/job_run with an explicit verify command, or\n"
            "3) mission_start with verify_command=…\n"
            "Do not monologue. Create falsification now."
        ),
    }


async def run_auto_verify(
    runtime: Any,
    state: Any,
    *,
    command: str = "",
    prefer_scoped: bool = True,
) -> dict[str, Any]:
    """Execute machine-owned verify (job_run path). Updates *state* in place.

    Prefers **scoped** pytest paths derived from write_set when possible;
    falls back to full suite. Seeds a smoke oracle if none exists.
    """
    from remedy.core.jobs import run_verify_job

    cmd = (command or getattr(state, "verify_command", None) or "").strip()
    if not cmd:
        cmd = discover_verify_command(runtime)
    if cmd and hasattr(state, "verify_command"):
        state.verify_command = cmd

    # Boundary: auto-seed smoke oracle when missing and we have writes
    if not cmd and getattr(state, "write_set", None):
        with suppress(Exception):
            from remedy.core.build_seed_oracle import seed_python_smoke_oracle

            if not getattr(state, "oracle_seeded", False):
                seed = seed_python_smoke_oracle(
                    runtime,
                    list(state.write_set or []),
                    home=getattr(getattr(runtime, "config", None), "home_dir", None),
                )
                if seed.get("ok") and seed.get("command"):
                    cmd = str(seed["command"])
                    state.verify_command = cmd
                    state.oracle_ok = True
                    state.oracle_seeded = True
                    state._seed_message = seed  # type: ignore[attr-defined]

    if not cmd:
        if hasattr(state, "oracle_ok"):
            state.oracle_ok = False
        if hasattr(state, "nudges_emitted") and "oracle_missing" not in state.nudges_emitted:
            state.nudges_emitted.append("oracle_missing")
        return {
            "ok": False,
            "auto": True,
            "oracle_missing": True,
            "summary": "No verify command discovered",
            "command": "",
            "scoped": False,
        }

    # Convergence cap — stop thrashing auto-verify forever
    cycles = int(getattr(state, "auto_verify_cycles", 0) or 0)
    max_c = int(getattr(state, "max_auto_verify_cycles", 6) or 6)
    if cycles >= max_c:
        return {
            "ok": False,
            "auto": True,
            "oracle_missing": False,
            "summary": (
                f"Auto-verify cycle cap reached ({max_c}). "
                "Escalate: narrow scope, fix remaining error vector manually, "
                "or raise max_auto_verify_cycles."
            ),
            "command": cmd,
            "scoped": False,
            "capped": True,
        }

    run_cmd = cmd
    scoped = False
    if prefer_scoped and getattr(state, "write_set", None):
        with suppress(Exception):
            from remedy.core.build_scoped import scoped_verify_command

            sc = scoped_verify_command(
                runtime, list(state.write_set or []), base_command=cmd
            )
            if sc:
                run_cmd = sc
                scoped = True
                state.last_scoped_command = sc

    if hasattr(state, "auto_verify_cycles"):
        state.auto_verify_cycles = cycles + 1

    result = await run_verify_job(runtime, command=run_cmd, timeout=300.0)
    ok = bool(getattr(result, "ok", False))
    summary = str(getattr(result, "summary", "") or "")[:2000]
    # Also parse exit_code from summary text
    if "exit_code=0" in summary.lower() or re.search(
        r"(?i)\b(passed|ok)\b", summary
    ) and "fail" not in summary.lower()[:200]:
        if getattr(result, "ok", None) is not False:
            ok = ok or "exit_code=0" in summary.lower()

    if hasattr(state, "verify_steps"):
        state.verify_steps = int(state.verify_steps or 0) + 1
    if hasattr(state, "last_verify_ok"):
        state.last_verify_ok = ok
    if hasattr(state, "last_verify_summary"):
        state.last_verify_summary = summary
    if hasattr(state, "phase"):
        state.phase = "done" if ok else "repair"
    if hasattr(state, "repair_steps") and not ok:
        state.repair_steps = int(state.repair_steps or 0) + 1
    if hasattr(state, "auto_verify_ran"):
        state.auto_verify_ran = True
    # On red, allow another auto cycle after further writes
    if not ok and hasattr(state, "auto_verify_ran"):
        # Stays True until react loop or new writes clear; cycles still counted
        pass
    if ok and hasattr(state, "clear_write_set_on_green"):
        with suppress(Exception):
            state.clear_write_set_on_green()
    # Structured error vector for repair tickets
    with suppress(Exception):
        from remedy.core.build_error_vector import parse_verify_output

        vec = parse_verify_output(summary, command=cmd, ok=ok)
        if hasattr(state, "last_error_vector"):
            state.last_error_vector = vec.to_public()

    # Persist ledger
    with suppress(Exception):
        from remedy.core.build_ledger import merge_turn_into_ledger
        from remedy.core.turn_context import turn_session_id

        proj = ""
        with suppress(Exception):
            proj = str(runtime.effective_project_path() or "")
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        merge_turn_into_ledger(
            state,
            project_path=proj,
            session_id=str(turn_session_id(runtime) or ""),
            home=home,
        )

    # Mission stickiness
    with suppress(Exception):
        from remedy.core.build_mission import note_mission_verify

        note_mission_verify(runtime, state, ok=ok, output=summary)

    # If scoped failed, note for full-suite retry next cycle
    if scoped and not ok and hasattr(state, "nudges_emitted"):
        if "scoped_failed" not in state.nudges_emitted:
            state.nudges_emitted.append("scoped_failed")

    return {
        "ok": ok,
        "auto": True,
        "oracle_missing": False,
        "summary": summary,
        "command": run_cmd,
        "full_command": cmd,
        "scoped": scoped,
        "capped": False,
        "seeded": bool(getattr(state, "oracle_seeded", False)),
    }


def should_auto_verify(state: Any) -> bool:
    """True when writes crossed threshold and machine should run falsification."""
    if state is None or not getattr(state, "active", False):
        return False
    cycles = int(getattr(state, "auto_verify_cycles", 0) or 0)
    max_c = int(getattr(state, "max_auto_verify_cycles", 6) or 6)
    if cycles >= max_c:
        return False
    writes = int(getattr(state, "write_steps", 0) or 0)
    need = int(getattr(state, "require_verify_after_writes", 2) or 2)
    verifies = int(getattr(state, "verify_steps", 0) or 0)
    # Fresh writes after last auto-verify
    if getattr(state, "auto_verify_ran", False):
        # Re-run when write_set non-empty after a red, or new mutations cleared green
        if getattr(state, "write_set", None) and getattr(state, "last_verify_ok", None) is not True:
            # Avoid infinite loop same cycle — require write_steps growth
            if "auto_verify_repair" in getattr(state, "nudges_emitted", []):
                # Allow re-verify after repair writes: clear flag when write_set grew
                if writes > verifies:
                    return True
            return False
        return False
    if writes >= need and verifies == 0:
        return True
    if writes >= need and getattr(state, "last_verify_ok", None) is False:
        if "auto_verify_repair" not in getattr(state, "nudges_emitted", []):
            return True
    # Oracle seed path: have writes but no command yet
    if writes >= 1 and not getattr(state, "verify_command", None):
        return True
    return False


def format_auto_verify_message(
    result: dict[str, Any],
    *,
    state: Any = None,
) -> dict[str, str]:
    """User-role message with machine verify results for the model."""
    if result.get("oracle_missing"):
        return oracle_missing_nudge(None)
    ok = result.get("ok")
    cmd = result.get("command") or ""
    summary = (result.get("summary") or "")[:1200]
    scoped = bool(result.get("scoped"))
    full = result.get("full_command") or cmd
    if result.get("capped"):
        return {
            "role": "user",
            "content": (
                "[Build engine · AUTO VERIFY · CAP]\n"
                f"{summary}\n"
                "Stop auto-loop thrash. Fix the last error vector carefully, "
                "then run one manual verify."
            ),
        }
    if ok:
        scope_note = " (scoped)" if scoped else ""
        return {
            "role": "user",
            "content": (
                f"[Build engine · AUTO VERIFY · GREEN{scope_note}]\n"
                f"Machine ran: `{cmd}`\n"
                + (f"Full suite available: `{full}`\n" if scoped and full != cmd else "")
                + f"{summary}\n"
                "Verify passed. You may summarize DONE if the goal is met."
            ),
        }
    # RED → structured repair ticket (error vector)
    with suppress(Exception):
        from remedy.core.build_error_vector import (
            parse_verify_output,
            repair_ticket_message,
        )

        vec = None
        if state is not None and getattr(state, "last_error_vector", None):
            from remedy.core.build_error_vector import ErrorVector

            raw = state.last_error_vector
            if isinstance(raw, dict):
                vec = ErrorVector(
                    ok=False,
                    command=str(raw.get("command") or cmd),
                    exit_hint=str(raw.get("exit_hint") or ""),
                    failing_nodes=list(raw.get("failing_nodes") or []),
                    path_lines=list(raw.get("path_lines") or []),
                    snippets=list(raw.get("snippets") or []),
                )
        if vec is None:
            vec = parse_verify_output(summary, command=cmd, ok=False)
        return repair_ticket_message(vec)
    return {
        "role": "user",
        "content": (
            f"[Build engine · AUTO VERIFY · RED]\n"
            f"Machine ran: `{cmd}`\n"
            f"{summary}\n"
            "Verify FAILED. file_edit the failing units, then re-verify. "
            "Do not claim success."
        ),
    }
