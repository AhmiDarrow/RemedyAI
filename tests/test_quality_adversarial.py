"""Adversarial honesty tests for the 2026-08-28 quality fixes.

Symptom families, not one reproduction string. No live network, no real secrets.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.life_task_drive import drive_life_task, step_is_checkpoint
from remedy.core.react_loop.loop_util import browse_tool_ok


def test_navigate_unsuccessful_is_not_done():
    def run(_action, **_kw):
        return "Navigate unsuccessful — rail_failed"

    out = drive_life_task(
        goal="open shop",
        steps=[{"title": "Open", "action": "navigate", "url": "https://shop.example"}],
        run_action=run,
        max_retries=0,
    )
    assert out["ok"] is False
    ok_true, ok_false = browse_tool_ok("Navigate unsuccessful")
    assert ok_true is False
    wrapped = json.dumps({"ok": False, "message": "SUCCESS-looking unsuccessful"})
    t, f = browse_tool_ok(wrapped)
    assert t is False and f is True
    t2, _ = browse_tool_ok('{"ok": true, "message": "opened"}')
    assert t2 is True


def test_optimistic_pending_load_is_not_life_drive_done():
    def run(_action, **_kw):
        return json.dumps(
            {"ok": True, "observed": False, "pending_load": True, "via": "optimistic"}
        )

    out = drive_life_task(
        goal="open shop",
        steps=[{"title": "Open", "action": "navigate", "url": "https://shop.example"}],
        run_action=run,
        max_retries=0,
    )
    assert out["ok"] is False
    assert out["steps"][0]["status"] != "done"


def test_send_delete_are_checkpoints_add_to_cart_is_not():
    assert step_is_checkpoint({"title": "Send", "text": "Send"})
    assert step_is_checkpoint({"title": "Delete", "text": "Delete"})
    assert step_is_checkpoint({"title": "Pay", "text": "Pay"})
    assert step_is_checkpoint({"title": "Submit", "text": "Submit"})
    assert not step_is_checkpoint({"title": "Add to cart", "text": "Add to cart"})
    assert not step_is_checkpoint({"title": "Resend", "text": "Resend"})
    assert not step_is_checkpoint({"title": "PayPal", "text": "PayPal"})
    from remedy.core.approvals import sensitive_computer_checkpoint

    send = sensitive_computer_checkpoint("computer_click", "click Send")
    assert send
    cart = sensitive_computer_checkpoint("computer_click", "click Add to cart")
    assert not cart


def test_i_like_godot_does_not_arm_full_pack_make_a_game_does():
    from remedy.core.react_turn import resolve_tools

    all_t = [
        {
            "type": "function",
            "function": {"name": "file_write", "parameters": {"type": "object"}},
        }
    ]
    like = resolve_tools(message="I like godot", all_tools=all_t, turn_tier=1)
    assert like.pack in ("none", "peek")
    game = resolve_tools(message="make a game", all_tools=all_t, turn_tier=1)
    assert game.pack == "full"
    plat = resolve_tools(message="build a platformer", all_tools=all_t, turn_tier=1)
    assert plat.pack == "full"
    laugh = resolve_tools(message="make me laugh", all_tools=all_t, turn_tier=1)
    assert laugh.pack in ("none", "peek")
    me_game = resolve_tools(message="make me a game", all_tools=all_t, turn_tier=1)
    assert me_game.pack == "full"


def test_skip_pass_gate_is_not_drive_green():
    from remedy.core.build_persist import iterate_to_green_multi

    out = iterate_to_green_multi(
        lambda: {"ok": True, "verified": False, "passed_levels": ["L3"]},
        [("noop", lambda _v: {"ran": False})],
    )
    assert out.ok is False
    assert out.reason == "unverified"


def test_missing_suite_injects_do_not_claim_green(monkeypatch):
    import asyncio

    from remedy.core.build_oracle import run_casual_verify

    monkeypatch.setattr(
        "remedy.core.build_oracle.discover_verify_command",
        lambda *a, **k: "",
    )
    out = asyncio.run(run_casual_verify(SimpleNamespace()))
    assert out["ok"] is False
    assert "Do not claim green" in out["message"]


@pytest.mark.asyncio
async def test_hive_note_not_in_parent_search(tmp_path: Path):
    from remedy.memory.authority import stamp_entry_metadata
    from remedy.memory.partner_memory import search_partner_and_entries
    from remedy.memory.store import MemoryStore
    from remedy.models import MemoryEntry, MemoryEntryType

    mem = MemoryStore(tmp_path / "memory.db")
    await mem.initialize()
    meta = stamp_entry_metadata(
        {},
        source="hive",
        session_id="hive_abc",
        inferred=False,
        why="hive session note",
    )
    await mem.upsert(
        MemoryEntry(
            title="hive secret preference",
            content="daughter should not leak this to parent",
            entry_type=MemoryEntryType.NOTE,
            session_id="hive_abc",
            metadata=meta,
            importance=0.9,
        )
    )
    hits = await search_partner_and_entries(mem, "preference", limit=8)
    blob = json.dumps(hits).lower()
    assert "hive_abc" not in blob
    assert "daughter should not leak" not in blob


def test_vault_length_not_in_set_value_message(monkeypatch):
    from remedy.core.computer import desktop_uia as uia

    class El:
        def GetCurrentPattern(self, _pat):
            return self

        def QueryInterface(self, _iface):
            return self

        def SetValue(self, text):
            self._v = text

        @property
        def CurrentValue(self):
            return ""

    monkeypatch.setattr(
        uia,
        "_find_live_element",
        lambda *a, **k: (El(), SimpleNamespace(IUIAutomationValuePattern=object())),
    )
    monkeypatch.setattr(uia, "_el_role", lambda _e: "password")
    monkeypatch.setattr(uia, "_el_name", lambda _e: "Password")
    monkeypatch.setattr(uia, "_PAT_VALUE", 1)
    res = uia.element_action(1, "Password", role="password", action="set_value", text="secret-token-xyz")
    msg = str(res.get("message") or "")
    assert "secret-token" not in msg
    assert str(len("secret-token-xyz")) not in msg
    assert res.get("verified") is False


def test_unknown_cli_action_ok_false(tmp_path: Path, monkeypatch):
    from remedy.core.computer.cli_host import LocalComputerHost

    host = LocalComputerHost(home_dir=tmp_path)
    monkeypatch.setattr(
        "remedy.core.computer.desktop_os.native",
        lambda: SimpleNamespace(),
    )
    out = host._run_action("fill", {}, SimpleNamespace())
    assert out.get("ok") is False
    assert "Desktop" in str(out.get("message") or "")
    hover = host._run_action("hover", {}, SimpleNamespace())
    assert hover.get("ok") is False
    act = host._run_action("press_hold", {}, SimpleNamespace())
    assert act.get("ok") is False
    assert host._run_action("select", {}, SimpleNamespace()).get("ok") is False
    assert host._run_action("act", {}, SimpleNamespace()).get("ok") is False


def test_apply_patch_two_file_second_hunk_miss_leaves_file_1(tmp_path: Path):
    from remedy.core.build_apply_patch import apply_patch_text

    (tmp_path / "a.py").write_text("old\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("keep\n", encoding="utf-8")
    rt = SimpleNamespace(
        effective_project_path=lambda: tmp_path,
        resolve_tool_path=lambda p, **k: tmp_path / p,
        config=SimpleNamespace(home_dir=tmp_path),
    )
    diff = (
        "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-missing_this\n+newb\n"
    )
    res = apply_patch_text(rt, diff, root=tmp_path)
    assert res["ok"] is False
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "old\n"
    assert (tmp_path / "b.py").read_text(encoding="utf-8") == "keep\n"


def test_long_build_unverified_never_claims_green():
    from remedy.core.build_persist import iterate_to_green_multi

    hops = [{"ok": True, "verified": False} for _ in range(12)]
    idx = {"n": 0}

    def verify() -> dict:
        i = min(idx["n"], len(hops) - 1)
        idx["n"] += 1
        return hops[i]

    out = iterate_to_green_multi(verify, [("noop", lambda _v: {"ran": True})], max_rounds=8)
    assert out.ok is False
    assert out.reason == "unverified"


def test_parse_verify_output_official_line_only():
    from remedy.core.build_error_vector import parse_verify_output

    green = parse_verify_output("verify exit_code=0\n5 passed")
    assert green.ok is True
    chatter = parse_verify_output("the log said exit_code=0 inside failing stdout\nFAILED")
    assert chatter.ok is False
    prefixed = parse_verify_output("VERIFY_DEFERRED reason=x\ndeferred=true")
    assert prefixed.ok is False


def test_materialize_jails_absolute_and_dotdot(tmp_path: Path):
    from remedy.core.builds.reducer import materialize

    root = tmp_path / "hop"
    root.mkdir()
    with pytest.raises(PermissionError):
        materialize({"../outside.py": "x"}, root)
    abs_rel = str(tmp_path / "pwned.py")
    with pytest.raises(PermissionError):
        materialize({abs_rel: "x"}, root)
    materialize({"ok.py": "x = 1\n"}, root)
    assert (root / "ok.py").is_file()


def test_linux_hands_fail_closed_when_binaries_missing(monkeypatch):
    from remedy.core.computer import desktop_linux as lin

    monkeypatch.setattr(lin, "_which", lambda *n: None)
    monkeypatch.setattr(lin, "_require_linux", lambda: None)
    with pytest.raises(RuntimeError, match="xdotool or ydotool"):
        lin.press_hold(10, 10, hold_ms=100)
    with pytest.raises(RuntimeError, match="xdotool or ydotool"):
        lin.scroll(10, 10, dy=-1)
    with pytest.raises(RuntimeError, match="xdotool or ydotool"):
        lin.drag(1, 1, 2, 2)
    assert hasattr(lin, "focus_window")
    assert lin.focus_window(0) is False
    assert lin.focus_window(1) is False
    with pytest.raises(RuntimeError, match="grim|import|region"):
        lin.screenshot_region_png(0, 0, 10, 10)


def test_redact_secrets_hyphen_aware_and_skip_unchanged():
    from remedy.memory.partner_memory import looks_like_secret, redact_secrets

    key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345"
    assert looks_like_secret(key)
    red = redact_secrets(f"token {key} end")
    assert "sk-ant-api03" not in red
    assert "[redacted]" in red


def test_capture_browser_label_without_bounds_is_not_rail_success():
    from fastapi.testclient import TestClient

    from remedy.interfaces.api import create_app

    class Cfg:
        home_dir = "."

    class RT:
        config = Cfg()

        def list_tasks(self):
            return []

    app = create_app(runtime=RT(), api_key="")
    client = TestClient(app)
    r = client.post("/api/computer/capture", json={"label": "browser_rail"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is False
    assert "bounds" in str(body.get("error") or "").lower()


def test_challenge_wall_on_click_and_act():
    from remedy.core.approvals import SENSITIVE_PREFIX, challenge_wall_checkpoint

    r = challenge_wall_checkpoint("computer_click", "turnstile widget", "I'm not a robot")
    assert r and r.startswith(SENSITIVE_PREFIX)
    act = challenge_wall_checkpoint(
        "computer_act", "cloudflare challenge", "Verify you are human"
    )
    assert act and act.startswith(SENSITIVE_PREFIX)
    assert challenge_wall_checkpoint("computer_type", "turnstile", "hello") is None
    assert challenge_wall_checkpoint("computer_key", "turnstile", "enter") is None
    assert (
        challenge_wall_checkpoint(
            "computer_click", "https://site.example footer Protected by Cloudflare", "OK"
        )
        is None
    )


def test_browse_tool_ok_family_not_substring_success():
    t, f = browse_tool_ok('prefix {"ok": true} suffix')
    assert t is True and f is False
    t, f = browse_tool_ok('note {"ok": false} unsuccessful')
    assert t is False and f is True
    t, f = browse_tool_ok("unsuccessful navigate")
    assert t is False
    t, f = browse_tool_ok("SUCCESS")
    assert t is False


def test_parse_verify_official_line_family():
    from remedy.core.build_error_vector import parse_verify_output

    assert parse_verify_output("exit_code=0\nok").ok is True
    assert parse_verify_output("verify exit_code=0").ok is True
    assert parse_verify_output("the log said exit_code=0 inside FAIL").ok is False
    assert parse_verify_output("passed\nno official line").ok is False
    assert parse_verify_output("exit_code=1").ok is False


def test_casual_verify_green_does_not_say_do_not_claim(monkeypatch):
    import asyncio

    from remedy.core.build_oracle import run_casual_verify
    from remedy.core.jobs import JobResult

    async def fake_job(runtime, *, command="", path="", timeout=180.0):
        return JobResult(kind="verify", ok=True, summary="verify exit_code=0")

    monkeypatch.setattr("remedy.core.jobs.run_verify_job", fake_job)
    monkeypatch.setattr(
        "remedy.core.build_oracle.discover_verify_command",
        lambda *a, **k: "pytest -q",
    )
    out = asyncio.run(run_casual_verify(SimpleNamespace()))
    assert out["ok"] is True
    assert "Do not claim green" not in out["message"]


def test_drive_build_skip_pass_is_not_machine_loop_green(tmp_path, monkeypatch):
    from remedy.core.build_drive import drive_build

    root = tmp_path / "proj"
    root.mkdir()
    (root / "hello.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    rt = SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: (root / p) if not Path(p).is_absolute() else Path(p),
        config=SimpleNamespace(home_dir=root),
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
    )
    monkeypatch.setattr(
        "remedy.core.build_gate_tower.run_gate_tower",
        lambda *a, **k: {
            "ok": True,
            "verified": False,
            "passed_levels": ["L3_unit"],
            "message": "skip",
        },
    )
    res = drive_build(rt, goal="implement function greet in module hello.py", use_llm=False)
    msg = str(res.get("message") or "")
    assert "Machine loop green" not in msg
    assert res.get("gate", {}).get("verified") is False


def test_materialize_extra_tests_nested_dotdot(tmp_path: Path):
    from remedy.core.builds.reducer import run_project_tests

    root = tmp_path / "hop"
    root.mkdir()
    with pytest.raises(PermissionError):
        run_project_tests({"ok.py": "x=1\n"}, root, extra_tests={"nested/../../x.py": "pwn"})


def test_budget_hits_drops_hive():
    from remedy.memory.authority import budget_hits

    hits = [
        {"title": "hive", "content": "daughter leak", "authority": "hive", "inferred": False},
        {"title": "owner", "content": "oat milk", "authority": "owner", "inferred": False},
    ]
    out = budget_hits(hits, limit=6)
    blob = json.dumps(out).lower()
    assert "daughter leak" not in blob
    assert "oat milk" in blob


def test_l0_whats_next_without_life_cue_is_not_instant():
    from remedy.core.metabolism.tier import TurnTier, classify_turn_tier

    assert classify_turn_tier("what's next", tools_enabled=False) != TurnTier.L0_INSTANT
    assert classify_turn_tier("what should I do", tools_enabled=False) != TurnTier.L0_INSTANT
    assert classify_turn_tier("what's next on my goals", tools_enabled=False) == TurnTier.L0_INSTANT


def test_turn_skip_ask_does_not_skip_host_run_or_life_drive(monkeypatch):
    from remedy.core.approvals import APPROVALS
    from remedy.core.turn_context import begin_turn, end_turn, set_turn_skip_ask

    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "ask", "access_scope": "project"},
    )
    prev = APPROVALS.mode
    APPROVALS.set_mode("ask")
    tokens = begin_turn("skip-ask", project_raw=None, active_path=".")
    try:
        set_turn_skip_ask(True)
        assert APPROVALS.needs_ask("write src/a.py", tool_name="file_write") is None
        assert APPROVALS.needs_ask("next step", tool_name="life_drive") is not None
        assert APPROVALS.needs_ask("click", tool_name="computer_click") is not None
        host_before = APPROVALS.needs_ask("pytest", tool_name="host_run")
        set_turn_skip_ask(False)
        host_after = APPROVALS.needs_ask("pytest", tool_name="host_run")
        set_turn_skip_ask(True)
        assert host_before == host_after
    finally:
        end_turn("skip-ask", *tokens)
        APPROVALS.set_mode(prev)


def test_redact_secrets_consumes_password_value():
    from remedy.memory.partner_memory import redact_secrets

    out = redact_secrets("password: hunter2 extra")
    assert "hunter2" not in out
    assert "[redacted]" in out


def test_is_valid_navigate_url_dns_miss_and_loopback(monkeypatch):
    from remedy.core.computer.router import is_valid_navigate_url

    assert is_valid_navigate_url("http://127.0.0.1:8188/") is True
    assert is_valid_navigate_url("http://localhost:11434/") is True

    def boom(_host):
        raise OSError("dns fail")

    monkeypatch.setattr("remedy.core.agent_web_tools._resolve_public_ips", boom)
    assert is_valid_navigate_url("https://example.invalid") is False

    monkeypatch.setattr(
        "remedy.core.agent_web_tools._resolve_public_ips",
        lambda _h: [],
    )
    assert is_valid_navigate_url("https://evil.example") is False


def test_capture_label_family_without_bounds():
    from fastapi.testclient import TestClient

    from remedy.interfaces.api import create_app

    class RT:
        config = SimpleNamespace(home_dir=".")

        def list_tasks(self):
            return []

    client = TestClient(create_app(runtime=RT(), api_key=""))
    for label in ("browser", "rail", "Browser rail"):
        body = client.post("/api/computer/capture", json={"label": label}).json()
        assert body.get("ok") is False


def test_tdd_jail_any_exception_does_not_write(tmp_path: Path):
    from remedy.core.build_tdd import materialize_tdd_tests

    class RT:
        def resolve_tool_path(self, p, **k):
            raise RuntimeError("boom")

        def effective_project_path(self):
            return tmp_path

    out = materialize_tdd_tests(
        RT(),
        [{"path": "hello.py", "symbol": "greet", "behavior": "hi"}],
        root=tmp_path,
    )
    assert out.get("ok") is False
    assert not (tmp_path / "tests" / "test_greet.py").exists()


@pytest.mark.asyncio
async def test_git_restore_reapply_false_keeps_owner_deletes_round_untracked(tmp_path):
    import subprocess

    from remedy.core.self_inject import git_capture, git_restore

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "keep.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "keep.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "keep.txt").write_text("base\nowner-wip\n", encoding="utf-8")
    snap = await git_capture(repo)
    (repo / "evil.py").write_text("bad\n", encoding="utf-8")
    await git_restore(
        repo, snap, round_paths=["evil.py"], reapply_snapshot=False
    )
    assert "owner-wip" in (repo / "keep.txt").read_text(encoding="utf-8")
    assert not (repo / "evil.py").exists()


@pytest.mark.asyncio
async def test_git_restore_empty_delta_is_noop_keeps_owner_wip(tmp_path):
    import subprocess

    from remedy.core.self_inject import git_capture, git_restore

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "keep.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "keep.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "keep.txt").write_text("base\nowner-wip\n", encoding="utf-8")
    snap = await git_capture(repo)
    await git_restore(repo, snap, round_paths=[], reapply_snapshot=False)
    assert "owner-wip" in (repo / "keep.txt").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_self_inject_empty_delta_untracked_vs_head_gone_on_restore(tmp_path):
    import subprocess

    from remedy.core.agent_self_inject_tools import round_write_paths
    from remedy.core.self_inject import git_capture, git_restore

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "keep.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "keep.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "keep.txt").write_text("base\nowner-wip\n", encoding="utf-8")
    (repo / "evil.py").write_text("bad\n", encoding="utf-8")
    snapshot = await git_capture(repo)
    after = await git_capture(repo)
    paths = round_write_paths(snapshot, after)
    assert "evil.py" in paths
    assert "keep.txt" not in paths
    snap_was_clean = not (snapshot.get("changed") or snapshot.get("untracked"))
    await git_restore(
        repo, snapshot, round_paths=paths, reapply_snapshot=bool(snap_was_clean)
    )
    assert "owner-wip" in (repo / "keep.txt").read_text(encoding="utf-8")
    assert not (repo / "evil.py").exists()


def test_vault_desktop_refuses_button_no_click_type(tmp_path, monkeypatch):
    from remedy.core.computer.desktop_os import native
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    typed: list[str] = []
    mod = native()
    monkeypatch.setattr(
        mod, "type_text", lambda *a, **k: typed.append("type") or 0, raising=False
    )
    monkeypatch.setattr(
        mod,
        "type_text_fast",
        lambda *a, **k: typed.append("fast") or {"method": "keystrokes"},
        raising=False,
    )
    monkeypatch.setattr(
        mod, "click_element", lambda *a, **k: typed.append("click"), raising=False
    )
    monkeypatch.setattr(ComputerExecutor, "_abort_check", lambda self: False)
    monkeypatch.setattr(ComputerExecutor, "_web_task_in_flight", lambda self: False)
    ex = ComputerExecutor(home_dir=tmp_path)
    monkeypatch.setattr(
        ex.bridge,
        "get_element_by_ref",
        lambda ref: {
            "ref": ref,
            "x": 10,
            "y": 20,
            "role": "button",
            "name": "Submit",
        },
    )
    out = ex._run_desktop(ComputerAction.TYPE, text="{{vault:pw}}", ref="c1")
    assert out.get("ok") is False
    assert not typed
    extra = out.get("extra") or {}
    assert extra.get("length") is None


def test_vault_browser_refuses_non_editable_ref(tmp_path):
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    ex = ComputerExecutor(home_dir=tmp_path)
    ex.bridge.set_last_elements(
        [{"ref": "e1", "name": "Submit", "role": "button", "tag": "button"}],
        target="browser",
    )
    out = ex._run_browser(ComputerAction.TYPE, text="{{vault:pw}}", ref="e1")
    assert out.get("ok") is False
    msg = str(out.get("message") or "").lower()
    assert "editable" in msg or "input" in msg


def test_bisect_restores_last_good(tmp_path: Path):
    from remedy.core.build_snapshot import bisect_red_wave, snapshot_paths

    f = tmp_path / "a.py"
    f.write_text("v=1\n", encoding="utf-8")
    snapshot_paths(tmp_path, ["a.py"], note="g")
    f.write_text("v=2\n", encoding="utf-8")
    snapshot_paths(tmp_path, ["a.py"], note="r1")
    f.write_text("v=3\n", encoding="utf-8")
    snapshot_paths(tmp_path, ["a.py"], note="r2")

    def verify(project):
        return (project / "a.py").read_text(encoding="utf-8") == "v=1\n"

    out = bisect_red_wave(tmp_path, verify_fn=verify)
    assert out.get("ok") is True
    assert f.read_text(encoding="utf-8") == "v=1\n"


def test_element_is_editable_real_fields_only():
    from remedy.core.computer.executor import _element_is_editable

    assert _element_is_editable({"role": "textbox"}) is True
    assert _element_is_editable({"role": "entry", "source": "atspi"}) is True
    assert _element_is_editable({"role": "password text"}) is True
    assert _element_is_editable({"role": "edit"}) is True
    assert _element_is_editable({"type": "password"}) is True
    assert _element_is_editable({"tag": "input", "type": "text"}) is True
    assert _element_is_editable({"role": "text", "name": "Password"}) is False
    assert _element_is_editable({"tag": "input", "type": "submit"}) is False
    assert _element_is_editable({"role": "button"}) is False
    assert _element_is_editable({"role": "input"}) is False


def test_nested_extra_pending_load_is_not_life_drive_done():
    def run(_action, **_kw):
        return json.dumps({"ok": True, "extra": {"pending_load": True, "observed": True}})

    out = drive_life_task(
        goal="open shop",
        steps=[{"title": "Open", "action": "navigate", "url": "https://shop.example"}],
        run_action=run,
        max_retries=0,
    )
    assert out["ok"] is False
    assert out["steps"][0]["status"] != "done"


def test_executor_screenshot_without_bounds_ok_false(tmp_path, monkeypatch):
    from remedy.core.computer.desktop_os import native
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    mod = native()
    monkeypatch.setattr(mod, "find_webview_host_hwnd", lambda: None, raising=False)
    monkeypatch.setattr(
        mod,
        "screenshot_region_png",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no full desk")),
        raising=False,
    )
    ex = ComputerExecutor(home_dir=tmp_path)
    monkeypatch.setattr(ex.bridge, "get_browser_bounds", lambda: None)
    monkeypatch.setattr(ex.bridge, "host_connected", lambda: False)
    out = ex._run_browser(ComputerAction.SCREENSHOT)
    assert out.get("ok") is False
    assert "bounds" in str(out.get("message") or "").lower()


def test_models_active_custom_url_is_not_refused(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import remedy.interfaces.routes.catalog as catalog_mod
    from remedy.interfaces.routes.catalog import register_catalog_routes

    owner_url = "https://llm.owner.example/v1"
    monkeypatch.setattr(
        catalog_mod,
        "load_config",
        lambda: {
            "llm_provider": "custom",
            "llm_base_url": owner_url,
        },
    )
    monkeypatch.setattr(
        "remedy.interfaces.config.resolve_provider_api_key",
        lambda cfg, provider: "sk-not-a-real-key-for-tests",
    )
    app = FastAPI()
    register_catalog_routes(app, runtime=None, gateway=None, memory=None)
    r = TestClient(app).get(
        "/api/models",
        params={"provider": "custom", "base_url": owner_url},
    )
    assert r.status_code == 200
    body = r.json()
    assert "Refused" not in str(body.get("error") or "")


def test_budget_hits_drops_hive_session_id():
    from remedy.memory.authority import budget_hits

    hits = [
        {"title": "h", "content": "hive-sid-leak", "session_id": "hive_abc"},
        {"title": "ok", "content": "keep-me", "session_id": "owner"},
    ]
    blob = json.dumps(budget_hits(hits, limit=6)).lower()
    assert "hive-sid-leak" not in blob
    assert "keep-me" in blob


def test_memory_search_api_drops_hive(tmp_path):
    import asyncio

    from fastapi.testclient import TestClient

    from remedy.interfaces.api import create_app
    from remedy.memory.authority import stamp_entry_metadata
    from remedy.memory.store import MemoryStore
    from remedy.models import MemoryEntry, MemoryEntryType

    mem = MemoryStore(tmp_path / "memory.db")
    asyncio.run(mem.initialize())
    hive_meta = stamp_entry_metadata(
        {}, source="hive", session_id="hive_zz", inferred=False
    )
    owner_meta = stamp_entry_metadata(
        {}, source="owner", session_id="owner-sess", inferred=False
    )
    asyncio.run(
        mem.upsert(
            MemoryEntry(
                title="hive api leak",
                content="daughter api leak",
                entry_type=MemoryEntryType.NOTE,
                session_id="hive_zz",
                metadata=hive_meta,
            )
        )
    )
    asyncio.run(
        mem.upsert(
            MemoryEntry(
                title="owner leak note",
                content="owner oat-milk leak",
                entry_type=MemoryEntryType.NOTE,
                session_id="owner-sess",
                metadata=owner_meta,
            )
        )
    )

    class RT:
        config = SimpleNamespace(home_dir=str(tmp_path))

        def list_tasks(self):
            return []

    client = TestClient(create_app(runtime=RT(), memory=mem, api_key=""))
    body = client.get("/api/memory/search", params={"query": "leak"}).json()
    blob = json.dumps(body).lower()
    assert "owner oat-milk leak" in blob
    assert "daughter api leak" not in blob
    assert "hive_zz" not in blob


def test_memory_facts_route_skips_hive(tmp_path):
    import asyncio

    from fastapi.testclient import TestClient

    from remedy.interfaces.api import create_app
    from remedy.memory.profile import UserFact, UserProfile
    from remedy.memory.store import MemoryStore

    mem = MemoryStore(tmp_path / "memory.db")
    asyncio.run(mem.initialize())
    profile = UserProfile(user_id="default")
    profile.facts.append(UserFact(fact="likes oat milk", authority="owner"))
    profile.facts.append(UserFact(fact="hive should hide", authority="hive"))
    asyncio.run(mem.save_user_profile(profile))

    class RT:
        config = SimpleNamespace(home_dir=str(tmp_path))

        def list_tasks(self):
            return []

    client = TestClient(create_app(runtime=RT(), memory=mem, api_key=""))
    body = client.get("/api/memory/facts").json()
    texts = [str(f.get("text") or "") for f in body.get("facts") or []]
    assert any("oat milk" in t for t in texts)
    assert not any("hive should hide" in t for t in texts)


def test_authorize_unknown_denied_memory_save_not_unknown():
    from remedy.core.hive.policy import reset_hive_depth, set_hive_depth
    from remedy.core.turn_pipeline import authorize_tool

    rt = SimpleNamespace(access_scope=lambda: "project")
    tok = set_hive_depth(1)
    try:
        unk = authorize_tool(rt, "zzz_not_a_real_tool", {})
        assert unk and "unknown" in unk.lower()
        mem = authorize_tool(rt, "memory_save", {"text": "note"})
        assert mem is None or "unknown" not in (mem or "").lower()
    finally:
        reset_hive_depth(tok)


def test_soul_skips_persist_on_secret(tmp_path):
    from remedy.memory.soul.update import update_soul_after_turn

    sf = update_soul_after_turn(
        user_text="password: hunter2 extra",
        assistant_text="ok",
        home=tmp_path,
    )
    blob = json.dumps(sf.to_dict()).lower()
    assert "hunter2" not in blob
    assert not any("hunter2" in str(getattr(ep, "arc", "")) for ep in (sf.episodes or []))
    assert not any("hunter2" in t for t in (sf.relational.open_threads or []))
    assert not any("hunter2" in t for t in (sf.relational.tensions or []))


def test_l0_life_drive_skips_when_build_active(tmp_path, monkeypatch):
    from remedy.core.metabolism.l0 import try_l0_system_reply
    from remedy.memory.life_goals import LifeGoalStore

    LifeGoalStore(tmp_path).add("Land the job", next_action="Rewrite the resume")
    monkeypatch.setattr(
        "remedy.core.build_engine.get_build_state",
        lambda _rt: SimpleNamespace(active=True),
    )
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=str(tmp_path)))
    text = try_l0_system_reply(rt, "what should I do on my goals?", preclassified=True)
    assert text is None


def test_computer_fill_vault_does_not_click_then_type(tmp_path, monkeypatch):
    from remedy.core.computer.executor import ComputerExecutor

    calls: list[str] = []
    ex = ComputerExecutor(home_dir=tmp_path)

    def fake_run(act, **kw):
        calls.append(act.value if hasattr(act, "value") else str(act))
        return {"ok": False, "message": "vault check"}

    monkeypatch.setattr(ex, "_run_browser", fake_run)
    out = ex._computer_fill(
        {"fields": [{"text": "Password", "value": "{{vault:pw}}"}]}
    )
    assert "click" not in calls
    assert "type" in calls
    assert out.get("ok") is False


def test_computer_fill_success_is_unverified(tmp_path, monkeypatch):
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    ex = ComputerExecutor(home_dir=tmp_path)

    def fake_run(act, **_kw):
        return {"ok": True, "action": getattr(act, "value", act), "message": "ok"}

    monkeypatch.setattr(ex, "_run_browser", fake_run)
    out = ex._computer_fill({"fields": [{"text": "Name", "value": "Ada"}]})
    assert out.get("ok") is False
    assert out.get("unverified") is True
    assert ComputerAction.FILL.value == "fill"


def test_hwnd_uia_button_vault_refuses_no_set_value(tmp_path, monkeypatch):
    from remedy.core.computer import desktop_uia as uia
    from remedy.core.computer.desktop_os import native
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    typed: list[str] = []
    set_calls: list[str] = []
    mod = native()
    monkeypatch.setattr(
        mod, "type_text", lambda *a, **k: typed.append("type") or 0, raising=False
    )
    monkeypatch.setattr(
        uia,
        "element_action",
        lambda *a, **k: set_calls.append("set") or {"ok": True},
    )
    monkeypatch.setattr(ComputerExecutor, "_abort_check", lambda self: False)
    monkeypatch.setattr(ComputerExecutor, "_web_task_in_flight", lambda self: False)
    ex = ComputerExecutor(home_dir=tmp_path)
    monkeypatch.setattr(
        ex.bridge,
        "get_element_by_ref",
        lambda ref: {
            "ref": ref,
            "hwnd": 7,
            "uia": True,
            "role": "button",
            "name": "Submit",
        },
    )
    out = ex._run_desktop(ComputerAction.TYPE, text="{{vault:pw}}", ref="c1")
    assert out.get("ok") is False
    assert not typed
    assert not set_calls


def test_ocr_role_text_vault_refuses(tmp_path, monkeypatch):
    from remedy.core.computer.desktop_os import native
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    typed: list[str] = []
    mod = native()
    monkeypatch.setattr(
        mod, "type_text", lambda *a, **k: typed.append("type") or 0, raising=False
    )
    monkeypatch.setattr(ComputerExecutor, "_abort_check", lambda self: False)
    monkeypatch.setattr(ComputerExecutor, "_web_task_in_flight", lambda self: False)
    ex = ComputerExecutor(home_dir=tmp_path)
    monkeypatch.setattr(
        ex.bridge,
        "get_element_by_ref",
        lambda ref: {
            "ref": ref,
            "role": "text",
            "source": "ocr",
            "name": "Password",
            "x": 10,
            "y": 20,
        },
    )
    out = ex._run_desktop(ComputerAction.TYPE, text="{{vault:pw}}", ref="c1")
    assert out.get("ok") is False
    assert not typed


def test_atspi_entry_vault_clicks_center_then_types(tmp_path, monkeypatch):
    from remedy.core.computer.desktop_os import native
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    typed: list[str] = []
    clicks: list = []
    mod = native()
    monkeypatch.setattr(
        mod,
        "type_text",
        lambda *a, **k: typed.append("type") or 0,
        raising=False,
    )
    monkeypatch.setattr(
        mod, "click_element", lambda el, **k: clicks.append(el), raising=False
    )
    monkeypatch.setattr(ComputerExecutor, "_abort_check", lambda self: False)
    monkeypatch.setattr(ComputerExecutor, "_web_task_in_flight", lambda self: False)
    ex = ComputerExecutor(home_dir=tmp_path)
    monkeypatch.setattr(
        ex,
        "_expand_vault_text",
        lambda text, **k: ("SECRET", None),
    )
    monkeypatch.setattr(
        ex.bridge,
        "get_element_by_ref",
        lambda ref: {
            "ref": ref,
            "role": "entry",
            "source": "atspi",
            "name": "Password",
            "x": 40,
            "y": 50,
        },
    )
    out = ex._run_desktop(ComputerAction.TYPE, text="{{vault:pw}}", ref="c1")
    assert out.get("ok") is True
    assert clicks and typed
    assert out.get("length") is None
    assert out.get("method") == "atspi_type"


def test_secret_job_retain_has_no_length():
    from remedy.core.computer.host_bridge import _scrub_retained_payload

    out = _scrub_retained_payload(
        {"text": "hunter2secretxx", "_has_secret": True, "action": "type"}
    )
    assert out.get("text") == "[redacted]"
    assert "chars=" not in json.dumps(out)
    assert str(len("hunter2secretxx")) not in json.dumps(out)


def test_browser_vault_none_editable_fail_closed(tmp_path):
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    ex = ComputerExecutor(home_dir=tmp_path)
    out = ex._run_browser(
        ComputerAction.TYPE, text="{{vault:pw}}", query="Password"
    )
    assert out.get("ok") is False


