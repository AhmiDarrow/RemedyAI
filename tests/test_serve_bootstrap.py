"""Wiring messenger channels onto the gateway when `remedy serve` starts.

The handler registered here is what turns an inbound Telegram message into a
reply that actually arrives. Two things it has to get right:

* A messenger reply is *sent back over the channel*, not merely yielded. The
  API surface consumes the yielded stream; a phone does not. Dropping the
  send_to leaves the owner staring at a chat where Remedy never answered
  while the server log shows a perfectly good response.
* A channel failing to register must not stop `serve` — the other channels,
  and the API, still have to come up.

No network, no real gateway.
"""

from __future__ import annotations

import pytest

from remedy.gateway.serve_bootstrap import attach_messengers_to_gateway


class Event:
    def __init__(self, channel="telegram", payload=None, source_id="u1") -> None:
        self.channel = type("Kind", (), {"value": channel})()
        self.payload = payload if payload is not None else {"chat_id": "111"}
        self.source_id = source_id


class FakeGateway:
    def __init__(self, adapter=None) -> None:
        self.handler = None
        self.sent: list[tuple[str, str | None]] = []
        self._adapter = adapter

    def register_handler(self, fn) -> None:
        self.handler = fn

    def get_channel(self, _kind):
        return self._adapter

    async def send_to(self, _channel, part, target=None) -> None:
        self.sent.append((part, target))


class TypingAdapter:
    def __init__(self) -> None:
        self.typed: list[str] = []

    async def send_typing(self, chat_id) -> None:
        self.typed.append(str(chat_id))


class Runtime:
    def __init__(self, chunks=("direct ",)) -> None:
        self._chunks = chunks
        self.handled: list = []

    async def handle_event(self, event):
        self.handled.append(event)
        for c in self._chunks:
            yield c


@pytest.fixture()
def wire(monkeypatch):
    """Attach with the messenger machinery stubbed; returns a builder."""

    def build(*, reply=("hello ", "there"), is_messenger=True, adapter=None,
              register=lambda gw, cfg: ["telegram"], runtime=None, split=None):
        async def _handle(_rt, _ev):
            for chunk in reply:
                yield chunk

        monkeypatch.setattr(
            "remedy.gateway.session_bridge.handle_messenger_event", _handle
        )
        # Patched before attach: the handler imports these names into its
        # closure at wiring time, so a later monkeypatch would not be seen.
        monkeypatch.setattr(
            "remedy.gateway.session_bridge.outbound_chunks",
            split or (lambda full, ch: [full]),
        )
        monkeypatch.setattr(
            "remedy.gateway.messengers.is_messenger_channel", lambda ch: is_messenger
        )
        monkeypatch.setattr(
            "remedy.gateway.channel_registry.register_messenger_channels", register
        )
        monkeypatch.setattr("remedy.interfaces.api_support.load_config", lambda: {})
        gw = FakeGateway(adapter=adapter)
        rt = runtime or Runtime()
        registered = attach_messengers_to_gateway(rt, gw)
        return {"gateway": gw, "runtime": rt, "registered": registered}

    return build


async def drain(gw, event):
    return [c async for c in gw.handler(event)]


# --- registration ------------------------------------------------------------


def test_a_handler_is_registered(wire):
    assert wire()["gateway"].handler is not None


def test_the_registered_channels_are_returned(wire):
    assert wire(register=lambda gw, cfg: ["telegram", "slack"])["registered"] == [
        "telegram",
        "slack",
    ]


def test_no_channels_configured_is_not_an_error(wire):
    assert wire(register=lambda gw, cfg: [])["registered"] == []


def test_a_channel_that_fails_to_register_does_not_stop_serve(wire, caplog):
    """The API and the other channels still have to come up."""

    def boom(gw, cfg):
        raise RuntimeError("bad telegram token")

    with caplog.at_level("ERROR"):
        out = wire(register=boom)
    assert out["registered"] == []
    assert out["gateway"].handler is not None
    assert any("register" in r.message.lower() for r in caplog.records)


# --- messenger replies -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_messenger_reply_is_sent_back_over_the_channel(wire):
    """Yielding is for the API; the phone only sees what send_to delivers."""
    out = wire(reply=("hello ", "there"))
    await drain(out["gateway"], Event())
    assert out["gateway"].sent == [("hello there", "111")]


@pytest.mark.asyncio
async def test_the_reply_is_also_streamed_to_the_caller(wire):
    out = wire(reply=("a", "b"))
    assert await drain(out["gateway"], Event()) == ["a", "b"]


@pytest.mark.asyncio
async def test_an_empty_reply_sends_nothing(wire):
    """Better silence than an empty bubble on the owner's phone."""
    out = wire(reply=("", "   "))
    await drain(out["gateway"], Event())
    assert out["gateway"].sent == []


@pytest.mark.asyncio
async def test_a_reply_of_only_nones_sends_nothing(wire):
    out = wire(reply=(None, None))
    await drain(out["gateway"], Event())
    assert out["gateway"].sent == []


@pytest.mark.asyncio
async def test_a_long_reply_is_split_the_way_the_channel_needs(wire):
    out = wire(
        reply=("part one part two",),
        split=lambda full, ch: ["part one", "part two"],
    )
    await drain(out["gateway"], Event())
    assert [p for p, _ in out["gateway"].sent] == ["part one", "part two"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "source", "expected"),
    [
        ({"chat_id": "111"}, "u1", "111"),
        ({"channel_id": "222"}, "u1", "222"),
        ({}, "u1", "u1"),
        ({}, None, None),
    ],
)
async def test_the_reply_goes_where_the_message_came_from(
    wire, payload, source, expected
):
    out = wire(reply=("hi",))
    await drain(out["gateway"], Event(payload=payload, source_id=source))
    assert out["gateway"].sent[0][1] == expected


@pytest.mark.asyncio
async def test_a_messenger_event_does_not_also_go_through_the_plain_runtime(wire):
    out = wire(reply=("hi",))
    await drain(out["gateway"], Event())
    assert out["runtime"].handled == []


# --- typing indicator --------------------------------------------------------


@pytest.mark.asyncio
async def test_typing_is_shown_while_the_model_works(wire):
    adapter = TypingAdapter()
    out = wire(reply=("hi",), adapter=adapter)
    await drain(out["gateway"], Event())
    assert adapter.typed == ["111"]


@pytest.mark.asyncio
async def test_a_channel_without_typing_support_is_fine(wire):
    out = wire(reply=("hi",), adapter=object())
    await drain(out["gateway"], Event())
    assert out["gateway"].sent == [("hi", "111")]


@pytest.mark.asyncio
async def test_a_failing_typing_indicator_never_costs_the_reply(wire):
    """Cosmetic polish must not be able to swallow the answer."""

    class Broken:
        async def send_typing(self, _chat_id):
            raise RuntimeError("telegram rate limit")

    out = wire(reply=("hi",), adapter=Broken())
    await drain(out["gateway"], Event())
    assert out["gateway"].sent == [("hi", "111")]


# --- non-messenger events ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_non_messenger_event_goes_to_the_runtime(wire):
    out = wire(is_messenger=False, runtime=Runtime(chunks=("from runtime",)))
    assert await drain(out["gateway"], Event(channel="web")) == ["from runtime"]
    assert len(out["runtime"].handled) == 1


@pytest.mark.asyncio
async def test_a_non_messenger_event_is_not_sent_back_over_a_channel(wire):
    out = wire(is_messenger=False)
    await drain(out["gateway"], Event(channel="web"))
    assert out["gateway"].sent == []


@pytest.mark.asyncio
async def test_none_chunks_from_the_runtime_are_dropped(wire):
    out = wire(is_messenger=False, runtime=Runtime(chunks=(None, "real", None)))
    assert await drain(out["gateway"], Event(channel="web")) == ["real"]
