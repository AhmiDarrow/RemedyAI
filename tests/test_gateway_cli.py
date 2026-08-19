"""`remedy gateway …` — start, status, channels.

Nothing here starts a real gateway; the run is intercepted. What is checked is
the dispatch and the two things that protect the owner:

* A messenger token passed as a command-line argument is visible in the process
  list to every other user on the machine. It still works — refusing would take
  away a way of running Remedy — but it says so.
* An unsafe home is refused before anything is opened under it.
"""

from __future__ import annotations

import argparse
import asyncio

import pytest

from remedy.gateway import cli as G


def args(**kw):
    kw.setdefault("home", None)
    return argparse.Namespace(**kw)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.cli.util.resolve_cli_home", lambda h: tmp_path
    )
    return tmp_path


@pytest.fixture()
def no_run(monkeypatch):
    """Catch whatever would have been run, without running it."""
    ran: list = []
    monkeypatch.setattr(asyncio, "run", lambda coro: ran.append(coro) or coro.close())
    return ran


# --- listing channels --------------------------------------------------------


def test_channels_lists_the_local_surfaces(home, capsys):
    G.main_gateway(args(gateway_cmd="channels"))
    out = capsys.readouterr().out
    for surface in ("cli", "web", "api"):
        assert surface in out


def test_channels_lists_the_messengers(home, capsys):
    G.main_gateway(args(gateway_cmd="channels"))
    out = capsys.readouterr().out
    assert "telegram" in out
    assert "Messengers" in out


def test_channels_says_which_way_each_messenger_talks(home, capsys):
    """The direction column used to vanish entirely.

    ``[in/out]`` is rich markup unless it is escaped, so rich swallowed it as a
    style tag and printed nothing — the column only ever appeared for a
    messenger supporting neither direction, which is the opposite of its point.
    """
    G.main_gateway(args(gateway_cmd="channels"))
    out = capsys.readouterr().out
    assert "[in/out]" in out


# --- an unsafe home ----------------------------------------------------------


def test_an_unsafe_home_is_refused_before_anything_is_opened(monkeypatch, capsys):
    from remedy.interfaces.cli.util import UnsafeHomeError

    def boom(_h):
        raise UnsafeHomeError("refusing to use C:\\ as a home")

    monkeypatch.setattr("remedy.interfaces.cli.util.resolve_cli_home", boom)
    with pytest.raises(SystemExit) as exc:
        G.main_gateway(args(gateway_cmd="status"))
    assert exc.value.code == 2
    assert "refusing" in capsys.readouterr().out


# --- starting ----------------------------------------------------------------


def test_start_runs_the_gateway(home, no_run):
    G.main_gateway(args(gateway_cmd="start"))
    assert len(no_run) == 1


def test_a_token_on_the_command_line_is_flagged(home, no_run, capsys):
    """It is visible in the process list to anyone else on the machine."""
    G.main_gateway(args(gateway_cmd="start", telegram_token="secret-token"))
    out = capsys.readouterr().out
    assert "process lists" in out
    assert "TELEGRAM_BOT_TOKEN" in out


def test_the_warning_does_not_echo_the_token(home, no_run, capsys):
    G.main_gateway(args(gateway_cmd="start", telegram_token="secret-token"))
    assert "secret-token" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "field", ["telegram_token", "discord_token", "slack_token"]
)
def test_every_token_argument_is_flagged(home, no_run, capsys, field):
    G.main_gateway(args(gateway_cmd="start", **{field: "abc"}))
    assert "process lists" in capsys.readouterr().out


def test_tokens_from_the_environment_are_not_flagged(home, no_run, capsys, monkeypatch):
    """The recommended way must not nag."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-env")
    G.main_gateway(args(gateway_cmd="start"))
    assert "process lists" not in capsys.readouterr().out


def test_starting_with_no_tokens_at_all_is_quiet(home, no_run, capsys, monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    G.main_gateway(args(gateway_cmd="start"))
    assert "process lists" not in capsys.readouterr().out


# --- status ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_reports_on_a_database_that_does_not_exist_yet(tmp_path, capsys):
    """A fresh install must get a status table, not a stack trace."""
    await G.gateway_status(tmp_path / "memory.db")
    out = capsys.readouterr().out
    assert "Gateway Status" in out


@pytest.mark.asyncio
async def test_status_counts_sessions(tmp_path, capsys):
    from remedy.memory.store import MemoryStore
    from remedy.models import ChatSession

    db = tmp_path / "memory.db"
    async with MemoryStore(db) as store:
        await store.create_chat_session(ChatSession(title="one"))
        await store.create_chat_session(ChatSession(title="two"))
    await G.gateway_status(db)
    out = capsys.readouterr().out
    assert "sessions" in out


@pytest.mark.asyncio
async def test_a_broken_store_is_reported_in_the_table_not_raised(tmp_path, capsys, monkeypatch):
    async def boom(self, limit=1000):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(
        "remedy.memory.store.MemoryStore.list_chat_sessions", boom, raising=False
    )
    await G.gateway_status(tmp_path / "memory.db")
    assert "error" in capsys.readouterr().out.lower()


# --- serve -------------------------------------------------------------------


def test_serve_hands_off_to_the_same_path_as_remedy_serve(home, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        "remedy.interfaces.cli.cmd_runtime._cmd_serve", lambda a: seen.append(a)
    )
    G.main_gateway(args(gateway_cmd="serve"))
    assert len(seen) == 1


def test_an_unknown_gateway_command_does_nothing_rather_than_crashing(home, no_run):
    G.main_gateway(args(gateway_cmd="frobnicate"))
    assert no_run == []
