"""Working out how to check the work — before the model is asked.

Falsification is not optional in the build engine, so the machine discovers the
verify command itself rather than waiting for the model to propose one. Two
ways this goes wrong, and both are worse than finding nothing:

* Naming a command that cannot run here. `pytest -q` in a Rust crate fails for
  reasons that have nothing to do with the change, and the model then spends
  the turn chasing a phantom.
* Naming one for a tree with nothing to run. A folder of HTML has no test
  command, and inventing one manufactures a red that can never go green.

All filesystem-driven, so these build real little project trees.
"""

from __future__ import annotations

import os

import pytest

from remedy.core.build_oracle import _discover_c_verify_command, discover_verify_command


class RT:
    def __init__(self, root) -> None:
        self.root = root

    def effective_project_path(self):
        return self.root

    def resolve_tool_path(self, path, *, for_write=False):
        return self.root / (path or ".")


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A project root whose stack fingerprint suggests nothing by default."""
    monkeypatch.setattr(
        "remedy.core.project_fingerprint.fingerprint_path",
        lambda root: type("F", (), {"suggest_verify": ""})(),
    )
    return tmp_path


def touch(root, *names, body=""):
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


# --- the fingerprint wins when it has an answer -------------------------------


def test_a_stack_fingerprint_is_used_when_it_has_one(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "remedy.core.project_fingerprint.fingerprint_path",
        lambda root: type("F", (), {"suggest_verify": "make check"})(),
    )
    assert discover_verify_command(RT(tmp_path)) == "make check"


def test_a_path_argument_narrows_the_search(tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        "remedy.core.project_fingerprint.fingerprint_path",
        lambda root: seen.append(root) or type("F", (), {"suggest_verify": "x"})(),
    )
    sub = tmp_path / "packages" / "api"
    sub.mkdir(parents=True)
    discover_verify_command(RT(tmp_path), path="packages/api")
    assert str(seen[0]).endswith("api")


def test_a_file_path_is_read_as_its_directory(tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(
        "remedy.core.project_fingerprint.fingerprint_path",
        lambda root: seen.append(root) or type("F", (), {"suggest_verify": "x"})(),
    )
    touch(tmp_path, "src/app.py")
    discover_verify_command(RT(tmp_path), path="src/app.py")
    assert str(seen[0]).endswith("src")


# --- python -------------------------------------------------------------------


@pytest.mark.parametrize("marker", ["pyproject.toml", "pytest.ini"])
def test_a_python_project_gets_pytest(project, marker):
    touch(project, marker)
    assert discover_verify_command(RT(project)) == "pytest -q"


def test_a_tests_directory_with_real_tests_gets_pytest(project):
    touch(project, "tests/test_thing.py", body="def test_x(): pass\n")
    assert discover_verify_command(RT(project)) == "pytest -q"


def test_the_go_style_test_suffix_counts_too(project):
    touch(project, "tests/thing_test.py", body="def test_x(): pass\n")
    assert discover_verify_command(RT(project)) == "pytest -q"


def test_a_tests_directory_holding_only_the_seed_smoke_test_does_not(project):
    """An empty tests/ from a bad scaffold is not evidence of a test suite."""
    touch(project, "tests/test_remedy_build_smoke.py", body="def test_x(): pass\n")
    assert discover_verify_command(RT(project)) == ""


def test_an_empty_tests_directory_does_not(project):
    (project / "tests").mkdir()
    assert discover_verify_command(RT(project)) == ""


# --- other stacks -------------------------------------------------------------


def test_a_node_project_gets_npm_test(project):
    touch(project, "package.json", body="{}")
    assert discover_verify_command(RT(project)) == "npm test"


def test_a_rust_crate_gets_cargo_test(project):
    touch(project, "Cargo.toml", body="[package]\n")
    assert discover_verify_command(RT(project)) == "cargo test"


def test_a_go_module_gets_go_test(project):
    touch(project, "go.mod", body="module x\n")
    assert discover_verify_command(RT(project)) == "go test ./..."


def test_python_wins_over_node_when_both_are_present(project):
    """A python repo with a package.json for tooling is still a python repo."""
    touch(project, "pyproject.toml", "package.json")
    assert discover_verify_command(RT(project)) == "pytest -q"


# --- a tree with nothing to run ----------------------------------------------


def test_a_static_page_gets_no_command(project):
    """Inventing one here manufactures a red that can never go green."""
    touch(project, "index.html", body="<html></html>")
    assert discover_verify_command(RT(project)) == ""


def test_a_folder_of_html_gets_no_command(project):
    touch(project, "about.html", "contact.html")
    assert discover_verify_command(RT(project)) == ""


def test_html_alongside_a_python_project_does_not_suppress_pytest(project):
    touch(project, "index.html", "pyproject.toml")
    assert discover_verify_command(RT(project)) == "pytest -q"


def test_an_empty_directory_gets_no_command(project):
    assert discover_verify_command(RT(project)) == ""


def test_a_runtime_with_no_project_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        "remedy.core.project_fingerprint.fingerprint_path",
        lambda root: type("F", (), {"suggest_verify": ""})(),
    )

    class NoProject:
        def effective_project_path(self):
            raise RuntimeError("no project set")

    assert discover_verify_command(NoProject()) == ""


# --- C, which the fingerprint does not cover ----------------------------------


def _gcc_line(stem):
    return (
        f"gcc -o {stem}.exe {stem}.c && {stem}.exe"
        if os.name == "nt"
        else f"gcc -o {stem} {stem}.c && ./{stem}"
    )


def test_a_single_c_file_gets_compiled_and_run(project):
    touch(project, "hello.c", body="int main(void){return 0;}\n")
    assert discover_verify_command(RT(project)) == _gcc_line("hello")


@pytest.mark.parametrize("name", ["hello.c", "main.c", "app.c", "program.c"])
def test_the_conventional_entrypoint_names_are_recognised(project, name):
    touch(project, name, body="int main(void){return 0;}\n")
    assert discover_verify_command(RT(project)) == _gcc_line(name[:-2])


def test_with_several_c_files_the_one_with_main_is_chosen(tmp_path):
    touch(tmp_path, "util.c", body="int add(int a){return a;}\n")
    touch(tmp_path, "entry.c", body="int main(void){return 0;}\n")
    assert _discover_c_verify_command(tmp_path) == _gcc_line("entry")


def test_with_no_main_anywhere_the_first_file_is_used(tmp_path):
    touch(tmp_path, "a.c", body="int f(void){return 1;}\n")
    touch(tmp_path, "b.c", body="int g(void){return 2;}\n")
    assert _discover_c_verify_command(tmp_path).startswith("gcc -o a")


def test_a_directory_with_no_c_files_gets_nothing(tmp_path):
    assert _discover_c_verify_command(tmp_path) == ""


def test_a_path_that_is_not_a_directory_gets_nothing(tmp_path):
    f = tmp_path / "notadir.c"
    f.write_text("int main(void){return 0;}\n", encoding="utf-8")
    assert _discover_c_verify_command(f) == ""


def test_a_c_tree_overrides_a_pytest_suggestion(tmp_path, monkeypatch):
    """The fingerprint calls almost anything python; gcc is what runs here."""
    monkeypatch.setattr(
        "remedy.core.project_fingerprint.fingerprint_path",
        lambda root: type("F", (), {"suggest_verify": "pytest -q"})(),
    )
    touch(tmp_path, "hello.c", body="int main(void){return 0;}\n")
    assert discover_verify_command(RT(tmp_path)) == _gcc_line("hello")


def test_a_real_python_project_containing_c_extensions_keeps_pytest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "remedy.core.project_fingerprint.fingerprint_path",
        lambda root: type("F", (), {"suggest_verify": "pytest -q"})(),
    )
    touch(tmp_path, "pyproject.toml", "speedup.c")
    assert discover_verify_command(RT(tmp_path)) == "pytest -q"


def test_a_non_pytest_suggestion_is_not_overridden_by_c_files(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "remedy.core.project_fingerprint.fingerprint_path",
        lambda root: type("F", (), {"suggest_verify": "make test"})(),
    )
    touch(tmp_path, "hello.c", body="int main(void){return 0;}\n")
    assert discover_verify_command(RT(tmp_path)) == "make test"
