"""Every slash command the owner can type must come back with an answer.

913 lines and no test until now. ``/security-status`` and both its aliases
raised UnboundLocalError: ``load_config`` was imported inside the ``/plan``
branch, which makes it a local of the *whole* function, so every other branch
that used it referred to a name Python considered unbound.
"""

from __future__ import annotations

import pytest

from remedy.interfaces.slash_commands import _BUILTIN_COMMANDS, handle_slash_command


def _every_name() -> list[str]:
    out: list[str] = []
    for c in _BUILTIN_COMMANDS:
        out.append(c["name"])
        for alias in c.get("aliases") or []:
            out.append(alias if alias.startswith("/") else f"/{alias}")
    return out


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))


def test_there_are_commands_to_drive():
    assert len(_every_name()) >= 30


@pytest.mark.parametrize("name", _every_name())
@pytest.mark.asyncio
async def test_every_command_answers_without_a_runtime(name):
    """The desktop can invoke any of these before a runtime exists — during
    boot, or with no provider connected. None may raise."""
    result = await handle_slash_command(name, "sess-1", None, None)
    assert isinstance(result, dict), f"{name} returned {type(result).__name__}"


@pytest.mark.parametrize("name", ["/security-status", "/security", "/secstatus"])
@pytest.mark.asyncio
async def test_the_security_report_actually_renders(name):
    """It raised on every invocation, so nobody had ever seen this output."""
    result = await handle_slash_command(name, "sess-1", None, None)
    text = str(result.get("text") or "")
    assert len(text) > 80, "the report came back empty"
    assert "pproval" in text, "the approval mode is missing from the report"


@pytest.mark.asyncio
async def test_help_lists_every_advertised_command():
    result = await handle_slash_command("/help", "sess-1", None, None)
    text = str(result.get("text") or "")
    for c in _BUILTIN_COMMANDS:
        assert c["name"] in text, f"{c['name']} is advertised but absent from /help"


@pytest.mark.asyncio
async def test_an_unknown_command_is_refused_not_crashed():
    result = await handle_slash_command("/definitely-not-a-command", "s", None, None)
    assert isinstance(result, dict)


@pytest.mark.parametrize("junk", ["/", "//", "   ", "/HELP", "/Help  "])
@pytest.mark.asyncio
async def test_odd_input_never_raises(junk):
    assert isinstance(await handle_slash_command(junk, "s", None, None), dict)


@pytest.mark.asyncio
async def test_a_missing_session_id_is_tolerated():
    for name in ("/help", "/security-status", "/new"):
        assert isinstance(await handle_slash_command(name, None, None, None), dict)
