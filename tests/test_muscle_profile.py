"""Muscle profile — capable provider sensing for builder agency."""

from __future__ import annotations

from remedy.core.muscle_profile import (
    TIER_FRONTIER,
    TIER_MID,
    TIER_TINY,
    builder_system_addendum,
    classify_muscle,
)


def test_grok_is_frontier():
    p = classify_muscle("xai", "grok-4")
    assert p.tier == TIER_FRONTIER
    assert p.builder_contract
    assert p.max_parallel_tools >= 20
    add = builder_system_addendum(p)
    assert "build" in add.lower()
    assert "RESEARCH → PLAN → BUILD" not in add


def test_anthropic_sonnet_frontier():
    p = classify_muscle("anthropic", "claude-sonnet-4")
    assert p.is_frontier
    assert p.prefer_spread


def test_openai_default_frontier():
    p = classify_muscle("openai", "")
    assert p.tier == TIER_FRONTIER


def test_tiny_local_lean():
    p = classify_muscle("ollama", "qwen2.5-1.5b")
    assert p.tier <= TIER_TINY + 1
    assert not p.builder_contract
    assert builder_system_addendum(p) == ""
    assert p.max_parallel_tools <= 8


def test_chat_turns_do_not_get_the_coding_syllabus():
    from types import SimpleNamespace

    from remedy.core.agent_react_preamble import append_plan_and_computer_addenda

    rt = SimpleNamespace(
        config=SimpleNamespace(home_dir=None),
        _llm_provider="xai",
        _llm_model="grok-4.5",
        effective_project_path=lambda: "",
    )
    out = append_plan_and_computer_addenda(
        "", session_id=None, plan_mode=False, runtime=rt, message="hi"
    )
    assert "Explore in parallel" not in out
    assert "Never kill app.exe" not in out


def test_frontier_work_gets_short_build_addendum():
    from types import SimpleNamespace

    from remedy.core.agent_react_preamble import append_plan_and_computer_addenda

    rt = SimpleNamespace(
        config=SimpleNamespace(home_dir=None),
        _llm_provider="xai",
        _llm_model="grok-4.5",
        effective_project_path=lambda: "",
    )
    out = append_plan_and_computer_addenda(
        "",
        session_id=None,
        plan_mode=False,
        runtime=rt,
        message="implement a calculator",
    )
    assert "file_edit" in out
    assert "7400" in out
    assert "1. **Explore" not in out
    assert "RESEARCH → PLAN → BUILD" not in out
    assert "computer_act" not in out
    assert "Add to cart" not in out


def test_computer_playbook_loads_only_when_needed():
    from types import SimpleNamespace

    from remedy.core.agent_react_preamble import append_plan_and_computer_addenda

    rt = SimpleNamespace(
        config=SimpleNamespace(home_dir=None),
        _llm_provider="xai",
        _llm_model="grok-4.5",
        effective_project_path=lambda: "",
    )
    code = append_plan_and_computer_addenda(
        "",
        session_id=None,
        plan_mode=False,
        runtime=rt,
        message="implement a calculator",
    )
    assert "computer_act" not in code
    shop = append_plan_and_computer_addenda(
        "",
        session_id=None,
        plan_mode=False,
        runtime=rt,
        message="goto walmart and buy milk",
    )
    assert "computer_act" in shop
    assert "Add to cart" in shop or "walmart" in shop.lower()


def test_mid_flash():
    p = classify_muscle("google", "gemini-flash")
    assert p.tier == TIER_MID
    assert p.is_capable
