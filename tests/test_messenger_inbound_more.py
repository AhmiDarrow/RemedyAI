"""Matrix, Mattermost and Signal — the three messengers nobody watches.

These are the least-used adapters in the gateway, which is exactly what makes
them worth pinning down: each one is a public address that reaches an agent
able to read files, spend money and drive a desktop. Four things have to hold.
A stranger is refused (an empty allowlist admits nobody). The bot never answers
its own echo, or it talks to itself for ever. A malformed frame from the server
is dropped instead of killing the poll loop. And stopping a channel really does
cancel its background task and close its socket, rather than leaking both into
whatever runs next.

The HTTP paths run against a real loopback server (``tests.harness.fake_http``),
so the URL, the Authorization header and the JSON body are inspected as they go
out on the wire rather than at a mock boundary that proves nothing.

signal-cli is never executed. ``asyncio.create_subprocess_exec`` is replaced by
a double throughout: the real binary would talk to the owner's Signal account.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from remedy.gateway.channels.matrix import (
    MatrixChannel,
    _load_matrix_since,
    _matrix_home,
    _save_matrix_since,
)
from remedy.gateway.channels.mattermost import MattermostChannel
from remedy.gateway.channels.signal_cli import SignalChannel
from remedy.models import ChannelKind
from tests.harness.fake_http import fake_http_fixture  # noqa: F401


class FakeGateway:
    """Records what the adapter decided to hand upstream, and nothing else."""

    def __init__(self) -> None:
        self.events: list = []

    async def emit(self, event) -> None:
        self.events.append(event)


@pytest_asyncio.fixture
async def adopt():
    """Register channels so their aiohttp session and tasks are torn down.

    An adapter that reaches ensure_http() owns a ClientSession; leaving it open
    leaks a connector into the next test and prints an unclosed-session warning
    from a completely unrelated file.
    """
    made: list = []

    def _adopt(channel):
        made.append(channel)
        return channel

    yield _adopt
    for channel in made:
        await channel.stop()


def _instant_sleep(monkeypatch, stop=None) -> list[float]:
    """Make backoff instant, record what it asked for, optionally halt the loop."""
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def _sleep(delay, *args, **kwargs):
        slept.append(delay)
        if stop is not None:
            stop()
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return slept


# --- Matrix: the /sync cursor on disk ----------------------------------------


def test_a_missing_since_file_reads_as_empty_not_an_error(tmp_path):
    assert _load_matrix_since(str(tmp_path)) == ""


def test_the_since_cursor_survives_a_restart(tmp_path):
    """Without it, a restart replays the room and re-answers messages already answered."""
    _save_matrix_since(str(tmp_path), "s_12345")
    assert _load_matrix_since(str(tmp_path)) == "s_12345"


def test_a_stored_cursor_is_read_back_without_its_newline(tmp_path):
    _save_matrix_since(str(tmp_path), "s_1")
    raw = (tmp_path / "locks" / "matrix_since.txt").read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert _load_matrix_since(str(tmp_path)) == "s_1"


def test_an_empty_cursor_is_never_written(tmp_path):
    """Writing "" would read back as a valid cursor and silently reset the stream."""
    _save_matrix_since(str(tmp_path), "")
    assert not (tmp_path / "locks").exists()


def test_a_cursor_that_cannot_be_written_is_reported_not_raised(tmp_path):
    (tmp_path / "locks").write_text("not a directory", encoding="utf-8")
    _save_matrix_since(str(tmp_path), "s_1")  # must not raise
    assert _load_matrix_since(str(tmp_path)) == ""


def test_the_home_directory_defaults_to_the_dot_remedy_folder():
    from remedy.home import default_home

    assert _matrix_home(None) == default_home()


def test_a_tilde_home_is_expanded():
    assert _matrix_home("~/somewhere") == Path("~/somewhere").expanduser()


# --- Matrix: construction ----------------------------------------------------


def test_matrix_strips_a_trailing_slash_from_the_homeserver():
    ch = MatrixChannel(FakeGateway(), homeserver="https://hs.example/")
    assert ch.homeserver == "https://hs.example"


def test_the_configured_room_is_allowed_without_listing_it_twice():
    """Naming the room to post in is consent to read it."""
    ch = MatrixChannel(FakeGateway(), room_id="!room:hs", allow_ids=[])
    assert "!room:hs" in ch._allowed


def test_matrix_with_no_room_and_no_allowlist_admits_nobody():
    ch = MatrixChannel(FakeGateway())
    assert ch._allowed == frozenset()
    assert ch.allow_all is False


# --- Matrix: outbound send ---------------------------------------------------


@pytest.mark.asyncio
async def test_matrix_send_without_a_token_is_a_silent_success(fake_http, adopt):
    """Stub mode: an unconfigured channel must not fail a broadcast, nor touch the network."""
    ch = adopt(MatrixChannel(FakeGateway(), access_token="", homeserver=fake_http.base_url))
    assert await ch.send("hi") is True
    assert fake_http.requests == []


@pytest.mark.asyncio
async def test_matrix_send_without_a_homeserver_is_a_silent_success(fake_http, adopt):
    ch = adopt(MatrixChannel(FakeGateway(), access_token="tok", homeserver=""))
    assert await ch.send("hi") is True


@pytest.mark.asyncio
async def test_matrix_send_with_no_room_at_all_fails_rather_than_guessing(fake_http, adopt):
    ch = adopt(MatrixChannel(FakeGateway(), access_token="tok", homeserver=fake_http.base_url))
    assert await ch.send("hi") is False
    assert fake_http.requests == []


@pytest.mark.asyncio
async def test_matrix_send_puts_the_message_into_the_room(fake_http, adopt):
    fake_http.route("*", json={"event_id": "$1"})
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="tok",
            homeserver=fake_http.base_url,
            room_id="!room:hs",
        )
    )
    assert await ch.send("hello there") is True
    req = fake_http.last_request
    assert req.method == "PUT"
    assert req.path.startswith("/_matrix/client/v3/rooms/!room:hs/send/m.room.message/")
    assert req.header("authorization") == "Bearer tok"
    assert req.json_body() == {"msgtype": "m.text", "body": "hello there"}


@pytest.mark.asyncio
async def test_matrix_send_prefers_the_room_it_was_given_over_the_configured_one(
    fake_http, adopt
):
    fake_http.route("*", json={})
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="tok",
            homeserver=fake_http.base_url,
            room_id="!default:hs",
        )
    )
    await ch.send("hi", target="!other:hs")
    assert "/rooms/!other:hs/" in fake_http.last_request.path


@pytest.mark.asyncio
async def test_matrix_send_truncates_an_enormous_message(fake_http, adopt):
    """A homeserver rejects an oversized event outright; a truncated reply beats none."""
    fake_http.route("*", json={})
    ch = adopt(
        MatrixChannel(
            FakeGateway(), access_token="t", homeserver=fake_http.base_url, room_id="!r:hs"
        )
    )
    await ch.send("x" * 9000)
    assert len(fake_http.last_request.json_body()["body"]) == 4000


@pytest.mark.asyncio
async def test_every_matrix_send_gets_its_own_transaction_id(fake_http, adopt):
    """A reused txn id makes the homeserver dedupe the second message away."""
    fake_http.route("*", json={})
    ch = adopt(
        MatrixChannel(
            FakeGateway(), access_token="t", homeserver=fake_http.base_url, room_id="!r:hs"
        )
    )
    await ch.send("one")
    await ch.send("two")
    txns = [r.path.rsplit("/", 1)[-1] for r in fake_http.requests]
    assert len(txns) == 2
    assert txns[0] != txns[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"), [(200, True), (201, True), (403, False), (500, False)]
)
async def test_matrix_send_reports_the_homeservers_verdict(fake_http, adopt, status, expected):
    fake_http.route("*", status=status, json={})
    ch = adopt(
        MatrixChannel(
            FakeGateway(), access_token="t", homeserver=fake_http.base_url, room_id="!r:hs"
        )
    )
    assert await ch.send("hi") is expected


@pytest.mark.asyncio
async def test_a_homeserver_that_drops_the_connection_is_a_failed_send_not_a_crash(
    fake_http, adopt
):
    fake_http.route("*", drop=True)
    ch = adopt(
        MatrixChannel(
            FakeGateway(), access_token="t", homeserver=fake_http.base_url, room_id="!r:hs"
        )
    )
    assert await ch.send("hi") is False


# --- Matrix: typing notification ---------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"access_token": "", "user_id": "@me:hs", "room_id": "!r:hs"}, "no token"),
        ({"access_token": "t", "user_id": "", "room_id": "!r:hs"}, "no user id"),
        ({"access_token": "t", "user_id": "@me:hs", "room_id": ""}, "no room"),
    ],
)
async def test_matrix_typing_is_skipped_when_it_cannot_be_addressed(
    fake_http, adopt, kwargs, why
):
    ch = adopt(MatrixChannel(FakeGateway(), homeserver=fake_http.base_url, **kwargs))
    await ch.send_typing()
    assert fake_http.requests == [], f"typing was sent with {why}"


@pytest.mark.asyncio
async def test_matrix_typing_is_addressed_to_our_own_user_in_that_room(fake_http, adopt):
    fake_http.route("*", json={})
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="t",
            homeserver=fake_http.base_url,
            user_id="@me:hs",
            room_id="!r:hs",
        )
    )
    await ch.send_typing()
    req = fake_http.last_request
    assert req.method == "PUT"
    assert req.path == "/_matrix/client/v3/rooms/!r:hs/typing/@me:hs"
    assert req.json_body() == {"typing": True, "timeout": 10000}


@pytest.mark.asyncio
async def test_a_failed_typing_notification_never_reaches_the_caller(fake_http, adopt):
    """Typing is decoration; it must not take down the reply that follows it."""
    fake_http.route("*", drop=True)
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="t",
            homeserver=fake_http.base_url,
            user_id="@me:hs",
            room_id="!r:hs",
        )
    )
    await ch.send_typing()  # must not raise


# --- Matrix: start / stop ----------------------------------------------------


@pytest.mark.asyncio
async def test_an_unconfigured_matrix_channel_starts_no_sync_loop(fake_http, adopt):
    ch = adopt(MatrixChannel(FakeGateway(), access_token="", homeserver=""))
    await ch.start()
    assert ch.running is True
    assert ch._sync_task is None
    assert fake_http.requests == []


@pytest.mark.asyncio
async def test_matrix_asks_the_homeserver_who_it_is_when_the_user_id_is_unset(
    fake_http, adopt, tmp_path
):
    """Without our own user id the self-message guard cannot fire: the bot answers itself."""
    fake_http.route("/_matrix/client/v3/account/whoami", json={"user_id": "@bot:hs"})
    fake_http.route("*", json={"next_batch": "s_1"})
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="t",
            homeserver=fake_http.base_url,
            home_dir=str(tmp_path),
        )
    )
    await ch.start()
    assert ch.user_id == "@bot:hs"
    assert ch._sync_task is not None


@pytest.mark.asyncio
async def test_a_known_user_id_is_not_looked_up_again(fake_http, adopt, tmp_path):
    fake_http.route("*", json={"next_batch": "s_1"})
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="t",
            homeserver=fake_http.base_url,
            user_id="@me:hs",
            home_dir=str(tmp_path),
        )
    )
    await ch.start()
    assert fake_http.requests_for("/_matrix/client/v3/account/whoami") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [{"status": 401, "json": {}}, {"drop": True}])
async def test_a_failed_whoami_still_leaves_the_channel_running(
    fake_http, adopt, tmp_path, failure
):
    fake_http.route("/_matrix/client/v3/account/whoami", **failure)
    fake_http.route("*", json={"next_batch": "s_1"})
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="t",
            homeserver=fake_http.base_url,
            home_dir=str(tmp_path),
        )
    )
    await ch.start()
    assert ch.user_id == ""
    assert ch._sync_task is not None


@pytest.mark.asyncio
async def test_matrix_resumes_from_the_cursor_it_stored_last_time(fake_http, adopt, tmp_path):
    _save_matrix_since(str(tmp_path), "s_resume")
    fake_http.route("*", json={"next_batch": "s_resume"})
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="t",
            homeserver=fake_http.base_url,
            user_id="@me:hs",
            home_dir=str(tmp_path),
        )
    )
    await ch.start()
    assert ch._since == "s_resume"


@pytest.mark.asyncio
async def test_stopping_matrix_cancels_the_sync_loop_and_closes_the_socket(fake_http, tmp_path):
    fake_http.route("*", json={"next_batch": "s_1"})
    ch = MatrixChannel(
        FakeGateway(),
        access_token="t",
        homeserver=fake_http.base_url,
        user_id="@me:hs",
        home_dir=str(tmp_path),
    )
    await ch.start()
    session = await ch.ensure_http()  # the same session the sync loop reuses
    await ch.stop()
    assert ch._sync_task is None
    assert ch.running is False
    assert session.closed is True
    await ch.stop()  # idempotent


@pytest.mark.asyncio
async def test_stopping_matrix_cancels_typing_notifications_still_in_flight():
    """A pending typing PUT must not outlive the channel and resurrect its session."""
    ch = MatrixChannel(FakeGateway())
    pending = asyncio.create_task(asyncio.sleep(30))
    ch._typing_tasks.add(pending)
    await ch.stop()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert ch._typing_tasks == set()


@pytest.mark.asyncio
async def test_the_matrix_session_is_reused_and_outlasts_a_long_poll(adopt):
    """/sync blocks for 30s server-side; a 60s default leaves little room to spare."""
    ch = adopt(MatrixChannel(FakeGateway()))
    first = await ch.ensure_http()
    assert await ch.ensure_http() is first
    assert first.timeout.total == 90.0
    await ch.close_http()
    assert first.closed is True
    assert ch._session is None


# --- Matrix: what a sync payload is allowed to do ----------------------------


def _sync(
    room="!r:hs",
    sender="@her:hs",
    body="hello",
    msgtype="m.text",
    etype="m.room.message",
):
    return {
        "next_batch": "s_next",
        "rooms": {
            "join": {
                room: {
                    "timeline": {
                        "events": [
                            {
                                "type": etype,
                                "sender": sender,
                                "content": {"msgtype": msgtype, "body": body},
                            }
                        ]
                    }
                }
            }
        },
    }


def _matrix(gw, **kw):
    kw.setdefault("access_token", "")
    return MatrixChannel(gw, **kw)


@pytest.mark.asyncio
async def test_a_matrix_stranger_is_refused_when_no_allowlist_is_set():
    gw = FakeGateway()
    await _matrix(gw, allow_ids=[])._handle_sync(_sync())
    assert gw.events == []


@pytest.mark.asyncio
async def test_an_unlisted_matrix_room_and_sender_are_both_refused():
    gw = FakeGateway()
    await _matrix(gw, allow_ids=["!other:hs"])._handle_sync(_sync())
    assert gw.events == []


@pytest.mark.asyncio
async def test_a_listed_matrix_sender_gets_through_even_from_a_new_room():
    gw = FakeGateway()
    await _matrix(gw, allow_ids=["@her:hs"])._handle_sync(_sync(room="!fresh:hs"))
    assert len(gw.events) == 1


@pytest.mark.asyncio
async def test_the_configured_matrix_room_needs_no_extra_allowlist_entry():
    gw = FakeGateway()
    await _matrix(gw, room_id="!r:hs", allow_ids=[])._handle_sync(_sync(room="!r:hs"))
    assert len(gw.events) == 1


@pytest.mark.asyncio
async def test_matrix_allow_all_lets_anyone_through():
    gw = FakeGateway()
    await _matrix(gw, allow_all=True)._handle_sync(_sync(sender="@stranger:elsewhere"))
    assert len(gw.events) == 1


@pytest.mark.asyncio
async def test_matrix_never_answers_its_own_echo():
    """Our own message comes straight back down /sync; replying to it never ends."""
    gw = FakeGateway()
    ch = _matrix(gw, user_id="@me:hs", allow_all=True)
    await ch._handle_sync(_sync(sender="@me:hs"))
    assert gw.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"etype": "m.reaction"}, "a reaction is not a message"),
        ({"etype": "m.room.member"}, "a join event is not a message"),
        ({"msgtype": "m.image"}, "an image has no text to act on"),
        ({"msgtype": "m.notice"}, "a notice is another bot talking"),
        ({"body": ""}, "an empty body says nothing"),
        ({"body": "   "}, "whitespace says nothing"),
    ],
)
async def test_matrix_ignores_events_it_cannot_act_on(kwargs, why):
    gw = FakeGateway()
    await _matrix(gw, allow_all=True)._handle_sync(_sync(**kwargs))
    assert gw.events == [], why


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{}, {"rooms": {}}, {"rooms": {"join": {}}}, {"rooms": None}]
)
async def test_a_sync_payload_with_nothing_joined_is_not_an_error(payload):
    gw = FakeGateway()
    await _matrix(gw, allow_all=True)._handle_sync(payload)
    assert gw.events == []


@pytest.mark.asyncio
async def test_an_invited_room_is_not_read_as_if_we_had_joined_it():
    """Anyone can invite the bot; only a joined room is a conversation we agreed to."""
    gw = FakeGateway()
    payload = _sync()
    payload["rooms"]["invite"] = payload["rooms"].pop("join")
    await _matrix(gw, allow_all=True)._handle_sync(payload)
    assert gw.events == []


@pytest.mark.asyncio
async def test_the_matrix_event_carries_the_ids_the_session_is_keyed_on():
    gw = FakeGateway()
    await _matrix(gw, allow_all=True)._handle_sync(_sync(room="!r:hs", sender="@her:hs"))
    ev = gw.events[0]
    assert ev.channel is ChannelKind.MATRIX
    assert ev.session_id == "!r:hs"
    assert ev.source_id == "@her:hs"
    assert ev.payload["chat_id"] == "!r:hs"
    assert ev.payload["room_id"] == "!r:hs"
    assert ev.payload["user_id"] == "@her:hs"
    assert ev.payload["message"] == "hello"


@pytest.mark.asyncio
async def test_matrix_typing_tasks_do_not_accumulate_across_messages():
    """The set is a cancellation handle, not a leak: a finished task drops out of it."""
    gw = FakeGateway()
    ch = _matrix(gw, allow_all=True)  # stub mode: send_typing returns immediately
    for _ in range(3):
        await ch._handle_sync(_sync())
    for _ in range(6):
        await asyncio.sleep(0)
    assert ch._typing_tasks == set()
    assert len(gw.events) == 3


# --- Matrix: the sync loop ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_sync_loop_advances_and_stores_the_cursor(fake_http, adopt, tmp_path):
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="t",
            homeserver=fake_http.base_url,
            user_id="@me:hs",
            allow_all=True,
            home_dir=str(tmp_path),
        )
    )
    fake_http.route("*", json=_sync())
    original_handle = ch._handle_sync

    async def handle_once(data):
        await original_handle(data)
        ch._running = False  # one pass is enough to assert on

    ch._handle_sync = handle_once
    ch._running = True
    ch._since = "s_old"

    await ch._sync_loop()

    assert ch._since == "s_next"
    assert _load_matrix_since(str(tmp_path)) == "s_next"
    query = fake_http.requests_for("/_matrix/client/v3/sync")[0].query
    assert "since=s_old" in query
    assert "timeout=30000" in query
    assert len(ch.gateway.events) == 1


@pytest.mark.asyncio
async def test_a_failing_sync_backs_off_instead_of_hot_looping(
    fake_http, adopt, tmp_path, monkeypatch
):
    """A 502 from the homeserver must not become a request-per-microsecond flood."""
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="t",
            homeserver=fake_http.base_url,
            user_id="@me:hs",
            allow_all=True,
            home_dir=str(tmp_path),
        )
    )
    fake_http.route("*", status=502, body="bad gateway")
    ch._running = True
    ch._since = "s_old"
    slept = _instant_sleep(monkeypatch, stop=lambda: setattr(ch, "_running", False))

    await ch._sync_loop()

    assert slept == [3.0]
    assert ch._since == "s_old", "a failed sync must not advance the cursor"
    assert ch.gateway.events == []


@pytest.mark.asyncio
async def test_an_exploding_sync_handler_backs_off_rather_than_killing_the_loop(
    fake_http, adopt, tmp_path, monkeypatch
):
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="t",
            homeserver=fake_http.base_url,
            user_id="@me:hs",
            home_dir=str(tmp_path),
        )
    )
    fake_http.route("*", json=_sync())

    async def boom(_data):
        raise RuntimeError("handler is broken")

    ch._handle_sync = boom
    ch._running = True
    slept = _instant_sleep(monkeypatch, stop=lambda: setattr(ch, "_running", False))

    await ch._sync_loop()  # must not propagate

    assert slept == [3.0]


@pytest.mark.asyncio
async def test_cancelling_the_sync_loop_is_not_swallowed_as_an_error(fake_http, adopt, tmp_path):
    """Shutdown relies on CancelledError escaping; catching it would hang stop()."""
    ch = adopt(
        MatrixChannel(
            FakeGateway(),
            access_token="t",
            homeserver=fake_http.base_url,
            user_id="@me:hs",
            home_dir=str(tmp_path),
        )
    )
    fake_http.route("*", hang=True)
    ch._running = True
    task = asyncio.create_task(ch._sync_loop())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if fake_http.requests:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- Mattermost: the websocket URL -------------------------------------------


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://mm.example", "wss://mm.example/api/v4/websocket"),
        ("https://mm.example/", "wss://mm.example/api/v4/websocket"),
        ("http://mm.example:8065", "ws://mm.example:8065/api/v4/websocket"),
        ("http://127.0.0.1:8065", "ws://127.0.0.1:8065/api/v4/websocket"),
        ("mm.example", "ws://mm.example/api/v4/websocket"),
    ],
)
def test_the_mattermost_socket_url_follows_the_scheme_of_the_site(base_url, expected):
    """Only https becomes wss; a plain http site must not be silently upgraded."""
    ch = MattermostChannel(FakeGateway(), base_url=base_url)
    assert ch._ws_url() == expected


# --- Mattermost: outbound send -----------------------------------------------


@pytest.mark.asyncio
async def test_mattermost_send_without_a_token_is_a_silent_success(fake_http, adopt):
    ch = adopt(MattermostChannel(FakeGateway(), bot_token="", base_url=fake_http.base_url))
    assert await ch.send("hi") is True
    assert fake_http.requests == []


@pytest.mark.asyncio
async def test_mattermost_send_with_no_channel_fails_rather_than_guessing(fake_http, adopt):
    ch = adopt(MattermostChannel(FakeGateway(), bot_token="t", base_url=fake_http.base_url))
    assert await ch.send("hi") is False
    assert fake_http.requests == []


@pytest.mark.asyncio
async def test_mattermost_send_posts_to_the_channel(fake_http, adopt):
    fake_http.route("/api/v4/posts", json={"id": "p1"}, status=201)
    ch = adopt(
        MattermostChannel(
            FakeGateway(), bot_token="tok", base_url=fake_http.base_url, channel_id="c1"
        )
    )
    assert await ch.send("hello there") is True
    req = fake_http.last_request
    assert req.method == "POST"
    assert req.header("authorization") == "Bearer tok"
    assert req.json_body() == {"channel_id": "c1", "message": "hello there"}


@pytest.mark.asyncio
async def test_mattermost_send_prefers_the_channel_it_was_given(fake_http, adopt):
    fake_http.route("*", json={})
    ch = adopt(
        MattermostChannel(
            FakeGateway(), bot_token="t", base_url=fake_http.base_url, channel_id="c1"
        )
    )
    await ch.send("hi", target="c2")
    assert fake_http.last_request.json_body()["channel_id"] == "c2"


@pytest.mark.asyncio
async def test_mattermost_send_truncates_an_enormous_message(fake_http, adopt):
    fake_http.route("*", json={})
    ch = adopt(
        MattermostChannel(
            FakeGateway(), bot_token="t", base_url=fake_http.base_url, channel_id="c1"
        )
    )
    await ch.send("y" * 9000)
    assert len(fake_http.last_request.json_body()["message"]) == 4000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"), [(200, True), (201, True), (401, False), (500, False)]
)
async def test_mattermost_send_reports_the_servers_verdict(fake_http, adopt, status, expected):
    fake_http.route("*", status=status, json={})
    ch = adopt(
        MattermostChannel(
            FakeGateway(), bot_token="t", base_url=fake_http.base_url, channel_id="c1"
        )
    )
    assert await ch.send("hi") is expected


@pytest.mark.asyncio
async def test_a_mattermost_server_that_drops_the_connection_is_a_failed_send(fake_http, adopt):
    fake_http.route("*", drop=True)
    ch = adopt(
        MattermostChannel(
            FakeGateway(), bot_token="t", base_url=fake_http.base_url, channel_id="c1"
        )
    )
    assert await ch.send("hi") is False


@pytest.mark.asyncio
async def test_mattermost_typing_is_a_deliberate_no_op(fake_http, adopt):
    ch = adopt(
        MattermostChannel(
            FakeGateway(), bot_token="t", base_url=fake_http.base_url, channel_id="c1"
        )
    )
    assert await ch.send_typing("c1") is None
    assert fake_http.requests == []


# --- Mattermost: inbound events ----------------------------------------------


def _mm_event(text="hello", channel="c1", user="u1", props=..., event="posted"):
    post = {"message": text, "channel_id": channel, "user_id": user}
    if props is not ...:
        post["props"] = props
    return {"event": event, "data": {"post": json.dumps(post)}}


def _mattermost(gw, **kw):
    kw.setdefault("bot_token", "t")
    kw.setdefault("base_url", "https://mm.example")
    return MattermostChannel(gw, **kw)


@pytest.mark.asyncio
async def test_a_mattermost_stranger_is_refused_when_no_allowlist_is_set():
    gw = FakeGateway()
    await _mattermost(gw, allow_ids=[])._on_event(_mm_event())
    assert gw.events == []


@pytest.mark.asyncio
async def test_an_unlisted_mattermost_sender_is_refused():
    gw = FakeGateway()
    await _mattermost(gw, allow_ids=["nobody"])._on_event(_mm_event())
    assert gw.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", ["c1", "u1", "team-9"])
async def test_any_one_matching_mattermost_id_admits_the_post(allowed):
    """Channel, user and team are all offered to the allowlist; one match is enough."""
    gw = FakeGateway()
    ch = _mattermost(gw, allow_ids=[allowed], team_id="team-9")
    await ch._on_event(_mm_event())
    assert len(gw.events) == 1


@pytest.mark.asyncio
async def test_the_configured_mattermost_channel_needs_no_extra_allowlist_entry():
    gw = FakeGateway()
    await _mattermost(gw, channel_id="c1", allow_ids=[])._on_event(_mm_event(channel="c1"))
    assert len(gw.events) == 1


@pytest.mark.asyncio
async def test_mattermost_allow_all_lets_anyone_through():
    gw = FakeGateway()
    await _mattermost(gw, allow_all=True)._on_event(_mm_event(user="stranger"))
    assert len(gw.events) == 1


@pytest.mark.asyncio
async def test_mattermost_never_answers_another_bot():
    """from_bot also covers our own echo; without it the bot argues with itself."""
    gw = FakeGateway()
    await _mattermost(gw, allow_all=True)._on_event(_mm_event(props={"from_bot": "true"}))
    assert gw.events == []


@pytest.mark.asyncio
async def test_a_post_with_an_empty_props_map_is_still_delivered():
    gw = FakeGateway()
    await _mattermost(gw, allow_all=True)._on_event(_mm_event(props={}))
    assert len(gw.events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["post_edited", "typing", "hello", "status_change", ""])
async def test_mattermost_only_acts_on_a_posted_event(event):
    gw = FakeGateway()
    await _mattermost(gw, allow_all=True)._on_event(_mm_event(event=event))
    assert gw.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame",
    [
        {"event": "posted", "data": {"post": "not json at all"}},
        {"event": "posted", "data": {"post": ""}},
        {"event": "posted", "data": {}},
        {"event": "posted"},
    ],
)
async def test_a_malformed_mattermost_frame_is_dropped_not_raised(frame):
    """_ws_loop reads any exception as a disconnect, so one bad frame from the
    server would otherwise reconnect the channel in a loop."""
    gw = FakeGateway()
    await _mattermost(gw, allow_all=True)._on_event(frame)
    assert gw.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "\n\t "])
async def test_an_empty_mattermost_message_is_ignored(text):
    gw = FakeGateway()
    await _mattermost(gw, allow_all=True)._on_event(_mm_event(text=text))
    assert gw.events == []


@pytest.mark.asyncio
async def test_the_mattermost_event_carries_the_ids_the_session_is_keyed_on():
    gw = FakeGateway()
    await _mattermost(gw, allow_all=True)._on_event(_mm_event(channel="c1", user="u1"))
    ev = gw.events[0]
    assert ev.channel is ChannelKind.MATTERMOST
    assert ev.session_id == "c1"
    assert ev.source_id == "u1"
    assert ev.payload["chat_id"] == "c1"
    assert ev.payload["user_id"] == "u1"
    assert ev.payload["message"] == "hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["[]", "null", '"just a string"', "42"])
async def test_a_post_that_is_not_an_object_is_dropped_rather_than_crashing_the_socket(
    body,
):
    """The try wrapped only json.loads, so a post that parsed but was not an
    object raised out of _on_event. _ws_loop read that as a disconnect, slept
    three seconds and reconnected — forever. One crafted post from anyone who
    can write in a watched channel took the channel down indefinitely."""
    gw = FakeGateway()
    frame = {"event": "posted", "data": {"post": body}}
    await _mattermost(gw, allow_all=True)._on_event(frame)
    assert gw.events == []


@pytest.mark.asyncio
async def test_a_real_post_whose_props_are_null_is_still_delivered():
    """Null props is an ordinary post, not garbage — it used to crash the
    socket, and it must not now be silently dropped instead."""
    gw = FakeGateway()
    frame = {
        "event": "posted",
        "data": {"post": json.dumps({"message": "hi", "props": None})},
    }
    await _mattermost(gw, allow_all=True)._on_event(frame)
    assert len(gw.events) == 1
    assert gw.events[0].payload["message"] == "hi"


@pytest.mark.asyncio
async def test_a_bot_echo_is_still_recognised_through_real_props():
    gw = FakeGateway()
    frame = {
        "event": "posted",
        "data": {"post": json.dumps({"message": "hi", "props": {"from_bot": "true"}})},
    }
    await _mattermost(gw, allow_all=True)._on_event(frame)
    assert gw.events == []


# --- Mattermost: the websocket loop, against a socket double -----------------


class _FakeWSMessage:
    def __init__(self, type_, data: str = "") -> None:
        self.type = type_
        self.data = data


class _FakeWS:
    """Just enough of aiohttp's ClientWebSocketResponse for _ws_loop."""

    def __init__(self, messages, on_exit=None, block: asyncio.Event | None = None) -> None:
        self._messages = list(messages)
        self._on_exit = on_exit
        self._block = block  # a socket that stays open and idle, like a real one
        self.sent: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        if self._on_exit is not None:
            self._on_exit()
        return False

    async def send_json(self, body) -> None:
        self.sent.append(body)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            if self._block is not None:
                await self._block.wait()
            raise StopAsyncIteration
        return self._messages.pop(0)


class _FakeSession:
    """Stands in for the ClientSession HttpSessionMixin would otherwise build."""

    def __init__(self, sockets) -> None:
        self._sockets = list(sockets)
        self.urls: list[str] = []
        self.closed = False

    def ws_connect(self, url):
        self.urls.append(url)
        return self._sockets.pop(0)

    async def close(self) -> None:
        self.closed = True


def _ws_msg_types():
    import aiohttp

    return aiohttp.WSMsgType


@pytest.mark.asyncio
async def test_mattermost_authenticates_before_reading_anything():
    """The server sends nothing until the challenge lands; skipping it hangs silently."""
    T = _ws_msg_types()
    gw = FakeGateway()
    ch = _mattermost(gw, allow_all=True)
    ws = _FakeWS(
        [_FakeWSMessage(T.TEXT, json.dumps(_mm_event(text="hi there")))],
        on_exit=lambda: setattr(ch, "_running", False),
    )
    ch._session = _FakeSession([ws])
    ch._running = True

    await ch._ws_loop()

    assert ws.sent == [{"seq": 1, "action": "authentication_challenge", "data": {"token": "t"}}]
    assert ch._session.urls == ["wss://mm.example/api/v4/websocket"]
    assert [e.payload["message"] for e in gw.events] == ["hi there"]


@pytest.mark.asyncio
async def test_the_mattermost_sequence_number_never_repeats_across_reconnects():
    T = _ws_msg_types()
    ch = _mattermost(FakeGateway(), allow_all=True)
    first = _FakeWS([])
    second = _FakeWS([_FakeWSMessage(T.CLOSED)], on_exit=lambda: setattr(ch, "_running", False))
    ch._session = _FakeSession([first, second])
    ch._running = True

    await ch._ws_loop()

    assert [f["seq"] for f in first.sent + second.sent] == [1, 2]
    assert ch._seq == 3


@pytest.mark.asyncio
async def test_mattermost_ignores_frames_that_are_not_text():
    T = _ws_msg_types()
    gw = FakeGateway()
    ch = _mattermost(gw, allow_all=True)
    ws = _FakeWS(
        [
            _FakeWSMessage(T.PING),
            _FakeWSMessage(T.BINARY, "ignored"),
            _FakeWSMessage(T.TEXT, json.dumps(_mm_event(text="the real one"))),
        ],
        on_exit=lambda: setattr(ch, "_running", False),
    )
    ch._session = _FakeSession([ws])
    ch._running = True

    await ch._ws_loop()

    assert [e.payload["message"] for e in gw.events] == ["the real one"]


@pytest.mark.asyncio
@pytest.mark.parametrize("closing", ["CLOSED", "ERROR"])
async def test_a_closed_or_failed_socket_stops_reading_that_connection(closing):
    """Reading on past a CLOSED frame spins on a dead socket for ever."""
    T = _ws_msg_types()
    gw = FakeGateway()
    ch = _mattermost(gw, allow_all=True)
    ws = _FakeWS(
        [
            _FakeWSMessage(getattr(T, closing)),
            _FakeWSMessage(T.TEXT, json.dumps(_mm_event(text="after the close"))),
        ],
        on_exit=lambda: setattr(ch, "_running", False),
    )
    ch._session = _FakeSession([ws])
    ch._running = True

    await ch._ws_loop()

    assert gw.events == []


@pytest.mark.asyncio
async def test_a_broken_websocket_backs_off_before_reconnecting(monkeypatch):
    ch = _mattermost(FakeGateway(), allow_all=True)

    class _Refusing:
        closed = False

        def ws_connect(self, url):
            raise OSError("connection refused")

        async def close(self):
            self.closed = True

    ch._session = _Refusing()
    ch._running = True
    slept = _instant_sleep(monkeypatch, stop=lambda: setattr(ch, "_running", False))

    await ch._ws_loop()  # must not propagate

    assert slept == [3.0]


@pytest.mark.asyncio
async def test_cancelling_the_websocket_loop_is_not_swallowed_as_an_error():
    """Shutdown relies on CancelledError escaping; catching it would hang stop()."""
    ch = _mattermost(FakeGateway(), allow_all=True)
    forever = asyncio.Event()

    class _Idle:
        """An open socket with nothing to say — what a quiet channel looks like."""

        closed = False

        def ws_connect(self, url):
            return _FakeWS([], block=forever)

        async def close(self):
            self.closed = True

    ch._session = _Idle()
    ch._running = True
    task = asyncio.create_task(ch._ws_loop())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_an_unconfigured_mattermost_channel_starts_no_socket_loop(adopt):
    ch = adopt(MattermostChannel(FakeGateway(), bot_token="", base_url=""))
    await ch.start()
    assert ch.running is True
    assert ch._ws_task is None


@pytest.mark.asyncio
async def test_stopping_mattermost_cancels_the_socket_loop_and_closes_the_session():
    ch = _mattermost(FakeGateway(), allow_all=True)
    session = _FakeSession([_FakeWS([], block=asyncio.Event())])
    ch._session = session
    await ch.start()
    assert ch._ws_task is not None
    await ch.stop()
    assert ch._ws_task is None
    assert ch.running is False
    assert session.closed is True
    await ch.stop()  # idempotent


# --- Signal: locating the binary ---------------------------------------------


def test_a_signal_cli_path_that_is_a_real_file_is_used_as_is(tmp_path):
    binary = tmp_path / "signal-cli"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    ch = SignalChannel(FakeGateway(), cli_path=str(binary))
    assert ch._bin() == str(binary)


def test_a_bare_name_is_looked_up_on_the_path(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    ch = SignalChannel(FakeGateway(), cli_path="signal-cli")
    assert ch._bin() == "/usr/bin/signal-cli"


def test_the_resolved_binary_is_looked_up_only_once(monkeypatch):
    calls: list[str] = []

    def which(name):
        calls.append(name)
        return "/usr/bin/signal-cli"

    monkeypatch.setattr("shutil.which", which)
    ch = SignalChannel(FakeGateway(), cli_path="signal-cli")
    ch._bin()
    ch._bin()
    assert calls == ["signal-cli"]


def test_a_missing_signal_cli_resolves_to_nothing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    ch = SignalChannel(FakeGateway(), cli_path="signal-cli-not-installed")
    assert ch._bin() is None


def test_a_directory_is_not_mistaken_for_the_signal_binary(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    ch = SignalChannel(FakeGateway(), cli_path=str(tmp_path))
    assert ch._bin() is None


# --- Signal: running the binary (never for real) ------------------------------


class _FakeProc:
    """A signal-cli that never existed. Nothing here reaches CreateProcess."""

    def __init__(self, out: bytes = b"", err: bytes = b"", code: int | None = 0, hang=False):
        self._out = out
        self._err = err
        self.returncode = code
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(3600)
        return self._out, self._err

    def kill(self):
        self.killed = True


def _spy_exec(monkeypatch, *procs):
    """Replace subprocess creation; return the list that records each argv."""
    recorded: list[list[str]] = []
    queue = list(procs)

    async def _exec(*cmd, **_kwargs):
        recorded.append(list(cmd))
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    return recorded


def _fake_binary(tmp_path):
    binary = tmp_path / "signal-cli"
    binary.write_text("", encoding="utf-8")
    return str(binary)


@pytest.mark.asyncio
async def test_running_signal_cli_without_the_binary_is_an_error_not_a_launch(monkeypatch):
    recorded = _spy_exec(monkeypatch, _FakeProc())
    monkeypatch.setattr("shutil.which", lambda name: None)
    ch = SignalChannel(FakeGateway(), cli_path="nope", account="+15550100")
    code, out, err = await ch._run("receive")
    assert (code, out, err) == (1, "", "signal-cli not found")
    assert recorded == [], "no process may be spawned when the binary is missing"


@pytest.mark.asyncio
async def test_the_account_is_passed_before_the_subcommand(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    recorded = _spy_exec(monkeypatch, _FakeProc(out=b"ok\n"))
    ch = SignalChannel(FakeGateway(), cli_path=binary, account="+15550100")
    code, out, _err = await ch._run("receive", "-t", "10")
    assert code == 0
    assert out == "ok\n"
    assert recorded == [[binary, "-a", "+15550100", "receive", "-t", "10"]]


@pytest.mark.asyncio
async def test_a_hung_signal_cli_is_killed_rather_than_waited_on_for_ever(monkeypatch, tmp_path):
    proc = _FakeProc(hang=True)
    _spy_exec(monkeypatch, proc)
    ch = SignalChannel(FakeGateway(), cli_path=_fake_binary(tmp_path), account="+1")
    code, out, err = await ch._run("receive", timeout=0.05)
    assert (code, out, err) == (1, "", "timeout")
    assert proc.killed is True


@pytest.mark.asyncio
async def test_undecodable_output_is_replaced_rather_than_raising(monkeypatch, tmp_path):
    _spy_exec(monkeypatch, _FakeProc(out=b"\xff\xfe", err=b"\xff", code=2))
    ch = SignalChannel(FakeGateway(), cli_path=_fake_binary(tmp_path), account="+1")
    code, out, err = await ch._run("receive")
    assert code == 2
    assert "�" in out
    assert "�" in err


@pytest.mark.asyncio
async def test_a_process_with_no_return_code_yet_counts_as_success(monkeypatch, tmp_path):
    _spy_exec(monkeypatch, _FakeProc(code=None))
    ch = SignalChannel(FakeGateway(), cli_path=_fake_binary(tmp_path), account="+1")
    assert (await ch._run("receive"))[0] == 0


# --- Signal: outbound send ----------------------------------------------------


@pytest.mark.asyncio
async def test_signal_send_without_a_binary_fails_loudly(monkeypatch):
    """Unlike the HTTP channels there is no stub mode: False is the honest answer."""
    monkeypatch.setattr("shutil.which", lambda name: None)
    recorded = _spy_exec(monkeypatch, _FakeProc())
    ch = SignalChannel(FakeGateway(), cli_path="nope", account="+15550100")
    assert await ch.send("hi", target="+15550111") is False
    assert recorded == []


@pytest.mark.asyncio
async def test_signal_send_without_an_account_fails(monkeypatch, tmp_path):
    recorded = _spy_exec(monkeypatch, _FakeProc())
    ch = SignalChannel(FakeGateway(), cli_path=_fake_binary(tmp_path), account="")
    assert await ch.send("hi", target="+15550111") is False
    assert recorded == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [None, "", "   "])
async def test_signal_refuses_to_send_without_a_recipient(monkeypatch, tmp_path, target):
    """There is no default recipient; guessing one would text the wrong person."""
    recorded = _spy_exec(monkeypatch, _FakeProc())
    ch = SignalChannel(FakeGateway(), cli_path=_fake_binary(tmp_path), account="+1")
    assert await ch.send("hi", target=target) is False
    assert recorded == []


@pytest.mark.asyncio
async def test_signal_send_passes_the_message_and_the_number(monkeypatch, tmp_path):
    binary = _fake_binary(tmp_path)
    recorded = _spy_exec(monkeypatch, _FakeProc())
    ch = SignalChannel(FakeGateway(), cli_path=binary, account="+15550100")
    assert await ch.send("hello there", target=" +15550111 ") is True
    assert recorded == [[binary, "-a", "+15550100", "send", "-m", "hello there", "+15550111"]]


@pytest.mark.asyncio
async def test_a_nonzero_exit_from_signal_cli_is_a_failed_send(monkeypatch, tmp_path):
    _spy_exec(monkeypatch, _FakeProc(err=b"Unregistered user", code=1))
    ch = SignalChannel(FakeGateway(), cli_path=_fake_binary(tmp_path), account="+1")
    assert await ch.send("hi", target="+2") is False


@pytest.mark.asyncio
async def test_a_signal_send_gets_a_longer_budget_than_a_receive_poll(monkeypatch, tmp_path):
    """signal-cli can spend a minute on the first send to a new recipient."""
    seen: list[float] = []

    async def spy_run(*_args, timeout=60.0):
        seen.append(timeout)
        return 0, "", ""

    ch = SignalChannel(FakeGateway(), cli_path=_fake_binary(tmp_path), account="+1")
    ch._run = spy_run
    await ch.send("hi", target="+2")
    assert seen == [90.0]


@pytest.mark.asyncio
async def test_signal_typing_is_a_deliberate_no_op():
    assert await SignalChannel(FakeGateway()).send_typing("+1") is None


# --- Signal: inbound envelopes ------------------------------------------------


def _envelope(source="+15550111", text="hello", key="source"):
    return {"envelope": {key: source, "dataMessage": {"message": text}}}


@pytest.mark.asyncio
async def test_a_signal_stranger_is_refused_when_no_allowlist_is_set():
    gw = FakeGateway()
    await SignalChannel(gw, allow_from=[])._on_envelope(_envelope())
    assert gw.events == []


@pytest.mark.asyncio
async def test_an_unlisted_signal_number_is_refused():
    gw = FakeGateway()
    await SignalChannel(gw, allow_from=["+15550999"])._on_envelope(_envelope())
    assert gw.events == []


@pytest.mark.asyncio
async def test_a_listed_signal_number_gets_through():
    gw = FakeGateway()
    await SignalChannel(gw, allow_from=["+15550111"])._on_envelope(_envelope())
    assert len(gw.events) == 1


@pytest.mark.asyncio
async def test_signal_allow_all_lets_anyone_through():
    gw = FakeGateway()
    await SignalChannel(gw, allow_all=True)._on_envelope(_envelope(source="+19998887777"))
    assert len(gw.events) == 1


@pytest.mark.asyncio
async def test_a_source_number_field_is_understood_too():
    """Newer signal-cli reports sourceNumber; the older key is source."""
    gw = FakeGateway()
    ch = SignalChannel(gw, allow_from=["+15550111"])
    await ch._on_envelope(_envelope(key="sourceNumber"))
    assert len(gw.events) == 1


@pytest.mark.asyncio
async def test_an_envelope_that_arrives_unwrapped_is_still_read():
    gw = FakeGateway()
    ch = SignalChannel(gw, allow_all=True)
    await ch._on_envelope({"source": "+1", "dataMessage": {"message": "flat"}})
    assert gw.events[0].payload["message"] == "flat"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "envelope",
    [
        {"envelope": {"source": "+1"}},
        {"envelope": {"source": "+1", "dataMessage": {}}},
        {"envelope": {"source": "+1", "dataMessage": {"message": ""}}},
        {"envelope": {"source": "+1", "dataMessage": {"message": "   "}}},
        {"envelope": {"source": "+1", "receiptMessage": {"isDelivery": True}}},
        {"envelope": {"source": "+1", "typingMessage": {"action": "STARTED"}}},
    ],
)
async def test_a_signal_envelope_with_no_text_emits_nothing(envelope):
    """Receipts and typing notices arrive on the same stream as real messages."""
    gw = FakeGateway()
    await SignalChannel(gw, allow_all=True)._on_envelope(envelope)
    assert gw.events == []


@pytest.mark.asyncio
async def test_the_signal_event_carries_the_ids_the_session_is_keyed_on():
    gw = FakeGateway()
    await SignalChannel(gw, allow_all=True)._on_envelope(_envelope(source="+15550111"))
    ev = gw.events[0]
    assert ev.channel is ChannelKind.SIGNAL
    assert ev.session_id == "+15550111"
    assert ev.source_id == "+15550111"
    assert ev.payload["chat_id"] == "+15550111"
    assert ev.payload["user_id"] == "+15550111"
    assert ev.payload["message"] == "hello"


@pytest.mark.asyncio
async def test_an_anonymous_sender_still_lands_in_a_named_session():
    """An empty source would otherwise key the session on "", which collides."""
    gw = FakeGateway()
    ch = SignalChannel(gw, allow_all=True)
    await ch._on_envelope({"envelope": {"dataMessage": {"message": "who am i"}}})
    assert gw.events[0].payload["chat_id"] == "signal"


# --- Signal: the receive loop -------------------------------------------------


@pytest.mark.asyncio
async def test_the_receive_loop_reads_one_json_object_per_line():
    gw = FakeGateway()
    ch = SignalChannel(gw, allow_all=True)
    lines = "\n".join(json.dumps(_envelope(text=t)) for t in ("first", "second", "third"))
    seen: list[tuple] = []

    async def fake_run(*args, timeout=60.0):
        seen.append((args, timeout))
        ch._running = False
        return 0, lines, ""

    ch._run = fake_run
    ch._running = True
    await ch._receive_loop()

    assert [e.payload["message"] for e in gw.events] == ["first", "second", "third"]
    assert seen == [(("receive", "-t", "10", "--json"), 30.0)]


@pytest.mark.asyncio
async def test_one_unparsable_line_does_not_lose_the_rest_of_the_batch():
    gw = FakeGateway()
    ch = SignalChannel(gw, allow_all=True)
    out = "\n".join(["", "not json", json.dumps(_envelope(text="survivor")), "   ", "{broken"])

    async def fake_run(*_args, timeout=60.0):
        ch._running = False
        return 0, out, ""

    ch._run = fake_run
    ch._running = True
    await ch._receive_loop()

    assert [e.payload["message"] for e in gw.events] == ["survivor"]


@pytest.mark.asyncio
async def test_a_failing_receive_still_reads_whatever_it_printed():
    """signal-cli exits non-zero on a partial receive; the messages it printed are real."""
    gw = FakeGateway()
    ch = SignalChannel(gw, allow_all=True)

    async def fake_run(*_args, timeout=60.0):
        ch._running = False
        return 1, json.dumps(_envelope(text="late arrival")), "warning: rate limited"

    ch._run = fake_run
    ch._running = True
    await ch._receive_loop()

    assert [e.payload["message"] for e in gw.events] == ["late arrival"]


@pytest.mark.asyncio
async def test_an_exploding_receive_backs_off_rather_than_killing_the_loop(monkeypatch):
    gw = FakeGateway()
    ch = SignalChannel(gw, allow_all=True)

    async def fake_run(*_args, timeout=60.0):
        raise RuntimeError("signal-cli vanished")

    ch._run = fake_run
    ch._running = True
    slept = _instant_sleep(monkeypatch, stop=lambda: setattr(ch, "_running", False))

    await ch._receive_loop()  # must not propagate

    assert slept == [2.0]


@pytest.mark.asyncio
async def test_cancelling_the_receive_loop_is_not_swallowed_as_an_error():
    ch = SignalChannel(FakeGateway(), allow_all=True)
    blocked = asyncio.Event()

    async def fake_run(*_args, timeout=60.0):
        await blocked.wait()
        return 0, "", ""

    ch._run = fake_run
    ch._running = True
    task = asyncio.create_task(ch._receive_loop())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- Signal: start / stop -----------------------------------------------------


@pytest.mark.asyncio
async def test_signal_without_an_account_starts_no_poll_loop(monkeypatch, tmp_path):
    recorded = _spy_exec(monkeypatch, _FakeProc())
    ch = SignalChannel(FakeGateway(), cli_path=_fake_binary(tmp_path), account="")
    await ch.start()
    assert ch.running is True
    assert ch._poll_task is None
    assert recorded == []
    await ch.stop()


@pytest.mark.asyncio
async def test_signal_without_the_binary_starts_no_poll_loop(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    recorded = _spy_exec(monkeypatch, _FakeProc())
    ch = SignalChannel(FakeGateway(), cli_path="nope", account="+15550100")
    await ch.start()
    assert ch._poll_task is None
    assert recorded == []
    await ch.stop()


@pytest.mark.asyncio
async def test_stopping_signal_cancels_the_poll_loop(tmp_path):
    ch = SignalChannel(FakeGateway(), cli_path=_fake_binary(tmp_path), account="+15550100", home_dir=str(tmp_path))
    started = asyncio.Event()
    blocked = asyncio.Event()

    async def fake_run(*_args, timeout=60.0):
        started.set()
        await blocked.wait()
        return 0, "", ""

    ch._run = fake_run
    await ch.start()
    assert ch._poll_task is not None
    await asyncio.wait_for(started.wait(), timeout=2.0)
    await ch.stop()
    assert ch._poll_task is None
    assert ch.running is False
    await ch.stop()  # idempotent
