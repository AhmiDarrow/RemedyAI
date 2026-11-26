"""Production-path organism wiring: assemble pulse, turn-end learn, hearth tick, voice."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from remedy.runtime import rmdy_tool_worker as worker


@pytest.fixture(autouse=True)
def _restore_unattended_skill_tick() -> None:
    """run_unattended_improve records a process-global skill-tick time.

    Leave it as we found it so later tests can still fire one tick.
    """
    import remedy.core.self_inject as si

    prev = si._last_skill_tick
    yield
    si._last_skill_tick = prev


def _write_home(tmp_path: Path) -> Path:
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "openai"\nllm_model = "gpt-4o-mini"\n',
        encoding="utf-8",
    )
    return tmp_path




def test_turn_end_writes_learning_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remedy.memory.cas import configure_cas
    from remedy.memory.middleman import reset_middleman_state

    home = _write_home(tmp_path)
    monkeypatch.setenv("REMEDY_HOME", str(home))
    monkeypatch.setenv("REMEDY_WORKSPACE", str(home))
    reset_middleman_state()
    configure_cas(home)

    message = "I want to always use TypeScript from now on for this project."
    out = worker._prompt_turn_end(
        {
            "message": message,
            "assistant_text": "From now on we will use TypeScript for this project.",
            "session_id": "sess-learn",
            "home_dir": str(home),
            "tool_steps": [
                {"tool": "file_read", "ok": True, "result": "found tsconfig"},
                {"tool": "bash_exec", "ok": True, "result": "npm test exit 0"},
                {"tool": "file_write", "ok": True, "result": "wrote client"},
                {"tool": "bash_exec", "ok": True, "result": "tests green"},
                {"tool": "file_read", "ok": True, "result": "verified client"},
            ],
        }
    )
    assert out.get("ok") is True
    assert out.get("residue"), f"expected residue key, got {out}"
    assert int(out.get("genome") or 0) >= 1
    genome_path = home / "skill_genome" / "phenotypes.json"
    assert genome_path.is_file(), "skill genome was not persisted"
    body = genome_path.read_text(encoding="utf-8")
    assert "read" in body or "skill" in body.lower()
    assert out.get("learned") is True
    skills = home / "skills"
    assert skills.exists()
    # A learned skill or loop entry landed under the temp home.
    found = list(skills.rglob("*"))
    assert found, f"expected a LearningLoop artifact under {skills}"

    configure_cas(None)
    reset_middleman_state()




def test_start_voice_evolution_runs_daily_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remedy.voice import service as voice_service

    voice_service.reset_voice_evolution_for_tests()
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.delenv("REMEDY_NO_VOICE_EVOLUTION", raising=False)

    called: list[object] = []

    def fake_drift(home_dir, memory=None):
        called.append(home_dir)
        return True

    import threading

    released = threading.Event()

    def stop_loop(*_a, **_k):
        released.set()
        released.wait(timeout=30)

    monkeypatch.setattr("remedy.voice.evolution.daily_drift", fake_drift)
    monkeypatch.setattr(voice_service.time, "sleep", stop_loop)

    voice_service.start_voice_evolution(tmp_path)
    deadline = time.time() + 2.0
    while time.time() < deadline and not called:
        time.sleep(0.02)
    assert called, "daily_drift never ran"

    # Idempotent: a second start does not spawn another loop.
    voice_service.start_voice_evolution(tmp_path)
    released.set()





def test_turn_end_writes_learn_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remedy.core.metabolism.organism import persist_vitals
    from remedy.core.production_learn import recent_learn_runs
    from remedy.memory.cas import configure_cas
    from remedy.memory.middleman import reset_middleman_state

    home = _write_home(tmp_path)
    monkeypatch.setenv("REMEDY_HOME", str(home))
    monkeypatch.setenv("REMEDY_WORKSPACE", str(home))
    reset_middleman_state()
    configure_cas(home)
    persist_vitals({"ts": time.time(), "alive": True, "life_title": "Finish the novel"}, home)

    worker._prompt_turn_end(
        {
            "message": "remember this preference",
            "assistant_text": "I'll remember that.",
            "session_id": "sess-log",
            "home_dir": str(home),
            "tool_steps": [
                {"tool": "file_read", "ok": True, "result": "ok"},
                {"tool": "bash_exec", "ok": True, "result": "ok"},
            ],
        }
    )
    kinds = [row["kind"] for row in recent_learn_runs(str(home), limit=20)]
    assert "turn_end" in kinds, kinds
    for row in recent_learn_runs(str(home)):
        assert "duration_ms" in row
        assert "ts" in row
    configure_cas(None)
    reset_middleman_state()


def test_learn_log_rotates_when_large(tmp_path: Path) -> None:
    from remedy.core import production_learn as pl

    home = _write_home(tmp_path)
    path = pl.learn_log_path(str(home))
    assert path is not None
    line = '{"ts":"2026-01-01T00:00:00Z","kind":"improve","duration_ms":1,"ok":true}\n'
    path.write_text(line * ((pl.LEARN_LOG_MAX_BYTES // len(line)) + 8), encoding="utf-8")
    pl.record_learn_run(kind="turn_end", home=str(home), duration_ms=2.5, ok=True, changed={"learned": True})
    assert path.stat().st_size < pl.LEARN_LOG_MAX_BYTES
    rows = pl.recent_learn_runs(str(home), limit=50)
    assert rows and rows[-1]["kind"] == "turn_end"
    assert rows[-1]["changed"]["learned"] is True


def test_turn_end_binds_the_go_turn_session_to_the_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-turn work (consolidation, soul notes) reads the session through
    ``turn_session_id(runtime)``. The bind imported a ``set_turn_session_id``
    that never existed, so the ImportError was swallowed and that work saw the
    runtime's previous session instead of the Go turn's."""
    from types import SimpleNamespace

    from remedy.core import production_learn as pl
    from remedy.core.turn_context import turn_session_id

    runtime = SimpleNamespace(_session_id="sess-old", memory=None)
    monkeypatch.setattr(pl, "_runtime_for", lambda inp: runtime)
    out = pl.finish_production_turn(
        {"session_id": "sess-go", "message": "", "home_dir": str(tmp_path)}
    )
    assert out["ok"] is True
    assert turn_session_id(runtime) == "sess-go"
