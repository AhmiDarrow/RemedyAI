"""Regressions for five gates that were open in 0.38.

Each test below stands for a way an approval or capability check looked
present and did nothing. They are grouped because they share one shape: the
gate existed, the value it was handed did not discriminate, so it passed.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remedy.core.approvals import ApprovalQueue
from remedy.core.build_mutant import mutant_kill_score
from remedy.core.hive.policy import DAUGHTER_CAPABILITIES, is_mother_only_tool
from remedy.core.turn_pipeline import _approval_key
from remedy.interfaces.api import create_app
from remedy.tools.catalog import descriptor_for

# --- hive: the owner's correspondence is not a daughter surface --------------
# catalog._infer had no branch for the read verbs, so they took the {FS_READ}
# fallback -- which is inside DAUGHTER_CAPABILITIES -- and the denylist only
# matched mail_send / mail_reply. A spawned daughter could read the mailbox.

MAIL_AND_CALENDAR = (
    "mail_list",
    "mail_get",
    "mail_archive",
    "mail_create_draft",
    "mail_mark_read",
    "mail_disconnect",
    "calendar_list_events",
    "calendar_update_event",
    "calendar_cancel_event",
)


def test_daughters_cannot_reach_mail_or_calendar() -> None:
    for name in MAIL_AND_CALENDAR:
        assert is_mother_only_tool(name), f"{name} is reachable by a daughter"


def test_mail_and_calendar_carry_a_capability_daughters_lack() -> None:
    """The denylist is one layer; the cap gate must not be inert underneath it."""
    for name in MAIL_AND_CALENDAR:
        caps = descriptor_for(name).capabilities
        assert caps - DAUGHTER_CAPABILITIES, (
            f"{name} capabilities {caps} sit entirely inside DAUGHTER_CAPABILITIES, "
            "so the hive capability check cannot restrain it"
        )


def test_ordinary_tools_are_still_daughter_reachable() -> None:
    """Guard the prefix widening against over-reach."""
    for name in ("file_read", "repo_search", "list_dir", "web_search"):
        assert not is_mother_only_tool(name)


# --- approvals: one skill_run approval is not every skill_run ----------------
# For a high-impact tool whose arguments expose no command/cmd/argv/path/url,
# the fingerprint collapsed to "<tool>::<tool>".


def test_approval_key_discriminates_skill_run_arguments() -> None:
    benign = _approval_key("skill_run", {"skill": "notes", "script": "sync.py"})
    dangerous = _approval_key("skill_run", {"skill": "deploy", "script": "wipe_prod.py"})
    assert benign != dangerous
    assert benign != "skill_run"
    assert "wipe_prod.py" in dangerous


def test_approval_key_is_stable_for_the_same_call() -> None:
    args = {"script": "sync.py", "skill": "notes"}
    assert _approval_key("skill_run", args) == _approval_key(
        "skill_run", dict(reversed(list(args.items())))
    )


def test_approval_key_falls_back_to_the_bare_name_when_nothing_discriminates() -> None:
    assert _approval_key("some_tool", {}) == "some_tool"


# --- approvals: Autonomous does not un-gate an untrusted workspace -----------
# The AUTONOMOUS waiver ran after the untrusted-scope check without re-testing
# it, so untrusted/sandbox/strict/download stopped asking entirely.


def _queue_with_config(monkeypatch, cfg: dict) -> ApprovalQueue:
    import remedy.interfaces.api_support as api_support

    monkeypatch.setattr(api_support, "load_config", lambda *a, **k: dict(cfg))
    return ApprovalQueue()


def test_untrusted_workspace_still_asks_under_autonomous(monkeypatch) -> None:
    queue = _queue_with_config(
        monkeypatch,
        {
            "access_scope": "untrusted",
            "trust_profile": "autonomous",
            "approval_mode": "ask",
        },
    )
    for tool, command in (
        ("bash_exec", "python setup.py install"),
        ("file_write", "notes.txt"),
        ("run_python_file", "build.py"),
    ):
        assert queue.needs_ask(command, tool_name=tool) is not None, (
            f"{tool} skipped its approval in an untrusted workspace"
        )


def test_autonomous_still_waives_high_impact_ask_in_a_trusted_project(
    monkeypatch,
) -> None:
    """The paired control: the waiver must still work where it is meant to."""
    queue = _queue_with_config(
        monkeypatch,
        {
            "access_scope": "project",
            "trust_profile": "autonomous",
            "approval_mode": "ask",
        },
    )
    assert queue.needs_ask("echo hi", tool_name="bash_exec") is None


# --- settings: the panel round-trips whatever GET returns --------------------
# trust_profile reached config.toml but never came back out, so the panel read
# undefined and wrote "balanced" over a saved profile on the next save.


def test_settings_get_emits_trust_profile() -> None:
    client = TestClient(create_app())
    body = client.get("/api/settings").json()
    assert "trust_profile" in body, (
        "GET /api/settings omits trust_profile; the Settings panel will read it "
        "as undefined and write balanced back over the owner's saved profile"
    )
    assert body["trust_profile"] in ("conservative", "balanced", "autonomous")


# --- build_mutant: a mutant row names the file that was mutated --------------


def test_mutant_rows_name_the_mutated_file(tmp_path: Path) -> None:
    (tmp_path / "alpha.py").write_text("def f(x):\n    return x == 1\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("def g(y):\n    return y == 2\n", encoding="utf-8")

    # No test_command_paths -> no pytest subprocess; every mutant "survives".
    result = mutant_kill_score(tmp_path, ["alpha.py", "beta.py"])

    assert result["ok"] is True
    files = {row["file"] for row in result["details"]}
    assert files == {"alpha.py", "beta.py"}, (
        f"mutant rows attributed to {files}; each row must name the file that "
        "was actually mutated, not the last one copied"
    )
    assert all(isinstance(row["file"], str) for row in result["details"])
