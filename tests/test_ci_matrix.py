"""Keep public CI aligned with the runtimes Remedy actually ships."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow_jobs(name: str) -> dict[str, object]:
    payload = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text("utf-8"))
    assert isinstance(payload, dict)
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict)
    return jobs


def _steps(job: object) -> list[dict[str, object]]:
    assert isinstance(job, dict)
    steps = job.get("steps")
    assert isinstance(steps, list)
    return [step for step in steps if isinstance(step, dict)]


def _run_commands(job: object) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def _step_index(job: object, needle: str) -> int:
    """Index of the first step whose ``run`` contains ``needle``."""
    for index, step in enumerate(_steps(job)):
        if needle in str(step.get("run", "")):
            return index
    raise AssertionError(f"no step runs {needle!r}")


def _uses_step(job: object, action: str) -> dict[str, object]:
    for step in _steps(job):
        if str(step.get("uses", "")).startswith(action + "@"):
            return step
    raise AssertionError(f"no step uses {action}")


ZIG_BUILD = "zig build -Doptimize=ReleaseSafe"


def test_ci_covers_every_shipped_runtime_and_artifact() -> None:
    jobs = _workflow_jobs("ci.yml")
    assert {
        "test",
        "test-windows",
        "desktop",
        "android-connect",
        "rust-desktop",
        "native-core",
    } <= jobs.keys()

    linux_python = _run_commands(jobs["test"])
    for command in (
        "ruff check",
        "mypy",
        "check_mypy_exclude.py",
        "create_app()",
        "check_docs.py",
        "pytest -q",
        "uv build",
    ):
        assert command in linux_python

    windows_python = _run_commands(jobs["test-windows"])
    assert "pytest -q --tb=short" in windows_python
    assert "tests/test_" not in windows_python, "Windows must run the full suite, not a stale file list"

    desktop = _run_commands(jobs["desktop"])
    assert "npm test" in desktop
    assert "npm run build" in desktop

    android = _run_commands(jobs["android-connect"])
    for task in ("testDebugUnitTest", "lintDebug", "assembleDebug", "assembleRelease"):
        assert task in android

    rust = _run_commands(jobs["rust-desktop"])
    rust_job = jobs["rust-desktop"]
    assert isinstance(rust_job, dict)
    rust_tauri_config = str(rust_job.get("env", {}).get("TAURI_CONFIG", ""))
    assert "\"active\":false" in rust_tauri_config
    assert "\"externalBin\":[]" in rust_tauri_config
    assert "\"resources\":null" in rust_tauri_config
    assert rust_job.get("env", {}).get("RUSTFLAGS") == "-D warnings"
    assert "cargo test --locked" in rust
    assert "cargo check --locked" in rust

    native = _run_commands(jobs["native-core"])
    for command in (
        "go test ./...",
        "go test -race ./...",
        "go vet ./...",
        "check-boundaries",
        "benchcheck",
        "zig build test",
        "zig build test -Doptimize=ReleaseSafe",
        "zig build -Doptimize=ReleaseSafe",
    ):
        assert command in native


def test_release_builds_both_desktop_operating_systems_and_native_cores() -> None:
    jobs = _workflow_jobs("desktop-release.yml")
    assert {"build-sidecar", "build-tauri", "build-sidecar-linux", "build-tauri-linux"} <= jobs.keys()

    windows = _run_commands(jobs["build-sidecar"]) + _run_commands(jobs["build-tauri"])
    assert "remedy-runtime.exe" in windows
    assert "remedy-runtime-x86_64-pc-windows-msvc.exe" in windows
    assert "remedy_core.dll" in windows
    assert "tauri build --bundles nsis" in windows

    linux = _run_commands(jobs["build-sidecar-linux"]) + _run_commands(jobs["build-tauri-linux"])
    assert "remedy-runtime" in linux
    assert "remedy-runtime-x86_64-unknown-linux-gnu" in linux
    assert "libremedy_core.so" in linux
    assert "tauri build --bundles deb,appimage" in linux


def test_ci_python_jobs_build_the_zig_core_before_pytest() -> None:
    """Library-backed tests execute in CI instead of skipping.

    Both pytest jobs install the same pinned Zig as native-core, build the
    release-safe core before the suite, and hand its path to the loader via
    REMEDY_NATIVE_CORE_LIB in $GITHUB_ENV.
    """
    jobs = _workflow_jobs("ci.yml")
    pinned = _uses_step(jobs["native-core"], "mlugg/setup-zig")
    for name, library in (
        ("test", "zig-out/lib/libremedy_core.so"),
        ("test-windows", "zig-out/bin/remedy_core.dll"),
    ):
        job = jobs[name]
        setup = _uses_step(job, "mlugg/setup-zig")
        assert setup["uses"] == pinned["uses"], f"{name} must pin the same setup-zig as native-core"
        assert setup["with"] == pinned["with"], f"{name} must use the same Zig version as native-core"
        build = _step_index(job, ZIG_BUILD)
        assert build < _step_index(job, "pytest -q"), f"{name} must build Zig before pytest"
        step = _steps(job)[build]
        assert step.get("working-directory") == "native/zig"
        run = str(step["run"])
        assert "REMEDY_NATIVE_CORE_LIB=" in run, f"{name} must export the core library path"
        assert "GITHUB_ENV" in run, f"{name} must export via $GITHUB_ENV so pytest sees it"
        assert library in run


def test_release_builds_runtime_and_core_without_python_sidecar() -> None:
    """Packaged Desktop is remedy-runtime + Zig core; no PyInstaller sidecar."""
    jobs = _workflow_jobs("desktop-release.yml")
    for name, runtime, core in (
        ("build-sidecar", "remedy-runtime.exe", "remedy_core.dll"),
        ("build-sidecar-linux", "remedy-runtime", "libremedy_core.so"),
    ):
        job = jobs[name]
        runs = _run_commands(job)
        assert "go build" in runs, f"{name}: builds Go remedy-runtime"
        assert ZIG_BUILD in runs, f"{name}: builds Zig core"
        assert runtime in runs, f"{name}: produces {runtime}"
        assert core in runs, f"{name}: ships {core}"
        assert "build_desktop.py" not in runs, f"{name}: must not build remedy-desktop"
        assert "remedy-desktop" not in runs, f"{name}: must not reference remedy-desktop"


def _build_desktop_module():
    import importlib.util
    import sys

    path = ROOT / "scripts" / "build_desktop.py"
    spec = importlib.util.spec_from_file_location("remedy_build_desktop", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_desktop_bundles_the_core_library_at_the_archive_root(tmp_path: Path) -> None:
    """The sidecar carries the Zig core where sys._MEIPASS resolves it."""
    import os
    import sys

    import pytest

    build_desktop = _build_desktop_module()
    source = (ROOT / "scripts" / "build_desktop.py").read_text("utf-8")
    assert "--add-binary" in source
    assert "core_library_add_binary(core_library)" in source, "build() must pass the core to PyInstaller"

    expected = {
        "win32": "remedy_core.dll",
        "darwin": "libremedy_core.dylib",
    }.get(sys.platform, "libremedy_core.so")
    assert build_desktop.core_library_name() == expected
    default = build_desktop.default_core_library_path()
    assert default.name == expected
    assert default.parent.parent == ROOT / "native" / "zig" / "zig-out"

    library = tmp_path / expected
    args = build_desktop.core_library_add_binary(library)
    assert args[0] == "--add-binary"
    src, dest = args[1].rsplit(os.pathsep, 1)
    assert src == str(library)
    assert dest == ".", "destination must be the archive root (sys._MEIPASS)"

    with pytest.raises(SystemExit):
        build_desktop.resolve_core_library(tmp_path / "missing" / expected)
    wrong_name = tmp_path / "other.bin"
    wrong_name.write_bytes(b"")
    with pytest.raises(SystemExit):
        build_desktop.resolve_core_library(wrong_name)

    assert "required_abi = 5" in source
    assert "remedy_core_abi_version()" in source

    parser_help = source[source.index("__main__") :]
    assert "--core-lib" in parser_help


def _prepush_module():
    import importlib.util
    import sys

    path = ROOT / "scripts" / "prepush.py"
    spec = importlib.util.spec_from_file_location("remedy_prepush", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


def test_prepush_gate_runs_every_public_ci_command() -> None:
    """The local gate and public CI must not drift apart.

    Every product command CI runs appears verbatim in scripts/prepush.py, and
    the Rust lane compiles with the same flags CI uses (warnings are errors).
    """
    prepush = _prepush_module()
    local = "\n".join(step.command for lane in prepush.LANES for step in lane.steps)
    jobs = _workflow_jobs("ci.yml")
    expected = {
        "test": (
            "ruff check . --no-fix",
            "uv run mypy",
            "check_mypy_exclude.py",
            "create_app()",
            "check_docs.py",
            "pytest -q --tb=short",
            "uv build",
        ),
        "desktop": ("npm test", "npm run build"),
        "android-connect": ("testDebugUnitTest lintDebug assembleDebug assembleRelease",),
        "rust-desktop": ("cargo test --locked", "cargo check --locked"),
        "native-core": (
            "go test ./...",
            "go test -race ./...",
            "go vet ./...",
            "check-boundaries",
            "benchcheck",
            "zig build test",
            "zig build test -Doptimize=ReleaseSafe",
            "zig build -Doptimize=ReleaseSafe",
        ),
    }
    for job, commands in expected.items():
        in_ci = _run_commands(jobs[job])
        for command in commands:
            assert command in in_ci, f"ci.yml {job} lost {command!r}"
            assert command in local, f"scripts/prepush.py lost {command!r}"

    rust_job = jobs["rust-desktop"]
    assert isinstance(rust_job, dict)
    ci_rust_env = rust_job.get("env", {})
    for step in prepush.RUST.steps:
        assert step.env["RUSTFLAGS"] == ci_rust_env["RUSTFLAGS"]
        assert step.env["TAURI_CONFIG"] == ci_rust_env["TAURI_CONFIG"]

    # The Linux suite is reproduced from this checkout when the host is Windows.
    assert any(step.command == "__wsl_pytest__" for step in prepush.LINUX.steps)
    assert prepush.PYTHON.serial_after_others, "pytest must not share the box with cargo/gradle"


def test_prepush_python_lane_consumes_the_native_lane_core() -> None:
    """Local pytest loads the core the native lane just built, or refuses to run."""
    prepush = _prepush_module()
    assert prepush.PYTHON.requires == ("native",)
    assert "zig build -Doptimize=ReleaseSafe" in "\n".join(s.command for s in prepush.NATIVE.steps)
    assert prepush.with_required_lanes({"python"}) == {"python", "native"}
    assert prepush.LANES.index(prepush.NATIVE) < prepush.LANES.index(prepush.PYTHON)

    library = prepush.native_core_library_path()
    assert library.parent.parent == ROOT / "native" / "zig" / "zig-out"
    assert library.name in {"remedy_core.dll", "libremedy_core.so", "libremedy_core.dylib"}
    pytest_step = next(s for s in prepush.PYTHON.steps if s.name == "pytest")
    assert pytest_step.env["REMEDY_NATIVE_CORE_LIB"] == str(library)
    names = [s.name for s in prepush.PYTHON.steps]
    assert names.index("native core present") < names.index("pytest")

    # The WSL lane builds its own .so (into /tmp, never the checkout's zig-out)
    # and hands it to the loader, when WSL has zig.
    linux = [s.command for s in prepush.LINUX.steps]
    assert linux.index(prepush.WSL_ZIG_BUILD) < linux.index(prepush.WSL_PYTEST)
    assert prepush.WSL_NATIVE_CORE_LIB.startswith("/tmp/")
    assert prepush.REQUIRED_NATIVE_ABI == 5
    if prepush.IS_WINDOWS and prepush.shutil.which("wsl"):
        build = prepush._wsl_zig_build_command()
        assert build and "zig build -Doptimize=ReleaseSafe" in build
        assert f"--prefix {prepush.WSL_ZIG_PREFIX}" in build
        assert "--cache-dir" in build
        assert "rm -f" in build and "libremedy_core.so" in build
        assert f"assert v=={prepush.REQUIRED_NATIVE_ABI}" in build
        assert "wiping zig caches and rebuilding" in build
        pytest_cmd = prepush._wsl_pytest_command()
        assert pytest_cmd
        assert "rm -f" in pytest_cmd and "libremedy_core.so" in pytest_cmd
        assert (f"REMEDY_NATIVE_CORE_LIB={prepush.WSL_NATIVE_CORE_LIB}" in pytest_cmd) == prepush._wsl_has_zig()


def test_prepush_hook_is_wired_and_executable() -> None:
    import subprocess

    hook = ROOT / ".githooks" / "pre-push"
    text = hook.read_text("utf-8")
    assert text.startswith("#!/bin/sh")
    assert "scripts/prepush.py --hook" in text
    listed = subprocess.run(
        ["git", "ls-files", "-s", ".githooks/pre-push"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    if listed:
        assert listed.startswith("100755"), "pre-push must be tracked executable"


def test_prepush_protects_only_public_branches_and_release_tags() -> None:
    prepush = _prepush_module()
    assert set(prepush.PROTECTED_BRANCHES) == {"master", "main"}
    assert prepush.RELEASE_TAG_RE.match("v0.50.2")
    assert prepush.RELEASE_TAG_RE.match("v1.2.3-rc.1")
    assert not prepush.RELEASE_TAG_RE.match("archive/v0.41.5")
    assert not prepush.RELEASE_TAG_RE.match("0.50.2")


def test_release_builds_nothing_before_the_commit_is_verified() -> None:
    jobs = _workflow_jobs("desktop-release.yml")
    verify = jobs["verify"]
    gate = _run_commands(verify)
    assert "actions/workflows/ci.yml/runs?head_sha=" in gate, "release must check CI is green"
    assert "sync_version.py check" in gate, "release must check version surfaces"
    assert "merge-base --is-ancestor" in gate, "release must come from master"
    for name in ("build-sidecar", "build-tauri", "build-sidecar-linux", "build-tauri-linux", "release"):
        job = jobs[name]
        assert isinstance(job, dict)
        needs = job.get("needs")
        needs = [needs] if isinstance(needs, str) else needs
        assert needs and "verify" in needs, f"{name} must need verify"


def test_ci_reports_every_python_version() -> None:
    jobs = _workflow_jobs("ci.yml")
    test = jobs["test"]
    assert isinstance(test, dict)
    assert test["strategy"]["fail-fast"] is False


def test_public_changelog_never_carries_unreleased_notes() -> None:
    """The public repo never mentions unreleased work: [Unreleased] stays empty."""
    prepush = _prepush_module()
    assert prepush.unreleased_section("## [Unreleased]\n\n## [0.1.0] - 2026-01-01\n- x\n") == ""
    assert prepush.unreleased_section("## [Unreleased]\n\n### New thing\n- y\n\n## [0.1.0]\n") == "### New thing\n- y"
    assert prepush.unreleased_section("# Changelog\n\n## [0.1.0]\n- x\n") == ""
    assert prepush.unreleased_section("## [Unreleased]\n\n- only entry at the end\n") != ""

    committed = (ROOT / "CHANGELOG.md").read_text("utf-8")
    assert prepush.unreleased_section(committed) == "", "keep pending notes in docs/UNRELEASED.md"
