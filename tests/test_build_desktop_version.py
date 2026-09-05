"""Desktop staging helpers — Go runtime + Zig core into desktop/bin."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_DESKTOP = ROOT / "scripts" / "build_desktop.py"


def _load_build_desktop():
    spec = importlib.util.spec_from_file_location("build_desktop", BUILD_DESKTOP)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bd():
    return _load_build_desktop()


def test_runtime_bin_paths_linux_has_no_exe(bd, monkeypatch) -> None:
    monkeypatch.setattr(bd.sys, "platform", "linux")
    monkeypatch.setenv("TAURI_ENV_TARGET_TRIPLE", "x86_64-unknown-linux-gnu")
    plain, triple = bd.runtime_bin_paths()
    assert plain.name == "remedy-runtime"
    assert triple.name == "remedy-runtime-x86_64-unknown-linux-gnu"


def test_runtime_bin_paths_windows_keeps_exe(bd, monkeypatch) -> None:
    monkeypatch.setattr(bd.sys, "platform", "win32")
    monkeypatch.setenv("TAURI_ENV_TARGET_TRIPLE", "x86_64-pc-windows-msvc")
    plain, triple = bd.runtime_bin_paths()
    assert plain.name == "remedy-runtime.exe"
    assert triple.name == "remedy-runtime-x86_64-pc-windows-msvc.exe"


def test_no_pyinstaller_or_remedy_desktop_sidecar(bd) -> None:
    """Packaging path stages runtime+core; no frozen remedy-desktop build."""
    source = BUILD_DESKTOP.read_text(encoding="utf-8")
    for banned in (
        "PyInstaller",
        "pyinstaller",
        "ensure_pyinstaller",
        "write_sidecar_version_file",
        "core_library_add_binary",
        "SIDECAR_EXCLUDES",
        "--onefile",
        "--add-binary",
    ):
        assert banned not in source, banned
    assert "cmd/remedy-runtime" in source
    assert 'glob("remedy-desktop*")' in source
    assert not hasattr(bd, "write_sidecar_version_file")
    assert not hasattr(bd, "ensure_pyinstaller")
    assert not hasattr(bd, "core_library_add_binary")
    assert hasattr(bd, "runtime_bin_paths")
    assert hasattr(bd, "stage_core")
    assert hasattr(bd, "build_runtime")


def test_stage_removes_legacy_remedy_desktop(bd, tmp_path, monkeypatch) -> None:
    """Staging must delete leftover remedy-desktop* so they cannot be preferred."""
    desktop_bin = tmp_path / "desktop" / "bin"
    desktop_bin.mkdir(parents=True)
    legacy = desktop_bin / "remedy-desktop.exe"
    legacy.write_bytes(b"stale")
    (desktop_bin / "remedy-desktop-x86_64-pc-windows-msvc.exe").write_bytes(b"stale")

    monkeypatch.setattr(bd, "ROOT", tmp_path)
    monkeypatch.setattr(bd, "DESKTOP_BIN", desktop_bin)
    monkeypatch.setattr(bd, "sync_versions", lambda: "0.0.0")
    monkeypatch.setattr(bd, "check_third_party_notices", lambda: None)
    monkeypatch.setattr(bd, "build_runtime", lambda: None)
    monkeypatch.setattr(bd, "stage_core", lambda *a, **k: None)

    bd.build(skip_runtime=True, skip_core=True)
    assert list(desktop_bin.glob("remedy-desktop*")) == []


def test_tauri_external_bin_is_runtime_only() -> None:
    """Phase 6: packaged Desktop launches remedy-runtime, never remedy-desktop."""
    import json

    for rel in (
        "desktop/src-tauri/tauri.conf.json",
        "desktop/src-tauri/tauri.linux.conf.json",
    ):
        conf = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        bins = conf.get("bundle", {}).get("externalBin") or []
        assert bins == ["../bin/remedy-runtime"], rel
        resources = conf.get("bundle", {}).get("resources") or {}
        resource_text = json.dumps(resources)
        assert "remedy-desktop" not in resource_text, rel

    windows = json.loads(
        (ROOT / "desktop/src-tauri/tauri.windows.conf.json").read_text(encoding="utf-8")
    )
    assert "remedy-desktop" not in json.dumps(windows.get("bundle", {}).get("resources") or {})

    lib_rs = (ROOT / "desktop/src-tauri/src/lib.rs").read_text(encoding="utf-8")
    assert "fn find_python_sidecar" not in lib_rs
    assert "find_runtime_sidecar" in lib_rs
    # Launch path must not soft-pick legacy PyInstaller sidecars from desktop/bin.
    assert 'join("remedy-desktop' not in lib_rs
    assert "Go remedy-runtime owns :7400 whenever it is staged" in lib_rs


def test_sync_versions_stamps_package_lock(bd, tmp_path, monkeypatch) -> None:
    """Build-time sync must not leave package-lock.json root version stale."""
    import json

    root = tmp_path
    (root / "pyproject.toml").write_text(
        '[project]\nname = "remedy-ai"\nversion = "0.19.9"\n',
        encoding="utf-8",
    )
    desktop = root / "desktop"
    desktop.mkdir()
    (desktop / "package.json").write_text(
        json.dumps({"name": "remedy-desktop", "version": "0.1.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    (desktop / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "remedy-desktop",
                "version": "0.1.0",
                "lockfileVersion": 3,
                "packages": {"": {"name": "remedy-desktop", "version": "0.1.0"}},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    tauri = root / "desktop" / "src-tauri"
    tauri.mkdir(parents=True)
    (tauri / "tauri.conf.json").write_text(
        json.dumps({"version": "0.1.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    (tauri / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(bd, "ROOT", root)
    out = bd.sync_versions()
    assert out == "0.19.9"

    lock = json.loads((desktop / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == "0.19.9"
    assert lock["packages"][""]["version"] == "0.19.9"
    pkg = json.loads((desktop / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == "0.19.9"
    conf = json.loads((tauri / "tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["version"] == "0.19.9"
