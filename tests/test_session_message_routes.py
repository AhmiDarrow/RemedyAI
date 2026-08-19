"""The three session message routes: history, the build checklist, and send.

If this code is wrong the chat window breaks in ways that are hard to see. A
history page that does not cap message bodies ships a multi-megabyte JSON blob
to the UI on every scroll. A todos route that falls back to the shared runtime
cache shows one project's checklist inside another project's tab. And the send
route is the one place where a second POST can start a second generation on the
same session, where an empty composer submit can create a junk turn, and where a
forged attachment path can drag an arbitrary file into the model's context.

These tests pin the refusals: what must be rejected (503 without a runtime, 400
on an empty message, 409 while a turn is running), what must be left alone (a
sticky per-session provider that a stale model id must not steal), and what must
not silently leak (attachments outside the session's own folder).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from remedy.core.build_todos import upsert_todos
from remedy.interfaces.attachments import session_attachments_dir
from remedy.interfaces.routes.sessions.messages import register_messages_routes
from remedy.models import ChatMessage, ChatMessageRole, ChatSession

# --- doubles ------------------------------------------------------------------


class Memory:
    """Just enough of the store for these routes, with recorded calls."""

    def __init__(self, sessions=None, messages=None) -> None:
        self.sessions = {s.id: s for s in (sessions or [])}
        self.messages = list(messages or [])
        self.added: list[ChatMessage] = []
        self.updates: list[tuple[str, dict]] = []
        self.list_calls: list[tuple[str, int, int]] = []
        self.deleted = False

    async def get_chat_session(self, session_id):
        if self.deleted:
            return None
        return self.sessions.get(session_id)

    async def get_chat_messages(self, session_id, limit=50, offset=0):
        self.list_calls.append((session_id, limit, offset))
        return list(self.messages)

    async def add_chat_message(self, msg):
        self.added.append(msg)
        return msg

    async def update_chat_session(self, session_id, **fields):
        self.updates.append((session_id, dict(fields)))
        sess = self.sessions.get(session_id)
        for k, v in fields.items():
            setattr(sess, "model" if k == "model" else k, v)
        return sess


class Runtime:
    """Streams a fixed token list, recording exactly what the route asked for."""

    def __init__(self, tokens=("hello",), *, raises=None, on_token=None) -> None:
        self._tokens = list(tokens)
        self._raises = raises
        self._on_token = on_token
        self.calls: list[dict] = []
        self.consumed = 0

    def stream_response(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})

        async def _gen():
            if self._raises is not None:
                if self._on_token:
                    self._on_token(0)
                raise self._raises
            for i, tok in enumerate(self._tokens):
                self.consumed += 1
                if self._on_token:
                    self._on_token(i)
                yield tok

        return _gen()


class Gateway:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_to(self, kind, text, *, target=None):
        self.sent.append((str(kind), text, target))
        return True


def session(sid="s1", **kw) -> ChatSession:
    return ChatSession(id=sid, title=kw.pop("title", "T"), **kw)


def message(**kw) -> ChatMessage:
    base = {
        "session_id": "s1",
        "role": ChatMessageRole.ASSISTANT,
        "content": "hi",
        "created_at": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
    }
    base.update(kw)
    return ChatMessage(**base)


def make_client(*, runtime=None, gateway=None, memory=None) -> TestClient:
    app = FastAPI()
    register_messages_routes(app, runtime=runtime, gateway=gateway, memory=memory)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    """Keep config-derived paths (attachment jail) inside tmp, and never leave a
    stream claim behind — the claim table is process-global."""
    monkeypatch.setattr(
        "remedy.interfaces.routes.sessions.messages.load_config",
        lambda: {"home_dir": str(tmp_path)},
    )
    yield
    from remedy.core.turn_context import release_session_stream_claim

    for sid in ("s1", "s2", "gone", "  "):
        release_session_stream_claim(sid)


# --- GET /messages ------------------------------------------------------------


def test_listing_messages_without_a_store_is_unavailable_not_a_crash():
    r = make_client(memory=None).get("/api/sessions/s1/messages")
    assert r.status_code == 503
    assert "Memory store" in r.json()["detail"]


def test_listing_messages_for_an_unknown_session_is_a_404():
    r = make_client(memory=Memory()).get("/api/sessions/nope/messages")
    assert r.status_code == 404


def test_a_message_is_flattened_into_the_wire_shape():
    msg = message(
        role=ChatMessageRole.USER,
        content="hi",
        thinking="pondering",
        tool_calls=[{"name": "shell"}],
        tool_results=[{"output": "ok"}],
        model="grok-4-latest",
        agent="main",
        tokens=12,
    )
    r = make_client(memory=Memory([session()], [msg])).get("/api/sessions/s1/messages")
    assert r.status_code == 200
    row = r.json()["messages"][0]
    assert row["id"] == str(msg.id)
    assert row["role"] == "user"
    assert row["content"] == "hi"
    assert row["thinking"] == "pondering"
    assert row["tool_calls"] == [{"name": "shell"}]
    assert row["tool_results"] == [{"output": "ok"}]
    assert row["model"] == "grok-4-latest"
    assert row["agent"] == "main"
    assert row["tokens"] == 12
    assert row["created_at"] == "2024-01-02T03:04:05+00:00"
    assert row["reverted"] is False


@pytest.mark.parametrize(
    ("size", "truncated"),
    [(31_999, False), (32_000, False), (32_001, True)],
)
def test_content_is_capped_only_past_the_ceiling(size, truncated):
    mem = Memory([session()], [message(content="x" * size)])
    row = make_client(memory=mem).get("/api/sessions/s1/messages").json()["messages"][0]
    assert ("[truncated" in row["content"]) is truncated
    assert row["content"].startswith("x" * 100)


def test_a_capped_body_says_how_much_was_dropped():
    mem = Memory([session()], [message(content="x" * 32_050)])
    row = make_client(memory=mem).get("/api/sessions/s1/messages").json()["messages"][0]
    assert row["content"].endswith("[truncated 50 chars]")


def test_thinking_gets_half_the_budget_of_content():
    """16k, not 32k — reasoning blocks are the bulkiest part of a long session."""
    mem = Memory([session()], [message(thinking="t" * 16_001, content="short")])
    row = make_client(memory=mem).get("/api/sessions/s1/messages").json()["messages"][0]
    assert row["thinking"].endswith("[truncated 1 chars]")
    assert row["content"] == "short"


@pytest.mark.parametrize("empty", [None, ""])
def test_an_absent_thinking_block_stays_absent(empty):
    mem = Memory([session()], [message(thinking=empty)])
    row = make_client(memory=mem).get("/api/sessions/s1/messages").json()["messages"][0]
    assert row["thinking"] == empty


def test_a_long_tool_output_is_capped_harder_than_message_text():
    mem = Memory([session()], [message(tool_results=[{"output": "o" * 8_001}])])
    row = make_client(memory=mem).get("/api/sessions/s1/messages").json()["messages"][0]
    assert row["tool_results"][0]["output"].endswith("[truncated 1 chars]")


def test_tool_result_fields_other_than_output_are_left_alone():
    mem = Memory(
        [session()],
        [message(tool_results=[{"name": "shell", "args": "a" * 9_000}])],
    )
    row = make_client(memory=mem).get("/api/sessions/s1/messages").json()["messages"][0]
    assert row["tool_results"][0] == {"name": "shell", "args": "a" * 9_000}


def test_tool_calls_are_never_truncated():
    """The UI parses these as structure; a truncated arg blob is unparseable."""
    mem = Memory([session()], [message(tool_calls=[{"args": "z" * 40_000}])])
    row = make_client(memory=mem).get("/api/sessions/s1/messages").json()["messages"][0]
    assert row["tool_calls"][0]["args"] == "z" * 40_000


def test_tool_results_that_are_not_a_list_pass_straight_through():
    # Built unvalidated: the store's own rows are always lists, but the route
    # still has to survive a row that is not one rather than raise.
    msg = ChatMessage.model_construct(
        id=uuid4(),
        session_id="s1",
        role=ChatMessageRole.TOOL,
        content="c",
        thinking=None,
        tool_calls=[],
        tool_results=None,
        model=None,
        agent=None,
        tokens=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        reverted=False,
    )
    row = make_client(memory=Memory([session()], [msg])).get(
        "/api/sessions/s1/messages"
    ).json()["messages"][0]
    assert row["tool_results"] is None


def test_a_non_dict_tool_result_entry_is_still_capped():
    msg = ChatMessage.model_construct(
        id=uuid4(),
        session_id="s1",
        role=ChatMessageRole.TOOL,
        content="c",
        thinking=None,
        tool_calls=[],
        tool_results=["r" * 8_005],
        model=None,
        agent=None,
        tokens=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        reverted=False,
    )
    row = make_client(memory=Memory([session()], [msg])).get(
        "/api/sessions/s1/messages"
    ).json()["messages"][0]
    assert row["tool_results"][0].endswith("[truncated 5 chars]")


def test_paging_arguments_reach_the_store():
    mem = Memory([session()])
    make_client(memory=mem).get("/api/sessions/s1/messages?limit=7&offset=3")
    assert mem.list_calls == [("s1", 7, 3)]


@pytest.mark.parametrize("qs", ["limit=501", "offset=-1", "limit=abc"])
def test_an_out_of_range_page_is_rejected_before_the_store_is_touched(qs):
    mem = Memory([session()])
    r = make_client(memory=mem).get(f"/api/sessions/s1/messages?{qs}")
    assert r.status_code == 422
    assert mem.list_calls == []


def test_the_default_page_is_a_hundred_newest():
    mem = Memory([session()])
    make_client(memory=mem).get("/api/sessions/s1/messages")
    assert mem.list_calls == [("s1", 100, 0)]


# --- GET /todos ---------------------------------------------------------------


def test_todos_without_a_store_are_empty_rather_than_a_404():
    r = make_client(memory=None).get("/api/sessions/s1/todos")
    assert r.status_code == 200
    assert r.json() == {"todos": []}


def test_todos_for_an_unknown_session_are_a_404():
    r = make_client(memory=Memory()).get("/api/sessions/nope/todos")
    assert r.status_code == 404


@pytest.mark.parametrize("raw", [None, "", "   ", ".", "./", "C:\\", "/"])
def test_a_session_with_no_real_project_has_no_checklist(raw):
    """Otherwise the volume root grows a .remedy-build folder shared by every tab."""
    mem = Memory([session(project_path=raw)])
    r = make_client(memory=mem).get("/api/sessions/s1/todos")
    assert r.json() == {"todos": []}


def test_a_projects_checklist_is_read_from_its_own_folder(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    upsert_todos(None, [{"id": "t1", "content": "ship it", "status": "pending"}], root=proj)
    mem = Memory([session(project_path=str(proj))])
    r = make_client(memory=mem).get("/api/sessions/s1/todos")
    assert r.json() == {"todos": [{"id": "t1", "content": "ship it", "status": "pending"}]}


def test_a_project_path_that_is_a_file_resolves_to_its_folder(tmp_path):
    proj = tmp_path / "proj2"
    proj.mkdir()
    upsert_todos(None, [{"id": "t1", "content": "fix", "status": "in_progress"}], root=proj)
    f = proj / "main.py"
    f.write_text("x", encoding="utf-8")
    mem = Memory([session(project_path=str(f))])
    assert make_client(memory=mem).get("/api/sessions/s1/todos").json()["todos"]


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_a_finished_checklist_is_reported_as_nothing_to_do(tmp_path, status):
    proj = tmp_path / f"proj_{status}"
    proj.mkdir()
    upsert_todos(None, [{"id": "t1", "content": "done", "status": status}], root=proj)
    mem = Memory([session(project_path=str(proj))])
    assert make_client(memory=mem).get("/api/sessions/s1/todos").json() == {"todos": []}


def test_a_project_without_a_checklist_file_is_empty(tmp_path):
    proj = tmp_path / "bare"
    proj.mkdir()
    mem = Memory([session(project_path=str(proj))])
    assert make_client(memory=mem).get("/api/sessions/s1/todos").json() == {"todos": []}


def test_another_tabs_cached_checklist_is_never_served(tmp_path):
    """The route passes runtime=None on purpose: the in-memory cache belongs to
    whichever turn last ran, not to this session."""
    other = tmp_path / "other"
    other.mkdir()
    upsert_todos(None, [{"id": "x", "content": "someone else's work"}], root=other)
    runtime = SimpleNamespace(
        _build_todos=["leaked"],
        effective_project_path=lambda: str(other),
    )
    mem = Memory([session(project_path="")])
    client = make_client(runtime=runtime, memory=mem)
    assert client.get("/api/sessions/s1/todos").json() == {"todos": []}


# --- POST /messages: guards ---------------------------------------------------


def test_sending_without_a_runtime_is_unavailable():
    r = make_client(memory=Memory([session()])).post(
        "/api/sessions/s1/messages", json={"message": "hi"}
    )
    assert r.status_code == 503


@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
def test_an_empty_composer_submit_is_refused(text):
    rt = Runtime()
    r = make_client(runtime=rt, memory=Memory([session()])).post(
        "/api/sessions/s1/messages", json={"message": text}
    )
    assert r.status_code == 400
    assert rt.calls == []


def test_an_empty_message_is_allowed_when_files_are_attached(tmp_path):
    d = session_attachments_dir("s1", tmp_path)
    f = d / "note.txt"
    f.write_text("body", encoding="utf-8")
    rt = Runtime()
    r = make_client(runtime=rt, memory=Memory([session()])).post(
        "/api/sessions/s1/messages",
        json={"message": "", "attachments": [{"path": str(f), "name": "note.txt"}]},
    )
    assert r.status_code == 200
    assert rt.calls


def test_a_second_send_while_one_is_running_is_a_conflict():
    from remedy.core.turn_context import try_claim_session_stream

    assert try_claim_session_stream("s1")
    rt = Runtime()
    r = make_client(runtime=rt, memory=Memory([session()])).post(
        "/api/sessions/s1/messages", json={"message": "hi"}
    )
    assert r.status_code == 409
    assert "Stop the current turn" in r.json()["detail"]
    assert rt.calls == []  # the busy check runs before anything is persisted


def test_the_claim_is_released_so_the_next_send_goes_through():
    client = make_client(runtime=Runtime(), memory=Memory([session()]))
    assert client.post("/api/sessions/s1/messages", json={"message": "a"}).status_code == 200
    assert client.post("/api/sessions/s1/messages", json={"message": "b"}).status_code == 200


def test_the_claim_is_released_even_when_the_turn_blows_up():
    mem = Memory([session()])
    bad = make_client(runtime=Runtime(raises=RuntimeError("boom")), memory=mem)
    assert bad.post("/api/sessions/s1/messages", json={"message": "a"}).status_code == 500
    ok = make_client(runtime=Runtime(), memory=mem)
    assert ok.post("/api/sessions/s1/messages", json={"message": "b"}).status_code == 200


def test_sending_to_an_unknown_session_is_a_404():
    rt = Runtime()
    r = make_client(runtime=rt, memory=Memory()).post(
        "/api/sessions/nope/messages", json={"message": "hi"}
    )
    assert r.status_code == 404
    assert rt.calls == []


def test_without_a_store_no_session_check_happens_at_all():
    """Documents the coupling: the 404 above comes from memory, not the route."""
    rt = Runtime()
    r = make_client(runtime=rt, memory=None).post(
        "/api/sessions/whatever/messages", json={"message": "hi"}
    )
    assert r.status_code == 200
    assert len(rt.calls) == 1


# --- POST /messages: the happy path -------------------------------------------


def test_a_reply_comes_back_with_its_request_and_session_ids():
    r = make_client(runtime=Runtime(["hel", "lo"]), memory=Memory([session()])).post(
        "/api/sessions/s1/messages", json={"message": "hi"}
    )
    body = r.json()
    assert body["response"] == "hello"
    assert body["session_id"] == "s1"
    assert UUID(body["request_id"])  # a real request id, not a placeholder
    assert isinstance(body["processing_time_ms"], float)


def test_tool_lifecycle_tokens_are_kept_out_of_the_saved_reply():
    """@@-prefixed control tokens drive the UI; they are not the assistant's words."""
    mem = Memory([session()])
    rt = Runtime(["real ", "@@tool:start", "text"])
    r = make_client(runtime=rt, memory=mem).post(
        "/api/sessions/s1/messages", json={"message": "hi"}
    )
    assert r.json()["response"] == "real text"
    assert mem.added[-1].content == "real text"


def test_both_halves_of_the_turn_are_persisted():
    mem = Memory([session()])
    make_client(runtime=Runtime(["out"]), memory=mem).post(
        "/api/sessions/s1/messages", json={"message": " hi "}
    )
    assert [m.role for m in mem.added] == [ChatMessageRole.USER, ChatMessageRole.ASSISTANT]
    assert mem.added[0].content == "hi"  # stored trimmed
    assert mem.added[1].content == "out"


def test_a_silent_model_stores_no_assistant_row_but_still_answers():
    mem = Memory([session()])
    r = make_client(runtime=Runtime([]), memory=mem).post(
        "/api/sessions/s1/messages", json={"message": "hi"}
    )
    assert r.json()["response"] == "Processed."
    assert [m.role for m in mem.added] == [ChatMessageRole.USER]


def test_plan_mode_is_forwarded_to_the_runtime():
    rt = Runtime()
    make_client(runtime=rt, memory=Memory([session()])).post(
        "/api/sessions/s1/messages", json={"message": "hi", "plan_mode": True}
    )
    assert rt.calls[0]["plan_mode"] is True


def test_plan_mode_defaults_to_off():
    rt = Runtime()
    make_client(runtime=rt, memory=Memory([session()])).post(
        "/api/sessions/s1/messages", json={"message": "hi"}
    )
    assert rt.calls[0]["plan_mode"] is False


# --- POST /messages: the sticky provider bind ---------------------------------


def test_an_explicit_provider_and_model_pair_is_used_and_persisted():
    mem = Memory([session()])
    rt = Runtime()
    make_client(runtime=rt, memory=mem).post(
        "/api/sessions/s1/messages",
        json={"message": "hi", "provider": "xai", "model": "grok-4-latest"},
    )
    assert mem.updates == [("s1", {"llm_provider": "xai", "model": "grok-4-latest"})]
    assert rt.calls[0]["provider"] == "xai"
    assert rt.calls[0]["model"] == "grok-4-latest"


def test_a_bound_session_keeps_its_provider_when_the_request_says_nothing():
    mem = Memory([session(llm_provider="xai", model="grok-4-latest")])
    rt = Runtime()
    make_client(runtime=rt, memory=mem).post(
        "/api/sessions/s1/messages", json={"message": "hi"}
    )
    assert rt.calls[0]["provider"] == "xai"
    assert rt.calls[0]["model"] == "grok-4-latest"


def test_a_stale_model_id_from_another_host_cannot_steal_a_bound_session():
    """A lone model string (global picker / stale tab) must not repoint an
    xai session at DeepSeek — that call would go to the wrong host entirely."""
    mem = Memory([session(llm_provider="xai", model="grok-4-latest")])
    rt = Runtime()
    make_client(runtime=rt, memory=mem).post(
        "/api/sessions/s1/messages", json={"message": "hi", "model": "deepseek-chat"}
    )
    assert rt.calls[0]["provider"] == "xai"
    assert rt.calls[0]["model"] == "grok-4-latest"


def test_an_unbound_session_with_no_preference_writes_nothing():
    mem = Memory([session()])
    make_client(runtime=Runtime(), memory=mem).post(
        "/api/sessions/s1/messages", json={"message": "hi"}
    )
    assert mem.updates == []


# --- POST /messages: attachments ----------------------------------------------


def test_an_attachment_outside_the_session_folder_never_reaches_the_model(tmp_path):
    """The whole point of the jail: a forged path is an exfiltration attempt."""
    secret = tmp_path / "secret.txt"
    secret.write_text("password hunter2", encoding="utf-8")
    rt = Runtime()
    r = make_client(runtime=rt, memory=Memory([session()])).post(
        "/api/sessions/s1/messages",
        json={
            "message": "read this",
            "attachments": [{"path": str(secret), "name": "secret.txt", "mime": "text/plain"}],
        },
    )
    assert r.status_code == 200
    assert rt.calls[0]["attachments"] == []
    assert "hunter2" not in str(rt.calls[0])


def test_another_sessions_upload_is_dropped_too(tmp_path):
    other = session_attachments_dir("s2", tmp_path) / "theirs.txt"
    other.write_text("their private notes", encoding="utf-8")
    rt = Runtime()
    make_client(runtime=rt, memory=Memory([session()])).post(
        "/api/sessions/s1/messages",
        json={
            "message": "hi",
            "attachments": [{"path": str(other), "name": "theirs.txt", "mime": "text/plain"}],
        },
    )
    assert rt.calls[0]["attachments"] == []


def test_a_real_upload_is_described_and_inlined_in_the_stored_message(tmp_path):
    f = session_attachments_dir("s1", tmp_path) / "note.txt"
    f.write_text("the file body", encoding="utf-8")
    mem = Memory([session()])
    rt = Runtime()
    make_client(runtime=rt, memory=mem).post(
        "/api/sessions/s1/messages",
        json={
            "message": "look",
            "attachments": [
                {"path": str(f), "name": "note.txt", "mime": "text/plain", "size": 13}
            ],
        },
    )
    assert rt.calls[0]["attachments"][0]["name"] == "note.txt"
    stored = mem.added[0].content
    assert stored.startswith("look")
    assert "Attached files (saved for this session):" in stored
    assert "the file body" in stored


def test_the_prompt_the_model_sees_is_the_raw_text_not_the_attachment_block(tmp_path):
    """Attachment context is delivered as structured refs; duplicating it into
    the prompt string would double the cost of every attached turn."""
    f = session_attachments_dir("s1", tmp_path) / "note.txt"
    f.write_text("body", encoding="utf-8")
    rt = Runtime()
    make_client(runtime=rt, memory=Memory([session()])).post(
        "/api/sessions/s1/messages",
        json={"message": "look", "attachments": [{"path": str(f), "name": "note.txt"}]},
    )
    assert rt.calls[0]["prompt"] == "look"


def test_a_file_only_turn_still_stores_something_readable(tmp_path):
    f = session_attachments_dir("s1", tmp_path) / "pic.png"
    f.write_bytes(b"\x89PNG")
    mem = Memory([session()])
    rt = Runtime()
    make_client(runtime=rt, memory=mem).post(
        "/api/sessions/s1/messages",
        json={
            "message": "",
            "attachments": [{"path": str(f), "name": "pic.png", "mime": "image/png"}],
        },
    )
    assert mem.added[0].content
    assert rt.calls[0]["prompt"] == "(see attached files)"


# --- POST /messages: failure paths --------------------------------------------


def test_a_runtime_explosion_is_a_500_not_a_leaked_traceback():
    r = make_client(
        runtime=Runtime(raises=RuntimeError("kaboom")), memory=Memory([session()])
    ).post("/api/sessions/s1/messages", json={"message": "hi"})
    assert r.status_code == 500
    assert "kaboom" not in r.text


def test_an_http_error_raised_downstream_keeps_its_own_status():
    """A 429 from the provider layer must not be laundered into a 500."""
    r = make_client(
        runtime=Runtime(raises=HTTPException(429, "rate limited")),
        memory=Memory([session()]),
    ).post("/api/sessions/s1/messages", json={"message": "hi"})
    assert r.status_code == 429
    assert r.json()["detail"] == "rate limited"


def test_a_session_deleted_mid_turn_is_a_404():
    mem = Memory([session()])

    def kill(_i):
        mem.deleted = True

    rt = Runtime(["done"], on_token=kill)
    r = make_client(runtime=rt, memory=mem).post(
        "/api/sessions/s1/messages", json={"message": "hi"}
    )
    assert r.status_code == 404
    assert len(mem.added) == 1  # the user row landed; no orphan assistant row


def test_the_mid_stream_abort_check_actually_stops_the_stream():
    """The in-loop 404 used to be raised inside contextlib.suppress(Exception),
    which swallowed it — so every remaining token was still generated and paid
    for on behalf of a session that no longer existed, and the 404 only arrived
    from the post-stream check afterwards.
    """
    mem = Memory([session()])

    def kill(i):
        if i == 0:
            mem.deleted = True

    # 64 chars trips the `len(response_text) % 64 == 0` sampling on token one.
    rt = Runtime(["x" * 64, "y", "z"], on_token=kill)
    r = make_client(runtime=rt, memory=mem).post(
        "/api/sessions/s1/messages", json={"message": "hi"}
    )
    assert r.status_code == 404
    assert rt.consumed < 3, "the rest of the stream was generated anyway"


def test_a_session_that_vanishes_before_the_error_handler_is_reported_as_a_404():
    """A row deleted mid-turn is the caller's own doing, not a server fault.

    The 404 was raised inside a contextlib.suppress(Exception) that ate it, so
    the caller got an opaque 500 — the exact answer this branch exists to avoid.
    """
    mem = Memory([session()])

    def kill(_i):
        mem.deleted = True

    r = make_client(
        runtime=Runtime(raises=RuntimeError("fk violation"), on_token=kill), memory=mem
    ).post("/api/sessions/s1/messages", json={"message": "hi"})
    assert mem.deleted  # the branch the handler checks really was taken
    assert r.status_code == 404


# --- POST /messages: messenger mirroring --------------------------------------


def test_a_desktop_reply_is_mirrored_to_the_originating_chat():
    gw = Gateway()
    mem = Memory([session(origin_channel="telegram", external_chat_id="4242")])
    make_client(runtime=Runtime(["pong"]), gateway=gw, memory=mem).post(
        "/api/sessions/s1/messages", json={"message": "ping"}
    )
    assert gw.sent and gw.sent[0][2] == "4242"
    assert "pong" in gw.sent[0][1]


def test_a_plain_desktop_session_is_not_mirrored_anywhere():
    gw = Gateway()
    make_client(runtime=Runtime(["pong"]), gateway=gw, memory=Memory([session()])).post(
        "/api/sessions/s1/messages", json={"message": "ping"}
    )
    assert gw.sent == []


def test_a_messenger_session_with_no_chat_id_is_not_mirrored():
    gw = Gateway()
    mem = Memory([session(origin_channel="telegram", external_chat_id=None)])
    make_client(runtime=Runtime(["pong"]), gateway=gw, memory=mem).post(
        "/api/sessions/s1/messages", json={"message": "ping"}
    )
    assert gw.sent == []


def test_a_mirroring_failure_never_breaks_the_reply():
    class Angry:
        async def send_to(self, *a, **k):
            raise OSError("telegram down")

    mem = Memory([session(origin_channel="telegram", external_chat_id="1")])
    r = make_client(runtime=Runtime(["pong"]), gateway=Angry(), memory=mem).post(
        "/api/sessions/s1/messages", json={"message": "ping"}
    )
    assert r.status_code == 200
    assert r.json()["response"] == "pong"
