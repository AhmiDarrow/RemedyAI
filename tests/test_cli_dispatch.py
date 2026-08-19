"""`remedy <command>` reaches the handler that command is named after.

This module is nothing but a dispatch table, which is exactly why it is worth
a test: a subcommand wired to the neighbouring handler still parses, still
runs, and still exits 0 — it just does the wrong thing, quietly, forever. The
only way that shows up is by asserting the mapping.

Every handler is intercepted, so no command actually runs.
"""

from __future__ import annotations

import importlib

import pytest

# The cli package re-exports a `main` *function*, which shadows the
# submodule of the same name for both `from … import main` and
# `import …cli.main as M`. import_module returns the module itself.
M = importlib.import_module("remedy.interfaces.cli.main")

#: (argv, the handler that argv must reach)
COMMANDS = [
    (["memory", "list"], "_cmd_memory"),
    (["user", "show"], "_cmd_user"),
    (["session", "start"], "_cmd_session"),
    (["skill", "list"], "_cmd_skill"),
    (["tool", "list"], "_cmd_tool"),
    (["exec", "echo hi"], "_cmd_exec"),
    (["learn", "history"], "_cmd_learn"),
    (["handoff", "list"], "_cmd_handoff"),
    (["migrate", "hermes", "/tmp/x"], "_cmd_migrate"),
    (["gateway", "status"], "main_gateway"),
    (["config", "show"], "_cmd_config"),
    (["settings"], "_cmd_settings"),
    (["computer"], "_cmd_computer"),
    (["auth", "status"], "_cmd_auth"),
    (["chat"], "_cmd_chat"),
    (["serve"], "_cmd_serve"),
    (["desktop", "status"], "_cmd_desktop"),
    (["setup"], "run_wizard"),
    (["update"], "run_update"),
    (["uninstall"], "run_uninstall"),
]

HANDLERS = sorted({h for _, h in COMMANDS})


@pytest.fixture()
def dispatch(monkeypatch, tmp_path):
    """Replace every handler with a recorder and neutralise asyncio.run."""
    called: list[str] = []
    monkeypatch.setattr(M, "_get_db_path", lambda home: tmp_path / "memory.db")

    for name in HANDLERS:
        monkeypatch.setattr(
            M, name, (lambda n: lambda *a, **kw: called.append(n))(name)
        )
    # Async handlers are wrapped in asyncio.run; the recorders are not
    # coroutines, so run has to become a plain call.
    monkeypatch.setattr(M.asyncio, "run", lambda x: x)
    return called


@pytest.mark.parametrize(("argv", "handler"), COMMANDS, ids=[c[0][0] for c in COMMANDS])
def test_a_command_reaches_its_own_handler(dispatch, argv, handler):
    M.main(argv)
    assert dispatch == [handler]


def test_every_handler_in_the_table_actually_exists():
    """Guards the table itself against a rename in the modules it names."""
    for name in HANDLERS:
        assert hasattr(M, name), name


def test_no_two_commands_share_a_handler_by_accident():
    seen: dict[str, list] = {}
    for argv, handler in COMMANDS:
        seen.setdefault(handler, []).append(argv[0])
    duplicates = {h: cmds for h, cmds in seen.items() if len(cmds) > 1}
    assert not duplicates, f"commands sharing a handler: {duplicates}"


# --- the cases that are not a handler ---------------------------------------


def test_no_command_at_all_prints_help_and_exits_nonzero(dispatch, capsys):
    with pytest.raises(SystemExit) as exc:
        M.main([])
    assert exc.value.code == 2
    assert "usage" in capsys.readouterr().out.lower()


def test_an_unsafe_home_is_refused_before_any_command_runs(monkeypatch, capsys):
    from remedy.interfaces.cli.util import UnsafeHomeError

    def boom(_home):
        raise UnsafeHomeError("refusing to use a drive root as a home")

    monkeypatch.setattr(M, "_get_db_path", boom)
    with pytest.raises(SystemExit) as exc:
        M.main(["memory", "list"])
    assert exc.value.code == 2
    assert "refusing" in capsys.readouterr().out


def test_mcp_serve_runs_the_stdio_server(dispatch, monkeypatch):
    monkeypatch.setattr(
        "remedy.tools.mcp_server.run_stdio_server", lambda: 0, raising=False
    )
    with pytest.raises(SystemExit) as exc:
        M.main(["mcp", "serve"])
    assert exc.value.code == 0


def test_mcp_without_a_subcommand_says_how_to_use_it(dispatch, capsys):
    """argparse gets there first: mcp_cmd is required, so the usage line comes
    from the parser rather than the module's own fallback branch."""
    with pytest.raises(SystemExit) as exc:
        M.main(["mcp"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "remedy mcp" in err
    assert "serve" in err


# --- the arguments that reach the handler ------------------------------------


def test_setup_passes_its_skip_flags_through(monkeypatch, tmp_path):
    seen: list[dict] = []
    monkeypatch.setattr(M, "_get_db_path", lambda home: tmp_path / "memory.db")
    monkeypatch.setattr(M, "run_wizard", lambda **kw: seen.append(kw))
    M.main(["setup", "--quick", "--skip-providers"])
    assert seen[0]["quick"] is True
    assert seen[0]["skip_providers"] is True
    assert seen[0]["skip_messaging"] is False


def test_update_passes_check_only_through(monkeypatch, tmp_path):
    seen: list[dict] = []
    monkeypatch.setattr(M, "_get_db_path", lambda home: tmp_path / "memory.db")
    monkeypatch.setattr(M, "run_update", lambda **kw: seen.append(kw))
    M.main(["update", "--check"])
    assert seen[0] == {"check_only": True}


def test_uninstall_defaults_to_not_purging(monkeypatch, tmp_path):
    """The destructive flag must never be on unless it was asked for."""
    seen: list[dict] = []
    monkeypatch.setattr(M, "_get_db_path", lambda home: tmp_path / "memory.db")
    monkeypatch.setattr(M, "run_uninstall", lambda **kw: seen.append(kw))
    M.main(["uninstall"])
    assert seen[0]["purge"] is False


def test_uninstall_passes_purge_and_dry_run_through(monkeypatch, tmp_path):
    seen: list[dict] = []
    monkeypatch.setattr(M, "_get_db_path", lambda home: tmp_path / "memory.db")
    monkeypatch.setattr(M, "run_uninstall", lambda **kw: seen.append(kw))
    M.main(["uninstall", "--purge", "--dry-run"])
    assert seen[0]["purge"] is True
    assert seen[0]["dry_run"] is True


def test_the_database_path_comes_from_the_home_argument(monkeypatch, tmp_path):
    seen: list = []
    monkeypatch.setattr(M, "_get_db_path", lambda home: seen.append(home) or tmp_path)
    monkeypatch.setattr(M, "_cmd_memory", lambda *a, **kw: None)
    monkeypatch.setattr(M.asyncio, "run", lambda x: x)
    M.main(["--home", str(tmp_path / "elsewhere"), "memory", "list"])
    assert str(tmp_path / "elsewhere") in str(seen[0])
