"""Review follow-ups for the build engine: read != write for todo rows, TDD
stubs never clobber real tests, anchored patch hunks, force keeps live caps,
manifest writes invalidate green, stale/corrupt ledgers."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from remedy.core.build_engine import BuildTurnState, _is_source_path


def _rt(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
        config=SimpleNamespace(home_dir=root),
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
    )


# --- todos --------------------------------------------------------------------


def test_reading_a_named_file_does_not_close_its_todo(tmp_path: Path):
    from remedy.core.build_todos import sync_todos_with_build, upsert_todos

    (tmp_path / "tab_score.py").write_text("x = 1\n", encoding="utf-8")
    rt = _rt(tmp_path)
    upsert_todos(
        rt,
        [{"id": "u1", "content": "Fix the scoring bug in tab_score.py", "status": "pending"}],
        merge=False,
    )
    st = BuildTurnState(active=True, write_steps=0, last_verify_ok=True)
    st.project_path = str(tmp_path)
    st.paths_touched = ["tab_score.py"]  # a file_read, not a write
    items = sync_todos_with_build(rt, st)
    assert any(t.id == "u1" and t.status in {"pending", "in_progress"} for t in items)
    assert st.open_todo_count >= 1


# --- tdd ---------------------------------------------------------------------


def test_tdd_stub_never_overwrites_an_existing_test(tmp_path: Path):
    from remedy.core.build_tdd import materialize_tdd_tests

    tests = tmp_path / "tests"
    tests.mkdir()
    real = tests / "test_greet.py"
    real.write_text("def test_real():\n    assert 1 == 1\n", encoding="utf-8")
    out = materialize_tdd_tests(
        _rt(tmp_path),
        [{"path": "greet.py", "symbol": "greet", "behavior": "say hi"}],
        root=tmp_path,
    )
    assert real.read_text(encoding="utf-8").startswith("def test_real")
    assert out["written"] == ["tests/test_greet_remedy_tdd.py"]
    assert (tests / "test_greet_remedy_tdd.py").is_file()


def test_file_goal_does_not_assert_stem_is_a_symbol():
    from remedy.core.build_spec_compiler import compile_goal_to_spec

    c = compile_goal_to_spec("fix the bug in utils.py")
    assert c.get("ok")
    blob = json.dumps(c)
    assert "hasattr(mod, 'utils')" not in blob
    assert "importlib.import_module" in blob


# --- apply_patch -------------------------------------------------------------


def test_pure_add_hunk_lands_after_its_anchor(tmp_path: Path):
    from remedy.core.build_apply_patch import apply_patch_text

    src = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    (tmp_path / "mod.py").write_text(src, encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: mod.py\n@@ def a():\n+    print('hi')\n*** End Patch\n"
    res = apply_patch_text(_rt(tmp_path), patch, root=tmp_path)
    assert res.get("ok"), res
    text = (tmp_path / "mod.py").read_text(encoding="utf-8")
    assert text.index("print('hi')") < text.index("def b()")
    assert text.splitlines()[1] == "    print('hi')"


def test_contextless_add_to_nonempty_file_is_refused(tmp_path: Path):
    from remedy.core.build_apply_patch import apply_patch_text

    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    patch = "*** Begin Patch\n*** Update File: mod.py\n+y = 2\n*** End Patch\n"
    res = apply_patch_text(_rt(tmp_path), patch, root=tmp_path)
    assert not res.get("ok")
    assert "anchor" in json.dumps(res).lower()
    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == "x = 1\n"


def test_add_file_still_works_without_anchor(tmp_path: Path):
    from remedy.core.build_apply_patch import apply_patch_text

    patch = "*** Begin Patch\n*** Add File: fresh.py\n+z = 3\n*** End Patch\n"
    res = apply_patch_text(_rt(tmp_path), patch, root=tmp_path)
    assert res.get("ok"), res
    assert (tmp_path / "fresh.py").read_text(encoding="utf-8") == "z = 3\n"


# --- engine ------------------------------------------------------------------


def test_force_reuses_the_active_state_instead_of_resetting_caps(tmp_path: Path):
    from remedy.core.build_engine import begin_build_turn

    rt = _rt(tmp_path)
    st = begin_build_turn(rt, "implement a REST API for todos")
    assert st is not None
    st.auto_verify_cycles = 3
    st.machine_injects = 2
    st.nudges_emitted = ["no_c_toolchain"]
    st.write_set = ["api.py"]
    again = begin_build_turn(rt, "implement a REST API for todos", force=True)
    assert again is st
    assert again.auto_verify_cycles == 3
    assert again.machine_injects == 2
    assert again.nudges_emitted == ["no_c_toolchain"]
    assert again.write_set == ["api.py"]


def test_manifest_and_script_writes_invalidate_green():
    assert _is_source_path("pyproject.toml")
    assert _is_source_path("package.json")
    assert _is_source_path("build/Makefile")
    assert _is_source_path("scripts/build.sh")
    assert _is_source_path("src/app.py")
    assert not _is_source_path("README.md")
    assert not _is_source_path("notes.txt")


# --- ledger ------------------------------------------------------------------


def test_stale_red_ledger_is_not_resumed():
    from remedy.core.build_ledger import BuildLedgerEntry, is_stale_entry, needs_resume_drive

    old = BuildLedgerEntry.from_dict(
        {
            "goal": "g",
            "phase": "implement",
            "last_verify_ok": False,
            "write_set": ["a.py"],
            "updated_ts": time.time() - 80 * 3600,
        }
    )
    fresh = BuildLedgerEntry.from_dict(
        {
            "goal": "g",
            "phase": "implement",
            "last_verify_ok": False,
            "write_set": ["a.py"],
            "updated_ts": time.time() - 3600,
        }
    )
    assert is_stale_entry(old) and not is_stale_entry(fresh)
    assert needs_resume_drive(old) is False
    assert needs_resume_drive(fresh) is True


def test_corrupt_ledger_is_set_aside_not_overwritten(tmp_path: Path):
    from remedy.core.build_ledger import ledger_path, load_ledger

    project = tmp_path / "proj"
    project.mkdir()
    path = ledger_path(project, home=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_ledger(project, home=tmp_path) is None
    assert not path.exists()
    kept = list(path.parent.glob(path.name + ".corrupt-*"))
    assert kept and kept[0].read_text(encoding="utf-8") == "{not json"
