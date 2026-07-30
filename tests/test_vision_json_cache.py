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


def test_decode_cache_evicts_at_max():
    """Session decode cache must not grow without bound."""
    from remedy.vision import service as vs

    vs.clear_decode_cache()
    # Force a tiny max for the test
    old = vs._DECODE_CACHE_MAX
    try:
        vs._DECODE_CACHE_MAX = 3
        for i in range(5):
            # Simulate inserts like decode_for_turn
            while (
                len(vs._decode_cache) >= vs._DECODE_CACHE_MAX
                and f"k{i}" not in vs._decode_cache
            ):
                vs._decode_cache.pop(next(iter(vs._decode_cache)))
            vs._decode_cache[f"k{i}"] = f"brief-{i}"
        assert len(vs._decode_cache) <= 3
        assert "k0" not in vs._decode_cache
        assert "k4" in vs._decode_cache
    finally:
        vs._DECODE_CACHE_MAX = old
        vs.clear_decode_cache()
