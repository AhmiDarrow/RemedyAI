"""Sidecar PE version resource helpers (Defender / Wacatac mitigations)."""

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


def test_version_tuple_semver(bd) -> None:
    assert bd._version_tuple("0.11.1") == (0, 11, 1, 0)
    assert bd._version_tuple("1.2.3") == (1, 2, 3, 0)
    assert bd._version_tuple("0.11.1-rc1") == (0, 11, 1, 0)
    assert bd._version_tuple("2") == (2, 0, 0, 0)


def test_sidecar_bin_paths_linux_has_no_exe(bd, monkeypatch) -> None:
    monkeypatch.setattr(bd.sys, "platform", "linux")
    monkeypatch.setenv("TAURI_ENV_TARGET_TRIPLE", "x86_64-unknown-linux-gnu")
    plain, triple = bd.sidecar_bin_paths()
    assert plain.name == "remedy-desktop"
    assert triple.name == "remedy-desktop-x86_64-unknown-linux-gnu"


def test_sidecar_bin_paths_windows_keeps_exe(bd, monkeypatch) -> None:
    monkeypatch.setattr(bd.sys, "platform", "win32")
    monkeypatch.setenv("TAURI_ENV_TARGET_TRIPLE", "x86_64-pc-windows-msvc")
    plain, triple = bd.sidecar_bin_paths()
    assert plain.name == "remedy-desktop.exe"
    assert triple.name == "remedy-desktop-x86_64-pc-windows-msvc.exe"


def test_write_sidecar_version_file_has_product_identity(bd, tmp_path, monkeypatch) -> None:
    """Empty PE identity is a Defender ML signal — resource must name Remedy."""
    # Redirect generated file under tmp_path
    monkeypatch.setattr(bd, "ROOT", tmp_path)
    out = bd.write_sidecar_version_file("0.11.1")
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    for needle in (
        "CompanyName",
        "Remedy",
        "FileDescription",
        "Remedy Desktop",
        "FileVersion",
        "0.11.1",
        "OriginalFilename",
        "remedy-desktop.exe",
        "ProductName",
        "ProductVersion",
        "filevers=(0, 11, 1, 0)",
    ):
        assert needle in text, f"missing {needle!r}"


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
