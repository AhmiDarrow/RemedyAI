"""Full-suite host_run is a checkpoint, not a spawn loop."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.build_engine import BuildTurnState, observe_tool_batch
from remedy.core.build_verify_gate import (
    VERIFY_CACHED_PREFIX,
    VERIFY_DEFERRED_PREFIX,
    is_full_suite_verify,
    maybe_short_circuit_verify,
)


def test_full_suite_classifier() -> None:
    assert is_full_suite_verify(argv=["npm", "test"])
    assert is_full_suite_verify("npm test -- --run")
    assert is_full_suite_verify("pytest -q")
    assert not is_full_suite_verify(argv=["npx", "vitest", "run", "src/lib/foo.test.ts"])
    assert not is_full_suite_verify("pytest -q tests/test_foo.py")
    assert not is_full_suite_verify(argv=["gcc", "-o", "hello.exe", "hello.c"])


def test_defers_while_feature_todos_open() -> None:
    st = BuildTurnState(active=True, open_feature_todo_count=7, goal="build the converter")
    rt = SimpleNamespace(_build_turn=st)
    out = maybe_short_circuit_verify(rt, argv=["npm", "test"])
    assert out is not None
    assert out.startswith(VERIFY_DEFERRED_PREFIX)
    assert "exit_code=0" in out


def test_caches_when_green_and_no_new_source() -> None:
    st = BuildTurnState(
        active=True,
        last_verify_ok=True,
        last_verify_summary="16/16 passed",
        write_steps=4,
        write_steps_at_last_green=4,
        goal="keep going",
    )
    rt = SimpleNamespace(_build_turn=st)
    out = maybe_short_circuit_verify(rt, argv=["npm", "test"])
    assert out is not None
    assert out.startswith(VERIFY_CACHED_PREFIX)
    assert "16/16" in out


def test_runs_after_new_source_write() -> None:
    st = BuildTurnState(
        active=True,
        last_verify_ok=True,
        write_set=["src/lib/audioToMidi.ts"],
        write_steps=5,
        write_steps_at_last_green=4,
        goal="keep going",
    )
    rt = SimpleNamespace(_build_turn=st)
    assert maybe_short_circuit_verify(rt, argv=["npm", "test"]) is None


def test_runs_when_red() -> None:
    st = BuildTurnState(
        active=True,
        last_verify_ok=False,
        open_feature_todo_count=3,
        goal="fix tests",
    )
    rt = SimpleNamespace(_build_turn=st)
    assert maybe_short_circuit_verify(rt, argv=["npm", "test"]) is None


def test_owner_run_tests_carve_out() -> None:
    st = BuildTurnState(
        active=True,
        last_verify_ok=True,
        open_feature_todo_count=2,
        goal="run the tests",
    )
    rt = SimpleNamespace(_build_turn=st)
    assert maybe_short_circuit_verify(rt, argv=["npm", "test"]) is None


def test_observe_host_run_argv_as_verify() -> None:
    st = BuildTurnState(active=True, write_steps=2, write_set=["src/a.ts"])
    observe_tool_batch(
        st,
        [{"id": "1", "name": "host_run", "arguments": {"argv": ["npm", "test"]}}],
        [{"role": "tool", "tool_call_id": "1", "content": "exit_code=0\n16 passed"}],
    )
    assert st.last_verify_ok is True


def test_observe_deferred_is_not_red() -> None:
    st = BuildTurnState(active=True, write_steps=1)
    observe_tool_batch(
        st,
        [{"id": "1", "name": "host_run", "arguments": {"argv": ["npm", "test"]}}],
        [
            {
                "role": "tool",
                "tool_call_id": "1",
                "content": VERIFY_DEFERRED_PREFIX + " reason=feature_items_open\nexit_code=0",
            }
        ],
    )
    assert st.last_verify_ok is not False


def test_engine_commands_count_as_full_suite_verify() -> None:
    for cmd in (
        r".\Godot_v4.3-stable_win64_console.exe --headless --path . --quit-after 1",
        r".\Godot_v4.3_console.exe --headless --path . -s tools/smoke_boot.gd",
        "godot4 --headless --path . --quit-after 1",
        "cargo check",
        "npm run build",
        "luac -p main.lua",
    ):
        assert is_full_suite_verify(cmd), cmd
    # A windowed run never exits — not a suite.
    assert not is_full_suite_verify(r".\Godot_v4.3.exe --path .")
    assert not is_full_suite_verify("godot --headless --path .")
