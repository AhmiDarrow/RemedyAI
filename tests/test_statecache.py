from __future__ import annotations

import json
import os

from remedy.memory import statecache


def test_json_cache_detects_same_size_atomic_replacement_with_same_mtime(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"value": "old"}), encoding="utf-8")
    first_stat = path.stat()
    assert statecache.read_json_cached(path) == {"value": "old"}

    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps({"value": "new"}), encoding="utf-8")
    os.utime(replacement, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))
    replacement.replace(path)

    assert path.stat().st_size == first_stat.st_size
    assert path.stat().st_mtime_ns == first_stat.st_mtime_ns
    assert statecache.read_json_cached(path) == {"value": "new"}
