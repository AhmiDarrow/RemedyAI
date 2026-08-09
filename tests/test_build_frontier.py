"""Frontier build machine: scoped verify, oracle seed, mission bind."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.build_engine import begin_build_turn
from remedy.core.build_scoped import map_source_to_test_candidates, scoped_verify_command
from remedy.core.build_seed_oracle import _safe_modname, seed_python_smoke_oracle


def test_safe_modname():
    assert _safe_modname("src/remedy/core/foo.py") == "remedy.core.foo"
    assert _safe_modname("pkg/__init__.py") == "pkg"
    assert _safe_modname("bad-name.py") is None


def test_map_source_to_tests(tmp_path):
    root = tmp_path
    (root / "tests").mkdir()
    src = root / "mylib.py"
    src.write_text("x=1\n", encoding="utf-8")
    t = root / "tests" / "test_mylib.py"
    t.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    hits = map_source_to_test_candidates("mylib.py", root)
    assert any(h.name == "test_mylib.py" for h in hits)


def test_scoped_verify_command(tmp_path):
    root = tmp_path
    (root / "tests").mkdir()
    (root / "mylib.py").write_text("x=1\n", encoding="utf-8")
    (root / "tests" / "test_mylib.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8"
    )
    rt = SimpleNamespace(effective_project_path=lambda: root)
    cmd = scoped_verify_command(
        rt, [str(root / "mylib.py")], base_command="pytest -q"
    )
    assert "pytest" in cmd
    assert "test_mylib" in cmd


def test_seed_oracle(tmp_path):
    root = tmp_path
    (root / "widget.py").write_text("VALUE = 1\n", encoding="utf-8")
    rt = SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
    )
    res = seed_python_smoke_oracle(rt, [str(root / "widget.py")])
    assert res["ok"] is True
    assert "pytest" in res["command"]
    smoke = root / "tests" / "test_remedy_build_smoke.py"
    assert smoke.is_file()
    body = smoke.read_text(encoding="utf-8")
    assert "widget" in body or "importlib" in body


def test_begin_build_binds_mission(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='t'\n", encoding="utf-8")
    (proj / "tests").mkdir()
    rt = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
        config=SimpleNamespace(home_dir=tmp_path),
        effective_project_path=lambda: proj,
    )
    st = begin_build_turn(rt, "implement feature X with tests")
    assert st is not None
    assert st.active
    # mission may be set
    assert isinstance(st.mission_id, str)
