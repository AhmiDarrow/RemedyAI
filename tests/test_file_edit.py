"""file_edit apply_search_replace + multi-hunk."""

import pytest

from remedy.core.file_edit import apply_multi_hunk, apply_search_replace


def test_unique_replace():
    r = apply_search_replace("hello world", "world", "there")
    assert r.ok
    assert r.new_content == "hello there"
    assert r.occurrences == 1


def test_not_found():
    r = apply_search_replace("abc", "zzz", "q")
    assert not r.ok
    assert "not found" in r.message.lower()


def test_multiple_requires_replace_all():
    r = apply_search_replace("aa aa", "aa", "b")
    assert not r.ok
    assert "2 times" in r.message
    r2 = apply_search_replace("aa aa", "aa", "b", replace_all=True)
    assert r2.ok
    assert r2.new_content == "b b"


def test_multi_hunk_ok():
    src = "alpha\nbeta\ngamma\n"
    r = apply_multi_hunk(
        src,
        [
            {"old_string": "alpha", "new_string": "ALPHA"},
            {"old_string": "gamma", "new_string": "GAMMA"},
        ],
    )
    assert r.ok
    assert r.hunks_applied == 2
    assert r.new_content == "ALPHA\nbeta\nGAMMA\n"


def test_multi_hunk_stops_on_failure():
    r = apply_multi_hunk(
        "only once\n",
        [
            {"old_string": "only once", "new_string": "twice"},
            {"old_string": "missing", "new_string": "x"},
        ],
    )
    assert not r.ok
    assert r.hunks_applied == 1
    assert "hunk 1 failed" in r.message


def test_normalize_edits_arg_accepts_list():
    from remedy.core.workspace_tools.guards import normalize_edits_arg

    raw = normalize_edits_arg([{"old_string": "a", "new_string": "b"}])
    assert '"old_string"' in raw
    assert normalize_edits_arg(None) == ""


def test_junk_write_name_patterns():
    from remedy.core.workspace_tools.guards import JUNK_WRITE_NAME_RE

    assert JUNK_WRITE_NAME_RE.search(r"C:\proj\_ref_Unlock.tsx")
    assert JUNK_WRITE_NAME_RE.search("scripts/_write_explorer.py")
    assert JUNK_WRITE_NAME_RE.search("scripts/_ex_a.tsx.txt")
    assert not JUNK_WRITE_NAME_RE.search("src/screens/ExplorerScreen.tsx")
    assert not JUNK_WRITE_NAME_RE.search("scripts/prepare_icon.py")


def test_history_stub_markers_cover_echo_bug():
    from remedy.core.workspace_tools.guards import HISTORY_STUB_MARKERS

    stub = (
        "<<NOT_SOURCE_CODE history_stub kind=file_write content chars=3903 "
        "DO_NOT_file_write_this_string file_read_the_path_instead>>"
    )
    assert any(m in stub for m in HISTORY_STUB_MARKERS)
    old = "[file_write content omitted from provider history: 3903 chars"
    assert any(m in old for m in HISTORY_STUB_MARKERS)


@pytest.mark.asyncio
async def test_file_write_refuses_history_stub_body(tmp_path, monkeypatch):
    """Never write provider history stubs to disk; large real bodies write fully."""
    from types import SimpleNamespace

    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.core.workspace import resolve_under_roots, write_roots_for_scope
    from remedy.skills.tool_registry import ToolRegistry

    monkeypatch.setattr(APPROVALS, "needs_ask", lambda *a, **k: None)

    proj = tmp_path / "proj"
    proj.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    class _RT:
        def __init__(self) -> None:
            self.tool_registry = ToolRegistry()
            self.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))
            self._session_id = "stub-test"

        def effective_project_path(self):
            return proj.resolve()

        def access_scope(self):
            return "project"

        def resolve_tool_path(self, path, for_write=False):
            roots = write_roots_for_scope("project", proj, home=home)
            return resolve_under_roots(path or ".", roots, access_scope="project")

        def _track_artifact(self, *_a, **_k):
            pass

        def _register_comfyui_tools(self):
            pass

        def _register_vision_tools(self):
            pass

        def _register_local_discover_tools(self):
            pass

        def _register_skill_tools(self):
            pass

    rt = _RT()
    register_workspace_tools(rt)
    reg = rt.tool_registry

    stub = (
        "<<NOT_SOURCE_CODE history_stub kind=file_write content chars=3903 "
        "DO_NOT_file_write_this_string file_read_the_path_instead>>"
    )
    result = await reg.execute("file_write", path="App.tsx", content=stub)
    assert "HISTORY_STUB" in result or "history" in result.lower()
    assert not (proj / "App.tsx").exists()

    # Large real body must still write fully (execute path no longer 8k-clips).
    # Use diverse lines so spam/low-diversity guard does not refuse.
    body = "\n".join(f"export const item{i} = {i};" for i in range(5000)) + "\n"
    ok = await reg.execute("file_write", path="Big.tsx", content=body)
    assert "HISTORY_STUB" not in ok
    assert "SPAM" not in ok
    assert (proj / "Big.tsx").read_text(encoding="utf-8") == body
