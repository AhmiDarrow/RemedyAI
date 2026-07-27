"""vision.json mtime cache for is_running hot path."""

from __future__ import annotations

from pathlib import Path

from remedy.vision import runtime as vr
from remedy.vision.config import save_vision_json, vision_json_path


def test_vision_json_mtime_cache_avoids_reparse(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "vision").mkdir()
    save_vision_json({"host": "127.0.0.1", "port": 18742}, home_dir=home)
    path = vision_json_path(home)

    # Reset module caches
    vr.invalidate_running_cache()
    vr._vision_json_cache["path"] = ""
    vr._vision_json_cache["mtime"] = -1.0

    data1 = vr._load_vision_json_cached(home)
    assert data1.get("port") == 18742
    m1 = vr._vision_json_cache["mtime"]

    # Second load same mtime — same cache entry
    data2 = vr._load_vision_json_cached(home)
    assert data2.get("port") == 18742
    assert vr._vision_json_cache["mtime"] == m1

    # Change file → cache miss
    save_vision_json({"host": "127.0.0.1", "port": 19999}, home_dir=home)
    data3 = vr._load_vision_json_cached(home)
    assert data3.get("port") == 19999
    assert vr._vision_json_cache["mtime"] != m1 or path.stat().st_mtime != m1
