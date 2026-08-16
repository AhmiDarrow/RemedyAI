"""C project verify discovery + no false-green Python smoke for .c writes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.core.build_engine import BuildTurnState, build_blocks_final_answer
from remedy.core.build_oracle import _discover_c_verify_command, discover_verify_command
from remedy.core.build_seed_oracle import seed_python_smoke_oracle


def test_discover_c_hello(tmp_path: Path):
    (tmp_path / "hello.c").write_text(
        '#include <stdio.h>\nint main(void){printf("hello partner\\n");return 0;}\n',
        encoding="utf-8",
    )
    cmd = _discover_c_verify_command(tmp_path)
    assert "gcc" in cmd
    assert "hello" in cmd


def test_discover_verify_prefers_c_over_empty_tests(tmp_path: Path):
    (tmp_path / "hello.c").write_text(
        '#include <stdio.h>\nint main(){return 0;}\n', encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    rt = SimpleNamespace(effective_project_path=lambda: tmp_path)
    cmd = discover_verify_command(rt)
    assert "gcc" in cmd
    assert "pytest" not in cmd


def test_seed_skips_c_only(tmp_path: Path):
    (tmp_path / "hello.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    rt = SimpleNamespace(
        effective_project_path=lambda: tmp_path,
        resolve_tool_path=lambda p, **k: tmp_path / p,
    )
    res = seed_python_smoke_oracle(rt, [str(tmp_path / "hello.c")])
    assert res.get("ok") is False
    assert res.get("reason") == "c_only" or "c_project" in str(res.get("error") or "")


def test_seed_skips_placeholder_without_imports(tmp_path: Path):
    # non-module junk name
    (tmp_path / "123-bad.py").write_text("x=1\n", encoding="utf-8")
    rt = SimpleNamespace(
        effective_project_path=lambda: tmp_path,
        resolve_tool_path=lambda p, **k: tmp_path / p,
    )
    res = seed_python_smoke_oracle(rt, [str(tmp_path / "123-bad.py")])
    assert res.get("ok") is False


def test_green_gate_blocks_c_without_gcc_verify():
    st = BuildTurnState(active=True, require_green_to_finish=True)
    st.write_steps = 1
    st.write_set = ["hello.c"]
    st.last_verify_ok = True  # false green from pytest
    st.verify_command = "pytest -q tests/test_remedy_build_smoke.py"
    assert build_blocks_final_answer(st) is True
    st.verify_command = "gcc -o hello.exe hello.c && hello.exe"
    st.last_verify_ok = True
    st.write_set = []  # cleared on green
    # with empty write_set and true verify ok, may allow — but has_c needs write_set
    # keep write_set until real green
    st.write_set = ["hello.c"]
    st.last_verify_ok = True
    # gcc in command + last ok → still blocks while write_set has .c?
    # Our rule: has_c and (not ok or no gcc) — if gcc and ok, allow
    assert build_blocks_final_answer(st) is False
