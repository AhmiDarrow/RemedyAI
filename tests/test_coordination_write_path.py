"""Cross-session write coordination — a sibling's held file is never overwritten.

Two concurrent build sessions (different providers — e.g. Grok and Fable) share
one filesystem. When session A writes a file, it claims it; session B's write to
the same file is refused with a plain block message until A's hold frees. B's
writes to other files proceed normally.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core import coordination as C


def _make_runtime(proj: Path, home: Path, session_id: str, remedy_home: Path):
    # noqa below: isort wants the aliased names split into four separate
    # import statements. One grouped import reads better here.
    from remedy.core.workspace import (  # noqa: I001
        allowed_roots_for_scope as _ar,
        effective_access_scope,
        resolve_under_roots as _ru,
        write_roots_for_scope as _wr,
    )

    class RT:
        def access_scope(self) -> str:
            return effective_access_scope("project", str(proj))

        def effective_project_path(self) -> Path:
            return proj.resolve()

        def allowed_roots(self):
            return _ar(self.access_scope(), proj, home=home)

        def write_roots(self):
            return _wr(self.access_scope(), proj, home=home)

        def project_path_is_unset(self) -> bool:
            return False

        def resolve_tool_path(self, path: str, *, for_write: bool = False) -> Path:
            if for_write:
                return _ru(path or ".", self.write_roots(), access_scope="project")
            return _ru(path or ".", self.allowed_roots(), access_scope=self.access_scope())

        def _track_artifact(self, _p: str) -> None:
            pass

        def _register_comfyui_tools(self) -> None:
            pass

        def _register_vision_tools(self) -> None:
            pass

        def _register_local_discover_tools(self) -> None:
            pass

        def _register_skill_tools(self) -> None:
            pass

    rt = RT()
    rt.config = SimpleNamespace(home_dir=str(remedy_home))
    rt._session_id = session_id
    return rt


@pytest.fixture()
def two_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry

    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "auto", "access_scope": "project"},
    )
    prev = APPROVALS._mode  # noqa: SLF001
    APPROVALS.set_mode("auto")

    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    remedy_home = tmp_path / "remedy_home"
    remedy_home.mkdir()
    # Keep every coordination path (write claim, abort release) on ONE registry:
    # abort_session has no runtime, so it resolves home via REMEDY_HOME.
    monkeypatch.setenv("REMEDY_HOME", str(remedy_home))

    def mk(sid: str):
        rt = _make_runtime(proj, home, sid, remedy_home)
        reg = ToolRegistry()
        rt.tool_registry = reg
        register_workspace_tools(rt)
        return rt, reg

    grok = mk("grok-session-1111")
    fable = mk("fable-session-2222")
    yield {"proj": proj, "remedy_home": remedy_home, "grok": grok, "fable": fable}
    APPROVALS.set_mode(prev)


def _in_session(sid: str, proj: Path):
    """Context manager binding the turn session id (what the claim uses)."""
    from remedy.core.turn_context import begin_turn, end_turn

    class _Ctx:
        def __enter__(self):
            self._t = begin_turn(sid, project_raw=str(proj), active_path=str(proj))
            return self

        def __exit__(self, *a):
            end_turn(sid, *self._t)

    return _Ctx()


@pytest.mark.asyncio
async def test_sibling_file_is_blocked_then_freed(two_sessions) -> None:
    proj = two_sessions["proj"]
    grok_rt, grok_reg = two_sessions["grok"]
    fable_rt, fable_reg = two_sessions["fable"]

    # Grok writes (and thereby claims) executor.py.
    with _in_session("grok-session-1111", proj):
        out = await grok_reg.execute(
            "file_write", path="executor.py", content="# grok's work\n"
        )
    assert "PATH_HELD" not in out
    assert (proj / "executor.py").read_text(encoding="utf-8") == "# grok's work\n"

    # Fable tries the SAME file → refused, file untouched.
    with _in_session("fable-session-2222", proj):
        blocked = await fable_reg.execute(
            "file_write", path="executor.py", content="# fable stomps\n"
        )
    assert "PATH_HELD_BY_OTHER_SESSION" in blocked
    assert (proj / "executor.py").read_text(encoding="utf-8") == "# grok's work\n"

    # Fable works on a different file → fine.
    with _in_session("fable-session-2222", proj):
        ok = await fable_reg.execute(
            "file_write", path="store.ts", content="// fable's lane\n"
        )
    assert "PATH_HELD" not in ok
    assert (proj / "store.ts").read_text(encoding="utf-8") == "// fable's lane\n"

    # Grok's session ends (Stop) → claims release → Fable may edit it now.
    from remedy.core.turn_context import abort_session

    abort_session("grok-session-1111")
    with _in_session("fable-session-2222", proj):
        now_ok = await fable_reg.execute(
            "file_write", path="executor.py", content="# fable, after handoff\n"
        )
    assert "PATH_HELD" not in now_ok
    assert (proj / "executor.py").read_text(encoding="utf-8") == "# fable, after handoff\n"


@pytest.mark.asyncio
async def test_file_edit_also_blocked(two_sessions) -> None:
    proj = two_sessions["proj"]
    grok_rt, grok_reg = two_sessions["grok"]
    fable_rt, fable_reg = two_sessions["fable"]

    (proj / "shared.py").write_text("x = 1\n", encoding="utf-8")
    with _in_session("grok-session-1111", proj):
        await grok_reg.execute("file_write", path="shared.py", content="x = 2\n")
    with _in_session("fable-session-2222", proj):
        blocked = await fable_reg.execute(
            "file_edit", path="shared.py", old_string="x = 2", new_string="x = 3"
        )
    assert "PATH_HELD_BY_OTHER_SESSION" in blocked
    assert (proj / "shared.py").read_text(encoding="utf-8") == "x = 2\n"


def test_coworkers_note_from_beacons(two_sessions) -> None:
    remedy_home = two_sessions["remedy_home"]
    C.register(
        "grok-session-1111",
        muscle="xai/grok-4",
        project_path=str(two_sessions["proj"]),
        goal="build the parser",
        phase="implement",
        home=remedy_home,
    )
    note = C.coworkers_note("fable-session-2222", home=remedy_home)
    assert "xai/grok-4" in note
    assert "build the parser" in note
