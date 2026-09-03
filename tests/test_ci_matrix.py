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


def _run_commands(job: object) -> str:
    assert isinstance(job, dict)
    steps = job.get("steps")
    assert isinstance(steps, list)
    return "\n".join(
        str(step.get("run", "")) for step in steps if isinstance(step, dict)
    )


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
    assert "remedy_core.dll" in windows
    assert "tauri build --bundles nsis" in windows

    linux = _run_commands(jobs["build-sidecar-linux"]) + _run_commands(jobs["build-tauri-linux"])
    assert "remedy-runtime" in linux
    assert "libremedy_core.so" in linux
    assert "tauri build --bundles deb,appimage" in linux


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
