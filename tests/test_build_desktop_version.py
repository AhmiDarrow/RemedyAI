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
