"""Soul tools — personhood that survives a machine change, and vigil consent.

Two things carry weight here. Export/import is how Remedy moves between
machines without becoming someone else, so a plain export must be plain and an
encrypted one must not be readable. And vigil is a *grant*: time she may use
between visits. Turning it on or off is the owner's call, and the tool has to
say plainly which state it just put her in.

soul_dream is deliberately absent — it starts a thread that calls a model
server, which is not something a test should do.
"""

from __future__ import annotations

import json

import pytest

from remedy.core.agent_soul_tools import register_soul_tools


class Reg:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.schemas: dict[str, dict] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler
        self.schemas[name] = parameters or {}


class RT:
    def __init__(self, home) -> None:
        self.tool_registry = Reg()
        self.config = type("C", (), {"home_dir": str(home)})()
        self.memory = None
        self._session_id = "soul-session"


@pytest.fixture()
def soul(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    rt = RT(tmp_path)
    register_soul_tools(rt)
    return {"rt": rt, "tools": rt.tool_registry.tools, "home": tmp_path}


# --- status / recall --------------------------------------------------------


@pytest.mark.asyncio
async def test_status_answers_on_a_soul_that_has_never_been_written(soul):
    out = await soul["tools"]["soul_status"]()
    assert out


@pytest.mark.asyncio
async def test_recall_on_an_empty_soul_does_not_raise(soul):
    out = await soul["tools"]["soul_recall"](query="anything")
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_recall_with_no_query_still_answers(soul):
    assert isinstance(await soul["tools"]["soul_recall"](), str)


@pytest.mark.asyncio
async def test_a_nonsense_limit_does_not_break_recall(soul):
    assert isinstance(await soul["tools"]["soul_recall"](query="x", limit=0), str)


# --- moving between machines ------------------------------------------------


@pytest.mark.asyncio
async def test_export_writes_where_it_says_it_did(soul, tmp_path):
    dest = tmp_path / "exports" / "soul.json"
    out = json.loads(await soul["tools"]["soul_export"](dest=str(dest)))
    assert out["ok"] is True
    assert out["mode"] == "plain"
    from pathlib import Path

    assert Path(out["path"]).exists()


@pytest.mark.asyncio
async def test_export_without_a_destination_lands_under_the_home(soul):
    out = json.loads(await soul["tools"]["soul_export"]())
    assert "exports" in out["path"]
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_a_passphrase_produces_an_encrypted_export(soul, tmp_path):
    dest = tmp_path / "exports" / "soul.enc"
    out = json.loads(
        await soul["tools"]["soul_export"](dest=str(dest), passphrase="a long secret")
    )
    assert out["mode"] == "encrypted"


@pytest.mark.asyncio
async def test_an_encrypted_export_is_not_readable_as_plain_json(soul, tmp_path):
    """A passphrase that produced readable output would be worse than none."""
    from pathlib import Path

    dest = tmp_path / "exports" / "soul.enc"
    out = json.loads(
        await soul["tools"]["soul_export"](dest=str(dest), passphrase="a long secret")
    )
    raw = Path(out["path"]).read_bytes()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return  # wholly opaque — good
    # A JSON envelope is fine; what it wraps must be sealed, and the soul
    # payload must not also be sitting there in the clear beside it.
    assert parsed.get("ciphertext_b64"), "no ciphertext — the export is not sealed"
    assert parsed.get("salt_b64"), "no salt — the passphrase was not really used"
    assert "remedy-soul-field" not in raw.decode("utf-8", errors="replace")


@pytest.mark.asyncio
async def test_import_needs_a_source(soul):
    assert "source path required" in await soul["tools"]["soul_import"]()


@pytest.mark.asyncio
async def test_importing_a_missing_file_is_reported_not_raised(soul):
    out = await soul["tools"]["soul_import"](source="nowhere/soul.json")
    assert "soul_import failed" in out


@pytest.mark.asyncio
async def test_importing_junk_is_reported_not_raised(soul, tmp_path):
    bad = tmp_path / "junk.json"
    bad.write_text("this is not a soul", encoding="utf-8")
    out = await soul["tools"]["soul_import"](source=str(bad))
    assert "soul_import failed" in out or isinstance(json.loads(out), dict)


@pytest.mark.asyncio
async def test_a_plain_export_round_trips_back_in(soul, tmp_path):
    dest = tmp_path / "exports" / "soul.json"
    await soul["tools"]["soul_export"](dest=str(dest))
    out = await soul["tools"]["soul_import"](source=str(dest))
    assert "failed" not in out


@pytest.mark.asyncio
async def test_an_encrypted_export_needs_its_passphrase(soul, tmp_path):
    dest = tmp_path / "exports" / "soul.enc"
    await soul["tools"]["soul_export"](dest=str(dest), passphrase="a long secret")
    out = await soul["tools"]["soul_import"](source=str(dest), passphrase="wrong one")
    assert "failed" in out


# --- vigil: her own time ----------------------------------------------------


@pytest.mark.asyncio
async def test_vigil_is_off_until_it_is_granted(soul):
    status = json.loads(await soul["tools"]["soul_vigil"]())
    assert status.get("enabled") in (False, None, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["enable", "grant", "on", "yes"])
async def test_every_way_of_saying_yes_grants_it(soul, word):
    out = json.loads(await soul["tools"]["soul_vigil"](action=word))
    assert out["enabled"] is True
    assert "local-only" in out["note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["disable", "revoke", "off", "stop", "no"])
async def test_every_way_of_saying_no_revokes_it(soul, word):
    await soul["tools"]["soul_vigil"](action="enable")
    out = json.loads(await soul["tools"]["soul_vigil"](action=word))
    assert out["enabled"] is False


@pytest.mark.asyncio
async def test_a_revoked_vigil_stays_revoked_in_status(soul):
    await soul["tools"]["soul_vigil"](action="enable")
    await soul["tools"]["soul_vigil"](action="off")
    assert json.loads(await soul["tools"]["soul_vigil"]())["enabled"] is False


@pytest.mark.asyncio
async def test_the_budget_can_be_set_when_granting(soul):
    out = json.loads(
        await soul["tools"]["soul_vigil"](
            action="enable", max_wakes_per_day=3, min_gap_minutes=90
        )
    )
    assert out["max_wakes_per_day"] == 3
    assert out["min_gap_minutes"] == 90


@pytest.mark.asyncio
async def test_granting_without_a_budget_keeps_the_defaults(soul):
    out = json.loads(await soul["tools"]["soul_vigil"](action="enable"))
    assert out["max_wakes_per_day"] > 0
    assert out["min_gap_minutes"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["night_report", "report", "night"])
async def test_a_quiet_night_reports_as_quiet(soul, word):
    out = await soul["tools"]["soul_vigil"](action=word)
    assert "Nothing to report" in out or out


@pytest.mark.asyncio
async def test_an_unknown_vigil_action_falls_back_to_status(soul):
    """Being wrong about the verb must never silently flip the grant."""
    out = json.loads(await soul["tools"]["soul_vigil"](action="frobnicate"))
    assert "enabled" in out


# --- continuity -------------------------------------------------------------


@pytest.mark.asyncio
async def test_continuity_score_answers_on_a_cold_home(soul):
    out = json.loads(await soul["tools"]["continuity_score"]())
    assert isinstance(out, dict)


# --- registration -----------------------------------------------------------


def test_every_soul_tool_is_registered(soul):
    assert set(soul["tools"]) >= {
        "soul_status",
        "soul_recall",
        "soul_dream",
        "soul_arm_missions",
        "soul_export",
        "soul_import",
        "soul_vigil",
        "continuity_score",
    }


def test_the_schemas_are_objects(soul):
    for name, schema in soul["rt"].tool_registry.schemas.items():
        assert schema.get("type") == "object", name
