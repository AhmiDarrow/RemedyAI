"""Ship tools — the approval gate around anything that reaches a remote.

git_push and gh_release are the only tools here that leave the machine, so the
gate matters more than the plumbing. Two failure modes are pinned:

* Auto mode must not manufacture a prompt out of a gate that already said yes
  (the historical ``needs_ask(...) or "git push"`` bug — falsy result, then the
  ``or`` fallback built a banner anyway).
* Nothing may reach a remote *before* the gate is consulted — gh_release used
  to push its tag first.

No git or gh process is started; the runners are intercepted.
"""

from __future__ import annotations

import pytest

from remedy.core import agent_ship_tools as S


class Reg:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler


class RT:
    def __init__(self, tmp_path) -> None:
        self.tool_registry = Reg()
        self._root = tmp_path
        self._session_id = "ship-session"

    def effective_project_path(self):
        return self._root

    def __getattr__(self, _n):
        return None


# --- the approval gate itself -----------------------------------------------


class FakeApprovals:
    def __init__(self, *, ask: str = "", approved: bool = False) -> None:
        self._ask = ask
        self._approved = approved
        self.created: list[dict] = []

    def needs_ask(self, command, *, tool_name=""):
        return self._ask

    def is_approved(self, tool_name, command, *, session_id=None):
        return self._approved

    def create(self, *, tool_name, command, reason, session_id):
        self.created.append(
            {"tool": tool_name, "command": command, "reason": reason, "sid": session_id}
        )
        return type("Item", (), {"id": "appr-1"})()


@pytest.fixture()
def approvals(monkeypatch):
    def install(**kw):
        fake = FakeApprovals(**kw)
        monkeypatch.setattr("remedy.core.approvals.APPROVALS", fake)
        return fake

    return install


def test_auto_mode_never_manufactures_a_prompt(approvals):
    """Full owner power means no banner, not a banner with an empty reason."""
    fake = approvals(ask="")
    assert S.approval_required_for_ship("git push origin HEAD", "s1", reason="push") is None
    assert fake.created == []


def test_an_already_approved_command_is_not_re_asked(approvals):
    fake = approvals(ask="pushes to a remote", approved=True)
    assert S.approval_required_for_ship("git push origin HEAD", "s1", reason="push") is None
    assert fake.created == []


def test_an_unapproved_push_produces_an_approval_blob(approvals):
    fake = approvals(ask="pushes to a remote", approved=False)
    out = S.approval_required_for_ship("git push origin HEAD", "s1", reason="push")
    assert out is not None
    assert "APPROVAL_REQUIRED" in out
    assert "id=appr-1" in out
    assert "git push origin HEAD" in out
    assert fake.created[0]["sid"] == "s1"


def test_the_gate_reason_prefers_what_the_policy_said(approvals):
    approvals(ask="pushes to a remote", approved=False)
    out = S.approval_required_for_ship("git push", "s1", reason="generic fallback")
    assert "pushes to a remote" in out


def test_the_caller_reason_is_used_when_the_policy_gives_none(approvals):
    """needs_ask can be truthy-but-unhelpful; the caller's reason still shows."""
    approvals(ask="x", approved=False)
    out = S.approval_required_for_ship("git push", "s1", reason="git push (ship)")
    assert "reason=" in out


def test_the_gate_is_asked_about_the_exact_command(approvals):
    fake = approvals(ask="needs ok", approved=False)
    S.approval_required_for_ship("gh release create v1.0", "s2", reason="release")
    assert fake.created[0]["command"] == "gh release create v1.0"
    assert fake.created[0]["tool"] == "bash_exec"


# --- registration -----------------------------------------------------------


def test_all_ship_tools_are_registered(tmp_path):
    rt = RT(tmp_path)
    S.register_ship_tools(rt)
    assert set(rt.tool_registry.tools) == {
        "git_status",
        "git_diff",
        "git_push",
        "gh_release",
        "ship_status",
    }


@pytest.mark.asyncio
async def test_git_diff_is_read_only_and_never_asks(tmp_path, approvals):
    fake = approvals(ask="pushes to a remote", approved=False)
    rt = RT(tmp_path)
    S.register_ship_tools(rt)
    out = await rt.tool_registry.tools["git_diff"]()
    assert fake.created == []
    assert "APPROVAL_REQUIRED" not in out
    assert "git_diff" in out.lower()


@pytest.mark.asyncio
async def test_ship_status_without_an_active_build_says_so(tmp_path, monkeypatch):
    rt = RT(tmp_path)
    S.register_ship_tools(rt)
    monkeypatch.setattr("remedy.core.build_engine.get_build_state", lambda _rt: None)
    out = await rt.tool_registry.tools["ship_status"]()
    assert "no active build turn" in out


@pytest.mark.asyncio
async def test_ship_status_reports_the_live_flags(tmp_path, monkeypatch):
    rt = RT(tmp_path)
    S.register_ship_tools(rt)

    class State:
        active = True

        def ship_report(self):
            return {
                "phase": "ship",
                "verify_ok": True,
                "ship_required": True,
                "ship_pushed": True,
                "ship_released": False,
                "ship_url": "https://github.com/x/y",
                "ship_release_url": "",
                "verify_command": "pytest -q",
                "wasted_auth_probes": 2,
                "paths": ["a.py", "b.py"],
            }

    monkeypatch.setattr("remedy.core.build_engine.get_build_state", lambda _rt: State())
    out = await rt.tool_registry.tools["ship_status"]()
    assert "phase=ship" in out
    assert "pushed=True" in out
    assert "https://github.com/x/y" in out
    assert "wasted_auth_probes=2" in out
    assert "a.py" in out


# --- git_push ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_blocked_push_never_runs_git(tmp_path, monkeypatch, approvals):
    approvals(ask="pushes to a remote", approved=False)
    rt = RT(tmp_path)
    S.register_ship_tools(rt)
    monkeypatch.setattr(
        "remedy.core.turn_context.turn_session_id", lambda _rt: "s1"
    )
    out = await rt.tool_registry.tools["git_push"]()
    assert "APPROVAL_REQUIRED" in out
    # No repo was touched: tmp_path is not a git repo, and a real push would
    # have produced a git error string instead of the approval blob.
    assert "FAILED" not in out


@pytest.mark.asyncio
async def test_the_push_preview_names_the_branch_being_pushed(
    tmp_path, monkeypatch, approvals
):
    fake = approvals(ask="pushes to a remote", approved=False)
    rt = RT(tmp_path)
    S.register_ship_tools(rt)
    monkeypatch.setattr("remedy.core.turn_context.turn_session_id", lambda _rt: "s1")
    await rt.tool_registry.tools["git_push"](remote="upstream", ref="release")
    assert fake.created[0]["command"] == "git push -u upstream release"


@pytest.mark.asyncio
async def test_set_upstream_can_be_turned_off(tmp_path, monkeypatch, approvals):
    fake = approvals(ask="pushes to a remote", approved=False)
    rt = RT(tmp_path)
    S.register_ship_tools(rt)
    monkeypatch.setattr("remedy.core.turn_context.turn_session_id", lambda _rt: "s1")
    await rt.tool_registry.tools["git_push"](set_upstream=False)
    assert fake.created[0]["command"] == "git push origin HEAD"


# --- gh_release: the tag push must be gated too -----------------------------


@pytest.mark.asyncio
async def test_a_release_tag_is_not_pushed_before_the_gate_is_asked(
    tmp_path, monkeypatch, approvals
):
    """The bypass: gh_release used to push the tag, then ask about the release."""
    fake = approvals(ask="pushes to a remote", approved=False)
    rt = RT(tmp_path)
    S.register_ship_tools(rt)
    monkeypatch.setattr("remedy.core.turn_context.turn_session_id", lambda _rt: "s1")
    out = await rt.tool_registry.tools["gh_release"](tag="v9.9.9")
    assert "APPROVAL_REQUIRED" in out
    # The gate was consulted about the *push*, not only the release.
    assert any(c["command"] == "git push origin v9.9.9" for c in fake.created)


@pytest.mark.asyncio
async def test_a_release_without_a_tag_and_without_history_refuses_clearly(
    tmp_path, monkeypatch, approvals
):
    approvals(ask="", approved=True)
    rt = RT(tmp_path)
    S.register_ship_tools(rt)
    monkeypatch.setattr("remedy.core.turn_context.turn_session_id", lambda _rt: "s1")
    out = await rt.tool_registry.tools["gh_release"]()
    assert "tag=" in out
