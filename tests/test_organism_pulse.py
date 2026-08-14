"""Organism pulse — living partner organs produce real inject text."""

from __future__ import annotations

from pathlib import Path

from remedy.core.metabolism.organism import (
    forge_pulse,
    immune_pulse,
    organism_pulse_block,
)
from remedy.core.metabolism.turn import begin_turn_metabolism
from remedy.memory.soul.field import SoulField, save_soul_field
from remedy.memory.soul.update import update_soul_after_turn


def test_organism_pulse_after_soul_update(tmp_path: Path) -> None:
    home = tmp_path / "h"
    home.mkdir()
    update_soul_after_turn(
        user_text="implement the login form and fix the bug",
        assistant_text="I'll open the files and write the form.",
        session_id="s1",
        provider="xai",
        model="grok-4",
        home=home,
    )
    block = organism_pulse_block(
        session_id="s1",
        tier=2,
        home=home,
        user_text="implement the login form and fix the bug",
        project_path=str(tmp_path / "proj"),
        max_chars=1200,
    )
    assert block
    assert "Organism" in block or "alive" in block or "Forge" in block
    assert "rapport" in block or "Forge" in block


def test_forge_pulse_on_build_intent() -> None:
    class _R:
        _llm_provider = "xai"
        _llm_model = "grok-4"
        _llm_base_url = ""

    out = forge_pulse(
        user_text="build a calculator and ship it",
        tier=2,
        runtime=_R(),
        session_id="s",
    )
    assert out
    assert "Forge" in out


def test_immune_pulse_when_verify_flagged() -> None:
    from remedy.core.metabolism.governor import get_governor, reset_governor

    reset_governor("imm1")
    g = get_governor("imm1")
    g.observe_and_decide(
        quality={"stuck_rate": 0.2, "max_tool_fail_streak": 3, "turns": 4},
        metabolism={"evidence_units": 1, "decision_units": 1, "waste_batch_rate": 0.1},
        tier=2,
    )
    out = immune_pulse(tier=2, session_id="imm1", gov_actions=g.last_actions)
    assert out
    assert "Immune" in out


def test_organism_pulse_includes_life_and_cas(tmp_path: Path) -> None:
    from remedy.memory.cas import configure_cas
    from remedy.memory.life_drive import take_step
    from remedy.memory.life_goals import LifeGoalStore
    from remedy.memory.middleman import reset_middleman_state

    home = tmp_path / "org"
    home.mkdir()
    reset_middleman_state()
    configure_cas(home)
    LifeGoalStore(home).add("Finish the novel", next_action="Outline chapter 3")
    take_step(home)
    from remedy.core.metabolism.organism import collect_vitals, persist_vitals

    persist_vitals(collect_vitals(home), home)
    block = organism_pulse_block(
        session_id="life-org",
        tier=1,
        home=home,
        max_chars=1200,
    )
    assert "Life: Finish the novel" in block
    assert "Outline" in block or "Last I did" in block
    assert "Memory:" in block
    configure_cas(None)
    reset_middleman_state()


def test_pulse_recalls_cas_fact_for_query(tmp_path: Path) -> None:
    from remedy.core.metabolism.organism import organism_recall_line
    from remedy.memory.cas import configure_cas
    from remedy.memory.middleman import get_session_middleman, reset_middleman_state

    home = tmp_path / "recall"
    home.mkdir()
    reset_middleman_state()
    configure_cas(home)
    get_session_middleman("r1").put(
        "decided the novel outline lives in Documents/Remedy Life",
        kind="fact",
        session_id="r1",
    )
    line = organism_recall_line(home, "where is the novel outline")
    assert line.startswith("Recalled:")
    assert "outline" in line.lower()
    block = organism_pulse_block(
        session_id="r1",
        tier=1,
        home=home,
        user_text="where is the novel outline I decided on",
        max_chars=900,
    )
    assert "Recalled:" in block
    configure_cas(None)
    reset_middleman_state()


def test_ingest_residue_and_heartbeat(tmp_path: Path) -> None:
    from remedy.core.metabolism.organism import ingest_turn_residue, organism_heartbeat
    from remedy.core.metabolism.time_crystal import get_time_crystal, reset_time_crystal
    from remedy.memory.cas import configure_cas
    from remedy.memory.life_goals import LifeGoalStore
    from remedy.memory.middleman import reset_middleman_state

    home = tmp_path / "beat"
    home.mkdir()
    reset_middleman_state()
    reset_time_crystal("life")
    configure_cas(home)
    key = ingest_turn_residue(
        home=home,
        session_id="life",
        user_text="I want to finish the novel this year",
        assistant_text="Decided the next move is a one-page outline.",
    )
    assert key
    LifeGoalStore(home).add("Finish the novel", next_action="Draft a one-page outline")
    beat = organism_heartbeat(home, session_id="life")
    assert beat["recalled"] >= 1
    crystal = get_time_crystal("life")
    texts = " ".join(f.text for f in crystal.facts)
    assert "outline" in texts.lower() or "novel" in texts.lower()
    # chatter does not persist
    assert (
        ingest_turn_residue(
            home=home,
            session_id="life",
            user_text="ok thanks",
            assistant_text="You're welcome.",
        )
        == ""
    )
    configure_cas(None)
    reset_middleman_state()


def test_organism_cycle_writes_vitals(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from remedy.core.metabolism.l0 import try_l0_system_reply
    from remedy.core.metabolism.organism import (
        format_vitals_markdown,
        load_vitals,
        organism_cycle,
    )
    from remedy.core.metabolism.tier import TurnTier, classify_turn_tier
    from remedy.memory.cas import configure_cas
    from remedy.memory.life_goals import LifeGoalStore
    from remedy.memory.middleman import reset_middleman_state

    home = tmp_path / "body"
    home.mkdir()
    reset_middleman_state()
    configure_cas(home)
    LifeGoalStore(home).add("Finish the novel", next_action="Outline chapter 3")
    out = organism_cycle(home, session_id="life")
    assert (home / "organism.json").is_file()
    vitals = load_vitals(home)
    assert vitals.get("alive") is True
    assert vitals.get("life_title") == "Finish the novel"
    assert vitals.get("next_action")
    md = format_vitals_markdown(vitals)
    assert "alive" in md.lower()
    assert "Finish the novel" in md
    assert classify_turn_tier("how are you") == TurnTier.L0_INSTANT
    assert classify_turn_tier("are you alive?") == TurnTier.L0_INSTANT
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=str(home)))
    reply = try_l0_system_reply(rt, "how are you", preclassified=True)
    assert reply and "alive" in reply.lower()
    assert out.get("vitals")
    configure_cas(None)
    reset_middleman_state()


def test_second_cycle_skips_life_resense(tmp_path: Path, monkeypatch) -> None:
    from remedy.core.metabolism import organism as org
    from remedy.core.metabolism.organism import organism_cycle, persist_vitals

    home = tmp_path / "skip"
    home.mkdir()
    persist_vitals(
        {
            "ts": 1,
            "alive": True,
            "life_title": "Keep this title",
            "next_action": "Stay put",
            "last_did": "Already did",
            "open_count": 1,
            "stalled": False,
            "last_cycle_at": 1e18,
            "mood": "calm",
            "who": "Remedy",
            "label": "Calm",
            "last_drive_at": 1e18,
            "last_pulse_at": 1e18,
            "last_heartbeat_at": 1e18,
            "cas_count": 0,
        },
        home,
    )
    monkeypatch.setattr(org, "organism_heartbeat", lambda *a, **k: {"recalled": 0})
    monkeypatch.setattr("remedy.memory.life_drive.drive_due", lambda *a, **k: False)
    monkeypatch.setattr("remedy.memory.life_goals.pulse_due", lambda *a, **k: False)

    def _boom(*_a, **_k):
        raise AssertionError("life store should not be opened")

    monkeypatch.setattr("remedy.memory.life_goals.LifeGoalStore", _boom)
    monkeypatch.setattr(
        "remedy.memory.soul.field.load_soul_field",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("soul should not load")),
    )
    out = organism_cycle(home, session_id="life")
    assert out["vitals"]["life_title"] == "Keep this title"
    assert out["vitals"]["next_action"] == "Stay put"
    assert out["vitals"]["mood"] == "calm"


def test_recall_prefers_session_ram(tmp_path: Path, monkeypatch) -> None:
    from remedy.core.metabolism.organism import organism_recall_line, persist_vitals
    from remedy.memory.cas import configure_cas
    from remedy.memory.middleman import get_session_middleman, reset_middleman_state

    home = tmp_path / "ram"
    home.mkdir()
    reset_middleman_state()
    configure_cas(home)
    persist_vitals({"ts": 1, "alive": True, "cas_count": 3, "cas_durable": 1}, home)
    get_session_middleman("ram1").put(
        "decided the piano exam is in March",
        kind="fact",
        session_id="ram1",
    )

    def _boom(*_a, **_k):
        raise AssertionError("CAS FTS should not run when RAM hits")

    monkeypatch.setattr("remedy.memory.cas.EternalCAS.search_fts", _boom)
    line = organism_recall_line(home, "when is the piano exam", session_id="ram1")
    assert line.startswith("Recalled:")
    assert "March" in line
    configure_cas(None)
    reset_middleman_state()


def test_persist_vitals_skips_disk_when_stable(tmp_path: Path) -> None:
    from remedy.core.metabolism.organism import persist_vitals

    home = tmp_path / "stable"
    home.mkdir()
    body = {
        "ts": 10,
        "alive": True,
        "life_title": "A",
        "next_action": "B",
        "last_cycle_at": 10,
    }
    p = persist_vitals(body, home)
    assert p is not None
    m1 = p.stat().st_mtime
    persist_vitals({**body, "ts": 99, "last_cycle_at": 99}, home)
    assert p.stat().st_mtime == m1


def test_soma_from_vitals_and_tray(tmp_path: Path) -> None:
    from remedy.core.metabolism.organism import (
        collect_vitals,
        load_vitals,
        persist_vitals,
        soma_from_vitals,
        status_pack,
    )

    home = tmp_path / "soma"
    home.mkdir()
    persist_vitals(
        {
            "ts": 1,
            "alive": True,
            "mood": "focused",
            "emoji": "◆",
            "label": "Focused",
            "rapport": 0.7,
            "trust": 0.6,
            "stance": "focused",
            "tray_tooltip": "Remedy ◆ Focused",
            "open_count": 1,
        },
        home,
    )
    packet = soma_from_vitals(load_vitals(home))
    assert packet["label"] == "Focused"
    assert packet["tray_tooltip"].startswith("Remedy")
    v = collect_vitals(home)
    assert "tray_tooltip" in v
    pack = status_pack(home, max_age=1e9)
    assert pack.get("alive") is True


def test_pulse_reads_cached_vitals_not_store(tmp_path: Path) -> None:
    import time

    from remedy.core.metabolism.organism import organism_pulse_block, persist_vitals

    home = tmp_path / "fast"
    home.mkdir()
    persist_vitals(
        {
            "ts": time.time(),
            "alive": True,
            "who": "Remedy",
            "mood": "focused",
            "emoji": "◆",
            "label": "Focused",
            "stance": "steady",
            "rapport": 0.6,
            "trust": 0.6,
            "life_title": "Cached goal",
            "next_action": "Do the cached move",
            "last_did": "Wrote the brief",
            "cas_count": 7,
            "cas_durable": 2,
        },
        home,
    )
    block = organism_pulse_block(session_id="f", tier=1, home=home, max_chars=900)
    assert "Organism" in block
    assert "Life: Cached goal" in block
    assert "Memory: 7 objects" in block
    assert "Wrote the brief" in block


def test_wake_digest_reports_solo_cycle(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from remedy.core.metabolism.organism import (
        _life_is_stalled,
        format_wake_digest,
        organism_cycle,
        organism_wake,
    )
    from remedy.memory.cas import configure_cas
    from remedy.memory.life_goals import LifeGoalStore
    from remedy.memory.middleman import reset_middleman_state

    home = tmp_path / "wake"
    home.mkdir()
    reset_middleman_state()
    configure_cas(home)
    store = LifeGoalStore(home)
    store.add("Finish the novel", next_action="Outline chapter 3")
    organism_cycle(home, session_id="life")
    digest = format_wake_digest(home, mark_seen=True)
    assert "While I was on my own" in digest or "took a step" in digest or "Did" in digest
    # second wake should not repeat unseen cycles
    again = format_wake_digest(home, mark_seen=True)
    assert "While I was on my own" not in again

    g = store.active()
    assert g is not None
    g.updated_at = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    store.patch(g.id, next_action=g.next_action)
    # patch refreshes updated_at — write stale stamp through JSON
    raw = store.path.read_text(encoding="utf-8")
    import json

    data = json.loads(raw)
    for row in data.get("goals") or []:
        row["updated_at"] = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    store.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    store2 = LifeGoalStore(home)
    assert _life_is_stalled(store2) is True

    text = organism_wake(home)
    assert text
    configure_cas(None)
    reset_middleman_state()


def test_begin_turn_includes_organism_inject(tmp_path: Path) -> None:
    home = tmp_path / "h2"
    home.mkdir()
    sf = SoulField()
    sf.identity_name = "Remedy"
    sf.relational.turns_together = 5
    sf.relational.rapport = 0.7
    save_soul_field(sf, home)

    class _R:
        _llm_provider = "anthropic"
        _llm_model = "claude-sonnet-4"
        config = type("C", (), {"home_dir": str(home)})()

    meta = begin_turn_metabolism(
        session_id="s2",
        user_text="fix the failing tests and implement the retry path",
        tools_enabled=True,
        pre_tier=2,
        runtime=_R(),
        home=home,
        project_path=str(tmp_path / "p"),
    )
    injects = "\n".join(meta.get("injects") or [])
    # Organism pulse and/or governor/tier notes should land for L2
    assert injects
    assert any(
        k in injects
        for k in ("Organism", "Forge", "Governor", "Metabolism", "tier", "L2", "L3")
    )
