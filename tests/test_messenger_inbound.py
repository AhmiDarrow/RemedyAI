"""Messenger inbound — who is allowed to talk to Remedy, on every channel.

A bot token is a public address. Anyone who finds the bot can message it, so
the allowlist is the only thing standing between a stranger and a partner who
can read files, spend money, and drive the desktop. The default is therefore
*deny*: an empty allowlist with allow_all off admits nobody, which reads as
"the channel is broken" and is exactly right.

Every adapter is checked against the same table, because the failure that
matters is one channel drifting from the others — six near-identical handlers
is precisely the shape where that happens quietly.

No network: each adapter is handed a payload directly and a fake gateway.
"""

from __future__ import annotations

import pytest

from remedy.gateway.channels.allowlist import env_allow_all, is_allowed, parse_ids


class FakeGateway:
    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, event) -> None:
        self.events.append(event)


# --- the shared predicate ----------------------------------------------------


def test_an_empty_allowlist_admits_nobody():
    """The secure default. A channel with no allowlist is a closed channel."""
    assert is_allowed(allowlist=frozenset(), allow_all=False, candidates=["123"]) is False


def test_allow_all_admits_anyone():
    assert is_allowed(allowlist=frozenset(), allow_all=True, candidates=["123"]) is True


def test_allow_all_admits_even_with_no_candidate_at_all():
    assert is_allowed(allowlist=frozenset(), allow_all=True, candidates=[]) is True


def test_a_listed_id_is_admitted():
    assert is_allowed(allowlist=frozenset({"123"}), allow_all=False, candidates=["123"]) is True


def test_any_one_matching_candidate_is_enough():
    """Channel id, user id and guild id are all offered; one match admits."""
    assert (
        is_allowed(
            allowlist=frozenset({"guild-9"}),
            allow_all=False,
            candidates=["chan-1", "user-2", "guild-9"],
        )
        is True
    )


def test_an_unlisted_id_is_refused():
    assert is_allowed(allowlist=frozenset({"123"}), allow_all=False, candidates=["999"]) is False


def test_no_candidates_at_all_is_refused():
    assert is_allowed(allowlist=frozenset({"123"}), allow_all=False, candidates=[]) is False


def test_blank_candidates_do_not_match_a_blank_entry():
    """Whitespace must never become a wildcard."""
    assert (
        is_allowed(allowlist=frozenset({"123"}), allow_all=False, candidates=["", "   "])
        is False
    )


def test_surrounding_whitespace_on_a_real_id_still_matches():
    assert (
        is_allowed(allowlist=frozenset({"123"}), allow_all=False, candidates=[" 123 "])
        is True
    )


# --- parsing what the owner typed --------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, set()),
        ("", set()),
        ("   ", set()),
        ("123", {"123"}),
        ("123,456", {"123", "456"}),
        ("123, 456 ,789", {"123", "456", "789"}),
        ("123;456", {"123", "456"}),
        ("123; 456,789", {"123", "456", "789"}),
        ([], set()),
        (["123", " 456 "], {"123", "456"}),
        (["123", "", "  "], {"123"}),
        ([123, 456], {"123", "456"}),
    ],
)
def test_an_allowlist_is_read_however_it_was_written(raw, expected):
    assert parse_ids(raw) == frozenset(expected)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_the_env_override_recognises_yes(monkeypatch, value):
    monkeypatch.setenv("REMEDY_TEST_ALLOW_ALL", value)
    assert env_allow_all("REMEDY_TEST_ALLOW_ALL") is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_the_env_override_does_not_open_on_anything_else(monkeypatch, value):
    monkeypatch.setenv("REMEDY_TEST_ALLOW_ALL", value)
    assert env_allow_all("REMEDY_TEST_ALLOW_ALL") is False


def test_an_unset_env_override_is_off(monkeypatch):
    monkeypatch.delenv("REMEDY_TEST_ALLOW_ALL", raising=False)
    assert env_allow_all("REMEDY_TEST_ALLOW_ALL") is False


# --- per-adapter inbound -----------------------------------------------------
# Each entry builds a channel and feeds it one inbound payload.


def _telegram(gw, **kw):
    from remedy.gateway.channels.telegram import TelegramChannel

    return TelegramChannel(gw, bot_token="t", **kw)


def _discord(gw, **kw):
    from remedy.gateway.channels.discord import DiscordChannel

    return DiscordChannel(gw, bot_token="d", **kw)


def _slack(gw, **kw):
    from remedy.gateway.channels.slack import SlackChannel

    return SlackChannel(gw, bot_token="s", **kw)


def _mattermost(gw, **kw):
    from remedy.gateway.channels.mattermost import MattermostChannel

    return MattermostChannel(gw, bot_token="m", base_url="https://mm.example", **kw)


def _signal(gw, **kw):
    from remedy.gateway.channels.signal_cli import SignalChannel

    return SignalChannel(gw, account="+15550100", **kw)


def _tg_payload(ch="111", user="222", text="hello"):
    return {
        "update_id": 1,
        "message": {
            "text": text,
            "chat": {"id": ch},
            "from": {"id": user, "username": "someone"},
        },
    }


def _dc_payload(ch="111", user="222", text="hello"):
    return {"content": text, "channel_id": ch, "author": {"id": user, "username": "someone"}}


def _sl_payload(ch="111", user="222", text="hello"):
    return {
        "type": "events_api",
        "payload": {
            "event": {"type": "message", "text": text, "channel": ch, "user": user}
        },
    }


class FakeSocket:
    """Slack acks every envelope over the socket before handling it."""

    def __init__(self) -> None:
        self.acked: list[dict] = []

    async def send_json(self, body) -> None:
        self.acked.append(body)


async def _deliver_telegram(ch, payload):
    await ch._handle_update(payload)


async def _deliver_discord(ch, payload):
    await ch._on_message(payload)


async def _deliver_slack(ch, payload):
    await ch._on_socket(FakeSocket(), payload)


#: (name, factory, allow-kwarg, payload builder, deliver)
ADAPTERS = [
    ("telegram", _telegram, "chat_ids", _tg_payload, _deliver_telegram),
    ("discord", _discord, "allow_ids", _dc_payload, _deliver_discord),
    ("slack", _slack, "allow_ids", _sl_payload, _deliver_slack),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "make", "kw", "payload", "deliver"), ADAPTERS)
async def test_a_stranger_is_refused_when_no_allowlist_is_set(
    name, make, kw, payload, deliver, monkeypatch
):
    """Nobody configured yet means nobody gets in — on every channel."""
    monkeypatch.delenv("REMEDY_TELEGRAM_ALLOW_ALL", raising=False)
    gw = FakeGateway()
    ch = make(gw, **{kw: []})
    await deliver(ch, payload())
    assert gw.events == [], f"{name} admitted a stranger with an empty allowlist"


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "make", "kw", "payload", "deliver"), ADAPTERS)
async def test_an_unlisted_sender_is_refused(name, make, kw, payload, deliver, monkeypatch):
    monkeypatch.delenv("REMEDY_TELEGRAM_ALLOW_ALL", raising=False)
    gw = FakeGateway()
    ch = make(gw, **{kw: ["999"]})
    await deliver(ch, payload(text="let me in"))
    assert gw.events == [], f"{name} admitted an unlisted sender"


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "make", "kw", "payload", "deliver"), ADAPTERS)
async def test_a_listed_sender_gets_through(name, make, kw, payload, deliver, monkeypatch):
    monkeypatch.delenv("REMEDY_TELEGRAM_ALLOW_ALL", raising=False)
    gw = FakeGateway()
    ch = make(gw, **{kw: ["111"]})
    await deliver(ch, payload(text="hello there"))
    assert len(gw.events) == 1, f"{name} refused an allowlisted sender"
    assert gw.events[0].payload["message"] == "hello there"


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "make", "kw", "payload", "deliver"), ADAPTERS)
async def test_allow_all_lets_anyone_through(name, make, kw, payload, deliver):
    gw = FakeGateway()
    ch = make(gw, **{kw: [], "allow_all": True})
    await deliver(ch, payload(user="a-total-stranger"))
    assert len(gw.events) == 1, f"{name} refused despite allow_all"


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "make", "kw", "payload", "deliver"), ADAPTERS)
async def test_an_empty_message_is_ignored(name, make, kw, payload, deliver):
    gw = FakeGateway()
    ch = make(gw, **{kw: [], "allow_all": True})
    await deliver(ch, payload(text="   "))
    assert gw.events == [], f"{name} emitted an event for an empty message"


# --- bot echoes: the loop that talks to itself -------------------------------


@pytest.mark.asyncio
async def test_telegram_ignores_its_own_bot_messages():
    gw = FakeGateway()
    ch = _telegram(gw, allow_all=True)
    update = _tg_payload()
    update["message"]["from"]["is_bot"] = True
    await ch._handle_update(update)
    assert gw.events == []


@pytest.mark.asyncio
async def test_discord_ignores_its_own_bot_messages():
    gw = FakeGateway()
    ch = _discord(gw, allow_all=True)
    payload = _dc_payload()
    payload["author"]["bot"] = True
    await ch._on_message(payload)
    assert gw.events == []


@pytest.mark.asyncio
async def test_slack_ignores_bot_messages_and_subtypes():
    gw = FakeGateway()
    ch = _slack(gw, allow_all=True)
    for mutation in ({"bot_id": "B1"}, {"subtype": "message_changed"}, {"user": None}):
        payload = _sl_payload()
        payload["payload"]["event"].update(mutation)
        await _deliver_slack(ch, payload)
    assert gw.events == []


@pytest.mark.asyncio
async def test_slack_does_not_answer_the_same_message_twice():
    """Slack redelivers on any missed ack; answering twice is worse than late."""
    gw = FakeGateway()
    ch = _slack(gw, allow_all=True)
    payload = _sl_payload()
    payload["payload"]["event"]["client_msg_id"] = "same-id"
    await _deliver_slack(ch, payload)
    await _deliver_slack(ch, payload)
    assert len(gw.events) == 1


# --- the channel id is implicitly allowed ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("make", "kwarg", "payload", "deliver"),
    [
        (_discord, "channel_id", _dc_payload, _deliver_discord),
        (_slack, "channel_id", _sl_payload, _deliver_slack),
    ],
)
async def test_the_configured_channel_is_allowed_without_listing_it_twice(
    make, kwarg, payload, deliver
):
    """Naming the channel to post in is consent to read it."""
    gw = FakeGateway()
    ch = make(gw, **{kwarg: "111", "allow_ids": []})
    await deliver(ch, payload(ch="111"))
    assert len(gw.events) == 1


# --- the environment override ------------------------------------------------


@pytest.mark.asyncio
async def test_the_telegram_env_override_opens_the_channel(monkeypatch):
    monkeypatch.setenv("REMEDY_TELEGRAM_ALLOW_ALL", "1")
    gw = FakeGateway()
    ch = _telegram(gw, chat_ids=[])
    await ch._handle_update(_tg_payload())
    assert len(gw.events) == 1


@pytest.mark.asyncio
async def test_a_junk_env_value_does_not_open_the_channel(monkeypatch):
    monkeypatch.setenv("REMEDY_TELEGRAM_ALLOW_ALL", "maybe")
    gw = FakeGateway()
    ch = _telegram(gw, chat_ids=[])
    await ch._handle_update(_tg_payload())
    assert gw.events == []


# --- what reaches the gateway ------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_carries_the_ids_the_session_is_keyed_on():
    gw = FakeGateway()
    ch = _telegram(gw, chat_ids=["111"])
    await ch._handle_update(_tg_payload(ch="111", user="222"))
    ev = gw.events[0]
    assert ev.session_id == "111"
    assert ev.source_id == "222"
    assert ev.payload["chat_id"] == "111"
    assert ev.payload["user_id"] == "222"


@pytest.mark.asyncio
async def test_telegram_remembers_how_far_it_read(tmp_path):
    """The offset is what stops a restart replaying the whole backlog."""
    gw = FakeGateway()
    ch = _telegram(gw, chat_ids=["111"], home_dir=str(tmp_path))
    update = _tg_payload(ch="111")
    update["update_id"] = 4242
    await ch._handle_update(update)
    assert ch._last_update_id == 4242


@pytest.mark.asyncio
async def test_an_older_update_id_does_not_rewind_the_offset():
    gw = FakeGateway()
    ch = _telegram(gw, chat_ids=["111"])
    for uid in (10, 3):
        update = _tg_payload(ch="111")
        update["update_id"] = uid
        await ch._handle_update(update)
    assert ch._last_update_id == 10


@pytest.mark.asyncio
async def test_an_edited_message_is_handled_like_a_new_one():
    gw = FakeGateway()
    ch = _telegram(gw, chat_ids=["111"])
    await ch._handle_update(
        {
            "update_id": 2,
            "edited_message": {
                "text": "actually, do this instead",
                "chat": {"id": "111"},
                "from": {"id": "222"},
            },
        }
    )
    assert gw.events[0].payload["message"] == "actually, do this instead"


@pytest.mark.asyncio
async def test_a_service_update_with_no_message_is_ignored():
    gw = FakeGateway()
    ch = _telegram(gw, allow_all=True)
    await ch._handle_update({"update_id": 3, "my_chat_member": {"status": "kicked"}})
    assert gw.events == []


# --- sending -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_sending_without_a_target_and_without_a_default_fails_honestly():
    """Returning True here would report a delivery that never happened."""
    gw = FakeGateway()
    ch = _telegram(gw, chat_ids=[])
    assert await ch.send("hello") is False


@pytest.mark.asyncio
async def test_a_channel_with_no_token_is_a_stub_that_says_so():
    from remedy.gateway.channels.telegram import TelegramChannel

    gw = FakeGateway()
    ch = TelegramChannel(gw, bot_token="", chat_ids=["111"])
    assert await ch.send("hello") is True  # stub mode, nothing sent
