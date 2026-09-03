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
