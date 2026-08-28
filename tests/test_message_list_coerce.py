"""List/tuple user messages must not die on (message or "").strip()."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.assistant.fast_path import match_assistant_fast_path
from remedy.core.agent_react_preamble import append_plan_and_computer_addenda, parse_browse_intent
from remedy.core.away_mode import looks_like_away_request
from remedy.core.build_engine import looks_like_build_request
from remedy.core.companion import looks_like_companion_request
from remedy.core.intent_policy import looks_like_readonly_request, policy_for_intent
from remedy.core.react_policy import (
    is_chat_only_message,
    is_feeling_presence_question,
    message_wants_tools,
    runtime_turn_is_chat_only,
)


def test_looks_like_away_request_joins_list() -> None:
    assert looks_like_away_request(["work", "alone"]) is True
    assert looks_like_away_request(("step", "away")) is True
    assert looks_like_away_request('["work alone"]') is True
    assert looks_like_away_request(["thanks"]) is False
    assert looks_like_away_request([]) is False
    assert looks_like_away_request(None) is False


def test_looks_like_companion_request_joins_list() -> None:
    assert looks_like_companion_request(["look at my screen"]) is True
    assert looks_like_companion_request(("what's on my", "clipboard")) is True
    assert looks_like_companion_request(["thanks"]) is False
    assert looks_like_companion_request([]) is False


def test_react_policy_list_message_does_not_crash_strip() -> None:
    assert is_chat_only_message(["hi"]) is True
    assert is_chat_only_message(["implement", "the API"]) is False
    assert is_feeling_presence_question(["how does a local model feel?"]) is True
    assert message_wants_tools(["look at my screen"]) is True
    assert message_wants_tools(["hi"]) is False
    assert runtime_turn_is_chat_only(message=["hello"]) is True
    assert runtime_turn_is_chat_only(message=["build me a CLI"]) is False


def test_build_and_intent_list_message_does_not_crash_strip() -> None:
    assert looks_like_build_request(["implement", "a REST API"]) is True
    assert looks_like_build_request(["thanks"]) is False
    assert looks_like_readonly_request(["explain", "the build loop"]) is True
    pack = policy_for_intent(["chat"], user_text=["work", "alone"])
    assert pack.get("id") == "autonomous"


def test_fast_path_list_message_does_not_crash_strip() -> None:
    p = match_assistant_fast_path(["daily", "brief"])
    assert p is not None and p.tool == "assistant_brief"
    assert match_assistant_fast_path(["hi"]) is None
    assert match_assistant_fast_path([]) is None


def test_preamble_list_message_does_not_crash_strip(monkeypatch, tmp_path) -> None:
    """suppress used to hide AttributeError so away/companion never injected."""
    monkeypatch.setattr(
        "remedy.core.turn_context.current_last_user_text",
        lambda runtime=None: ["work", "alone"],
    )
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=tmp_path))
    out = append_plan_and_computer_addenda(
        "ctx",
        session_id="s1",
        plan_mode=True,
        runtime=rt,
        message=["step", "away"],
    )
    assert isinstance(out, str)
    assert "Away mode" in out
    flags = parse_browse_intent(["https://mail.google.com"])
    assert flags.browse_pre_url == "https://mail.google.com"


def test_remaining_owner_model_list_strip_does_not_crash(tmp_path) -> None:
    from remedy.core.agent_post_turn import distill_user_message_now
    from remedy.core.build_ledger import looks_like_continue
    from remedy.core.companion_taste import extract_taste
    from remedy.core.intent_learn import consult
    from remedy.core.local_agent_optimize import (
        looks_like_tutorial_monologue,
        message_wants_implement,
    )
    from remedy.core.metabolism.l0 import try_l0_system_reply
    from remedy.core.react_open_work import message_asks_to_finish_everything
    from remedy.core.react_stream import should_enable_tools
    from remedy.interfaces.attachments import build_multimodal_user_content
    from remedy.memory.life_drive import classify_action, looks_like_step_done
    from remedy.memory.life_goals import looks_like_life_goal_statement

    assert distill_user_message_now(
        SimpleNamespace(memory=None), ["remember", "I like teal"]
    )["added"] == 0
    assert looks_like_continue(["keep", "going"]) is True
    assert looks_like_continue(["implement a REST API"]) is False
    assert extract_taste(["I prefer dark mode and 8px spacing."])
    assert consult(["thanks"], regex_verdict=False, home=tmp_path) is False
    assert message_wants_implement(["create", "a calculator"]) is True
    assert looks_like_tutorial_monologue(["I will build the architecture next"]) in (
        True,
        False,
    )
    assert message_asks_to_finish_everything(["keep", "working"]) is True
    tools = [{"type": "function", "function": {"name": "x"}}]
    assert should_enable_tools(["look at my screen"], tools, has_attachments=False) is True
    assert should_enable_tools(["hi"], tools, has_attachments=False) is False
    assert build_multimodal_user_content(["hello"], None) == "hello"
    assert looks_like_life_goal_statement(["I want to finish my novel this year"]) is True
    assert looks_like_step_done(["I did it"]) is True
    assert classify_action(["research the topic"]) == "research"
    out = try_l0_system_reply(
        SimpleNamespace(), ["what is your", "version"], preclassified=True
    )
    assert isinstance(out, str) and "Remedy" in out


def test_life_goal_add_joins_list_title(tmp_path) -> None:
    from remedy.memory.life_goals import LifeGoalStore

    store = LifeGoalStore(tmp_path)
    g = store.add(["Finish the novel this year"], why=["it has been ten years"])
    assert g.title == "Finish the novel this year"
    assert "ten years" in g.why


def test_session_req_message_list_joins_like_routes() -> None:
    # stream.py / messages.py call coerce_text_arg(req.message).
    from remedy.core.build_oracle import coerce_text_arg

    req = SimpleNamespace(message=["goto", "gmail"])
    assert coerce_text_arg(req.message) == "goto gmail"
    assert coerce_text_arg(["keep going"]) == "keep going"
