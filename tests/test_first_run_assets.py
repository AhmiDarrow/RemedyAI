"""Voice and vision download with Remedy — not as Settings options.

First-run ensure helpers must kick off missing pieces, and must stay quiet
inside pytest so the suite never pulls hundreds of MB.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_ensure_voice_assets_skips_inside_pytest(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("REMEDY_ENSURE_ASSETS", raising=False)
    started: list[str] = []
    monkeypatch.setattr(
        "remedy.voice.service.install_tts_background",
        lambda *a, **k: started.append("tts") or True,
    )
    from remedy.voice.service import ensure_voice_assets

    r = ensure_voice_assets(tmp_path)
    assert r.get("skipped") is True
    assert started == []


def test_ensure_voice_assets_force_starts_missing(tmp_path: Path, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr("remedy.voice.service.tts_installed", lambda *a, **k: False)
    monkeypatch.setattr("remedy.voice.service.stt_installed", lambda *a, **k: True)
    monkeypatch.setattr(
        "remedy.voice.service.smart_turn_installed", lambda *a, **k: False
    )
    monkeypatch.setattr("remedy.voice.service.tts_deps_available", lambda: True)
    monkeypatch.setattr("remedy.voice.service.stt_deps_available", lambda: True)
    monkeypatch.setattr(
        "remedy.voice.service.install_tts_background",
        lambda *a, **k: started.append("tts") or True,
    )
    monkeypatch.setattr(
        "remedy.voice.service.install_smart_turn_background",
        lambda *a, **k: started.append("smart-turn") or True,
    )
    monkeypatch.setattr(
        "remedy.voice.service.install_voice_pack_background",
        lambda *a, **k: started.append("pack") or True,
    )
    from remedy.voice.service import ensure_voice_assets

    r = ensure_voice_assets(tmp_path, force=True)
    assert r["ok"] is True
    assert r["started"] == ["tts", "smart-turn"]
    assert "stt" in r["already"]
    assert started == ["tts", "smart-turn"]


def test_ensure_voice_assets_missing_extras_starts_pack(tmp_path: Path, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr("remedy.voice.service.tts_deps_available", lambda: False)
    monkeypatch.setattr("remedy.voice.service.stt_deps_available", lambda: False)
    monkeypatch.setattr(
        "remedy.voice.service.install_voice_pack_background",
        lambda *a, **k: started.append("pack") or True,
    )
    from remedy.voice.service import ensure_voice_assets

    r = ensure_voice_assets(tmp_path, force=True)
    assert r["ok"] is True
    assert r["started"] == ["pack"]
    assert started == ["pack"]


def test_ensure_voice_assets_no_op_when_present(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("remedy.voice.service.tts_deps_available", lambda: True)
    monkeypatch.setattr("remedy.voice.service.stt_deps_available", lambda: True)
    monkeypatch.setattr("remedy.voice.service.tts_installed", lambda *a, **k: True)
    monkeypatch.setattr("remedy.voice.service.stt_installed", lambda *a, **k: True)
    monkeypatch.setattr(
        "remedy.voice.service.smart_turn_installed", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "remedy.voice.service.install_tts_background",
        lambda *a, **k: pytest.fail("tts download should not start"),
    )
    from remedy.voice.service import ensure_voice_assets

    r = ensure_voice_assets(tmp_path, force=True)
    assert r["started"] == []
    assert set(r["already"]) == {"tts", "stt", "smart-turn"}


def test_maybe_ensure_local_model_skips_network_in_pytest(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("REMEDY_ENSURE_ASSETS", raising=False)
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    called = []
    monkeypatch.setattr(
        "remedy.vision.service.start_install",
        lambda **k: called.append(k) or {"ok": True},
    )
    from remedy.vision.service import maybe_ensure_local_model

    r = maybe_ensure_local_model(
        {"home_dir": str(tmp_path), "vision": {"enabled": True, "auto_start": True}}
    )
    assert called == []
    assert r.get("skipped") is True or r.get("ok") is False


def test_maybe_ensure_local_model_starts_download_when_forced(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("REMEDY_ENSURE_ASSETS", "1")
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    called: list[dict] = []

    monkeypatch.setattr("remedy.vision.service.is_installed", lambda *a, **k: False)
    monkeypatch.setattr(
        "remedy.vision.service.start_install",
        lambda **k: called.append(k) or {"ok": True, "mode": "download"},
    )
    from remedy.vision.service import maybe_ensure_local_model

    r = maybe_ensure_local_model({"home_dir": str(tmp_path)})
    assert r["ok"] is True
    assert called and called[0].get("cfg") == {"home_dir": str(tmp_path)}
