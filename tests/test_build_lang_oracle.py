"""Cheap syntax gates for the languages Python's `py_compile` cannot check.

The point of a brace-balance fallback is that a missing toolchain must not
silently accept broken code. So the tests care most about two directions:
genuinely unbalanced source is caught even with no compiler installed, and
ordinary source containing braces *inside strings and comments* is not falsely
rejected — a false red sends the model rewriting a file that was already fine.

And `scoped_lang_verify` picks the command that will actually run in a repo:
naming `npm test` in a pnpm workspace produces a failure that has nothing to do
with the change.
"""

from __future__ import annotations

import pytest

from remedy.core.build_lang_oracle import (
    LANG_SUFFIXES,
    brace_balance,
    check_lang_paths,
    check_lang_syntax,
    scoped_lang_verify,
)
from remedy.core.build_syntax import check_paths_syntax

# --- brace balance -----------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "",
        "function f() { return 1; }",
        "const a = [1, 2, {b: 3}];",
        "if (x) { y(); } else { z(); }",
        "fn main() { let v = vec![1, 2]; }",
        "int main(void) { return 0; }",
    ],
)
def test_balanced_source_passes(src):
    ok, err = brace_balance(src)
    assert ok, err


@pytest.mark.parametrize(
    ("src", "reason"),
    [
        ("function f() { return 1;", "unclosed"),
        ("const a = [1, 2;", "unclosed"),
        ("f()) ", "unbalanced"),
        ("}{", "unbalanced"),
        ("const s = 'unterminated", "unterminated"),
    ],
)
def test_broken_source_is_caught_and_named(src, reason):
    ok, err = brace_balance(src)
    assert ok is False
    assert reason in err


def test_a_brace_inside_a_string_is_not_a_brace():
    """The most common false red: `const s = "{"`."""
    ok, _ = brace_balance('const s = "{ not real";')
    assert ok is True


@pytest.mark.parametrize("quote", ["'", '"', "`"])
def test_every_string_style_is_understood(quote):
    ok, _ = brace_balance(f"const s = {quote}{{{quote};")
    assert ok is True


def test_an_escaped_quote_does_not_end_the_string():
    ok, _ = brace_balance(r'const s = "he said \" and { ";')
    assert ok is True


def test_a_brace_in_a_line_comment_is_ignored():
    ok, _ = brace_balance("// this { is a comment\nconst a = 1;")
    assert ok is True


def test_a_brace_in_a_block_comment_is_ignored():
    ok, _ = brace_balance("/* { unbalanced in here */ const a = 1;")
    assert ok is True


def test_a_multi_line_block_comment_is_ignored():
    ok, _ = brace_balance("/*\n {\n {\n*/\nconst a = 1;")
    assert ok is True


def test_a_division_is_not_the_start_of_a_comment():
    ok, _ = brace_balance("const half = (total) / 2;")
    assert ok is True


def test_a_template_literal_spanning_lines_is_handled():
    ok, _ = brace_balance("const t = `line one\nline { two`;")
    assert ok is True


def test_the_wrong_closer_is_reported_not_ignored():
    ok, err = brace_balance("const a = [1, 2};")
    assert ok is False
    assert "}" in err


# --- per-file checks ---------------------------------------------------------


def test_a_missing_file_is_reported_as_such(tmp_path):
    out = check_lang_syntax(tmp_path / "nope.ts")
    assert out["ok"] is False
    assert out["error"] == "not a file"
    assert out["engine"] == "stat"


@pytest.mark.parametrize("suffix", ["ts", "tsx", "js", "jsx"])
def test_a_balanced_file_passes(tmp_path, suffix):
    f = tmp_path / f"ok.{suffix}"
    f.write_text("function add(a, b) { return a + b; }", encoding="utf-8")
    assert check_lang_syntax(f)["ok"] is True


@pytest.mark.parametrize("suffix", ["ts", "js"])
def test_an_unbalanced_file_fails(tmp_path, suffix):
    f = tmp_path / f"bad.{suffix}"
    f.write_text("function add(a, b) { return a + b;", encoding="utf-8")
    out = check_lang_syntax(f)
    assert out["ok"] is False
    assert out["error"]


@pytest.mark.parametrize("suffix", ["tsx", "jsx"])
def test_an_unbalanced_jsx_file_is_red_only_from_a_real_parser(tmp_path, suffix):
    """JSX gets a verdict from a JSX-aware parser or no verdict at all."""
    from remedy.core import build_lang_oracle as O

    f = tmp_path / f"bad.{suffix}"
    f.write_text("function add(a, b) { return a + b;", encoding="utf-8")
    out = check_lang_syntax(f)
    if O._jsx_checker():
        assert out["ok"] is False
        assert out["error"]
    else:
        assert out["ok"] is True
        assert out["engine"].startswith("skip")


def test_the_result_says_which_engine_judged_it(tmp_path):
    f = tmp_path / "ok.ts"
    f.write_text("const a = 1;\n", encoding="utf-8")
    assert check_lang_syntax(f)["engine"]


def test_an_unknown_extension_is_skipped_not_failed(tmp_path):
    """A .md file is not broken code; it is not code."""
    f = tmp_path / "notes.md"
    f.write_text("# hello {\n", encoding="utf-8")
    out = check_lang_syntax(f)
    assert out["ok"] is True
    assert out["engine"] == "skip"


def test_real_jsx_is_not_reported_as_broken(tmp_path):
    """node --check rejects the .jsx *extension* before reading a character.

    Every .jsx file came back red whatever was in it, and the error handed to
    the model was a Node internals traceback about file formats — which reads
    as "your code is broken" and sends it rewriting a working component.
    """
    f = tmp_path / "Widget.jsx"
    f.write_text("const A = () => <div>hi</div>;\n", encoding="utf-8")
    out = check_lang_syntax(f)
    assert out["ok"] is True, out["error"]
    assert "node" not in out["engine"]


def test_a_broken_jsx_file_is_caught_when_a_parser_exists(tmp_path):
    from remedy.core import build_lang_oracle as O

    f = tmp_path / "Broken.jsx"
    f.write_text("const A = () => { return <div>hi</div>;\n", encoding="utf-8")
    out = check_lang_syntax(f)
    if O._jsx_checker():
        assert out["ok"] is False
    else:
        assert out["ok"] is True
        assert out["engine"].startswith("skip")


@pytest.fixture
def no_jsx_parser(monkeypatch):
    from remedy.core import build_lang_oracle as O

    monkeypatch.setattr(O, "_TOOLCHAIN", {"esbuild": None, "tsc": None})


@pytest.mark.parametrize("suffix", ["jsx", "tsx"])
def test_an_apostrophe_in_jsx_text_is_not_an_unterminated_string(tmp_path, suffix):
    """``<p>Don't click {x}</p>`` is prose, not a string literal. The brace
    heuristic saw ``'`` open a string that never closed and flagged a working
    component — the exact false red this oracle must never produce."""
    f = tmp_path / f"Hint.{suffix}"
    f.write_text("const Hint = ({x}) => <p>Don't click {x}</p>;\n", encoding="utf-8")
    out = check_lang_syntax(f)
    assert out["ok"] is True, out
    assert "brace" not in out["engine"]


@pytest.mark.parametrize("suffix", ["jsx", "tsx"])
def test_a_regex_literal_with_a_brace_in_jsx_is_not_unbalanced(tmp_path, suffix):
    f = tmp_path / f"Re.{suffix}"
    f.write_text(
        "const hasBrace = (s) => /[{]/.test(s);\nexport default hasBrace;\n", encoding="utf-8"
    )
    out = check_lang_syntax(f)
    assert out["ok"] is True, out
    assert "brace" not in out["engine"]


@pytest.mark.parametrize("suffix", ["jsx", "tsx"])
def test_without_a_jsx_parser_the_file_is_skipped_not_red(tmp_path, suffix, no_jsx_parser):
    """Same convention as an extension with no oracle: ok=True, engine "skip"."""
    f = tmp_path / f"Broken.{suffix}"
    f.write_text("const A = () => { return <div>hi</div>;\n", encoding="utf-8")
    out = check_lang_syntax(f)
    assert out["ok"] is True
    assert out["engine"].startswith("skip")
    assert out["error"] == ""


def test_a_jsx_parser_when_present_gets_the_verdict(tmp_path, monkeypatch):
    """With a JSX-aware parser on PATH its exit status is the answer."""
    from remedy.core import build_lang_oracle as O

    monkeypatch.setattr(O, "_TOOLCHAIN", {"esbuild": "/fake/esbuild", "tsc": None})
    calls: list[list[str]] = []

    def fake_run(cmd, **_):
        calls.append(cmd)
        return False, 'Expected "}" but found end of file'

    monkeypatch.setattr(O, "_run", fake_run)
    f = tmp_path / "Broken.jsx"
    f.write_text("const A = () => { return <div>hi</div>;\n", encoding="utf-8")
    out = check_lang_syntax(f)
    assert out["ok"] is False
    assert "end of file" in out["error"]
    assert out["engine"] == "esbuild (jsx)"
    assert calls and calls[0][0] == "/fake/esbuild" and calls[0][-1] == str(f)


def test_plain_javascript_still_goes_to_a_real_parser_when_node_is_present(tmp_path):
    """The fallback is for .jsx only; .js keeps the stronger check."""
    f = tmp_path / "plain.js"
    f.write_text("function add(a, b) { return a + b; }\n", encoding="utf-8")
    out = check_lang_syntax(f)
    assert out["ok"] is True
    assert out["engine"] in ("node --check", "brace")


# --- batches -----------------------------------------------------------------


def _write(tmp_path, name, body="const a = 1;\n"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_only_source_files_are_checked(tmp_path):
    paths = [
        _write(tmp_path, "a.ts"),
        _write(tmp_path, "notes.md", "# hi\n"),
        _write(tmp_path, "data.json", "{}\n"),
    ]
    checked = {r["path"] for r in check_lang_paths(paths)}
    assert any(p.endswith("a.ts") for p in checked)
    assert not any(p.endswith("notes.md") for p in checked)


def test_the_same_path_twice_is_checked_once(tmp_path):
    p = _write(tmp_path, "a.ts")
    assert len(check_lang_paths([p, p, p])) == 1


def test_blank_entries_are_skipped(tmp_path):
    assert check_lang_paths(["", "   ", None]) == []


def test_no_paths_at_all_is_not_an_error():
    assert check_lang_paths([]) == []
    assert check_lang_paths(None) == []


def test_a_batch_is_bounded(tmp_path):
    """A 400-file write set must not turn the gate into the slow part."""
    paths = [_write(tmp_path, f"f{i}.ts") for i in range(30)]
    assert len(check_lang_paths(paths)) <= 12


def test_a_stray_prose_fragment_is_not_treated_as_a_path(tmp_path):
    """Write sets sometimes carry a sentence; do not stat it as a file."""
    assert check_lang_paths(["I edited the parser file"]) == []


def test_a_real_path_containing_a_space_is_still_checked(tmp_path):
    p = _write(tmp_path, "my file.ts")
    assert len(check_lang_paths([p])) == 1


# --- picking the verify command ----------------------------------------------


def test_a_rust_crate_gets_cargo_test(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    assert scoped_lang_verify(tmp_path, ["src/lib.rs"]) == "cargo test"


def test_rust_without_a_manifest_gets_nothing(tmp_path):
    """`cargo test` outside a crate fails for reasons unrelated to the edit."""
    assert scoped_lang_verify(tmp_path, ["src/lib.rs"]) == ""


def test_a_go_module_gets_go_test(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    assert scoped_lang_verify(tmp_path, ["main.go"]) == "go test ./..."


def test_go_without_a_module_gets_nothing(tmp_path):
    assert scoped_lang_verify(tmp_path, ["main.go"]) == ""


@pytest.mark.parametrize("suffix", ["ts", "tsx", "js", "jsx"])
def test_a_node_project_gets_npm_test(tmp_path, suffix):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert scoped_lang_verify(tmp_path, [f"src/app.{suffix}"]) == "npm test"


def test_a_pnpm_workspace_gets_pnpm_test(tmp_path):
    """Running npm in a pnpm workspace fails for the wrong reason."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    assert scoped_lang_verify(tmp_path, ["src/app.ts"]) == "pnpm test"


def test_a_yarn_project_gets_yarn_test(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("", encoding="utf-8")
    assert scoped_lang_verify(tmp_path, ["src/app.ts"]) == "yarn test"


def test_the_lockfile_wins_over_the_default(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("", encoding="utf-8")
    assert scoped_lang_verify(tmp_path, ["src/app.ts"]) in ("pnpm test", "yarn test")


def test_typescript_without_a_package_json_gets_nothing(tmp_path):
    assert scoped_lang_verify(tmp_path, ["src/app.ts"]) == ""


def test_an_empty_write_set_gets_nothing(tmp_path):
    assert scoped_lang_verify(tmp_path, []) == ""


def test_a_python_only_write_set_is_left_to_the_python_oracle(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert scoped_lang_verify(tmp_path, ["src/app.py"]) == ""


# --- the suffix table --------------------------------------------------------


@pytest.mark.parametrize(
    "suffix", [".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".c", ".h", ".cpp"]
)
def test_the_languages_this_module_claims_to_cover(suffix):
    assert suffix in LANG_SUFFIXES


@pytest.mark.parametrize("suffix", [".md", ".txt", ".toml", ".png"])
def test_things_that_are_not_source_are_not_in_the_table(suffix):
    assert suffix not in LANG_SUFFIXES


# --- the shared entry point the build engine actually calls ------------------


def test_a_c_file_reaches_a_gate_through_check_paths_syntax(tmp_path):
    """build_syntax dispatches here for non-Python; gcc may or may not exist,
    but either engine has to come back with a verdict rather than nothing."""
    src = tmp_path / "hello.c"
    src.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    results = check_paths_syntax([str(src)])
    assert results
    assert "ok" in results[0]


def test_a_broken_c_file_is_caught_through_the_same_entry_point(tmp_path):
    src = tmp_path / "broken.c"
    src.write_text("int main(void) { return 0;\n", encoding="utf-8")
    results = check_paths_syntax([str(src)])
    assert results
    assert results[0]["ok"] is False


# --- Rust gate: cargo-first, never ``-o NUL``, dep errors are not syntax errors ---


def _tauri_main_rs() -> str:
    return (
        "#![cfg_attr(not(debug_assertions), windows_subsystem = \"windows\")]\n"
        "fn main() {\n    guitar_remedy_lib::run()\n}\n"
    )


def test_a_tauri_crate_does_not_use_cargo_check_as_a_syntax_gate(tmp_path, monkeypatch):
    """Desktop crates take too long for a write-gate; unmatched braces still fail."""
    import remedy.core.build_lang_oracle as oracle

    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname="app"\n[dependencies]\ntauri = "2"\n',
        encoding="utf-8",
    )
    f = tmp_path / "main.rs"
    f.write_text(_tauri_main_rs(), encoding="utf-8")
    calls: list[list[str]] = []

    def fake_which(name):
        return {"cargo": "cargo", "rustc": "rustc"}.get(name)

    def fake_run(cmd, *, cwd=None, timeout_s=20.0):
        calls.append(list(cmd))
        return False, (
            "error[E0433]: failed to resolve: use of undeclared crate or module "
            "`guitar_remedy_lib`"
        )

    monkeypatch.setattr(oracle, "_which", fake_which)
    monkeypatch.setattr(oracle, "_run", fake_run)
    out = oracle.check_lang_syntax(f)
    assert out["ok"] is True
    assert not any(c[:2] == ["cargo", "check"] for c in calls)
    assert out["engine"].startswith("brace")


def test_rust_in_a_cargo_crate_uses_cargo_check(tmp_path, monkeypatch):
    import remedy.core.build_lang_oracle as oracle

    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    f = src / "main.rs"
    f.write_text(_tauri_main_rs(), encoding="utf-8")
    calls: list[list[str]] = []

    def fake_which(name):
        return {"cargo": "cargo", "rustc": "rustc"}.get(name)

    def fake_run(cmd, *, cwd=None, timeout_s=20.0):
        calls.append(list(cmd))
        assert cwd == tmp_path
        return True, ""

    monkeypatch.setattr(oracle, "_which", fake_which)
    monkeypatch.setattr(oracle, "_run", fake_run)
    out = oracle.check_lang_syntax(f)
    assert out["ok"] is True
    assert out["engine"] == "cargo check"
    assert calls and calls[0][:2] == ["cargo", "check"]
    assert all("NUL" not in c for c in calls[0])


def test_bare_rustc_never_writes_to_nul_and_dep_errors_are_noise(tmp_path, monkeypatch):
    import remedy.core.build_lang_oracle as oracle

    f = tmp_path / "main.rs"
    f.write_text(_tauri_main_rs(), encoding="utf-8")
    calls: list[list[str]] = []

    def fake_which(name):
        return "rustc" if name == "rustc" else None

    def fake_run(cmd, *, cwd=None, timeout_s=20.0):
        calls.append(list(cmd))
        return False, (
            "error[E0433]: failed to resolve: use of undeclared crate or module "
            "`guitar_remedy_lib`"
        )

    monkeypatch.setattr(oracle, "_which", fake_which)
    monkeypatch.setattr(oracle, "_run", fake_run)
    out = oracle.check_lang_syntax(f)
    assert "-o" not in calls[0] and "NUL" not in calls[0]
    assert "--out-dir" in calls[0]
    assert out["ok"] is True, out
    assert out["engine"].startswith("brace")


def test_bare_rustc_real_syntax_error_still_fails(tmp_path, monkeypatch):
    import remedy.core.build_lang_oracle as oracle

    f = tmp_path / "lib.rs"
    f.write_text("fn broken( {\n", encoding="utf-8")
    monkeypatch.setattr(oracle, "_which", lambda n: "rustc" if n == "rustc" else None)
    monkeypatch.setattr(
        oracle, "_run", lambda cmd, *, cwd=None, timeout_s=20.0: (False, "error: expected pattern")
    )
    out = oracle.check_lang_syntax(f)
    assert out["ok"] is False
    assert out["engine"] == "rustc --emit=metadata"


def test_cargo_check_timeout_does_not_fail_closed(tmp_path, monkeypatch):
    import remedy.core.build_lang_oracle as oracle

    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    f = tmp_path / "lib.rs"
    f.write_text("pub fn ok() {}\n", encoding="utf-8")
    monkeypatch.setattr(oracle, "_which", lambda n: "cargo")
    monkeypatch.setattr(
        oracle,
        "_run",
        lambda cmd, *, cwd=None, timeout_s=20.0: (False, "Command 'cargo' timed out after 120 seconds"),
    )
    out = oracle.check_lang_syntax(f)
    assert out["ok"] is True
    assert "timed out" in out["engine"]
