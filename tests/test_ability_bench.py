"""Peer-framework ability benches — Remedy must match or beat the set.

Claude Code / Codex / Operator / CUA: coding + computer-use + recall stay
on the live round for every cloud (not Grok-only), jobs wake on enqueue,
chrome SQLite does not freeze the loop, thinking is visible on tool
rounds, hive does not write the owner.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from remedy.core.computer.types import COMPUTER_TOOL_NAMES, ComputerAction, action_from_tool
from remedy.core.react_stream import StreamRoundState, apply_openai_sse_chunk
from remedy.core.react_turn import (
    LOCAL_MAX_TOOLS_PER_STEP,
    WORK_MAX_TOOLS_CLOUD,
    WORK_MAX_TOOLS_PER_STEP,
    cap_tools_for_step,
    work_max_tools_for_step,
)
from remedy.core.turn_context import (
    abort_session,
    begin_turn,
    end_turn,
    is_turn_aborted,
    release_session_stream_claim,
    stream_claim_epoch,
    try_claim_session_stream,
)
from remedy.memory.authority import is_hive_writer
from remedy.memory.cas import EternalCAS
from remedy.memory.middleman import MemoryItem, content_key
from remedy.memory.store import MemoryStore
from remedy.models import ChatSession, MemoryEntry


def test_every_cloud_keeps_more_hands_than_local():
    assert work_max_tools_for_step(local=True) == LOCAL_MAX_TOOLS_PER_STEP
    grok = work_max_tools_for_step(local=False, provider="xai", model="grok-4.6")
    gpt = work_max_tools_for_step(local=False, provider="openai", model="gpt-4.1")
    claude = work_max_tools_for_step(
        local=False, provider="anthropic", model="claude-sonnet-4"
    )
    deepseek = work_max_tools_for_step(
        local=False, provider="deepseek", model="deepseek-chat"
    )
    gemini = work_max_tools_for_step(
        local=False, provider="google", model="gemini-2.5-pro"
    )
    assert grok == WORK_MAX_TOOLS_PER_STEP
    assert gpt == claude == deepseek == gemini == WORK_MAX_TOOLS_CLOUD
    assert grok < gpt
    assert gpt < 194


def test_every_cloud_keeps_recall_on_the_live_round():
    """Claude / GPT / Grok / DeepSeek / Gemini all keep memory on the operate pack."""

    def _schema(name: str) -> dict:
        return {"type": "function", "function": {"name": name, "parameters": {}}}

    flood = [_schema(f"extra_{i}") for i in range(180)] + [
        _schema("file_read"),
        _schema("memory_search"),
        _schema("soul_recall"),
        _schema("job_run"),
        _schema("help_list"),
    ]
    for provider, model, cap in (
        ("xai", "grok-4.6", WORK_MAX_TOOLS_PER_STEP),
        ("openai", "gpt-4.1", WORK_MAX_TOOLS_CLOUD),
        ("anthropic", "claude-sonnet-4", WORK_MAX_TOOLS_CLOUD),
        ("deepseek", "deepseek-chat", WORK_MAX_TOOLS_CLOUD),
        ("google", "gemini-2.5-pro", WORK_MAX_TOOLS_CLOUD),
        ("openrouter", "anthropic/claude-sonnet-4", WORK_MAX_TOOLS_CLOUD),
    ):
        n = work_max_tools_for_step(local=False, provider=provider, model=model)
        assert n == cap, provider
        names = {
            str((t.get("function") or {}).get("name"))
            for t in (cap_tools_for_step(flood, local=False, max_tools=n) or [])
        }
        assert "memory_search" in names, provider
        assert "soul_recall" in names, provider
        assert "job_run" in names, provider
        assert "help_list" not in names, provider


def test_hover_is_a_first_class_computer_hand():
    assert "computer_hover" in COMPUTER_TOOL_NAMES
    assert action_from_tool("computer_hover") is ComputerAction.HOVER


def test_stale_abort_epoch_does_not_kill_newer_turn():
    sid = "bench-epoch"
    assert try_claim_session_stream(sid)
    e1 = stream_claim_epoch(sid)
    release_session_stream_claim(sid, epoch=e1)
    assert try_claim_session_stream(sid)
    e2 = stream_claim_epoch(sid)
    toks = begin_turn(sid, project_raw=None, active_path=".")
    try:
        assert abort_session(sid, epoch=e1) == 0
        assert is_turn_aborted() is False
        assert abort_session(sid, epoch=e2) == 1
        assert is_turn_aborted() is True
    finally:
        end_turn(sid, *toks)
        release_session_stream_claim(sid, epoch=e2)


def test_sse_tool_round_still_emits_thinking_when_content_is_buffered():
    """Claude/GPT/DeepSeek SSE tool rounds must show thinking live.

    stream_live=False buffers assistant text (no DSML spam) but reasoning
    deltas still accumulate so consume can yield @@thinking.
    """
    state = StreamRoundState()
    live = apply_openai_sse_chunk(
        state,
        {
            "choices": [
                {
                    "delta": {
                        "reasoning_content": "I will click Sign in.",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "computer_click", "arguments": "{"},
                            }
                        ],
                    }
                }
            ]
        },
        stream_live=False,
    )
    assert live is None
    assert "Sign in" in state.reasoning_out
    assert 0 in state.tool_call_acc


@pytest.mark.asyncio
async def test_json_tool_round_says_working_before_the_body_arrives():
    from remedy.core.react_loop.stream_consume import consume_llm_http_response

    class _Resp:
        headers = {"Content-Type": "application/json"}
        content = None

        async def json(self):
            await asyncio.sleep(0)
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "click next",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "computer_click",
                                        "arguments": '{"text":"Next"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    adapter = SimpleNamespace(extract_response=lambda data: {})
    bind = SimpleNamespace(model="grok-4.6", provider="xai")
    state = StreamRoundState()
    collected: dict = {}
    tokens: list[str] = []
    async for tok, _user in consume_llm_http_response(
        _Resp(),
        round_state=state,
        collected=collected,
        adapter=adapter,
        bind=bind,
        body={"stream": False},
        use_openai_sse=False,
        stream_live=False,
    ):
        tokens.append(tok)
    joined = " ".join(tokens)
    assert "@@thinking_round" in joined
    assert "@@status:Working" in joined
    assert "@@thinking:" in joined
    assert "click next" in joined


def test_hover_enqueue_opens_the_browser_rail(tmp_path):
    from remedy.core.computer.host_bridge import ComputerHostBridge

    b = ComputerHostBridge(home_dir=tmp_path)
    job = b.enqueue("hover", {"text": "File"})
    cmd = b.peek_ui_command()
    assert cmd is not None
    assert cmd.get("action") == "open_browser"
    assert cmd.get("job_action") == "hover"
    assert cmd.get("job_id") == job.id


def test_fetch_hot_does_not_hydrate_hive_facts_into_the_owner(tmp_path):
    cas = EternalCAS(tmp_path)
    owner_body = "owner likes oat milk"
    hive_body = "hive decided the house language is COBOL"
    cas.put_item(
        MemoryItem(
            key=content_key(owner_body),
            kind="fact",
            body=owner_body,
            session_id="chat-1",
        )
    )
    cas.put_item(
        MemoryItem(
            key=content_key(hive_body),
            kind="fact",
            body=hive_body,
            session_id="hive_forager1",
        )
    )
    owner_hot = [getattr(i, "body", "") for i in cas.fetch_hot(session_id="chat-1")]
    assert any("oat milk" in b for b in owner_hot)
    assert not any("COBOL" in b for b in owner_hot)
    hive_hot = [
        getattr(i, "body", "") for i in cas.fetch_hot(session_id="hive_forager1")
    ]
    assert any("COBOL" in b for b in hive_hot)
    assert any("oat milk" in b for b in hive_hot)


def test_hive_residue_does_not_mint_parent_cas_facts(tmp_path):
    from remedy.core.metabolism.organism import ingest_turn_residue

    assert is_hive_writer("hive_forager1")
    key = ingest_turn_residue(
        home=tmp_path,
        session_id="hive_forager1",
        user_text="Remember we decided TypeScript is the house language forever.",
        assistant_text="Logged that TypeScript is the house language.",
    )
    assert key == ""


@pytest.mark.asyncio
async def test_chat_session_reads_run_off_the_event_loop(tmp_path, monkeypatch):
    store = MemoryStore(tmp_path / "bench.db")
    await store.initialize()
    off = {"n": 0}
    real = store._off_loop

    async def spy(fn, /, *args, **kwargs):
        off["n"] += 1
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(store, "_off_loop", spy)
    sess = ChatSession(title="bench")
    await store.create_chat_session(sess)
    got = await store.get_chat_session(sess.id)
    listed = await store.list_chat_sessions(limit=10)
    assert got is not None and got.id == sess.id
    assert any(s.id == sess.id for s in listed)
    assert off["n"] >= 3
    counts_before = off["n"]
    mem, summaries, chats = await store.status_counts()
    assert mem == 0 and summaries == 0 and chats >= 1
    assert off["n"] > counts_before
    search_before = off["n"]
    await store.upsert(
        MemoryEntry(
            title="oat milk",
            content="owner likes oat milk",
        )
    )
    hits = await store.search("oat milk", limit=5)
    assert any("oat milk" in (h.title or "") for h in hits)
    assert off["n"] > search_before
    del_before = off["n"]
    assert await store.delete_chat_session(sess.id) is True
    assert off["n"] > del_before
    await store.close()
