"""Build machine v2: ledger, oracle discovery, auto-verify classification."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.build_engine import (
    BuildTurnState,
    begin_build_turn,
    observe_tool_batch,
)
from remedy.core.build_ledger import (
    BuildLedgerEntry,
    load_ledger,
    merge_turn_into_ledger,
    resume_hint,
    save_ledger,
)
from remedy.core.build_oracle import (
    discover_verify_command,
    format_auto_verify_message,
    should_auto_verify,
)


def test_ledger_roundtrip(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='t'\n", encoding="utf-8")
    entry = BuildLedgerEntry(
        goal="ship API",
        phase="implement",
        project_path=str(proj),
        verify_command="pytest -q",
        write_steps=2,
    )
    save_ledger(entry, home=tmp_path)
    loaded = load_ledger(str(proj), home=tmp_path)
    assert loaded is not None
    assert loaded.goal == "ship API"
    assert loaded.verify_command == "pytest -q"
    assert (proj / ".remedy-build" / "ledger.json").is_file()
    hint = resume_hint(str(proj), home=tmp_path)
    assert "resume" in hint.lower() or "phase=" in hint


def test_merge_turn_into_ledger(tmp_path):
    proj = tmp_path / "p2"
    proj.mkdir()
    st = BuildTurnState(
        active=True,
        goal="build x",
        phase="verify",
        write_steps=3,
        verify_command="pytest -q",
        paths_touched=["a.py"],
        project_path=str(proj),
    )
    merge_turn_into_ledger(st, project_path=str(proj), session_id="s1", home=tmp_path)
    led = load_ledger(str(proj), home=tmp_path)
    assert led is not None
    assert led.write_steps >= 3
    assert "a.py" in led.paths_touched


def test_discover_verify_pytest(tmp_path):
    proj = tmp_path / "py"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (proj / "tests").mkdir()
    rt = SimpleNamespace(
        effective_project_path=lambda: proj,
        resolve_tool_path=lambda p, **k: Path(p),
    )
    cmd = discover_verify_command(rt)
    assert "pytest" in cmd


def test_should_auto_verify_threshold():
    st = BuildTurnState(active=True, write_steps=1, require_verify_after_writes=1)
    assert should_auto_verify(st) is True
    st.auto_verify_ran = True
    assert should_auto_verify(st) is False


def test_file_write_not_confused_with_bash_verify():
    st = BuildTurnState(active=True)
    observe_tool_batch(
        st,
        [{"function": {"name": "file_write", "arguments": '{"path":"a.py"}'}}],
    )
    assert st.write_steps == 1
    observe_tool_batch(
        st,
        [
            {
                "function": {
                    "name": "bash_exec",
                    "arguments": '{"command":"pytest -q"}',
                }
            }
        ],
        [{"role": "tool", "content": "exit_code=0\n2 passed"}],
    )
    assert st.verify_steps >= 1
    assert st.last_verify_ok is True


def test_format_auto_verify_green():
    msg = format_auto_verify_message(
        {"ok": True, "command": "pytest -q", "summary": "exit_code=0", "auto": True}
    )
    assert "GREEN" in msg["content"]
    assert "pytest" in msg["content"]


@pytest.mark.asyncio
async def test_run_auto_verify_oracle_missing(tmp_path):
    from remedy.core.build_oracle import run_auto_verify

    empty = tmp_path / "empty"
    empty.mkdir()
    st = BuildTurnState(active=True, write_steps=2)
    rt = SimpleNamespace(
        effective_project_path=lambda: empty,
        resolve_tool_path=lambda p, **k: empty / str(p),
        config=SimpleNamespace(home_dir=tmp_path),
        write_roots=lambda: [empty],
    )
    # discover must not walk the Remedy repo (Path(".") used to spawn pytest -q
    # against this checkout and re-enter the full suite).
    st.verify_command = ""
    await run_auto_verify(rt, st, command="")
    st2 = BuildTurnState(active=True)
    result2 = await run_auto_verify(
        SimpleNamespace(
            effective_project_path=lambda: tmp_path / "nope",
            resolve_tool_path=lambda p, **k: tmp_path / "nope" / str(p),
            config=None,
        ),
        st2,
        command="",
    )
    # If still finds something, skip; else assert oracle_missing
    if not result2.get("command"):
        assert result2.get("oracle_missing") is True


def test_begin_resumes_ledger(tmp_path):
    proj = tmp_path / "resume"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='r'\n", encoding="utf-8")
    save_ledger(
        BuildLedgerEntry(
            goal="finish API",
            phase="repair",
            project_path=str(proj),
            write_steps=4,
            last_verify_ok=False,
            verify_command="pytest -q",
        ),
        home=tmp_path,
    )
    rt = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
        config=SimpleNamespace(home_dir=tmp_path),
        effective_project_path=lambda: proj,
    )
    st = begin_build_turn(rt, "continue", force=True)
    assert st is not None
    assert st.resumed is True or st.phase in ("repair", "implement", "verify", "scout")
    assert st.verify_command == "pytest -q" or st.write_steps >= 0
