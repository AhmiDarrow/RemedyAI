"""Import graph, mutation cone, live hop oracle (no live LLM required)."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.build_import_graph import (
    build_import_graph,
    dry_run_import,
    import_cone,
    mutation_score_paths,
    parse_imports_from_source,
)
from remedy.core.build_live_hop import disk_oracle, live_unit_hop
from remedy.core.builds.reducer import Signature, UnitSpec


def test_parse_imports():
    src = "import os\nfrom pathlib import Path\nfrom pkg.sub import x\n"
    imps = parse_imports_from_source(src)
    assert "os" in imps
    assert "pathlib" in imps
    assert "pkg.sub" in imps


def test_import_graph_and_cone(tmp_path):
    root = tmp_path
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "b.py").write_text("import a\ny = a.x\n", encoding="utf-8")
    (root / "c.py").write_text("import b\nz = 1\n", encoding="utf-8")
    g = build_import_graph(root)
    assert "a" in g.edges or "a" in g.path_by_mod
    cone = import_cone(g, ["a"])
    # b and c import (transitively) a
    assert "a" in cone
    assert "b" in cone or "c" in cone


def test_mutation_score(tmp_path):
    root = tmp_path
    (root / "core_mod.py").write_text("V=1\n", encoding="utf-8")
    (root / "user_mod.py").write_text("import core_mod\n", encoding="utf-8")
    ms = mutation_score_paths(root, [str(root / "core_mod.py")])
    assert "core_mod" in ms["seed_mods"]
    assert ms["mutation_score"] >= 0


def test_dry_run_import_ok(tmp_path):
    (tmp_path / "hello_mod.py").write_text("VALUE = 42\n", encoding="utf-8")
    r = dry_run_import("hello_mod", root=tmp_path)
    assert r["ok"] is True


def test_dry_run_import_fail(tmp_path):
    (tmp_path / "broken_mod.py").write_text("raise RuntimeError('x')\n", encoding="utf-8")
    r = dry_run_import("broken_mod", root=tmp_path)
    assert r["ok"] is False


def test_live_unit_hop_structural(tmp_path):
    root = tmp_path
    rt = SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
        config=SimpleNamespace(home_dir=tmp_path),
    )
    res = live_unit_hop(
        rt,
        path="widget.py",
        symbol="helper",
        source="def helper():\n    return 1\n",
        use_llm=False,
    )
    assert res["ok"] is True
    assert (root / "widget.py").is_file()
    assert res.get("written") is True


def test_live_unit_hop_refuses_jail(tmp_path):
    from remedy.core.errors import SecurityError

    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "pwned.py"

    def refuse(path, **_k):
        raise SecurityError(f"Path outside allowed roots: {path}")

    rt = SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=refuse,
        config=SimpleNamespace(home_dir=tmp_path),
    )
    res = live_unit_hop(
        rt,
        path=str(outside),
        symbol="helper",
        source="def helper():\n    return 1\n",
        use_llm=False,
    )
    assert res["ok"] is False
    assert "jail" in str(res.get("error") or "").lower()
    assert not outside.exists()


def test_live_unit_hop_red_missing_symbol(tmp_path):
    root = tmp_path
    rt = SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
        config=SimpleNamespace(home_dir=tmp_path),
    )
    res = live_unit_hop(
        rt,
        path="empty.py",
        symbol="needed",
        source="x = 1\n",
        use_llm=False,
    )
    assert res["ok"] is False
    assert any("needed" in e for e in res.get("errors") or [])


def test_disk_oracle():
    unit = UnitSpec(
        id="f",
        path="f.py",
        declare=[Signature(symbol="foo", defines_path="f.py")],
    )
    errs = disk_oracle(unit, {"f.py": "def foo():\n    return 0\n"})
    assert errs == []
    errs2 = disk_oracle(unit, {"f.py": "def bar():\n    return 0\n"})
    assert errs2


def test_live_build_project_no_llm(tmp_path):
    from remedy.core.build_live_hop import live_build_project

    root = tmp_path
    # Pre-write source on disk so non-LLM model reads it
    (root / "svc.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    rt = SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
        config=SimpleNamespace(home_dir=tmp_path),
    )
    res = live_build_project(
        rt,
        [{"path": "svc.py", "symbol": "run", "behavior": "return 1"}],
        use_llm=False,
        max_repairs=1,
        max_iterations=5,
    )
    assert res.get("files")
    assert "svc.py" in (res.get("files") or [])
    assert (root / "svc.py").is_file()


def test_scoped_verify_uses_mutation_cone(tmp_path):
    """Importers of write_set should expand into scoped pytest selection."""
    from remedy.core.build_scoped import scoped_verify_command

    root = tmp_path
    (root / "tests").mkdir()
    (root / "core_mod.py").write_text("V=1\n", encoding="utf-8")
    (root / "user_mod.py").write_text("import core_mod\n", encoding="utf-8")
    (root / "tests" / "test_user_mod.py").write_text(
        "def test_u():\n    assert True\n", encoding="utf-8"
    )
    # Only core_mod in write_set; cone should pull user_mod → test_user_mod
    rt = SimpleNamespace(effective_project_path=lambda: root)
    cmd = scoped_verify_command(
        rt,
        [str(root / "core_mod.py")],
        base_command="pytest -q",
        use_mutation_cone=True,
    )
    assert "pytest" in cmd
    assert "test_user_mod" in cmd
    ms = getattr(rt, "_last_mutation_score", None)
    assert ms is not None
    assert "core_mod" in (ms.get("seed_mods") or [])


def test_format_import_dry_run_message():
    from remedy.core.build_import_graph import format_import_dry_run_message

    assert format_import_dry_run_message([{"ok": True, "module": "a"}]) is None
    msg = format_import_dry_run_message(
        [{"ok": False, "module": "broken", "error": "ImportError: x"}]
    )
    assert msg is not None
    assert msg["role"] == "user"
    assert "IMPORT DRY-RUN" in msg["content"]
    assert "broken" in msg["content"]


def test_format_import_dry_run_skips_interpreter_failures():
    """Sidecar/CLI as sys.executable must not send models on a fix chase."""
    from remedy.core.build_import_graph import format_import_dry_run_message

    msg = format_import_dry_run_message(
        [
            {
                "ok": False,
                "module": "healthy",
                "error": "remedy: error: argument command: invalid choice",
                "error_class": "interpreter",
            }
        ]
    )
    assert msg is not None
    assert "SKIPPED" in msg["content"]
    assert "not a module import bug" in msg["content"].lower() or "not" in msg["content"].lower()
    assert "file_edit those modules" not in msg["content"]


def test_python_cmd_for_subprocess_prefers_real_python(tmp_path):
    from pathlib import Path as _P

    from remedy.core.build_python import (
        host_python_executable,
        is_sidecar_spawn_error,
        is_usable_host_python,
        python_cmd_for_subprocess,
    )

    cmd = python_cmd_for_subprocess(tmp_path)
    # On CI / this machine we expect a real interpreter; empty is only OK if
    # the host truly has no Python (then dry-run soft-fails).
    if cmd:
        head = str(cmd[0]).lower().replace("\\", "/")
        name = _P(cmd[0]).name.lower()
        assert "remedy" not in name or head.endswith("python") or "uv" in head
    assert is_sidecar_spawn_error(
        'remedy: error: argument command: invalid choice: "import importlib"'
    )
    assert not is_sidecar_spawn_error("ImportError: cannot import name foo")
    assert is_usable_host_python(r"C:\Python313\python.exe")
    assert is_usable_host_python(r"C:\Windows\py.exe")
    assert not is_usable_host_python(r"C:\Program Files\Remedy Desktop\remedy-desktop.exe")
    assert not is_usable_host_python(
        r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"
    )
    assert not is_usable_host_python("")
    host = host_python_executable()
    if host:
        assert is_usable_host_python(host)
        assert "remedy-desktop" not in _P(host).name.lower()


def test_gate_l2_soft_pass_on_interpreter(tmp_path):
    from remedy.core.build_gate_tower import gate_l2_import

    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    # Monkeypatch dry_run to simulate sidecar failure
    import remedy.core.build_import_graph as big

    real = big.dry_run_imports_for_paths

    def fake(_paths, _root, **_k):
        return [
            {
                "ok": False,
                "module": "m",
                "error": "remedy: error: argument command: invalid choice",
                "error_class": "interpreter",
            }
        ]

    big.dry_run_imports_for_paths = fake  # type: ignore[assignment]
    try:
        gr = gate_l2_import(tmp_path, [str(tmp_path / "m.py")])
        assert gr.ok is True
        assert gr.verified is False
        assert "soft-pass" in (gr.summary or "").lower() or "cpython" in (gr.summary or "").lower()
    finally:
        big.dry_run_imports_for_paths = real  # type: ignore[assignment]


def test_build_tools_handlers_callable(tmp_path):
    """Registration must bind real async handlers (no NameError at call)."""
    import asyncio

    from remedy.core.agent_build_tools import register_build_tools

    handlers: dict[str, object] = {}

    class FakeReg:
        def register_builtin_handler(self, name, desc, fn, schema):  # noqa: ARG002
            handlers[name] = fn

    root = tmp_path
    (root / "widget.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    rt = SimpleNamespace(
        tool_registry=FakeReg(),
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
        config=SimpleNamespace(home_dir=tmp_path),
    )
    register_build_tools(rt)
    for name in (
        "build_status",
        "build_resume",
        "build_unit_hop",
        "build_live_project",
        "build_mutation_score",
    ):
        assert name in handlers, name
        assert callable(handlers[name])

    out = asyncio.run(
        handlers["build_unit_hop"](  # type: ignore[operator]
            path="widget.py",
            symbol="helper",
            source="def helper():\n    return 1\n",
            use_llm=False,
        )
    )
    assert "OK" in out or "build_unit_hop" in out

    out2 = asyncio.run(handlers["build_mutation_score"]())  # type: ignore[operator]
    assert isinstance(out2, str)
    assert "Mutation" in out2 or "write_set" in out2 or "seed" in out2.lower()
