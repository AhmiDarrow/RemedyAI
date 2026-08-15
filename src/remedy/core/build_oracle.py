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


def _discover_c_verify_command(root: Any) -> str:
    """gcc compile+run for a simple C project (hello.c / main.c / single .c)."""
    import os
    from pathlib import Path

    p = Path(root)
    if not p.is_dir():
        return ""
    candidates: list[Path] = []
    for name in ("hello.c", "main.c", "app.c", "program.c"):
        c = p / name
        if c.is_file():
            candidates.append(c)
            break
    if not candidates:
        c_files = sorted(p.glob("*.c"))
        if len(c_files) == 1:
            candidates = c_files
        elif c_files:
            # Prefer a file that looks like an entrypoint
            for c in c_files:
                try:
                    text = c.read_text(encoding="utf-8", errors="replace")[:4000]
                except OSError:
                    text = ""
                if "int main" in text or "main(" in text:
                    candidates = [c]
                    break
            if not candidates:
                candidates = [c_files[0]]
    if not candidates:
        return ""
    src = candidates[0]
    stem = src.stem
    rel = src.name
    if os.name == "nt":
        # cmd.exe-friendly chain; PATH must include gcc (Mingw/scoop)
        return f"gcc -o {stem}.exe {rel} && {stem}.exe"
    return f"gcc -o {stem} {rel} && ./{stem}"


def discover_verify_command(runtime: Any, *, path: str = "") -> str:
    """Return best-effort verify command for the active project (may be empty)."""
    cmd = ""
    root = None
    with suppress(Exception):
        from remedy.core.project_fingerprint import fingerprint_path

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

            if root is None:
                root = runtime.effective_project_path()
            p = Path(root)
            if (p / "pyproject.toml").exists() or (p / "pytest.ini").exists() or (
                p / "tests"
            ).is_dir():
                # Prefer real Python markers; empty tests/ from a bad seed does not count
                if (p / "pyproject.toml").exists() or (p / "pytest.ini").exists():
                    cmd = "pytest -q"
                else:
                    # tests/ only — use pytest only if non-seed tests exist
                    real_tests = [
                        t
                        for t in (p / "tests").rglob("test_*.py")
                        if t.name != "test_remedy_build_smoke.py"
                    ]
                    if real_tests or list((p / "tests").rglob("*_test.py")):
                        cmd = "pytest -q"
            if not cmd and (p / "package.json").exists():
                cmd = "npm test"
            if not cmd and (p / "Cargo.toml").exists():
                cmd = "cargo test"
            if not cmd and (p / "go.mod").exists():
                cmd = "go test ./..."
            if not cmd:
                # Simple C program tasks (hello.c etc.)
                cmd = _discover_c_verify_command(p)
    # Even when fingerprint suggested something, C-only trees need gcc
    if not cmd or cmd.startswith("pytest"):
        with suppress(Exception):
            from pathlib import Path

            p = Path(root if root is not None else runtime.effective_project_path())
            c_files = list(p.glob("*.c"))
            py_markers = (p / "pyproject.toml").exists() or (p / "setup.py").exists()
            if c_files and not py_markers:
                c_cmd = _discover_c_verify_command(p)
                if c_cmd:
                    cmd = c_cmd
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
        # Prefer native C verify before planting Python smoke
        with suppress(Exception):
            from pathlib import Path

            root = runtime.effective_project_path()
            c_cmd = _discover_c_verify_command(Path(root))
            writes = [str(w).lower() for w in (state.write_set or [])]
            if c_cmd and any(w.endswith((".c", ".cpp", ".cc")) for w in writes):
                cmd = c_cmd
                if hasattr(state, "verify_command"):
                    state.verify_command = cmd
                if hasattr(state, "oracle_ok"):
                    state.oracle_ok = True
        if not cmd:
            with suppress(Exception):
                from remedy.core.build_seed_oracle import seed_python_smoke_oracle

                if not getattr(state, "oracle_seeded", False):
                    seed = seed_python_smoke_oracle(
                        runtime,
                        list(state.write_set or []),
                        home=getattr(
                            getattr(runtime, "config", None), "home_dir", None
                        ),
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
    # Prefer the last failing pytest nodeids over a write-set guess.
    with suppress(Exception):
        raw = getattr(state, "last_error_vector", None)
        if isinstance(raw, dict) and not bool(raw.get("ok")):
            from remedy.core.build_error_vector import scoped_pytest_from_nodes

            sc_fail = scoped_pytest_from_nodes(
                list(raw.get("failing_nodes") or []),
                base=cmd,
            )
            if not sc_fail:
                sc_fail = str(raw.get("repair_command") or "").strip()
            if sc_fail:
                run_cmd = sc_fail
                scoped = True
                state.last_scoped_command = sc_fail
    if prefer_scoped and not scoped and getattr(state, "write_set", None):
        with suppress(Exception):
            from remedy.core.build_scoped import scoped_verify_command

            sc = scoped_verify_command(
                runtime, list(state.write_set or []), base_command=cmd
            )
            if sc:
                run_cmd = sc
                scoped = True
                state.last_scoped_command = sc

    # GUI / game sources: compile or py_compile only. Running pygame / a
    # windowed .exe here hung the turn for up to 300s and killed the window.
    verify_timeout = 300.0
    with suppress(Exception):
        from pathlib import Path as _P

        from remedy.core.interactive_launch import (
            compile_only_verify_command,
            write_set_looks_like_gui,
        )

        root_p = None
        with suppress(Exception):
            root_p = _P(runtime.effective_project_path())
        writes = list(getattr(state, "write_set", None) or [])
        if write_set_looks_like_gui(writes, cwd=root_p):
            adapted = compile_only_verify_command(run_cmd)
            if adapted and adapted != run_cmd:
                run_cmd = adapted
                scoped = True
        # Console `gcc && hello.exe` should finish in seconds; don't wait 300s
        if re.search(r"(?i)\.exe\b", run_cmd) and "pytest" not in run_cmd.lower():
            verify_timeout = 20.0

    result = await run_verify_job(runtime, command=run_cmd, timeout=verify_timeout)
    ok = bool(getattr(result, "ok", False))
    summary = str(getattr(result, "summary", "") or "")[:2000]
    # Ask-mode / jail is not a test failure — do not enter repair
    if "APPROVAL_REQUIRED" in summary or summary.startswith("WRITE_JAIL"):
        return {
            "ok": False,
            "auto": True,
            "blocked": True,
            "approval": "APPROVAL_REQUIRED" in summary,
            "oracle_missing": False,
            "summary": summary,
            "command": run_cmd,
            "full_command": cmd,
            "scoped": scoped,
            "capped": False,
            "seeded": bool(getattr(state, "oracle_seeded", False)),
        }
    # Count only real process results toward the auto-verify cap
    if hasattr(state, "auto_verify_cycles"):
        state.auto_verify_cycles = cycles + 1
    # Official runner line only — stdout may mention other exit_code=0
    m_exit = re.search(r"(?im)^(?:verify\s+)?exit_code=(\d+)", summary)
    if m_exit:
        ok = m_exit.group(1) == "0"

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
    """True when writes crossed threshold and machine should run falsification.

    Cooldown after green: only re-fire when *source* files were written past
    ``write_steps_at_last_green`` (docs/tmp/yml thrash does not re-verify).
    """
    if state is None or not getattr(state, "active", False):
        return False
    cycles = int(getattr(state, "auto_verify_cycles", 0) or 0)
    max_c = int(getattr(state, "max_auto_verify_cycles", 6) or 6)
    if cycles >= max_c:
        return False
    writes = int(getattr(state, "write_steps", 0) or 0)
    need = int(getattr(state, "require_verify_after_writes", 1) or 1)
    verifies = int(getattr(state, "verify_steps", 0) or 0)
    write_set = list(getattr(state, "write_set", None) or [])
    has_c = any(
        str(w).lower().endswith((".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"))
        for w in write_set
    )
    # C/C++ partner tasks: verify after the first source write (compile+run)
    if has_c:
        need = 1

    # Source-write helpers (BuildTurnState methods if present)
    source_pending: list[str] = []
    with suppress(Exception):
        if hasattr(state, "source_writes_pending"):
            source_pending = list(state.source_writes_pending() or [])
        else:
            from remedy.core.build_engine import _is_source_path

            source_pending = [p for p in write_set if _is_source_path(p)]

    last_green_ws = int(getattr(state, "write_steps_at_last_green", 0) or 0)
    green_ok = getattr(state, "last_verify_ok", None) is True
    auto_ran = bool(getattr(state, "auto_verify_ran", False))

    # Hard rule: no source mutation this turn → never auto-verify
    # (stops continue/proceed thrash re-running npm test for ~30–60s silence)
    if writes <= 0 and not source_pending:
        return False
    if not write_set and not source_pending and writes <= last_green_ws and green_ok:
        return False

    # Cooldown: green + no new source writes → never re-auto-verify
    if green_ok and not source_pending and writes <= last_green_ws:
        return False
    if green_ok and auto_ran and not source_pending:
        return False
    # Ship / done phase: do not thrash tests while pushing or after green
    phase = str(getattr(state, "phase", "") or "")
    if phase in ("ship", "done") and green_ok and not source_pending:
        return False

    # Fresh writes after last auto-verify
    if auto_ran:
        # Re-run only after *source* mutations post-green or red repair growth
        if source_pending and not green_ok:
            return bool(writes > last_green_ws or writes > verifies)
        if source_pending and green_ok:
            # New source after green → re-verify once
            return writes > last_green_ws
        if write_set and not green_ok:
            if "auto_verify_repair" in getattr(state, "nudges_emitted", []):
                if writes > verifies:
                    return True
            return False
        return False
    if writes >= need and verifies == 0:
        return True
    if writes >= need and getattr(state, "last_verify_ok", None) is False:
        if "auto_verify_repair" not in getattr(state, "nudges_emitted", []):
            return True
    # Source mutated after green was invalidated (auto_ran cleared)
    if (
        source_pending
        and writes > last_green_ws
        and getattr(state, "last_verify_ok", None) is not True
    ):
        return True
    # Oracle seed path: have writes but no command yet
    if writes >= 1 and not getattr(state, "verify_command", None):
        return True
    # C write present and never verified this turn
    return bool(has_c and writes >= 1 and verifies == 0)


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
        keep = False
        with suppress(Exception):
            from remedy.core.build_engine import keep_agency_after_green

            keep = bool(state is not None and keep_agency_after_green(state))
        done_line = (
            "Verify passed. Tools stay on — play/ship/iterate if the goal needs it."
            if keep
            else "Verify passed. You may summarize DONE if the goal is met."
        )
        return {
            "role": "user",
            "content": (
                f"[Build engine · AUTO VERIFY · GREEN{scope_note}]\n"
                f"Machine ran: `{cmd}`\n"
                + (f"Full suite available: `{full}`\n" if scoped and full != cmd else "")
                + f"{summary}\n"
                + done_line
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
                vec = ErrorVector.from_public(raw)
                if not vec.command:
                    vec.command = cmd
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
