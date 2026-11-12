"""The Windows CI path list must stay complete and must not drift stale."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST_PATH = ROOT / "tests" / "windows_ci_paths.txt"
RUNNER = ROOT / "scripts" / "run_windows_ci_pytest.py"

# Files this surface is required to keep even if they also run on Linux.
REQUIRED_FILES = (
    "tests/test_authorized_spawn_gates.py",
    "tests/test_spawn_authorized.py",
    "tests/test_spawn_piped.py",
    "tests/test_project_write_jail.py",
    "tests/test_jail_scope_contract.py",
    "tests/test_fail_closed_guards.py",
    "tests/test_native_bootstrap.py",
    "tests/test_clean_home_smoke.py",
    "tests/test_first_run_assets.py",
    "tests/test_secret_store.py",
    "tests/test_vault.py",
    "tests/test_web_fetch_ssrf.py",
    "tests/test_host_binding_windows.py",
    "tests/test_win_paths.py",
    "tests/test_python_interpreter_resolve.py",
    "tests/test_serve_runtime_owned.py",
    "tests/test_hidden_process.py",
    "tests/test_process_kill_tree.py",
)

# Tests gated to Windows (or Windows+Linux spawn) so Linux CI will skip them.
_WINDOWS_ONLY_GATES = (
    re.compile(r"skipif\(\s*sys\.platform\s*!=\s*[\"']win32[\"']", re.I),
    re.compile(r"skipif\(\s*sys\.platform\s*not in\s*\(\s*[\"']win32[\"']", re.I),
    re.compile(r"skipif\(\s*os\.name\s*!=\s*[\"']nt[\"']", re.I),
    re.compile(
        r"if sys\.platform != [\"']win32[\"']:\s*\n\s*(?:pytest\.skip|return)",
        re.M,
    ),
)


def _listed_paths() -> list[str]:
    paths: list[str] = []
    for raw in LIST_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            paths.append(line)
    return paths


def test_windows_ci_path_list_and_runner_exist() -> None:
    assert LIST_PATH.is_file()
    assert RUNNER.is_file()


def test_windows_ci_paths_exist_and_are_unique() -> None:
    paths = _listed_paths()
    assert paths, "windows_ci_paths.txt must list at least one test file"
    assert len(paths) == len(set(paths)), "duplicate entries in windows_ci_paths.txt"
    for rel in paths:
        assert (ROOT / rel).is_file(), f"listed path missing: {rel}"
        assert rel.startswith("tests/"), f"unexpected path: {rel}"


def test_windows_ci_paths_include_required_keep_files() -> None:
    listed = set(_listed_paths())
    missing = [p for p in REQUIRED_FILES if p not in listed]
    assert not missing, f"Windows CI dropped required files: {missing}"


def test_windows_ci_paths_include_every_windows_only_file() -> None:
    """A new skipif(sys.platform != 'win32') file must be added to the list."""
    listed = set(_listed_paths())
    missing: list[str] = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(pat.search(text) for pat in _WINDOWS_ONLY_GATES):
            rel = path.relative_to(ROOT).as_posix()
            if rel not in listed:
                missing.append(rel)
    assert not missing, (
        "these files have Windows-only tests but are not in "
        f"tests/windows_ci_paths.txt: {missing}"
    )
