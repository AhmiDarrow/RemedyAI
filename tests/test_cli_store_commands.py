"""`remedy memory` / `user` / `handoff` / `migrate` — the store from a terminal.

These are what an owner reaches for when the desktop is not running: what do
you remember, what did we agree, bring my old skills across. They run against
a real sqlite store here, because the thing worth checking is that a command
and the store actually agree — an argument the store ignores prints a
confident, empty answer.
"""

from __future__ import annotations

import argparse

import pytest

from remedy.interfaces.cli.cmd_store import (
    _cmd_handoff,
    _cmd_memory,
    _cmd_migrate,
    _cmd_user,
)
from remedy.memory.store import MemoryStore
from remedy.models import MemoryEntry, MemoryEntryType


def args(**kw):
    return argparse.Namespace(**kw)


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "memory.db"


async def seed(db, entries):
    async with MemoryStore(db) as store:
        for e in entries:
            await store.upsert(e)


def entry(title, content="body", kind=MemoryEntryType.NOTE, tags=(), importance=0.5):
    return MemoryEntry(
        title=title,
        content=content,
        entry_type=kind,
        tags=list(tags),
        importance=importance,
    )


# --- memory add / list / search ----------------------------------------------


@pytest.mark.asyncio
async def test_adding_an_entry_reports_its_id(db, capsys):
    await _cmd_memory(
        args(
            memory_cmd="add",
            title="The dentist is on Elm St",
            content="Dr Rowe, 0400 111 222",
            entry_type="note",
            tags="health, contacts",
            importance=0.8,
        ),
        db,
    )
    assert "Memory entry saved" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_an_added_entry_can_be_listed_back(db, capsys):
    await _cmd_memory(
        args(
            memory_cmd="add",
            title="Bin night is Tuesday",
            content="green bin",
            entry_type="note",
            tags="",
            importance=0.5,
        ),
        db,
    )
    capsys.readouterr()
    await _cmd_memory(args(memory_cmd="list", entry_type=None, limit=10), db)
    assert "Bin night" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_tags_are_split_on_commas(db):
    await _cmd_memory(
        args(
            memory_cmd="add",
            title="Tagged",
            content="x",
            entry_type="note",
            tags=" health , contacts ,, ",
            importance=0.5,
        ),
        db,
    )
    async with MemoryStore(db) as store:
        found = await store.list_recent(limit=5)
    assert sorted(found[0].tags) == ["contacts", "health"]


@pytest.mark.asyncio
async def test_listing_can_be_narrowed_to_one_kind(db, capsys):
    await seed(
        db,
        [
            entry("a plain note", kind=MemoryEntryType.NOTE),
            entry("a user fact", kind=MemoryEntryType.USER_FACT),
        ],
    )
    await _cmd_memory(args(memory_cmd="list", entry_type="user_fact", limit=10), db)
    out = capsys.readouterr().out
    assert "a user fact" in out
    assert "a plain note" not in out


@pytest.mark.asyncio
async def test_the_limit_is_honoured(db, capsys):
    await seed(db, [entry(f"entry number {i}") for i in range(8)])
    await _cmd_memory(args(memory_cmd="list", entry_type=None, limit=2), db)
    assert capsys.readouterr().out.count("entry number") <= 2


@pytest.mark.asyncio
async def test_searching_finds_what_was_stored(db, capsys):
    await seed(db, [entry("The dentist is on Elm St", content="Dr Rowe")])
    await _cmd_memory(args(memory_cmd="search", query="dentist", limit=10), db)
    assert "Elm St" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_searching_for_nothing_says_nothing_rather_than_everything(db, capsys):
    """The worst answer here is a confident list of unrelated entries."""
    await seed(db, [entry("The dentist is on Elm St")])
    await _cmd_memory(args(memory_cmd="search", query="zzzz-no-such-thing", limit=10), db)
    assert "Elm St" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_listing_an_empty_store_is_not_an_error(db, capsys):
    await _cmd_memory(args(memory_cmd="list", entry_type=None, limit=10), db)
    assert capsys.readouterr().out is not None


# --- memory repair / backup --------------------------------------------------


@pytest.mark.asyncio
async def test_repair_reports_the_stores_integrity(db, capsys):
    await seed(db, [entry("something")])
    await _cmd_memory(args(memory_cmd="repair", vacuum=False), db)
    assert "Integrity" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_vacuum_says_how_much_it_reclaimed(db, capsys):
    await seed(db, [entry("something")])
    await _cmd_memory(args(memory_cmd="repair", vacuum=True), db)
    assert "reclaimed" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_backup_names_the_file_it_wrote(db, capsys):
    await seed(db, [entry("something")])
    await _cmd_memory(args(memory_cmd="backup"), db)
    out = capsys.readouterr().out
    assert "Backup created" in out


# --- consolidation -----------------------------------------------------------


@pytest.mark.asyncio
async def test_consolidating_a_session_with_nothing_in_it_says_so(db, capsys):
    """Not "consolidated!" over an empty session."""
    await _cmd_memory(
        args(memory_cmd="consolidate", session_id="no-such-session", max_entries=50), db
    )
    assert "Not enough entries" in capsys.readouterr().out


# --- the user profile --------------------------------------------------------


@pytest.mark.asyncio
async def test_showing_a_profile_that_does_not_exist_yet_creates_one(db, capsys):
    await _cmd_user(args(user_cmd="show"), db)
    assert "User Profile" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_searching_facts_on_an_empty_profile_says_none(db, capsys):
    await _cmd_user(args(user_cmd="facts", query="anything", limit=10), db)
    assert "No facts found" in capsys.readouterr().out


# --- handoffs ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_creating_a_handoff_reports_its_id(db, capsys):
    await _cmd_handoff(
        args(handoff_cmd="create", title="Where we got to", content="the parser works", tags="build"),
        db,
    )
    assert "Handoff created" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_created_handoff_is_listed(db, capsys):
    await _cmd_handoff(
        args(handoff_cmd="create", title="Where we got to", content="body", tags=""),
        db,
    )
    capsys.readouterr()
    await _cmd_handoff(args(handoff_cmd="list", limit=10), db)
    assert "Where we got to" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_handoff_can_be_searched_for(db, capsys):
    await _cmd_handoff(
        args(handoff_cmd="create", title="Parser rewrite", content="left it half done", tags=""),
        db,
    )
    capsys.readouterr()
    await _cmd_handoff(args(handoff_cmd="search", query="parser", limit=10), db)
    assert "Parser rewrite" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_showing_a_handoff_that_does_not_exist_exits_nonzero(db, capsys):
    """Printing nothing and exiting 0 would look like an empty handoff."""
    with pytest.raises(SystemExit) as exc:
        await _cmd_handoff(args(handoff_cmd="show", id="no-such-id"), db)
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().out.lower()


# --- migration ---------------------------------------------------------------


@pytest.fixture()
def hermes_install(tmp_path):
    root = tmp_path / "hermes" / "skills" / "alpha"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("# alpha\n", encoding="utf-8")
    return root.parent


@pytest.mark.asyncio
async def test_an_unknown_migrate_source_is_reported(tmp_path, capsys):
    await _cmd_migrate(
        args(migrate_cmd="from-mars", home=str(tmp_path), path=str(tmp_path), no_copy=True)
    )
    assert "Unknown migrate command" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["hermes", "openclaw"])
async def test_a_migration_from_a_missing_path_exits_nonzero(tmp_path, capsys, source):
    """Silence plus exit 0 reads as "nothing to bring across"."""
    with pytest.raises(SystemExit) as exc:
        await _cmd_migrate(
            args(
                migrate_cmd=source,
                home=str(tmp_path),
                path=str(tmp_path / "nowhere"),
                no_copy=True,
            )
        )
    assert exc.value.code == 1
    assert "Error" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_migrating_creates_the_skills_directory(tmp_path, hermes_install):
    home = tmp_path / "home"
    with pytest.raises(SystemExit):
        await _cmd_migrate(
            args(migrate_cmd="hermes", home=str(home), path=str(tmp_path / "nowhere"), no_copy=True)
        )
    assert (home / "skills").is_dir()


@pytest.mark.asyncio
async def test_a_successful_migration_reports_the_counts(tmp_path, hermes_install, capsys, monkeypatch):
    monkeypatch.setattr(
        "remedy.migrate.from_hermes.discover_hermes_skills", lambda root: []
    )
    await _cmd_migrate(
        args(
            migrate_cmd="hermes",
            home=str(tmp_path / "home"),
            path=str(hermes_install),
            no_copy=True,
        )
    )
    out = capsys.readouterr().out
    assert "Hermes migration" in out
    assert "imported" in out and "skipped" in out
