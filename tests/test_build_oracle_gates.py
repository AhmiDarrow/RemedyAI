"""The gates around machine-owned falsification: when it runs, and what counts as green.

`build_oracle` is the part of the build engine that decides, without asking the
model, whether to run the tests and whether the result was a pass. Every way this
goes wrong is expensive:

* Calling a red run green — a timed-out or blocked verify that reports `ok` clears
  the unverified write set and lets the model announce DONE over work nobody ran.
* Never stopping — auto-verify re-firing on every "continue" burns 30-60s of
  silence per turn on a suite that already passed and has no new source writes.
* Swallowing a refusal — an approval prompt or a write-jail block is not a test
  failure, and treating it as one sends the model into a repair loop against code
  that is fine.

Nothing here starts a real process: `run_verify_job` is stubbed out for every test
in this module, so no test command is ever spawned.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.build_engine import BuildTurnState
from remedy.core.build_oracle import (
    _cheap_required_files_ok,
    format_auto_verify_message,
    oracle_missing_nudge,
    run_auto_verify,
    should_auto_verify,
)


class RT:
    """The slice of the runtime that build_oracle actually touches."""

    def __init__(self, root: Path, home: Path | None = None) -> None:
        self.root = Path(root)
        self.config = SimpleNamespace(home_dir=home or Path(root))

    def effective_project_path(self):
        return self.root

    def resolve_tool_path(self, path, *, for_write=False):
        return self.root / (path or ".")

    def write_roots(self):
        return [self.root]

    def project_path_is_unset(self):
        return False

    def access_scope(self):
        return "project"


class FakeVerify:
    """Stand-in for jobs.run_verify_job that records how it was called."""

    def __init__(self) -> None:
        self.calls: list[SimpleNamespace] = []
        self.ok = True
        self.summary = "verify exit_code=0\n1 passed"

    async def __call__(self, runtime, *, command="", path="", timeout=180.0):
        self.calls.append(SimpleNamespace(command=command, timeout=timeout))
        return SimpleNamespace(ok=self.ok, summary=self.summary)

    @property
    def command(self) -> str:
        return self.calls[-1].command

    @property
    def timeout(self) -> float:
        return self.calls[-1].timeout


@pytest.fixture(autouse=True)
def verify(monkeypatch):
    """Autouse so no test in this module can ever spawn a real test command."""
    fake = FakeVerify()
    monkeypatch.setattr("remedy.core.jobs.run_verify_job", fake)
    return fake


@pytest.fixture()
def rt(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    return RT(proj, home=tmp_path)


@pytest.fixture()
def no_discovery(monkeypatch):
    """Force discovery to find nothing, to reach the fail-closed boundary."""
    monkeypatch.setattr("remedy.core.build_oracle.discover_verify_command", lambda *a, **k: "")


# --- the fail-closed nudge ----------------------------------------------------


def test_the_oracle_missing_nudge_is_a_user_turn_the_model_must_act_on():
    msg = oracle_missing_nudge(None)
    assert msg["role"] == "user"
    assert "ORACLE MISSING" in msg["content"]
    assert "fail closed" in msg["content"]


def test_the_oracle_missing_nudge_forbids_claiming_done():
    assert "cannot claim DONE" in oracle_missing_nudge(None)["content"]


def test_the_oracle_missing_nudge_names_every_way_out():
    content = oracle_missing_nudge(None)["content"]
    for route in ("file_write", "bash_exec", "mission_start"):
        assert route in content


# --- the cheap "did the goal files land" check --------------------------------


def test_no_state_at_all_is_not_evidence_that_files_landed():
    assert _cheap_required_files_ok(None, None) is False


def test_a_state_still_missing_required_files_is_refused(tmp_path):
    st = SimpleNamespace(missing_required_files=lambda: ["index.html"])
    assert _cheap_required_files_ok(RT(tmp_path), st) is False


def test_named_required_files_with_nothing_missing_is_enough(tmp_path):
    st = SimpleNamespace(
        missing_required_files=lambda: [],
        named_required_files=lambda: ["index.html"],
    )
    assert _cheap_required_files_ok(RT(tmp_path), st) is True


def test_a_goal_that_is_not_about_a_page_gets_no_free_pass(tmp_path):
    """Only the HTML shape has a filesystem shortcut; everything else must run."""
    st = SimpleNamespace(goal="refactor the parser", write_set=["parser.py"])
    assert _cheap_required_files_ok(RT(tmp_path), st) is False


@pytest.mark.parametrize(
    "goal",
    ["build a landing page", "make an html file", "a web page for the shop", "start a wiki"],
)
def test_a_page_goal_is_satisfied_by_a_non_empty_page_on_disk(tmp_path, goal):
    (tmp_path / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    st = SimpleNamespace(goal=goal, write_set=[], project_path=str(tmp_path))
    assert _cheap_required_files_ok(RT(tmp_path), st) is True


def test_an_empty_page_file_does_not_count_as_landed(tmp_path):
    (tmp_path / "index.html").write_text("", encoding="utf-8")
    st = SimpleNamespace(goal="build a landing page", write_set=[], project_path=str(tmp_path))
    assert _cheap_required_files_ok(RT(tmp_path), st) is False


def test_a_page_goal_with_no_page_anywhere_is_refused(tmp_path):
    st = SimpleNamespace(goal="build a landing page", write_set=[], project_path=str(tmp_path))
    assert _cheap_required_files_ok(RT(tmp_path), st) is False


@pytest.mark.parametrize("written", ["style.css", "about.htm", "page.HTML"])
def test_a_page_shaped_write_set_opens_the_check_even_without_a_page_goal(tmp_path, written):
    (tmp_path / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    st = SimpleNamespace(goal="do the thing", write_set=[written], project_path=str(tmp_path))
    assert _cheap_required_files_ok(RT(tmp_path), st) is True


def test_without_a_project_path_the_runtime_root_is_used(tmp_path):
    (tmp_path / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    st = SimpleNamespace(goal="build a landing page", write_set=[], project_path="")
    assert _cheap_required_files_ok(RT(tmp_path), st) is True


def test_with_no_project_path_and_no_runtime_there_is_nothing_to_check():
    st = SimpleNamespace(goal="build a landing page", write_set=[], project_path="")
    assert _cheap_required_files_ok(None, st) is False


def test_the_state_project_path_wins_over_the_runtime(tmp_path):
    """The build's own root, not whatever the runtime happens to point at now."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    st = SimpleNamespace(goal="build a landing page", write_set=[], project_path=str(other))
    assert _cheap_required_files_ok(RT(empty), st) is True


# --- run_auto_verify: which command runs --------------------------------------


@pytest.mark.asyncio
async def test_an_explicit_command_beats_the_one_on_the_state(rt, verify):
    st = BuildTurnState(active=True, verify_command="npm test")
    await run_auto_verify(rt, st, command="pytest -q")
    assert verify.command == "pytest -q"
    assert st.verify_command == "pytest -q"


@pytest.mark.asyncio
async def test_the_state_command_is_used_when_none_is_passed(rt, verify):
    st = BuildTurnState(active=True, verify_command="cargo test")
    await run_auto_verify(rt, st)
    assert verify.command == "cargo test"


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_discovery")
async def test_a_whitespace_command_argument_currently_discards_the_state_command(rt, verify):
    """Documents today's behaviour: `command or state.verify_command` treats "   "
    as a real command, so it strips to empty and the state's oracle is skipped
    rather than used as the fallback the signature implies."""
    st = BuildTurnState(active=True, verify_command="npm test")
    res = await run_auto_verify(rt, st, command="   ")
    assert verify.calls == []
    assert res["oracle_missing"] is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_discovery")
async def test_with_no_command_anywhere_the_engine_fails_closed(rt, verify):
    st = BuildTurnState(active=True, write_steps=2)
    res = await run_auto_verify(rt, st)
    assert res["oracle_missing"] is True
    assert res["ok"] is False
    assert res["command"] == ""
    assert verify.calls == []
    assert st.oracle_ok is False
    assert "oracle_missing" in st.nudges_emitted


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_discovery")
async def test_the_oracle_missing_nudge_is_recorded_once_not_per_call(rt):
    st = BuildTurnState(active=True, write_steps=2)
    await run_auto_verify(rt, st)
    await run_auto_verify(rt, st)
    assert st.nudges_emitted.count("oracle_missing") == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_discovery")
async def test_a_missing_oracle_does_not_count_as_a_verify_step(rt):
    st = BuildTurnState(active=True, write_steps=2)
    await run_auto_verify(rt, st)
    assert st.verify_steps == 0
    assert st.auto_verify_cycles == 0


# --- run_auto_verify: seeding an oracle ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_discovery")
async def test_a_c_write_gets_a_compile_and_run_oracle_rather_than_python_smoke(rt, verify):
    (rt.root / "hello.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    st = BuildTurnState(active=True, write_steps=1, write_set=["hello.c"])
    await run_auto_verify(rt, st)
    assert "gcc" in verify.command
    assert "pytest" not in verify.command
    assert st.oracle_ok is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_discovery")
async def test_a_python_write_with_no_oracle_seeds_a_smoke_test(rt, verify, monkeypatch):
    monkeypatch.setattr(
        "remedy.core.build_seed_oracle.seed_python_smoke_oracle",
        lambda runtime, writes, **kw: {"ok": True, "command": "pytest -q tests/test_smoke.py"},
    )
    st = BuildTurnState(active=True, write_steps=1, write_set=["app.py"])
    res = await run_auto_verify(rt, st)
    assert verify.command == "pytest -q tests/test_smoke.py"
    assert st.oracle_seeded is True
    assert st.oracle_ok is True
    assert res["seeded"] is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_discovery")
async def test_a_refused_seed_leaves_the_oracle_missing_rather_than_inventing_one(rt, verify):
    """seed_python_smoke_oracle refuses junk write sets; that refusal must stick."""
    st = BuildTurnState(active=True, write_steps=1, write_set=["123-bad.py"])
    res = await run_auto_verify(rt, st)
    assert res["oracle_missing"] is True
    assert verify.calls == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("no_discovery")
async def test_the_smoke_oracle_is_seeded_at_most_once_per_build(rt, monkeypatch):
    seeds: list[int] = []

    def _seed(runtime, writes, **kw):
        seeds.append(1)
        return {"ok": True, "command": "pytest -q"}

    monkeypatch.setattr("remedy.core.build_seed_oracle.seed_python_smoke_oracle", _seed)
    st = BuildTurnState(active=True, write_steps=1, write_set=["app.py"], oracle_seeded=True)
    await run_auto_verify(rt, st)
    assert seeds == []


# --- run_auto_verify: the convergence cap -------------------------------------


@pytest.mark.asyncio
async def test_the_cycle_cap_stops_the_auto_loop_without_running_anything(rt, verify):
    st = BuildTurnState(active=True, verify_command="pytest -q", auto_verify_cycles=6)
    res = await run_auto_verify(rt, st)
    assert res["capped"] is True
    assert res["ok"] is False
    assert verify.calls == []
    assert st.verify_steps == 0


@pytest.mark.asyncio
async def test_the_cap_is_the_states_own_and_is_named_in_the_escalation(rt):
    st = BuildTurnState(
        active=True,
        verify_command="pytest -q",
        auto_verify_cycles=2,
        max_auto_verify_cycles=2,
    )
    res = await run_auto_verify(rt, st)
    assert res["capped"] is True
    assert "(2)" in res["summary"]


@pytest.mark.asyncio
async def test_one_cycle_below_the_cap_still_runs(rt, verify):
    st = BuildTurnState(
        active=True,
        verify_command="pytest -q",
        auto_verify_cycles=1,
        max_auto_verify_cycles=2,
    )
    res = await run_auto_verify(rt, st)
    assert res.get("capped") is False
    assert len(verify.calls) == 1
    assert st.auto_verify_cycles == 2


# --- run_auto_verify: refusals are not failures -------------------------------


@pytest.mark.asyncio
async def test_an_approval_prompt_is_not_a_test_failure(rt, verify):
    verify.ok = False
    verify.summary = "APPROVAL_REQUIRED id=7\nreason=shell\ncommand=pytest -q"
    st = BuildTurnState(active=True, verify_command="pytest -q")
    res = await run_auto_verify(rt, st)
    assert res["blocked"] is True
    assert res["approval"] is True
    assert st.phase == "scout"
    assert st.repair_steps == 0
    assert st.auto_verify_cycles == 0
    assert st.verify_steps == 0


@pytest.mark.asyncio
async def test_a_write_jail_block_is_not_a_test_failure(rt, verify):
    verify.ok = False
    verify.summary = "WRITE_JAIL: cannot resolve write roots\nShell verify was not run"
    st = BuildTurnState(active=True, verify_command="pytest -q")
    res = await run_auto_verify(rt, st)
    assert res["blocked"] is True
    assert res["approval"] is False
    assert st.phase == "scout"


@pytest.mark.asyncio
async def test_a_test_that_merely_prints_write_jail_is_still_a_red_run(rt, verify):
    """The jail marker only counts when the runner itself emitted it first."""
    verify.ok = False
    verify.summary = "verify exit_code=1\n1 failed — assert 'WRITE_JAIL' in out"
    st = BuildTurnState(active=True, verify_command="pytest -q")
    res = await run_auto_verify(rt, st)
    assert res.get("blocked") is None
    assert res["ok"] is False
    assert st.phase == "repair"


# --- run_auto_verify: what counts as green ------------------------------------


@pytest.mark.asyncio
async def test_the_runner_exit_line_overrides_a_job_that_claimed_success(rt, verify):
    verify.ok = True
    verify.summary = "verify exit_code=1\n1 failed"
    st = BuildTurnState(active=True, verify_command="pytest -q")
    res = await run_auto_verify(rt, st)
    assert res["ok"] is False
    assert st.last_verify_ok is False
    assert st.phase == "repair"
    assert st.repair_steps == 1


@pytest.mark.asyncio
async def test_the_runner_exit_line_also_rescues_a_job_that_claimed_failure(rt, verify):
    verify.ok = False
    verify.summary = "verify exit_code=0\n3 passed"
    st = BuildTurnState(active=True, verify_command="pytest -q")
    res = await run_auto_verify(rt, st)
    assert res["ok"] is True
    assert st.phase == "done"


@pytest.mark.asyncio
async def test_an_exit_code_mentioned_mid_line_does_not_fake_a_pass(rt, verify):
    """Only a line-start exit_code is the runner speaking; stdout is not."""
    verify.ok = False
    verify.summary = "verify exit_code=2\nchild process reported exit_code=0 (ignore me)"
    st = BuildTurnState(active=True, verify_command="pytest -q")
    res = await run_auto_verify(rt, st)
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_a_green_run_clears_the_unverified_write_set(rt, verify):
    verify.summary = "verify exit_code=0\n3 passed"
    st = BuildTurnState(active=True, verify_command="pytest -q", write_steps=3)
    st.write_set = ["app.py"]
    res = await run_auto_verify(rt, st)
    assert res["ok"] is True
    assert st.write_set == []
    assert st.write_steps_at_last_green == 3
    assert st.auto_verify_ran is True


@pytest.mark.asyncio
async def test_a_red_run_keeps_the_write_set_for_repair(rt, verify):
    verify.ok = False
    verify.summary = "verify exit_code=1\n1 failed"
    st = BuildTurnState(active=True, verify_command="pytest -q", write_steps=3)
    st.write_set = ["app.py"]
    await run_auto_verify(rt, st)
    assert st.write_set == ["app.py"]
    assert st.phase == "repair"


# --- run_auto_verify: timeouts are never green --------------------------------


TIMED_OUT = "verify exit_code=-1\ncommand=pytest -q\nstderr:\nCommand timed out after 45s"


@pytest.mark.asyncio
async def test_a_timed_out_verify_is_not_a_pass(rt, verify):
    verify.ok = True  # the sandbox may still hand back a truthy result
    verify.summary = TIMED_OUT
    st = BuildTurnState(active=True, verify_command="pytest -q", write_steps=2)
    st.write_set = ["app.py"]
    res = await run_auto_verify(rt, st)
    assert res["ok"] is False
    assert st.write_set == ["app.py"]
    assert st.phase == "verify"


@pytest.mark.asyncio
async def test_a_timed_out_verify_is_not_counted_as_a_repair_step(rt, verify):
    verify.summary = TIMED_OUT
    st = BuildTurnState(active=True, verify_command="pytest -q")
    await run_auto_verify(rt, st)
    assert st.repair_steps == 0


@pytest.mark.asyncio
async def test_a_timeout_drops_the_scoped_command_so_the_next_run_is_the_full_suite(rt, verify):
    verify.summary = TIMED_OUT
    st = BuildTurnState(active=True, verify_command="pytest -q", last_scoped_command="pytest -q x")
    await run_auto_verify(rt, st)
    assert st.last_scoped_command == ""


@pytest.mark.asyncio
async def test_a_timeout_with_the_goal_files_still_missing_goes_back_to_implement(rt, verify):
    verify.summary = TIMED_OUT
    st = BuildTurnState(active=True, verify_command="pytest -q", goal="write report.md")
    st.project_path = str(rt.root)
    await run_auto_verify(rt, st)
    assert st.phase == "implement"


@pytest.mark.asyncio
async def test_files_on_disk_after_a_timeout_are_reported_but_never_as_a_pass(rt, verify):
    (rt.root / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    verify.summary = TIMED_OUT
    st = BuildTurnState(active=True, verify_command="pytest -q", goal="build a landing page")
    st.project_path = str(rt.root)
    res = await run_auto_verify(rt, st)
    assert res["ok"] is False
    assert res["summary"].startswith("verify timed_out=true")
    assert "NOT a pass" in res["summary"]


@pytest.mark.asyncio
async def test_a_timeout_records_the_write_watermark_so_the_same_wave_does_not_rehang(rt, verify):
    verify.summary = TIMED_OUT
    st = BuildTurnState(active=True, verify_command="pytest -q", write_steps=4)
    await run_auto_verify(rt, st)
    assert st.write_steps_at_last_green == 4
    assert should_auto_verify(st) is False


# --- run_auto_verify: scoping -------------------------------------------------


@pytest.mark.asyncio
async def test_the_last_failing_nodeids_are_preferred_over_a_write_set_guess(rt, verify):
    st = BuildTurnState(active=True, verify_command="pytest -q")
    st.write_set = ["app.py"]
    st.last_error_vector = {"ok": False, "failing_nodes": ["tests/test_a.py::test_x"]}
    res = await run_auto_verify(rt, st)
    assert "tests/test_a.py::test_x" in verify.command
    assert res["scoped"] is True
    assert res["full_command"] == "pytest -q"


@pytest.mark.asyncio
async def test_a_green_error_vector_is_not_used_to_narrow_the_next_run(rt, verify):
    st = BuildTurnState(active=True, verify_command="pytest -q")
    st.last_error_vector = {"ok": True, "failing_nodes": ["tests/test_a.py::test_x"]}
    res = await run_auto_verify(rt, st)
    assert verify.command == "pytest -q"
    assert res["scoped"] is False


@pytest.mark.asyncio
async def test_an_explicit_repair_command_is_used_when_there_are_no_nodeids(rt, verify):
    st = BuildTurnState(active=True, verify_command="pytest -q")
    st.last_error_vector = {"ok": False, "failing_nodes": [], "repair_command": "pytest -q -k boom"}
    await run_auto_verify(rt, st)
    assert verify.command == "pytest -q -k boom"


@pytest.mark.asyncio
async def test_last_failed_is_never_re_run_after_a_hang(rt, verify):
    """--lf after a timeout re-runs the test that hung; the ledger sticks red forever."""
    st = BuildTurnState(active=True, verify_command="pytest -q")
    st.last_verify_summary = "Command timed out after 45s"
    st.last_error_vector = {"ok": False, "failing_nodes": [], "repair_command": "pytest -q --lf"}
    res = await run_auto_verify(rt, st)
    assert verify.command == "pytest -q"
    assert res["scoped"] is False
    assert st.last_scoped_command == ""


@pytest.mark.asyncio
async def test_the_write_set_narrows_the_run_when_there_is_no_error_vector(rt, verify, monkeypatch):
    monkeypatch.setattr(
        "remedy.core.build_scoped.scoped_verify_command",
        lambda runtime, writes, **kw: "pytest -q tests/test_app.py",
    )
    st = BuildTurnState(active=True, verify_command="pytest -q")
    st.write_set = ["app.py"]
    res = await run_auto_verify(rt, st)
    assert verify.command == "pytest -q tests/test_app.py"
    assert res["scoped"] is True
    assert st.last_scoped_command == "pytest -q tests/test_app.py"


@pytest.mark.asyncio
async def test_prefer_scoped_false_runs_the_whole_suite(rt, verify, monkeypatch):
    monkeypatch.setattr(
        "remedy.core.build_scoped.scoped_verify_command",
        lambda runtime, writes, **kw: "pytest -q tests/test_app.py",
    )
    st = BuildTurnState(active=True, verify_command="pytest -q")
    st.write_set = ["app.py"]
    res = await run_auto_verify(rt, st, prefer_scoped=False)
    assert verify.command == "pytest -q"
    assert res["scoped"] is False


@pytest.mark.asyncio
async def test_a_scoped_run_that_stays_red_asks_for_the_full_suite_next_time(rt, verify, monkeypatch):
    monkeypatch.setattr(
        "remedy.core.build_scoped.scoped_verify_command",
        lambda runtime, writes, **kw: "pytest -q tests/test_app.py",
    )
    verify.ok = False
    verify.summary = "verify exit_code=1\n1 failed"
    st = BuildTurnState(active=True, verify_command="pytest -q")
    st.write_set = ["app.py"]
    await run_auto_verify(rt, st)
    assert "scoped_failed" in st.nudges_emitted


# --- run_auto_verify: the turn budget -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pytest -q", 45.0),
        ("cargo test", 120.0),
        ("npm test", 120.0),
        ("go test ./...", 120.0),
        ("gcc -o hello.exe hello.c && hello.exe", 20.0),
    ],
)
async def test_auto_verify_must_not_occupy_the_turn_for_minutes(rt, verify, command, expected):
    st = BuildTurnState(active=True)
    await run_auto_verify(rt, st, command=command)
    assert verify.timeout == expected


@pytest.mark.asyncio
async def test_a_gui_write_set_is_verified_by_compiling_not_by_launching_a_window(
    rt, verify, monkeypatch
):
    monkeypatch.setattr(
        "remedy.core.interactive_launch.write_set_looks_like_gui",
        lambda paths, **kw: True,
    )
    st = BuildTurnState(active=True)
    st.write_set = ["game.py"]
    await run_auto_verify(rt, st, command="gcc -o game.exe game.c && game.exe")
    assert verify.command == "gcc -o game.exe game.c"


# --- run_auto_verify: tolerating a bare state ---------------------------------


@pytest.mark.asyncio
async def test_a_state_without_the_optional_fields_does_not_raise(rt, verify):
    """Every state write is behind a hasattr guard; a bare object must survive."""
    res = await run_auto_verify(rt, object(), command="pytest -q")
    assert res["ok"] is True
    assert len(verify.calls) == 1


@pytest.mark.asyncio
async def test_a_state_with_no_advance_hook_is_still_moved_to_done_on_green(rt, verify):
    verify.summary = "verify exit_code=0\n1 passed"
    st = SimpleNamespace(phase="verify", verify_command="pytest -q")
    await run_auto_verify(rt, st)
    assert st.phase == "done"


# --- should_auto_verify -------------------------------------------------------


def test_no_state_never_auto_verifies():
    assert should_auto_verify(None) is False


def test_an_inactive_build_never_auto_verifies():
    st = BuildTurnState(active=False, write_steps=5)
    assert should_auto_verify(st) is False


def test_a_turn_with_no_writes_at_all_never_auto_verifies():
    """Otherwise every "continue" re-runs npm test for 30-60s of silence."""
    st = BuildTurnState(active=True, verify_command="pytest -q")
    assert should_auto_verify(st) is False


def test_writes_past_the_threshold_with_no_verify_yet_do_auto_verify():
    st = BuildTurnState(active=True, write_steps=2, verify_command="pytest -q")
    assert should_auto_verify(st) is True


def test_a_full_suite_oracle_waits_for_two_writes():
    """One file_edit is not 'the build is ready for npm test'."""
    st = BuildTurnState(
        active=True,
        write_steps=1,
        require_verify_after_writes=1,
        verify_command="npm test",
    )
    st.write_set = ["src/lib/audioToMidi.ts"]
    assert should_auto_verify(st) is False
    st.write_steps = 2
    assert should_auto_verify(st) is True


def test_an_open_feature_checklist_does_not_auto_run_the_suite():
    """npm test after the first file_edit is not the job — the feature isn't built yet."""
    st = BuildTurnState(
        active=True,
        write_steps=3,
        verify_command="npm test",
        open_todo_count=8,
        open_feature_todo_count=7,
    )
    st.write_set = ["src/lib/audioToMidi.ts"]
    assert should_auto_verify(st) is False
    st.open_feature_todo_count = 0
    st.open_todo_count = 1  # only "npm test green" left
    assert should_auto_verify(st) is True


def test_c_still_verifies_immediately_with_an_open_checklist():
    st = BuildTurnState(
        active=True,
        write_steps=1,
        verify_command="gcc -o a.exe a.c && a.exe",
        open_feature_todo_count=4,
    )
    st.write_set = ["hello.c"]
    assert should_auto_verify(st) is True


def test_one_write_below_the_threshold_with_an_oracle_already_set_does_not():
    st = BuildTurnState(active=True, write_steps=1, verify_command="pytest -q")
    assert should_auto_verify(st) is False


def test_a_write_with_no_oracle_at_all_fires_so_one_can_be_seeded():
    st = BuildTurnState(active=True, write_steps=1)
    assert should_auto_verify(st) is True


def test_the_cycle_cap_also_stops_the_decision_to_verify():
    st = BuildTurnState(active=True, write_steps=5, auto_verify_cycles=6)
    assert should_auto_verify(st) is False


def test_the_cycle_cap_honours_a_raised_ceiling():
    st = BuildTurnState(
        active=True, write_steps=5, auto_verify_cycles=6, max_auto_verify_cycles=9
    )
    assert should_auto_verify(st) is True


@pytest.mark.parametrize("src", ["hello.c", "main.cpp", "engine.cc", "board.h"])
def test_a_single_c_write_verifies_immediately(src):
    """A C partner task has one compile+run oracle; waiting for a second write is silly."""
    st = BuildTurnState(active=True, write_steps=1, verify_command="gcc -o a.exe a.c && a.exe")
    st.write_set = [src]
    assert should_auto_verify(st) is True


def test_a_hung_verify_does_not_re_fire_on_the_same_write_wave():
    st = BuildTurnState(active=True, write_steps=3, verify_command="pytest -q")
    st.last_verify_summary = "Command timed out after 45s"
    st.auto_verify_ran = True
    st.write_steps_at_last_green = 3
    assert should_auto_verify(st) is False


def test_a_hung_verify_does_re_fire_once_new_source_lands():
    st = BuildTurnState(active=True, write_steps=4, verify_command="pytest -q")
    st.last_verify_summary = "Command timed out after 45s"
    st.auto_verify_ran = True
    st.write_steps_at_last_green = 3
    st.write_set = ["app.py"]
    assert should_auto_verify(st) is True


def test_after_green_with_no_new_writes_the_suite_is_left_alone():
    st = BuildTurnState(active=True, write_steps=5, verify_command="pytest -q")
    st.last_verify_ok = True
    st.auto_verify_ran = True
    st.write_steps_at_last_green = 5
    assert should_auto_verify(st) is False


def test_after_green_a_new_source_write_re_verifies_once():
    st = BuildTurnState(active=True, write_steps=6, verify_command="pytest -q")
    st.last_verify_ok = True
    st.auto_verify_ran = True
    st.write_steps_at_last_green = 5
    st.write_set = ["app.py"]
    assert should_auto_verify(st) is True


def test_after_green_a_source_write_that_predates_the_watermark_does_not():
    st = BuildTurnState(active=True, write_steps=5, verify_command="pytest -q")
    st.last_verify_ok = True
    st.auto_verify_ran = True
    st.write_steps_at_last_green = 5
    st.write_set = ["app.py"]
    assert should_auto_verify(st) is False


@pytest.mark.parametrize("phase", ["ship", "done"])
def test_pushing_or_finished_does_not_thrash_the_tests(phase):
    st = BuildTurnState(active=True, phase=phase, write_steps=3, verify_command="pytest -q")
    st.last_verify_ok = True
    st.write_set = ["NOTES.md"]  # docs churn, not source
    assert should_auto_verify(st) is False


def test_the_same_docs_churn_mid_build_still_verifies():
    """Contrast with the ship/done case: only the terminal phases get the pass."""
    st = BuildTurnState(active=True, phase="implement", write_steps=3, verify_command="pytest -q")
    st.last_verify_ok = True
    st.write_set = ["NOTES.md"]
    assert should_auto_verify(st) is True


def test_after_a_red_auto_run_non_source_churn_does_not_re_verify():
    st = BuildTurnState(active=True, write_steps=5, verify_steps=1, verify_command="pytest -q")
    st.last_verify_ok = False
    st.auto_verify_ran = True
    st.write_set = ["NOTES.md"]
    assert should_auto_verify(st) is False


def test_after_a_red_auto_run_an_outstanding_repair_nudge_lets_one_more_through():
    st = BuildTurnState(active=True, write_steps=5, verify_steps=1, verify_command="pytest -q")
    st.last_verify_ok = False
    st.auto_verify_ran = True
    st.write_set = ["NOTES.md"]
    st.nudges_emitted = ["auto_verify_repair"]
    assert should_auto_verify(st) is True


def test_docs_churn_at_the_green_watermark_does_not_re_verify():
    st = BuildTurnState(active=True, phase="implement", write_steps=3, verify_command="pytest -q")
    st.last_verify_ok = True
    st.write_steps_at_last_green = 3
    st.write_set = ["NOTES.md"]
    assert should_auto_verify(st) is False


def test_docs_churn_after_an_auto_green_does_not_re_verify_even_past_the_watermark():
    st = BuildTurnState(active=True, phase="implement", write_steps=4, verify_command="pytest -q")
    st.last_verify_ok = True
    st.auto_verify_ran = True
    st.write_steps_at_last_green = 3
    st.write_set = ["NOTES.md"]
    assert should_auto_verify(st) is False


def test_after_an_auto_run_with_nothing_pending_nothing_re_fires():
    st = BuildTurnState(active=True, write_steps=5, verify_command="pytest -q")
    st.auto_verify_ran = True
    assert should_auto_verify(st) is False


def test_a_red_verify_with_more_writes_since_gets_one_more_auto_run():
    st = BuildTurnState(active=True, write_steps=3, verify_steps=1, verify_command="pytest -q")
    st.last_verify_ok = False
    assert should_auto_verify(st) is True


def test_that_second_auto_run_is_not_offered_again_once_the_repair_nudge_is_out():
    st = BuildTurnState(active=True, write_steps=3, verify_steps=1, verify_command="pytest -q")
    st.last_verify_ok = False
    st.nudges_emitted = ["auto_verify_repair"]
    assert should_auto_verify(st) is False


def test_source_touched_after_green_was_invalidated_re_verifies():
    st = BuildTurnState(active=True, write_steps=3, verify_steps=1, verify_command="pytest -q")
    st.write_steps_at_last_green = 1
    st.write_set = ["app.py"]
    assert should_auto_verify(st) is True


def test_a_duck_typed_state_without_the_helper_methods_still_decides():
    """react_turn passes whatever it has; the path filter has a standalone fallback."""
    st = SimpleNamespace(
        active=True,
        write_steps=2,
        write_set=["app.py"],
        verify_command="pytest -q",
        auto_verify_ran=False,
    )
    assert should_auto_verify(st) is True


# --- format_auto_verify_message -----------------------------------------------


def test_a_missing_oracle_result_is_rendered_as_the_fail_closed_nudge():
    msg = format_auto_verify_message({"oracle_missing": True, "ok": False})
    assert "ORACLE MISSING" in msg["content"]


def test_a_capped_result_tells_the_model_to_stop_looping():
    msg = format_auto_verify_message(
        {"capped": True, "ok": False, "summary": "cap reached (6)", "command": "pytest -q"}
    )
    assert "CAP" in msg["content"]
    assert "cap reached (6)" in msg["content"]
    assert "Stop auto-loop thrash" in msg["content"]


def test_a_green_result_names_the_command_the_machine_actually_ran():
    msg = format_auto_verify_message(
        {"ok": True, "command": "pytest -q", "summary": "3 passed"}
    )
    assert msg["role"] == "user"
    assert "GREEN" in msg["content"]
    assert "`pytest -q`" in msg["content"]
    assert "may summarize DONE" in msg["content"]


def test_a_scoped_green_offers_the_full_suite_as_the_next_step():
    msg = format_auto_verify_message(
        {
            "ok": True,
            "scoped": True,
            "command": "pytest -q tests/test_a.py",
            "full_command": "pytest -q",
            "summary": "1 passed",
        }
    )
    assert "(scoped)" in msg["content"]
    assert "Full suite available: `pytest -q`" in msg["content"]


def test_a_scoped_green_whose_scope_was_the_whole_suite_does_not_repeat_itself():
    msg = format_auto_verify_message(
        {"ok": True, "scoped": True, "command": "pytest -q", "full_command": "pytest -q"}
    )
    assert "Full suite available" not in msg["content"]


def test_a_build_that_still_has_to_ship_keeps_its_tools_after_green():
    st = BuildTurnState(active=True, ship_required=True)
    msg = format_auto_verify_message({"ok": True, "command": "pytest -q"}, state=st)
    assert "Tools stay on" in msg["content"]
    assert "may summarize DONE" not in msg["content"]


def test_red_while_the_feature_is_open_does_not_become_a_repair_ticket():
    st = BuildTurnState(active=True, open_feature_todo_count=4)
    msg = format_auto_verify_message(
        {"ok": False, "command": "npm test", "summary": "3 failed"},
        state=st,
    )
    assert "slice not done" in msg["content"]
    assert "Do NOT stop to patch the suite" in msg["content"]
    assert "REPAIR TICKET" not in msg["content"]


def test_a_red_result_becomes_a_scoped_repair_ticket():
    msg = format_auto_verify_message(
        {"ok": False, "command": "pytest -q", "summary": "1 failed\nE   assert 1 == 2"}
    )
    assert "REPAIR TICKET" in msg["content"]
    assert "Do not claim success" in msg["content"]


def test_a_result_with_no_ok_key_is_treated_as_red():
    msg = format_auto_verify_message({"command": "pytest -q", "summary": "boom"})
    assert "REPAIR TICKET" in msg["content"]


def test_the_stored_error_vector_is_preferred_over_re_parsing_the_summary():
    st = BuildTurnState(active=True)
    st.last_error_vector = {
        "ok": False,
        "command": "",
        "failing_nodes": ["tests/test_a.py::test_x"],
    }
    msg = format_auto_verify_message(
        {"ok": False, "command": "pytest -q", "summary": "unrelated noise"}, state=st
    )
    assert "tests/test_a.py::test_x" in msg["content"]
    # An error vector persisted without its command still has to name one.
    assert "command=`pytest -q`" in msg["content"]


def test_if_the_ticket_builder_blows_up_the_model_still_hears_that_it_failed(monkeypatch):
    monkeypatch.setattr(
        "remedy.core.build_error_vector.repair_ticket_message",
        lambda vec: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    msg = format_auto_verify_message(
        {"ok": False, "command": "pytest -q", "summary": "1 failed"}
    )
    assert "AUTO VERIFY · RED" in msg["content"]
    assert "Do not claim success" in msg["content"]


# --- documented current behaviour (see BUGS in the handoff) -------------------


@pytest.mark.asyncio
async def test_a_line_start_exit_code_zero_in_stdout_currently_overrides_a_timeout(rt, verify):
    """Documents today's behaviour, which contradicts the guard above it.

    The timeout branch forces ok=False precisely so a hung run cannot go green,
    but the exit-line regex runs afterwards and its ``verify`` prefix is optional
    — so a test whose own stdout starts a line with ``exit_code=0`` re-greens the
    run, clears the write set and advances to done.
    """
    verify.ok = False
    verify.summary = "verify exit_code=-1\nexit_code=0\nCommand timed out after 45s"
    st = BuildTurnState(active=True, verify_command="pytest -q", write_steps=2)
    st.write_set = ["app.py"]
    res = await run_auto_verify(rt, st)
    assert res["ok"] is True
    assert st.phase == "done"
    assert st.write_set == []


def test_the_exit_line_regex_matches_an_unprefixed_stdout_line():
    """The pattern build_oracle trusts as the "official runner line"."""
    pattern = re.compile(r"(?im)^(?:verify\s+)?exit_code=(\d+)")
    assert pattern.search("verify exit_code=0") is not None
    assert pattern.search("exit_code=0") is not None  # stdout, not the runner
    assert pattern.search("verify exit_code=-1") is None
