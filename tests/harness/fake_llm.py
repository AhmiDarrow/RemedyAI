"""A scriptable stand-in for a streaming LLM provider.

If this file is wrong, every test built on it is wrong in the same direction:
the ReAct loop gets fed chunk shapes no real provider ever sends, and tests go
green while the shipped loop breaks (or go red while it is fine). So nothing
here is invented -- the wire shapes are copied from the code that *parses*
them: ``remedy.core.react_stream.apply_openai_sse_chunk`` for SSE deltas,
``apply_openai_completion_message`` / ``OpenAIProvider.extract_response`` for
non-stream JSON, and ``remedy.core.react_loop.loop`` for how the request is
posted.

Nothing here opens a socket, spawns a process, or touches ``~/.remedy``.

Scripting turns
---------------
One :class:`Turn` per model round. ``FakeLLM`` stands in for the
``aiohttp.ClientSession`` the loop builds, serves the turns in order, and
records every request::

    fake = FakeLLM([
        tool_turn("add", {"a": 2, "b": 3}),
        text_turn("The sum is 5."),
    ])
    with fake.patch(force_tools=True):
        text = await runtime._call_llm("add 2 and 3")

    assert fake.request_count == 2
    assert fake.requests[0].tool_names == ["add"]

Available turn kinds: :func:`text_turn`, :func:`tool_turn`, :func:`tools_turn`
(text + several tool calls in one turn), :func:`error_turn` (non-200),
:func:`empty_turn` (a completion with no content at all),
:func:`truncated_turn` (a stream that stops mid-token -- no ``[DONE]``, no
finish_reason), :func:`exception_turn` (the POST itself blows up) and
:func:`raw_turn` (hand-written bytes, for malformed-provider tests).

A turn renders itself as SSE **or** as a single JSON completion depending on
what the request asked for (``body["stream"]``). The loop forces
``stream=False`` for local/RMB bindings, so a fake that only spoke SSE would
quietly feed the wrong parser and prove nothing.

Scripting tools
---------------
:class:`FakeToolRegistry` *is* a real ``ToolRegistry`` with scripted handlers,
so ``openai_tools_payload`` and ``runtime.call_tool`` behave exactly as in
production while the results are yours::

    reg = FakeToolRegistry()
    reg.add("add", results=[{"sum": 5}])
    reg.add("flaky", results=[ToolFailure("disk on fire")])
    reg.install(runtime)
    ...
    assert reg.calls_to("add") == [RecordedToolCall("add", {"a": 2, "b": 3})]

Asserting on the request
------------------------
``fake.requests`` holds :class:`RecordedRequest` objects -- url, headers and
the full JSON body, with ``.model``, ``.messages``, ``.tools``,
``.tool_names``, ``.stream``, ``.tool_choice``, ``.texts_for_role()`` and
``.tool_result_texts`` for the assertions you actually want to write.

Consuming a response without the loop
-------------------------------------
For narrow tests, drive the real parser directly::

    resp = fake.session.post(url, json={"stream": True})
    state = StreamRoundState()
    async for token, is_user_text in consume_llm_http_response(
        resp, round_state=state, collected={}, adapter=fake_adapter(),
        bind=fake_binding(), body={"stream": True},
        use_openai_sse=True, stream_live=True,
    ):
        ...
"""

from __future__ import annotations

import copy
import json as jsonlib
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from remedy.core.llm_binding import LlmBinding
from remedy.skills.tool_registry import ToolRegistry

#: Where the ReAct loop looks up its HTTP session. Patching here (rather than
#: ``aiohttp.ClientSession`` globally) is what the rest of the suite does.
LOOP_CLIENT_SESSION = "remedy.core.react_loop.loop.aiohttp.ClientSession"
#: The tools-on/tools-off gate the loop consults before it arms any tool.
#: ``resolve_tools`` uses the ``_message_wants_tools`` alias. Do **not** patch
#: ``message_wants_tools`` itself — that also drives unfinished-work blocking
#: and would keep a text-only round looping until the step wall.
AGENT_WANTS_TOOLS = "remedy.core.agent._message_wants_tools"
POLICY_WANTS_TOOLS = "remedy.core.react_policy._message_wants_tools"

SSE_CONTENT_TYPE = "text/event-stream"
JSON_CONTENT_TYPE = "application/json"

DEFAULT_MODEL = "fake-model"
DEFAULT_BASE_URL = "http://llm.invalid/v1"


class ScriptExhaustedError(RuntimeError):
    """The loop asked for one more completion than the test scripted.

    Raised instead of hanging or silently repeating a turn, so a loop that
    suddenly takes an extra step shows up as a loud failure naming the count.
    """


# --------------------------------------------------------------------------
# binding / adapter -- the real ones, so parsing is the real parsing
# --------------------------------------------------------------------------


def fake_binding(
    *,
    provider: str = "openai",
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = "sk-fake",
) -> LlmBinding:
    """A real ``LlmBinding`` pointed at a host that does not exist.

    The base_url is deliberately unroutable: if a test ever escapes the fake
    session, it fails to connect instead of reaching a real provider.
    """
    return LlmBinding(
        provider=provider, model=model, base_url=base_url, api_key=api_key
    )


def fake_adapter(**kwargs: Any) -> Any:
    """The real provider adapter for :func:`fake_binding`."""
    return fake_binding(**kwargs).adapter()


# --------------------------------------------------------------------------
# turns
# --------------------------------------------------------------------------


def tool_call(
    name: str,
    arguments: dict[str, Any] | str | None = None,
    *,
    call_id: str | None = None,
    index: int = 0,
) -> dict[str, Any]:
    """One OpenAI-style tool call. Dict arguments are serialized like a provider."""
    if arguments is None:
        args = "{}"
    elif isinstance(arguments, str):
        args = arguments
    else:
        args = jsonlib.dumps(arguments)
    return {
        "index": index,
        "id": call_id or f"call_{index}",
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


@dataclass
class Turn:
    """One scripted provider response.

    Prefer the factory functions below; construct directly only for shapes
    they do not cover.
    """

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    reasoning: str = ""
    #: ``None`` means "derive it" (tool_calls -> ``tool_calls``, else
    #: ``stop``); a truncated turn never gets one, because the stream died.
    finish_reason: str | None = None
    status: int = 200
    error_body: str = ""
    #: Raised out of ``post()`` -- a connection that never produced a response.
    raises: BaseException | None = None
    #: 0 = one delta for the whole payload; >0 = split into deltas that size.
    chunk_size: int = 0
    #: Cut the stream after this many characters of ``text``, mid-JSON.
    truncate_at: int | None = None
    usage: dict[str, Any] | None = None
    #: Escape hatch: exact bytes to serve, bypassing rendering entirely.
    raw_lines: list[bytes] | None = None
    content_type: str | None = None
    model: str = DEFAULT_MODEL
    #: Extra response headers (e.g. ``Retry-After`` on a 429).
    headers: dict[str, str] | None = None

    @property
    def is_error(self) -> bool:
        return self.status != 200

    @property
    def is_truncated(self) -> bool:
        return self.truncate_at is not None

    def effective_finish_reason(self) -> str | None:
        if self.finish_reason is not None:
            return self.finish_reason
        if self.is_truncated:
            return None
        if self.tool_calls:
            return "tool_calls"
        return "stop"

    # -- rendering ---------------------------------------------------------

    def render_sse(self) -> list[bytes]:
        """The turn as SSE lines, the way an OpenAI-compatible host streams it."""
        if self.raw_lines is not None:
            return list(self.raw_lines)
        lines: list[bytes] = []

        def emit(delta: dict[str, Any], finish: str | None = None) -> None:
            chunk = {
                "id": "chatcmpl-fake",
                "object": "chat.completion.chunk",
                "model": self.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            lines.append(b"data: " + jsonlib.dumps(chunk).encode() + b"\n\n")

        for part in _split(self.reasoning, self.chunk_size):
            emit({"reasoning_content": part})

        body_text = self.text[: self.truncate_at] if self.is_truncated else self.text
        for part in _split(body_text, self.chunk_size):
            emit({"content": part})

        if self.is_truncated:
            # A real cut stream leaves a half-written data line and never sends
            # [DONE]. parse_sse_data_line drops it; the round just ends.
            lines.append(b'data: {"choices":[{"index":0,"delta":{"content":"')
            return lines

        for tc in self.tool_calls:
            for delta_tc in _split_tool_call(tc, self.chunk_size):
                emit({"tool_calls": [delta_tc]})

        final: dict[str, Any] = {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "model": self.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": self.effective_finish_reason(),
                }
            ],
        }
        if self.usage:
            final["usage"] = self.usage
        lines.append(b"data: " + jsonlib.dumps(final).encode() + b"\n\n")
        lines.append(b"data: [DONE]\n\n")
        return lines

    def render_json(self) -> dict[str, Any]:
        """The turn as a single non-stream completion (``stream=False`` rounds)."""
        text = self.text[: self.truncate_at] if self.is_truncated else self.text
        message: dict[str, Any] = {"role": "assistant", "content": text or None}
        if self.reasoning:
            message["reasoning_content"] = self.reasoning
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": tc.get("type", "function"),
                    "function": dict(tc["function"]),
                }
                for tc in self.tool_calls
            ]
        finish = self.effective_finish_reason()
        if self.is_truncated:
            # No stream to cut: the honest non-stream equivalent is a length stop.
            finish = "length"
        data: dict[str, Any] = {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": self.model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        }
        if self.usage:
            data["usage"] = self.usage
        return data

    def build_response(self, *, stream: bool) -> FakeResponse:
        if self.is_error:
            return FakeResponse(
                status=self.status,
                text_body=self.error_body,
                content_type=self.content_type or JSON_CONTENT_TYPE,
                headers=self.headers,
            )
        if stream:
            return FakeResponse(
                status=self.status,
                lines=self.render_sse(),
                content_type=self.content_type or SSE_CONTENT_TYPE,
            )
        return FakeResponse(
            status=self.status,
            payload=self.render_json(),
            content_type=self.content_type or JSON_CONTENT_TYPE,
        )


def _split(text: str, chunk_size: int) -> list[str]:
    if not text:
        return []
    if chunk_size and chunk_size > 0:
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    return [text]


def _split_tool_call(tc: dict[str, Any], chunk_size: int) -> list[dict[str, Any]]:
    """Split one tool call into streaming deltas the way providers do.

    The first delta carries id + name, later deltas carry only argument
    fragments -- exactly what ``accumulate_tool_call_delta`` reassembles.
    """
    idx = tc.get("index", 0)
    fn = tc.get("function") or {}
    args = str(fn.get("arguments") or "")
    parts = _split(args, chunk_size) or [""]
    deltas = [
        {
            "index": idx,
            "id": tc.get("id"),
            "type": tc.get("type", "function"),
            "function": {"name": fn.get("name"), "arguments": parts[0]},
        }
    ]
    for part in parts[1:]:
        deltas.append({"index": idx, "function": {"arguments": part}})
    return deltas


def text_turn(
    text: str,
    *,
    chunk_size: int = 0,
    finish_reason: str = "stop",
    reasoning: str = "",
    usage: dict[str, Any] | None = None,
) -> Turn:
    """A plain prose answer."""
    return Turn(
        text=text,
        chunk_size=chunk_size,
        finish_reason=finish_reason,
        reasoning=reasoning,
        usage=usage,
    )


def tool_turn(
    name: str,
    arguments: dict[str, Any] | str | None = None,
    *,
    text: str = "",
    call_id: str | None = None,
    chunk_size: int = 0,
) -> Turn:
    """A turn that calls exactly one tool (optionally with preamble text)."""
    return tools_turn(
        [(name, arguments)],
        text=text,
        chunk_size=chunk_size,
        call_ids=[call_id] if call_id else None,
    )


def tools_turn(
    calls: Sequence[tuple[str, Any] | dict[str, Any]],
    *,
    text: str = "",
    chunk_size: int = 0,
    call_ids: Sequence[str] | None = None,
) -> Turn:
    """A turn with one or more tool calls -- the parallel-batch case."""
    built: list[dict[str, Any]] = []
    for i, call in enumerate(calls):
        cid = call_ids[i] if call_ids and i < len(call_ids) else None
        if isinstance(call, dict) and "function" in call:
            entry = dict(call)
            entry.setdefault("index", i)
            entry.setdefault("id", cid or f"call_{i}")
            entry.setdefault("type", "function")
            built.append(entry)
        else:
            name, args = call
            built.append(tool_call(name, args, call_id=cid, index=i))
    return Turn(text=text, tool_calls=built, chunk_size=chunk_size)


def error_turn(
    status: int = 500,
    body: str = "upstream exploded",
    *,
    headers: dict[str, str] | None = None,
) -> Turn:
    """A non-200 provider response. The loop must surface it, never swallow it."""
    return Turn(status=status, error_body=body, headers=headers)


def empty_turn(*, finish_reason: str = "stop") -> Turn:
    """A completion with no content, no reasoning and no tool calls."""
    return Turn(finish_reason=finish_reason)


def truncated_turn(text: str, *, keep: int | None = None, chunk_size: int = 4) -> Turn:
    """A stream that dies mid-token: partial text, no finish_reason, no [DONE]."""
    if keep is None:
        keep = max(1, len(text) // 2)
    return Turn(text=text, truncate_at=keep, chunk_size=chunk_size)


def exception_turn(exc: BaseException) -> Turn:
    """The POST itself fails (connection reset, DNS, timeout)."""
    return Turn(raises=exc)


def raw_turn(
    lines: Sequence[bytes],
    *,
    status: int = 200,
    content_type: str = SSE_CONTENT_TYPE,
) -> Turn:
    """Hand-written bytes, for malformed / provider-specific shapes."""
    return Turn(raw_lines=list(lines), status=status, content_type=content_type)


# --------------------------------------------------------------------------
# HTTP doubles
# --------------------------------------------------------------------------


class _LineStream:
    """Stands in for ``aiohttp`` ``resp.content`` -- an async iterator of lines."""

    def __init__(self, lines: Sequence[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self) -> _LineStream:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class FakeResponse:
    """One provider response, usable as ``async with session.post(...) as resp``."""

    def __init__(
        self,
        *,
        status: int = 200,
        lines: Sequence[bytes] | None = None,
        payload: dict[str, Any] | None = None,
        text_body: str = "",
        content_type: str = SSE_CONTENT_TYPE,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.content = _LineStream(lines or [])
        self._payload = payload if payload is not None else {}
        self._text = text_body
        self.closed = False

    async def json(self) -> dict[str, Any]:
        return self._payload

    async def text(self) -> str:
        if self._text:
            return self._text
        return jsonlib.dumps(self._payload)

    def close(self) -> None:
        self.closed = True

    def release(self) -> None:
        self.closed = True

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


@dataclass(frozen=True)
class RecordedRequest:
    """What the loop actually asked the provider for."""

    url: str
    headers: dict[str, str]
    body: dict[str, Any]

    @property
    def model(self) -> str | None:
        return self.body.get("model")

    @property
    def messages(self) -> list[dict[str, Any]]:
        msgs = self.body.get("messages")
        return msgs if isinstance(msgs, list) else []

    @property
    def tools(self) -> list[dict[str, Any]]:
        tools = self.body.get("tools")
        return tools if isinstance(tools, list) else []

    @property
    def tool_names(self) -> list[str]:
        names: list[str] = []
        for t in self.tools:
            fn = t.get("function") if isinstance(t, dict) else None
            if isinstance(fn, dict) and fn.get("name"):
                names.append(str(fn["name"]))
        return names

    @property
    def stream(self) -> bool | None:
        return self.body.get("stream")

    @property
    def tool_choice(self) -> Any:
        return self.body.get("tool_choice")

    def texts_for_role(self, role: str) -> list[str]:
        """Message contents for one role, skipping non-string (multimodal) parts."""
        return [
            m["content"]
            for m in self.messages
            if m.get("role") == role and isinstance(m.get("content"), str)
        ]

    @property
    def tool_result_texts(self) -> list[str]:
        """Tool results fed back to the model on this request."""
        return self.texts_for_role("tool")

    def mentions(self, needle: str) -> bool:
        """True when *needle* appears anywhere in the serialized request body."""
        return needle in jsonlib.dumps(self.body, default=str)


class FakeSession:
    """Stands in for ``aiohttp.ClientSession`` inside the ReAct loop."""

    def __init__(
        self,
        turns: Sequence[Turn] = (),
        *,
        when_exhausted: Turn | None = None,
    ) -> None:
        self.turns: list[Turn] = list(turns)
        self.when_exhausted = when_exhausted
        self.requests: list[RecordedRequest] = []
        self.responses: list[FakeResponse] = []
        self.served: list[Turn] = []
        self.closed = False

    # ``json`` is aiohttp's own keyword for the body; shadowing it is deliberate.
    def post(
        self,
        url: str = "",
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> FakeResponse:
        body = copy.deepcopy(json) if isinstance(json, dict) else {}
        self.requests.append(
            RecordedRequest(url=str(url), headers=dict(headers or {}), body=body)
        )
        turn = self._next_turn()
        self.served.append(turn)
        if turn.raises is not None:
            raise turn.raises
        # The loop asks for SSE or a single JSON blob per request; serve what
        # was asked for, not what the script author happened to imagine.
        stream = body.get("stream")
        resp = turn.build_response(stream=stream is not False)
        self.responses.append(resp)
        return resp

    def _next_turn(self) -> Turn:
        if self.turns:
            return self.turns.pop(0)
        if self.when_exhausted is not None:
            return self.when_exhausted
        raise ScriptExhaustedError(
            f"the loop asked for completion #{len(self.requests)} but only "
            f"{len(self.served)} turns were scripted; add a Turn or pass "
            f"when_exhausted="
        )

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakeLLM:
    """A scripted provider plus the recorder of what was asked of it."""

    def __init__(
        self,
        turns: Sequence[Turn] = (),
        *,
        when_exhausted: Turn | None = None,
    ) -> None:
        self.session = FakeSession(turns, when_exhausted=when_exhausted)

    # -- scripting ---------------------------------------------------------

    def extend(self, turns: Sequence[Turn]) -> FakeLLM:
        self.session.turns.extend(turns)
        return self

    @property
    def turns_remaining(self) -> int:
        return len(self.session.turns)

    # -- recording ---------------------------------------------------------

    @property
    def requests(self) -> list[RecordedRequest]:
        return self.session.requests

    @property
    def request_count(self) -> int:
        return len(self.session.requests)

    @property
    def last_request(self) -> RecordedRequest:
        if not self.session.requests:
            raise AssertionError("the loop never called the LLM")
        return self.session.requests[-1]

    # -- installation ------------------------------------------------------

    @contextmanager
    def patch(self, *, force_tools: bool = False) -> Iterator[FakeLLM]:
        """Make the ReAct loop use this fake for the duration of the block.

        *force_tools* also pins ``_message_wants_tools`` on, so short prompts
        still arm tools -- otherwise a one-line message silently gets a
        tool-free request and a scripted tool turn can never be exercised.
        """
        with ExitStack() as stack:
            stack.enter_context(patch(LOOP_CLIENT_SESSION, return_value=self.session))
            if force_tools:
                stack.enter_context(patch(AGENT_WANTS_TOOLS, return_value=True))
                stack.enter_context(patch(POLICY_WANTS_TOOLS, return_value=True))
            yield self


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------


class ToolFailure:
    """Marker for a scripted result that must come back as a failed tool.

    ``runtime.call_tool`` maps a ``ValueError`` from a handler onto
    ``ToolResult(success=False)``, so that is what this raises.
    """

    def __init__(self, error: str) -> None:
        self.error = error

    def __repr__(self) -> str:
        return f"ToolFailure({self.error!r})"


@dataclass(frozen=True)
class RecordedToolCall:
    name: str
    arguments: dict[str, Any]


_NO_RESULT = object()


class FakeToolRegistry(ToolRegistry):
    """A real ``ToolRegistry`` whose handlers return scripted results.

    Subclassing the real thing (rather than faking its surface) keeps
    ``openai_tools_payload``, the schema-generation cache and
    ``runtime.call_tool``'s error mapping exactly as they are in production.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[RecordedToolCall] = []

    def add(
        self,
        name: str,
        *,
        description: str = "",
        parameters: dict[str, Any] | None = None,
        results: Sequence[Any] | None = None,
        result: Any = _NO_RESULT,
    ) -> FakeToolRegistry:
        """Register a scripted tool.

        ``results`` is consumed one per call; the last entry repeats once the
        list runs out, so a tool the loop retries does not fall off a cliff.
        An entry may be a plain value, a :class:`ToolFailure` (comes back as
        ``success=False``) or an exception instance (raised as-is).
        """
        if result is not _NO_RESULT:
            scripted: list[Any] = [result]
        elif results is not None:
            scripted = list(results)
        else:
            scripted = [f"{name} ok"]
        if not scripted:
            raise ValueError(f"tool {name!r} needs at least one scripted result")
        pending = list(scripted)

        async def handler(**kwargs: Any) -> Any:
            self.calls.append(RecordedToolCall(name, dict(kwargs)))
            value = pending.pop(0) if len(pending) > 1 else pending[0]
            if isinstance(value, ToolFailure):
                raise ValueError(value.error)
            if isinstance(value, BaseException):
                raise value
            return value

        self.register_builtin_handler(
            name,
            description or f"scripted {name}",
            handler,
            parameters=parameters or {"type": "object", "properties": {}},
        )
        return self

    def calls_to(self, name: str) -> list[RecordedToolCall]:
        return [c for c in self.calls if c.name == name]

    def install(self, runtime: Any) -> FakeToolRegistry:
        """Replace *runtime*'s registry with this one (deterministic tools payload)."""
        runtime.tool_registry = self
        return self
