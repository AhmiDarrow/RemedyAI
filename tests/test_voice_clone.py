"""Owner-voice clone is task-scoped, expiring, and revocable."""

from __future__ import annotations

import time
from pathlib import Path

from remedy.voice.clone import grant, read, revoke, sample_path


def test_grant_and_revoke(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    g = grant("call the clinic", b"RIFF" + b"\x00" * 80, ttl_s=600, home_dir=tmp_path)
    assert g.live
    assert g.task == "call the clinic"
    assert sample_path(tmp_path) is not None
    revoke(tmp_path)
    assert read(tmp_path) is None
    assert sample_path(tmp_path) is None


def test_expired_grant_is_dead(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    g = grant("one job", b"RIFF" + b"\x00" * 80, ttl_s=60, home_dir=tmp_path)
    # Force expiry in the grant file.
    import json

    from remedy.voice import clone as C

    p = C._grant_path(tmp_path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["expires_at"] = time.time() - 10
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert read(tmp_path) is None
    _ = g
