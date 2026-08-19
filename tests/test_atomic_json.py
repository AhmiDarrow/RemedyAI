"""State files must survive a crash mid-write.

``path.write_text(json.dumps(...))`` truncates first and fills second. Lose the
process in between and what is on disk is half a document — and every reader in
this codebase does::

    except (OSError, json.JSONDecodeError):
        return None

so a torn write is silent, permanent loss of that checkpoint, plan, or crystal.
Nobody is told, which is the wrong failure for a product built on continuity.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from remedy.core.atomic_json import write_json_atomic, write_text_atomic


def test_writes_and_reads_back(tmp_path):
    p = tmp_path / "state.json"
    write_json_atomic(p, {"a": 1, "b": [1, 2, 3]})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2, 3]}


def test_creates_missing_parents(tmp_path):
    p = tmp_path / "deep" / "nested" / "state.json"
    write_json_atomic(p, {"ok": True})
    assert json.loads(p.read_text(encoding="utf-8")) == {"ok": True}


def test_an_unserialisable_value_leaves_the_good_file_alone(tmp_path):
    """Serialising before touching the file is half the point: a bad payload
    must not empty the file and *then* fail."""
    p = tmp_path / "state.json"
    write_json_atomic(p, {"good": 1})

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(p, {"bad": Unserialisable()}, default=None)

    assert json.loads(p.read_text(encoding="utf-8")) == {"good": 1}


def test_a_failure_mid_write_leaves_the_previous_version(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    write_json_atomic(p, {"version": 1})

    real_replace = os.replace

    def _die(src, dst):
        raise OSError("power cut between write and rename")

    monkeypatch.setattr(os, "replace", _die)
    with pytest.raises(OSError):
        write_json_atomic(p, {"version": 2})
    monkeypatch.setattr(os, "replace", real_replace)

    assert json.loads(p.read_text(encoding="utf-8")) == {"version": 1}
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"], (
        "a scratch file was left behind"
    )


def test_the_scratch_file_is_a_sibling(tmp_path, monkeypatch):
    """os.replace is only atomic within one filesystem, so the temp file has to
    live next to its target rather than in the system temp dir."""
    seen: list[tuple[str, str]] = []
    real_replace = os.replace

    def _spy(src, dst):
        seen.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", _spy)
    p = tmp_path / "sub" / "state.json"
    write_json_atomic(p, {"a": 1})
    assert seen
    src, dst = seen[0]
    assert Path(src).parent == Path(dst).parent


def test_text_writer_round_trips_exactly(tmp_path):
    p = tmp_path / "terms.json"
    body = json.dumps({"version": 1}, indent=2) + "\n"
    write_text_atomic(p, body)
    assert p.read_text(encoding="utf-8") == body


@pytest.mark.parametrize(
    ("module", "func"),
    [
        ("remedy.core.checkpoint", "CheckpointStore"),
        ("remedy.core.plan_store", "PlanStore"),
    ],
)
def test_the_durable_stores_use_the_atomic_writer(module, func):
    """Named so the next person moving one back to write_text has to argue."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module))
    assert "write_json_atomic" in src, f"{module} no longer writes atomically"
