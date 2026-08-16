"""Multi-language cheap oracles."""

from __future__ import annotations

from pathlib import Path

from remedy.core.build_lang_oracle import brace_balance, check_lang_syntax, scoped_lang_verify
from remedy.core.build_syntax import check_paths_syntax


def test_brace_balance():
    assert brace_balance("function f() { return 1; }")[0] is True
    assert brace_balance("function f() { return 1;")[0] is False
    assert brace_balance("x = { a: [1, 2] }")[0] is True
    assert brace_balance('s = "not a { brace"')[0] is True


def test_js_syntax_via_brace_or_node(tmp_path: Path):
    good = tmp_path / "ok.js"
    good.write_text("function add(a, b) { return a + b; }\n", encoding="utf-8")
    bad = tmp_path / "bad.js"
    bad.write_text("function add(a, b) { return a + b;\n", encoding="utf-8")
    g = check_lang_syntax(good)
    b = check_lang_syntax(bad)
    assert g["ok"] is True
    assert b["ok"] is False


def test_c_syntax_gate_in_check_paths(tmp_path: Path):
    src = tmp_path / "hello.c"
    src.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    results = check_paths_syntax([str(src)])
    assert results
    # gcc may or may not be installed; either engine must return a result
    assert "ok" in results[0]


def test_scoped_lang_verify_cargo(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\nversion='0.1.0'\n", encoding="utf-8")
    cmd = scoped_lang_verify(tmp_path, ["src/lib.rs"])
    assert cmd == "cargo test"
