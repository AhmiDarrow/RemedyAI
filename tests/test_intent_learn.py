"""Per-partner intent learner: regex floor, arm-only override, local weights."""

from __future__ import annotations

from pathlib import Path

import remedy.core.intent_learn as il
from remedy.core.intent_learn import (
    consult,
    record_confirmed_work,
    record_tools_declined,
    snapshot,
)

MSG = "spin the flux capacitor for tonight's render"


def _fresh(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "remedy-home"
    monkeypatch.setattr(il, "_models", {})
    return home


def test_cold_learner_defers_to_regex_verdict(monkeypatch, tmp_path: Path):
    home = _fresh(monkeypatch, tmp_path)
    assert consult(MSG, regex_verdict=True, home=home) is True
    assert consult(MSG, regex_verdict=False, home=home) is False


def test_confirmed_work_family_arms_after_enough_outcomes(monkeypatch, tmp_path: Path):
    home = _fresh(monkeypatch, tmp_path)
    for _ in range(80):
        record_confirmed_work(MSG, home=home)
    assert snapshot(home)["outcome_samples"] >= il._MIN_OUTCOMES
    # Same phrase family the regexes miss now arms.
    assert consult(MSG, regex_verdict=False, home=home) is True
    # An unrelated cold phrase still follows the regexes.
    assert consult("what a lovely quiet evening", regex_verdict=False, home=home) is False


def test_never_disarms_a_regex_work_verdict(monkeypatch, tmp_path: Path):
    home = _fresh(monkeypatch, tmp_path)
    for _ in range(80):
        record_tools_declined(MSG, home=home)
    assert consult(MSG, regex_verdict=True, home=home) is True


def test_kill_switch_env(monkeypatch, tmp_path: Path):
    home = _fresh(monkeypatch, tmp_path)
    for _ in range(80):
        record_confirmed_work(MSG, home=home)
    monkeypatch.setenv("REMEDY_INTENT_LEARN", "0")
    assert consult(MSG, regex_verdict=False, home=home) is False
    monkeypatch.delenv("REMEDY_INTENT_LEARN")
    assert consult(MSG, regex_verdict=False, home=home) is True


def test_weights_persist_and_reload(monkeypatch, tmp_path: Path):
    home = _fresh(monkeypatch, tmp_path)
    for _ in range(80):
        record_confirmed_work(MSG, home=home)
    assert (home / "intent" / "model.json").is_file()
    # Fresh in-memory cache — the reloaded model keeps its confidence.
    monkeypatch.setattr(il, "_models", {})
    assert consult(MSG, regex_verdict=False, home=home) is True
    snap = snapshot(home)
    assert snap["counts"]["work"] >= 50
    assert snap["n_weights"] > 0


def test_ambiguous_turns_get_readonly_peek_pack():
    from remedy.core.react_turn import AMBIGUOUS_READONLY_TOOLS, resolve_tools

    def tool(name: str) -> dict:
        return {"type": "function", "function": {"name": name, "parameters": {}}}

    all_tools = [tool("file_read"), tool("memory_search"), tool("host_run"), tool("file_write")]
    d = resolve_tools(message="not sure where we landed on that", all_tools=all_tools)
    assert d.reason in ("ask_first", "no_work_request")
    assert d.run_until_done is False
    names = {((t.get("function") or {}).get("name") or "") for t in (d.tools or [])}
    assert names, "ambiguous turn should keep a read-only peek pack"
    assert names <= AMBIGUOUS_READONLY_TOOLS
    assert "host_run" not in names and "file_write" not in names

    # Pure chat still strips everything — no peek pack on "hi".
    d2 = resolve_tools(message="hi", all_tools=all_tools)
    assert d2.tools is None
