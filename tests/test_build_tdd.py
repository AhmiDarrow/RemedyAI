"""TDD stub writes stay inside the project jail."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.core.build_tdd import materialize_tdd_tests
from remedy.core.errors import SecurityError


def test_tdd_jail_refuse_does_not_write(tmp_path: Path):
    def refuse(path: str, **_k):
        raise SecurityError(f"Path outside allowed roots: {path}")

    rt = SimpleNamespace(
        effective_project_path=lambda: tmp_path,
        resolve_tool_path=refuse,
    )
    out = materialize_tdd_tests(
        rt,
        [{"path": "hello.py", "symbol": "greet", "behavior": "say hi"}],
        root=tmp_path,
    )
    assert out.get("ok") is False
    assert "jail" in str(out.get("error") or "").lower()
    assert not (tmp_path / "tests" / "test_greet.py").exists()


def test_tdd_any_exception_fail_closed(tmp_path: Path):
    def boom(path: str, **_k):
        raise RuntimeError("unexpected")

    rt = SimpleNamespace(
        effective_project_path=lambda: tmp_path,
        resolve_tool_path=boom,
    )
    out = materialize_tdd_tests(
        rt,
        [{"path": "hello.py", "symbol": "greet"}],
        root=tmp_path,
    )
    assert out.get("ok") is False
    assert not (tmp_path / "tests" / "test_greet.py").exists()
