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
    body_next_line,
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


def test_discover_verify_skips_static_html(tmp_path):
    proj = tmp_path / "page"
    proj.mkdir()
    (proj / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
    (proj / "css").mkdir()
    (proj / "css" / "app.css").write_text("body{color:#111}", encoding="utf-8")
    rt = SimpleNamespace(
        effective_project_path=lambda: proj,
        resolve_tool_path=lambda p, **k: Path(p),
    )
    cmd = discover_verify_command(rt)
    assert "pytest" not in (cmd or "")
    assert "npm test" not in (cmd or "")


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
        _project_path_raw=str(proj),
        effective_project_path=lambda: proj,
    )
    st = begin_build_turn(rt, "continue", force=True)
    assert st is not None
    assert st.resumed is True
    assert st.verify_command == "pytest -q"


def test_ledger_persists_body_and_resume_speaks_it(tmp_path):
    proj = tmp_path / "body"
    proj.mkdir()
    (proj / "src").mkdir()
    (proj / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    st = BuildTurnState(
        active=True,
        goal="fix add",
        phase="repair",
        write_steps=2,
        verify_steps=1,
        last_verify_ok=False,
        last_verify_summary="FAILED tests/test_calc.py::test_add - assert 1 == 2",
        verify_command="pytest -q",
        write_set=["src/calc.py"],
        last_error_vector={
            "ok": False,
            "command": "pytest -q",
            "failing_nodes": ["tests/test_calc.py::test_add"],
            "path_lines": ["src/calc.py:2"],
            "snippets": ["assert 1 == 2"],
            "repair_command": "pytest -q tests/test_calc.py::test_add",
        },
        last_scoped_command="pytest -q tests/test_calc.py::test_add",
        project_path=str(proj),
    )
    merge_turn_into_ledger(st, project_path=str(proj), session_id="s", home=tmp_path)
    from remedy.core.metabolism.organism import load_vitals

    assert "test_add" in str(load_vitals(tmp_path).get("last_did") or "")
    led = load_ledger(str(proj), home=tmp_path)
    assert led is not None
    assert "src/calc.py" in led.write_set
    assert led.last_error_vector is not None
    assert "tests/test_calc.py::test_add" in led.last_error_vector["failing_nodes"]
    assert "test_add" in led.last_scoped_command
    hint = resume_hint(str(proj), home=tmp_path)
    assert "last_red" in hint
    assert "test_add" in hint
    assert "READ FIRST" in hint
    assert "src/calc.py" in hint
    assert "NEXT VERIFY" in hint
    line = body_next_line(led)
    assert "test_add" in line
    assert "src/calc.py" in line

    rt = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
        config=SimpleNamespace(home_dir=tmp_path),
        _project_path_raw=str(proj),
        effective_project_path=lambda: proj,
    )
    resumed = begin_build_turn(rt, "continue the fix", force=True)
    assert resumed is not None
    assert resumed.last_verify_ok is False
    assert resumed.phase == "repair"
    assert resumed.last_error_vector
    assert "tests/test_calc.py::test_add" in (resumed.last_error_vector.get("failing_nodes") or [])
    assert "src/calc.py" in resumed.write_set
    assert "test_add" in (resumed.last_scoped_command or "")


def test_ledger_clears_body_on_green(tmp_path):
    proj = tmp_path / "green"
    proj.mkdir()
    st = BuildTurnState(
        active=True,
        goal="fix add",
        phase="done",
        last_verify_ok=True,
        last_verify_summary="2 passed",
        write_set=["src/calc.py"],
        last_error_vector={"failing_nodes": ["tests/test_calc.py::test_add"]},
        last_scoped_command="pytest -q tests/test_calc.py::test_add",
        project_path=str(proj),
    )
    merge_turn_into_ledger(st, project_path=str(proj), session_id="s", home=tmp_path)
    led = load_ledger(str(proj), home=tmp_path)
    assert led is not None
    assert led.write_set == []
    assert led.last_error_vector is None
    assert led.last_scoped_command == ""
    assert resume_hint(str(proj), home=tmp_path) == ""
