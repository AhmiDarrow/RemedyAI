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
        write_json_atomic(p, {"bad": Unserialisable()})

    assert json.loads(p.read_text(encoding="utf-8")) == {"good": 1}


def test_unserialisable_values_are_not_silently_stringified(tmp_path):
    """``default=str`` used to turn ``object()`` into ``"<object at 0x...>"``
    and write that into the state file. A value that cannot be serialised is a
    bug to surface, not something to coerce."""
    p = tmp_path / "state.json"
    with pytest.raises(TypeError):
        write_json_atomic(p, {"x": object()})
    assert not p.exists()
    assert list(tmp_path.iterdir()) == [], "a scratch file was left behind"


def test_a_caller_may_still_opt_into_default(tmp_path):
    p = tmp_path / "state.json"
    write_json_atomic(p, {"x": Path("a")}, default=str)
    assert json.loads(p.read_text(encoding="utf-8")) == {"x": "a"}


class TestConcurrency:
    def test_threads_in_one_process_do_not_collide(self, tmp_path):
        """The scratch name was pid-only, so two threads of one process wrote
        the same scratch file and one of them renamed the other's half-written
        bytes — or lost a FileNotFoundError race on the rename."""
        import threading

        p = tmp_path / "shared.json"
        errors: list[BaseException] = []

        def worker(n: int) -> None:
            try:
                for i in range(200):
                    write_json_atomic(p, {"thread": n, "i": i, "pad": "x" * 512}, fsync=False)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        doc = json.loads(p.read_text(encoding="utf-8"))
        assert doc["i"] == 199
        assert [f.name for f in tmp_path.iterdir()] == ["shared.json"]

    def test_scratch_name_carries_the_thread_id(self, tmp_path):
        import threading

        from remedy.core.atomic_json import scratch_path

        names: dict[int, str] = {}

        def grab(k: int) -> None:
            names[k] = scratch_path(tmp_path / "a.json").name

        ts = [threading.Thread(target=grab, args=(k,)) for k in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert names[0] != names[1]

    def test_a_reader_holding_the_target_open_does_not_fail_the_write(self, tmp_path):
        """Windows refuses os.replace while a plain reader has the target open.
        Readers are brief; the writer must wait them out rather than raise."""
        import threading
        import time

        p = tmp_path / "state.json"
        write_json_atomic(p, {"v": 1})
        opened = threading.Event()

        def hold() -> None:
            with open(p, encoding="utf-8") as fh:
                opened.set()
                time.sleep(0.2)
                fh.read()

        t = threading.Thread(target=hold)
        t.start()
        opened.wait(2.0)
        try:
            write_json_atomic(p, {"v": 2})
        finally:
            t.join()
        assert json.loads(p.read_text(encoding="utf-8")) == {"v": 2}
        assert [f.name for f in tmp_path.iterdir()] == ["state.json"]

    def test_a_permanent_permission_error_is_raised_and_cleaned_up(
        self, tmp_path, monkeypatch
    ):
        import time

        from remedy.core import atomic_json

        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", sleeps.append)

        def _denied(src, dst):
            raise PermissionError("in use")

        monkeypatch.setattr(os, "replace", _denied)
        p = tmp_path / "state.json"
        with pytest.raises(PermissionError):
            write_json_atomic(p, {"v": 1})
        assert len(sleeps) == atomic_json._REPLACE_ATTEMPTS - 1
        assert sum(sleeps) <= 1.0, "the retry budget must stay short"
        assert list(tmp_path.iterdir()) == [], "a scratch file was left behind"


class TestTextEncoding:
    def test_a_lone_surrogate_raises_under_strict_with_no_file_left(self, tmp_path):
        p = tmp_path / "out.txt"
        with pytest.raises(UnicodeEncodeError):
            write_text_atomic(p, "bad \udcff text")
        assert list(tmp_path.iterdir()) == []

    def test_errors_replace_writes_through(self, tmp_path):
        p = tmp_path / "out.txt"
        write_text_atomic(p, "bad \udcff text", errors="replace")
        assert p.read_text(encoding="utf-8") == "bad ? text"


def test_bytes_writer_applies_the_mode_before_the_rename(tmp_path, monkeypatch):
    """A secret store must never exist, even briefly, with loose permissions."""
    from remedy.core.atomic_json import write_bytes_atomic

    seen: list[tuple[str, int]] = []
    real_chmod = os.chmod

    def _spy(path, mode, *a, **kw):
        seen.append((str(path), mode))
        return real_chmod(path, mode, *a, **kw)

    monkeypatch.setattr(os, "chmod", _spy)
    p = tmp_path / "keys.bin"
    write_bytes_atomic(p, b"\x00\x01", mode=0o600)
    assert p.read_bytes() == b"\x00\x01"
    assert seen and seen[0][1] == 0o600
    assert Path(seen[0][0]).name != "keys.bin", "chmod must hit the scratch file"


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


def _is_fixed_tmp_name(node) -> bool:
    """Does this with_suffix() argument spell a shared ``.tmp`` scratch name?

    Catches the literal (``".tmp"``, ``".json.tmp"``), the concatenation
    (``path.suffix + ".tmp"``) and the f-string form.
    """
    import ast

    if isinstance(node, ast.Constant):
        return str(node.value).endswith(".tmp")
    if isinstance(node, (ast.BinOp, ast.JoinedStr)):
        # A call in the expression (os.getpid(), threading.get_ident()) is
        # what makes a name unique; without one, every writer shares it.
        if any(isinstance(n, ast.Call) for n in ast.walk(node)):
            return False
        return any(
            isinstance(n, ast.Constant) and str(n.value).endswith(".tmp")
            for n in ast.walk(node)
        )
    return False


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
                        and _is_fixed_tmp_name(n.args[0])):
                    offenders.append(f"{path.relative_to('src/remedy')}:{n.lineno}")
        assert not offenders, (
            "fixed scratch names (use atomic_json.scratch_path):\n  "
            + "\n  ".join(offenders)
        )

    def test_the_guard_sees_through_suffix_concatenation(self):
        """``path.with_suffix(path.suffix + ".tmp")`` is the same shared name
        with one more hop; seven sites used it and the first guard missed all
        seven."""
        import ast

        for src in (
            'p.with_suffix(".tmp")',
            'p.with_suffix(".json.tmp")',
            'p.with_suffix(p.suffix + ".tmp")',
            'p.with_suffix(".tmp" + p.suffix)',
            'p.with_suffix(f"{p.suffix}.tmp")',
        ):
            call = ast.parse(src).body[0].value
            assert _is_fixed_tmp_name(call.args[0]), src
        for src in (
            'p.with_suffix(".bak")',
            'p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")',
        ):
            ok = ast.parse(src).body[0].value
            assert not _is_fixed_tmp_name(ok.args[0]), src


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
