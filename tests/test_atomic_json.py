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


class TestScratchNames:
    """Two writers must not pick the same scratch file.

    ``path.with_suffix(".tmp")`` gave every writer the *same* name — the desktop
    app and a CLI, two threads, two windows — so they wrote it at once and
    whichever renamed second published a corrupted or interleaved result.
    Thirteen call sites did this.
    """

    def test_the_scratch_name_is_process_unique(self, tmp_path):
        import os

        from remedy.core.atomic_json import scratch_path

        got = scratch_path(tmp_path / "profiles.json")
        assert str(os.getpid()) in got.name
        assert got.name != "profiles.tmp"

    def test_it_stays_in_the_same_directory(self, tmp_path):
        """os.replace is only atomic within one filesystem."""
        from remedy.core.atomic_json import scratch_path

        target = tmp_path / "deep" / "state.json"
        assert scratch_path(target).parent == target.parent

    def test_the_full_name_is_kept_so_two_targets_never_collide(self, tmp_path):
        """with_suffix() collapsed catalog.json and catalog.json.sig onto the
        same scratch file."""
        from remedy.core.atomic_json import scratch_path

        a = scratch_path(tmp_path / "catalog.json")
        b = scratch_path(tmp_path / "catalog.json.sig")
        assert a != b

    def test_no_module_still_uses_a_fixed_scratch_name(self):
        import ast
        from pathlib import Path

        offenders = []
        for path in sorted(Path("src/remedy").rglob("*.py")):
            if "bundled_skills" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover
                continue
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call)
                        and getattr(n.func, "attr", "") == "with_suffix"
                        and n.args
                        and isinstance(n.args[0], ast.Constant)
                        and str(n.args[0].value).endswith(".tmp")):
                    offenders.append(f"{path.relative_to('src/remedy')}:{n.lineno}")
        assert not offenders, (
            "fixed scratch names (use atomic_json.scratch_path):\n  "
            + "\n  ".join(offenders)
        )


def test_saving_a_project_profile_is_one_operation():
    """The file write and the cache update have to be inside the same lock, or
    two writers leave the cache holding one version over the other's file."""
    import ast
    import inspect
    import textwrap

    from remedy.core import project_learning

    src = textwrap.dedent(inspect.getsource(project_learning.save_all))
    tree = ast.parse(src)
    withs = [n for n in ast.walk(tree) if isinstance(n, ast.With)]
    assert withs, "save_all no longer takes the lock"
    guarded = {
        getattr(inner, "lineno", -1)
        for w in withs
        for inner in ast.walk(w)
    }
    writes = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", "") == "write_json_atomic"
    ]
    assert writes, "save_all no longer writes atomically"
    assert all(w in guarded for w in writes), "the write is outside the lock"
