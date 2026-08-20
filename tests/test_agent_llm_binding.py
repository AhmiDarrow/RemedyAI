"""Everything between "the agent decided to talk to a model" and the HTTP wire.

If this module is wrong the damage is quiet rather than loud: tool schemas get
cached against a stale generation so the model is offered tools that no longer
exist; a 7B local host is handed 80 fat tool definitions and never finishes
prefilling; a provider error body containing an API key is echoed straight back
into the transcript; an expired xAI token is retried forever, or never retried
at all; or a 500 from the provider is raised as an exception into a chat loop
that was written to expect a string. These tests pin the guards, the refusals
and the error text. No socket is ever opened.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core import agent_llm
from remedy.core.llm_binding import (
    LlmBinding,
    get_llm_binding,
    reset_llm_binding,
    set_llm_binding,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _tool(
    name: str,
    description: str = "",
    props: dict | None = None,
    required: list | None = None,
) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description or f"{name} does a thing",
            "parameters": {
                "type": "object",
                "properties": props if props is not None else {"path": {"type": "string"}},
                "required": required if required is not None else [],
            },
        },
    }


class _FakeResponse:
    def __init__(self, status: int = 200, payload: dict | None = None, text: str = "") -> None:
        self.status = status
        self._payload = payload if payload is not None else {"ok": True}
        self._text = text

    async def json(self) -> dict:
        return self._payload

    async def text(self) -> str:
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _FakeSession:
    """Records every POST and refuses to serve more calls than the test allowed."""

    def __init__(self, *responses: _FakeResponse) -> None:
        self._queue = list(responses)
        self.calls: list[dict] = []

    def post(self, url, *, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if not self._queue:
            raise AssertionError("post_chat issued more HTTP calls than the test allowed")
        return self._queue.pop(0)


@pytest.fixture
def bound():
    """Set the per-turn LLM binding and restore whatever was there before."""
    tokens = []

    def _set(**kw):
        base = {
            "provider": "openai",
            "model": "gpt-test",
            "base_url": "https://llm.example/v1",
            "api_key": "sk-unit-test-key",
        }
        base.update(kw)
        tokens.append(set_llm_binding(LlmBinding(**base)))

    yield _set
    for tok in reversed(tokens):
        # An async test sets the binding inside the event loop's copied Context,
        # so it never escapes and its token cannot be reset from out here.
        with contextlib.suppress(ValueError):
            reset_llm_binding(tok)


@pytest.fixture
def no_sleev(monkeypatch):
    """Keep Sleev out of the endpoint decision so the adapter URL is the URL."""
    import remedy.core.sleev as sleev

    def _boom(**_kw):
        raise RuntimeError("sleev gateway unavailable")

    monkeypatch.setattr(sleev, "prepare_llm_http", _boom)


@pytest.fixture
def session(monkeypatch):
    def _install(*responses: _FakeResponse) -> _FakeSession:
        fake = _FakeSession(*responses)
        monkeypatch.setattr(agent_llm, "_get_shared_session", lambda: fake)
        return fake

    return _install


# ---------------------------------------------------------------------------
# _sleev_force
# ---------------------------------------------------------------------------


def test_no_runtime_means_no_forced_direct_route():
    assert agent_llm._sleev_force(None) is False


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("yes", True),
        ("", False),
        (None, False),
    ],
)
def test_the_force_direct_flag_is_read_as_a_plain_truth_value(attr, expected):
    runtime = SimpleNamespace(_sleev_force_direct=attr)
    assert agent_llm._sleev_force(runtime) is expected


def test_a_runtime_without_the_flag_is_not_forced_direct():
    assert agent_llm._sleev_force(SimpleNamespace()) is False


def test_a_broken_turn_context_falls_back_to_the_runtime_attribute(monkeypatch):
    """The turn ContextVar is the preferred source, but a failure there must not
    take the whole request down: the runtime snapshot still answers."""
    import remedy.core.turn_context as tc

    def _boom(_runtime=None):
        raise RuntimeError("turn context exploded")

    monkeypatch.setattr(tc, "turn_sleev_force_direct", _boom)
    assert agent_llm._sleev_force(SimpleNamespace(_sleev_force_direct=True)) is True
    assert agent_llm._sleev_force(SimpleNamespace()) is False


# ---------------------------------------------------------------------------
# _llm_timeout
# ---------------------------------------------------------------------------


def test_a_cloud_model_gets_the_two_minute_wall():
    bind = SimpleNamespace(provider="openai", model="gpt-4o", base_url="")
    t = agent_llm._llm_timeout(bind)
    assert t.total == 120.0
    assert t.connect == 30


def test_a_local_model_gets_the_long_wall(monkeypatch):
    monkeypatch.delenv("REMEDY_LOCAL_LLM_TIMEOUT", raising=False)
    bind = SimpleNamespace(provider="ollama", model="qwen2.5:7b", base_url="")
    assert agent_llm._llm_timeout(bind).total == 300.0


def test_the_local_wall_can_be_raised_by_environment(monkeypatch):
    monkeypatch.setenv("REMEDY_LOCAL_LLM_TIMEOUT", "600")
    bind = SimpleNamespace(provider="llamacpp", model="q", base_url="")
    assert agent_llm._llm_timeout(bind).total == 600.0


@pytest.mark.parametrize("garbage", ["not-a-number", "300s", "  ", "1e"])
def test_a_garbage_local_timeout_override_keeps_the_local_wall(monkeypatch, garbage):
    """The float() used to raise inside the same try that had already decided
    the model was local, so a local host got the 120s *cloud* wall — the one
    case the longer wall exists for, broken by the setting meant to tune it."""
    monkeypatch.setenv("REMEDY_LOCAL_LLM_TIMEOUT", garbage)
    bind = SimpleNamespace(provider="ollama", model="q", base_url="")
    assert agent_llm._llm_timeout(bind).total == 300.0


def test_a_valid_local_timeout_override_is_honoured(monkeypatch):
    monkeypatch.setenv("REMEDY_LOCAL_LLM_TIMEOUT", "600")
    bind = SimpleNamespace(provider="ollama", model="q", base_url="")
    assert agent_llm._llm_timeout(bind).total == 600.0


def test_a_binding_missing_every_field_is_treated_as_cloud():
    assert agent_llm._llm_timeout(object()).total == 120.0


def test_a_loopback_base_url_alone_marks_the_host_local(monkeypatch):
    monkeypatch.delenv("REMEDY_LOCAL_LLM_TIMEOUT", raising=False)
    bind = SimpleNamespace(provider="custom", model="m", base_url="http://127.0.0.1:8787/v1")
    assert agent_llm._llm_timeout(bind).total == 300.0


# ---------------------------------------------------------------------------
# openai_tools_payload
# ---------------------------------------------------------------------------


class _FakeRegistry:
    def __init__(self, tools, generation=1):
        self.tools = tools
        self.schema_generation = generation


def test_a_tool_without_a_description_is_described_by_its_own_name():
    reg = _FakeRegistry([SimpleNamespace(name="file_read", description="", parameters={})])
    assert agent_llm.openai_tools_payload(reg) == [
        {
            "type": "function",
            "function": {
                "name": "file_read",
                "description": "file_read",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_a_bare_property_map_is_wrapped_into_an_object_schema():
    """Tools registered with just ``{"path": {...}}`` are not valid JSON Schema;
    sending them raw makes strict providers reject the whole request."""
    reg = _FakeRegistry(
        [SimpleNamespace(name="t", description="d", parameters={"path": {"type": "string"}})]
    )
    params = agent_llm.openai_tools_payload(reg)[0]["function"]["parameters"]
    assert params == {"type": "object", "properties": {"path": {"type": "string"}}}


def test_an_already_typed_schema_is_passed_through_untouched():
    schema = {"type": "object", "properties": {"a": {"type": "number"}}, "required": ["a"]}
    reg = _FakeRegistry([SimpleNamespace(name="t", description="d", parameters=schema)])
    assert agent_llm.openai_tools_payload(reg)[0]["function"]["parameters"] == schema


def test_the_payload_is_cached_until_the_registry_generation_moves():
    reg = _FakeRegistry([SimpleNamespace(name="a", description="d", parameters={})], generation=3)
    first = agent_llm.openai_tools_payload(reg)
    reg.tools = [SimpleNamespace(name="b", description="d", parameters={})]
    assert agent_llm.openai_tools_payload(reg) is first, "the cache should still hold"
    reg.schema_generation = 4
    second = agent_llm.openai_tools_payload(reg)
    assert second is not first
    assert [t["function"]["name"] for t in second] == ["b"]


def test_generation_zero_is_never_cached():
    """``int(gen or -1)`` turns a real generation of 0 into -1, so the very first
    generation always re-walks the registry. Harmless today because a real
    ToolRegistry is at generation 0 only while it still has no tools."""
    reg = _FakeRegistry([SimpleNamespace(name="a", description="d", parameters={})], generation=0)
    assert agent_llm.openai_tools_payload(reg) is not agent_llm.openai_tools_payload(reg)


def test_a_registry_that_refuses_new_attributes_still_gets_its_payload():
    class Locked:
        __slots__ = ("schema_generation", "tools")

        def __init__(self):
            self.tools = [SimpleNamespace(name="a", description="d", parameters={})]
            self.schema_generation = 7

    payload = agent_llm.openai_tools_payload(Locked())
    assert [t["function"]["name"] for t in payload] == ["a"]


def test_a_real_registry_invalidates_its_cache_when_a_tool_is_registered():
    from remedy.skills.tool_registry import ToolRegistry

    reg = ToolRegistry()
    reg.register_builtin("file_read", "read a file", {"type": "object", "properties": {}})
    first = agent_llm.openai_tools_payload(reg)
    assert agent_llm.openai_tools_payload(reg) is first
    reg.register_builtin("file_write", "write a file", {"type": "object", "properties": {}})
    second = agent_llm.openai_tools_payload(reg)
    assert second is not first
    assert {t["function"]["name"] for t in second} == {"file_read", "file_write"}


# ---------------------------------------------------------------------------
# slim_tools_for_local
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty", [None, []])
def test_nothing_to_slim_is_returned_unchanged(empty):
    assert agent_llm.slim_tools_for_local(empty) is empty


def test_coding_tools_are_pushed_in_front_of_everything_else():
    tools = [_tool("web_search"), _tool("file_read"), _tool("zzz_unknown"), _tool("list_dir")]
    names = [t["function"]["name"] for t in agent_llm.slim_tools_for_local(tools)]
    assert names == ["list_dir", "file_read", "web_search", "zzz_unknown"]


def test_unranked_tools_keep_the_order_they_arrived_in():
    tools = [_tool("unk_a"), _tool("unk_b"), _tool("unk_c")]
    names = [t["function"]["name"] for t in agent_llm.slim_tools_for_local(tools)]
    assert names == ["unk_a", "unk_b", "unk_c"]


@pytest.mark.parametrize("asked", [0, 1, 7, -5])
def test_the_tool_budget_never_drops_below_eight(asked):
    """A caller asking for one tool would starve the model of every coding verb."""
    tools = [_tool(f"unk_{i}") for i in range(20)]
    assert len(agent_llm.slim_tools_for_local(tools, max_tools=asked)) == 8


def test_long_descriptions_are_truncated_to_the_budget():
    out = agent_llm.slim_tools_for_local([_tool("file_read", description="x" * 5000)], desc_chars=30)
    assert out[0]["function"]["description"] == "x" * 30


def test_only_the_first_properties_survive():
    props = {f"p{i}": {"type": "string"} for i in range(40)}
    out = agent_llm.slim_tools_for_local([_tool("file_read", props=props)], max_props=5)
    assert list(out[0]["function"]["parameters"]["properties"]) == ["p0", "p1", "p2", "p3", "p4"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"type": "integer"}, {"type": "integer"}),
        ({}, {"type": "string"}),
        ({"description": ""}, {"type": "string"}),
        ("not-a-dict", {"type": "string"}),
        (None, {"type": "string"}),
    ],
)
def test_a_property_always_ends_up_with_a_type(value, expected):
    out = agent_llm.slim_tools_for_local([_tool("file_read", props={"a": value})])
    assert out[0]["function"]["parameters"]["properties"]["a"] == expected


def test_a_short_property_description_is_kept_and_a_long_one_is_dropped():
    props = {
        "short": {"type": "string", "description": "s" * 100},
        "long": {"type": "string", "description": "l" * 101},
    }
    out = agent_llm.slim_tools_for_local([_tool("file_read", props=props)])
    slimmed = out[0]["function"]["parameters"]["properties"]
    assert slimmed["short"]["description"] == "s" * 100
    assert "description" not in slimmed["long"]


def test_enums_are_capped_at_sixteen_choices():
    props = {"mode": {"type": "string", "enum": [str(i) for i in range(50)]}}
    out = agent_llm.slim_tools_for_local([_tool("file_read", props=props)])
    assert out[0]["function"]["parameters"]["properties"]["mode"]["enum"] == [
        str(i) for i in range(16)
    ]


def test_required_names_are_capped_and_forced_to_strings():
    out = agent_llm.slim_tools_for_local(
        [_tool("file_read", required=[1, 2, *[f"r{i}" for i in range(20)]])]
    )
    req = out[0]["function"]["parameters"]["required"]
    assert len(req) == 12
    assert req[:2] == ["1", "2"]
    assert all(isinstance(x, str) for x in req)


def test_a_missing_required_list_becomes_an_empty_one():
    tools = [{"type": "function", "function": {"name": "file_read", "parameters": {}}}]
    out = agent_llm.slim_tools_for_local(tools)
    assert out[0]["function"]["parameters"] == {
        "type": "object",
        "properties": {},
        "required": [],
    }


@pytest.mark.parametrize("junk", ["a string", 42, None, ["nested"]])
def test_non_dict_entries_are_dropped_rather_than_crashing(junk):
    out = agent_llm.slim_tools_for_local([junk, _tool("file_read")])
    assert [t["function"]["name"] for t in out] == ["file_read"]


def test_a_tool_with_a_blank_name_is_dropped():
    tools = [{"function": {"name": "   ", "parameters": {}}}, _tool("file_read")]
    assert [t["function"]["name"] for t in agent_llm.slim_tools_for_local(tools)] == ["file_read"]


def test_flat_shaped_tools_do_not_spend_slots_they_cannot_use():
    """Scoring used to read the name off a flat ``{"name": ...}`` dict while
    emit accepted only the nested ``{"function": {...}}`` shape, so a flat tool
    won a slot and then vanished — the returned list came back short with valid
    tools left behind. They are skipped during scoring now."""
    flat = [{"name": f"unk_{i}", "parameters": {}} for i in range(8)]
    real = [_tool(n) for n in ("list_dir", "file_read", "repo_search")]
    out = agent_llm.slim_tools_for_local([*flat, *real], max_tools=8)
    names = [t["function"]["name"] for t in out]
    assert set(names) == {"list_dir", "file_read", "repo_search"}, (
        "valid tools were dropped to make room for shapes that cannot be emitted"
    )


def test_a_flat_tool_is_never_emitted():
    out = agent_llm.slim_tools_for_local([{"name": "flat", "parameters": {}}], max_tools=8)
    assert out == []


# ---------------------------------------------------------------------------
# tools_for_binding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty", [None, []])
def test_binding_aware_slimming_leaves_an_empty_tool_list_alone(empty):
    assert agent_llm.tools_for_binding(empty) is empty


def test_a_cloud_binding_gets_the_full_fat_tool_list_back(bound):
    bound(provider="openai", model="gpt-4o")
    tools = [_tool(f"unk_{i}", description="d" * 400) for i in range(60)]
    assert agent_llm.tools_for_binding(tools) is tools


def test_a_local_binding_gets_a_window_sized_pack(bound, monkeypatch):
    import remedy.core.endless_context as ec

    monkeypatch.setattr(ec, "resolve_local_window", lambda **_kw: 8192)
    bound(provider="ollama", model="qwen2.5:7b", base_url="http://127.0.0.1:11434/v1")
    tools = [_tool("file_read", description="d" * 900)]
    tools += [_tool(f"unk_{i}", description="d" * 900) for i in range(60)]

    out = agent_llm.tools_for_binding(tools)

    max_tools, desc_chars, _props = ec.tool_pack_for_window(8192)
    assert 0 < len(out) <= max_tools
    assert all(len(t["function"]["description"]) <= desc_chars for t in out)
    # The coding pack is an allowlist: the 60 unknown tools never reach the host.
    assert [t["function"]["name"] for t in out] == ["file_read"]


def test_a_broken_window_lookup_still_slims_for_the_local_host(bound, monkeypatch):
    """endless_context is an optimisation; when it fails the local host must
    still not be handed sixty full tool schemas."""
    import remedy.core.endless_context as ec

    def _boom(**_kw):
        raise RuntimeError("no window")

    monkeypatch.setattr(ec, "resolve_local_window", _boom)
    bound(provider="ollama", model="m", base_url="http://127.0.0.1:11434/v1")
    tools = [_tool(f"unk_{i}") for i in range(60)]
    assert len(agent_llm.tools_for_binding(tools)) == 48


def test_an_empty_pack_falls_back_to_the_generic_slimmer(bound, monkeypatch):
    import remedy.core.endless_context as ec

    monkeypatch.setattr(ec, "slim_tools_pack", lambda *_a, **_kw: [])
    bound(provider="ollama", model="m", base_url="http://127.0.0.1:11434/v1")
    tools = [_tool(f"unk_{i}") for i in range(60)]
    assert len(agent_llm.tools_for_binding(tools)) == 48


def test_an_unreadable_binding_leaves_the_tools_untouched(monkeypatch):
    import remedy.core.llm_binding as lb

    def _boom(_runtime=None):
        raise RuntimeError("no binding")

    monkeypatch.setattr(lb, "get_llm_binding", _boom)
    tools = [_tool("file_read")]
    assert agent_llm.tools_for_binding(tools) is tools


# ---------------------------------------------------------------------------
# post_chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_successful_call_returns_the_decoded_json(bound, no_sleev, session):
    bound()
    fake = session(_FakeResponse(200, {"choices": [{"message": {"content": "hi"}}]}))

    out = await agent_llm.post_chat(
        SimpleNamespace(), {"messages": [{"role": "user", "content": "x"}]}
    )

    assert out == {"choices": [{"message": {"content": "hi"}}]}
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://llm.example/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-unit-test-key"
    assert call["timeout"].total == 120.0


@pytest.mark.asyncio
async def test_the_sleev_endpoint_and_headers_win_when_the_gateway_is_up(
    bound, session, monkeypatch
):
    import remedy.core.sleev as sleev

    seen = {}

    def _prepare(**kw):
        seen.update(kw)
        return "https://gateway.example/route/chat", {"X-Sleev": "1"}

    monkeypatch.setattr(sleev, "prepare_llm_http", _prepare)
    bound()
    fake = session(_FakeResponse(200))

    await agent_llm.post_chat(SimpleNamespace(), {"messages": []})

    assert fake.calls[0]["url"] == "https://gateway.example/route/chat"
    assert fake.calls[0]["headers"] == {"X-Sleev": "1"}
    assert seen["provider"] == "openai"
    assert seen["force_direct"] is False


@pytest.mark.asyncio
async def test_a_body_that_is_not_a_dict_is_replaced_by_an_empty_one(bound, no_sleev, session):
    """post_chat is reachable from loops that can hand it None on a bad path; it
    must not put a list or a None on the wire as the chat body."""
    bound()
    fake = session(_FakeResponse(200))
    await agent_llm.post_chat(SimpleNamespace(), None)
    assert fake.calls[0]["json"] == {}


@pytest.mark.asyncio
async def test_the_caller_body_is_copied_not_mutated(bound, no_sleev, session):
    bound()
    body = {"model": "gpt-test", "messages": [{"role": "user", "content": "hello"}]}
    fake = session(_FakeResponse(200))

    await agent_llm.post_chat(SimpleNamespace(), body)

    assert fake.calls[0]["json"] is not body
    assert body == {"model": "gpt-test", "messages": [{"role": "user", "content": "hello"}]}


@pytest.mark.asyncio
async def test_non_dict_messages_are_stripped_before_the_wire(bound, no_sleev, session):
    bound()
    fake = session(_FakeResponse(200))

    await agent_llm.post_chat(
        SimpleNamespace(),
        {"messages": ["junk", None, {"role": "user", "content": "real"}]},
    )

    sent = fake.calls[0]["json"]["messages"]
    assert len(sent) == 1
    assert sent[0]["content"] == "real"


@pytest.mark.parametrize("status", [400, 404, 429, 500, 503])
@pytest.mark.asyncio
async def test_an_http_error_comes_back_as_text_not_an_exception(bound, no_sleev, session, status):
    """The ReAct loop treats a str return as "show this to the user"; raising
    here would abort the whole turn instead of reporting the failure."""
    bound()
    session(_FakeResponse(status, text="upstream said no"))

    out = await agent_llm.post_chat(SimpleNamespace(), {"messages": []})

    assert isinstance(out, str)
    assert f"[LLM ERROR — HTTP {status}]" in out
    assert "upstream said no" in out
    assert out.rstrip().endswith("[END LLM ERROR]")


@pytest.mark.asyncio
async def test_a_key_in_the_provider_error_body_is_redacted(bound, no_sleev, session):
    bound()
    session(_FakeResponse(401, text="invalid api_key: sk-proj-AAAAAAAABBBBBBBBCCCCCCCC provided"))

    out = await agent_llm.post_chat(SimpleNamespace(), {"messages": []})

    assert "sk-proj-AAAAAAAABBBBBBBBCCCCCCCC" not in out
    assert "[redacted]" in out


@pytest.mark.asyncio
async def test_a_huge_error_body_is_truncated(bound, no_sleev, session):
    bound()
    session(_FakeResponse(500, text="e" * 5000))
    out = await agent_llm.post_chat(SimpleNamespace(), {"messages": []})
    assert out.count("e") == 500


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.asyncio
async def test_a_non_xai_provider_is_never_re_authenticated(bound, no_sleev, session, status):
    bound(provider="openai")
    fake = session(_FakeResponse(status, text="nope"))

    out = await agent_llm.post_chat(SimpleNamespace(), {"messages": []})

    assert len(fake.calls) == 1, "only xAI has an OAuth refresh to try"
    assert f"[LLM ERROR — HTTP {status}]" in out


@pytest.mark.asyncio
async def test_an_expired_xai_token_is_refreshed_and_the_call_retried(
    bound, no_sleev, session, monkeypatch, tmp_path
):
    import remedy.interfaces.xai_auth as xa

    seen_home = {}

    def _refresh(home=None):
        seen_home["home"] = home
        return None

    monkeypatch.setattr(xa, "refresh_if_needed", _refresh)
    monkeypatch.setattr(xa, "resolve_bearer", lambda home=None: "xai-fresh-token")
    bound(provider="xai", api_key="xai-stale-token", base_url="https://api.x.ai/v1")
    runtime = SimpleNamespace(config=SimpleNamespace(home_dir=str(tmp_path)))
    fake = session(_FakeResponse(401, text="expired"), _FakeResponse(200, {"ok": "retried"}))

    out = await agent_llm.post_chat(runtime, {"messages": []})

    assert out == {"ok": "retried"}
    assert len(fake.calls) == 2
    assert fake.calls[1]["headers"]["Authorization"] == "Bearer xai-fresh-token"
    assert runtime._llm_api_key == "xai-fresh-token"
    assert get_llm_binding().api_key == "xai-fresh-token"
    assert seen_home["home"] == Path(str(tmp_path)).expanduser()


@pytest.mark.asyncio
async def test_the_same_token_coming_back_does_not_trigger_a_second_call(
    bound, no_sleev, session, monkeypatch
):
    """Refusing to retry with an identical bearer is what stops a 401 loop."""
    import remedy.interfaces.xai_auth as xa

    monkeypatch.setattr(xa, "refresh_if_needed", lambda home=None: None)
    monkeypatch.setattr(xa, "resolve_bearer", lambda home=None: "xai-stale-token")
    bound(provider="xai", api_key="xai-stale-token")
    fake = session(_FakeResponse(401, text="expired"))

    out = await agent_llm.post_chat(SimpleNamespace(config=None), {"messages": []})

    assert len(fake.calls) == 1
    assert "[LLM ERROR — HTTP 401]" in out


@pytest.mark.asyncio
async def test_no_token_at_all_does_not_trigger_a_second_call(bound, no_sleev, session, monkeypatch):
    import remedy.interfaces.xai_auth as xa

    monkeypatch.setattr(xa, "refresh_if_needed", lambda home=None: None)
    monkeypatch.setattr(xa, "resolve_bearer", lambda home=None: None)
    bound(provider="XAI", api_key="xai-stale-token")  # provider casing must not matter
    fake = session(_FakeResponse(403, text="expired"))

    out = await agent_llm.post_chat(SimpleNamespace(), {"messages": []})

    assert len(fake.calls) == 1
    assert "[LLM ERROR — HTTP 403]" in out


@pytest.mark.asyncio
async def test_a_failed_retry_asks_the_owner_to_sign_in_again(bound, no_sleev, session, monkeypatch):
    import remedy.interfaces.xai_auth as xa

    monkeypatch.setattr(xa, "refresh_if_needed", lambda home=None: None)
    monkeypatch.setattr(xa, "resolve_bearer", lambda home=None: "xai-fresh-token")
    bound(provider="xai", api_key="xai-stale-token")
    fake = session(
        _FakeResponse(401, text="expired"),
        _FakeResponse(401, text="still expired"),
    )

    out = await agent_llm.post_chat(SimpleNamespace(), {"messages": []})

    assert len(fake.calls) == 2
    assert "[auth required]" in out
    assert "remedy auth login xai" in out
    assert "still expired" not in out, "the second body is logged, not shown to the owner"


@pytest.mark.asyncio
async def test_a_broken_auth_module_degrades_to_the_plain_http_error(
    bound, no_sleev, session, monkeypatch
):
    import remedy.interfaces.xai_auth as xa

    def _boom(home=None):
        raise RuntimeError("credential store unreadable")

    monkeypatch.setattr(xa, "refresh_if_needed", _boom)
    bound(provider="xai", api_key="xai-stale-token")
    fake = session(_FakeResponse(401, text="expired"))

    out = await agent_llm.post_chat(SimpleNamespace(), {"messages": []})

    assert len(fake.calls) == 1
    assert "[LLM ERROR — HTTP 401]" in out


@pytest.mark.asyncio
async def test_a_local_binding_posts_with_the_long_timeout(bound, no_sleev, session, monkeypatch):
    monkeypatch.delenv("REMEDY_LOCAL_LLM_TIMEOUT", raising=False)
    bound(provider="ollama", model="qwen2.5:7b", base_url="http://127.0.0.1:11434/v1")
    fake = session(_FakeResponse(200))

    await agent_llm.post_chat(SimpleNamespace(), {"messages": []})

    assert fake.calls[0]["timeout"].total == 300.0


# ---------------------------------------------------------------------------
# fallback_response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("msg", ["hello", "Hi", "HEY!", "greetings", "yo", "hello world", "  hi  "])
def test_a_greeting_is_answered_with_the_configured_name(msg):
    runtime = SimpleNamespace(config=SimpleNamespace(name="Ada"))
    assert agent_llm.fallback_response(runtime, msg) == "Hello! I'm Ada. How can I help you?"


@pytest.mark.parametrize("runtime", [None, SimpleNamespace(), SimpleNamespace(config=None)])
def test_without_a_configured_name_she_calls_herself_remedy(runtime):
    assert "I'm Remedy." in agent_llm.fallback_response(runtime, "hello")


def test_a_blank_configured_name_does_not_leak_an_empty_greeting():
    runtime = SimpleNamespace(config=SimpleNamespace(name=""))
    assert "I'm Remedy." in agent_llm.fallback_response(runtime, "hello")


@pytest.mark.parametrize("msg", ["yo-yo string", "history", "shell"])
def test_a_word_that_merely_contains_a_greeting_is_not_a_greeting(msg):
    assert "How can I help you?" not in agent_llm.fallback_response(None, msg)


@pytest.mark.parametrize("msg", ["help", "can you help", "what now?", "HELP"])
def test_asking_for_help_gets_the_capability_blurb(msg):
    assert "basic agent runtime" in agent_llm.fallback_response(None, msg)


def test_a_greeting_with_a_question_mark_is_still_a_greeting():
    """Trailing punctuation is stripped before the greeting check, so a greeting
    wins over the "any question mark means help" rule."""
    assert "How can I help you?" in agent_llm.fallback_response(None, "hi?")


@pytest.mark.parametrize("msg", ["remember this", "what is in memory"])
def test_memory_talk_gets_the_memory_blurb(msg):
    assert "stored our conversation in memory" in agent_llm.fallback_response(None, msg)


def test_help_wins_over_memory_when_a_message_mentions_both():
    assert "basic agent runtime" in agent_llm.fallback_response(None, "help me remember")


@pytest.mark.parametrize("msg", ["", "the weather in Oslo", "42"])
def test_anything_else_says_plainly_that_there_is_no_model(msg):
    out = agent_llm.fallback_response(None, msg)
    assert out.startswith(f"Received: {msg}.")
    assert "fallback mode" in out
    assert "REMEDY_LLM_API_KEY" in out


def test_a_very_long_message_is_echoed_back_truncated():
    out = agent_llm.fallback_response(None, "z" * 5000)
    assert out.startswith("Received: " + "z" * 200 + ".")
    assert out.count("z") == 200
