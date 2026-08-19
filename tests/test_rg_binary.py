"""rg locator / ensure helpers (no network required for find_* tests)."""

from pathlib import Path

from remedy.core.rg_binary import (
    RG_VERSION,
    engine_label,
    find_bundled_rg,
    find_rg,
    find_system_rg,
)


def test_engine_label():
    assert engine_label("bundled") == "bundled-rg"
    assert engine_label("system") == "rg"
    assert engine_label("none") == "python"


def test_find_rg_returns_tuple(tmp_path: Path):
    path, source = find_rg(tmp_path)
    assert source in ("bundled", "system", "none")
    if path is not None:
        assert path.exists()
        assert source in ("bundled", "system")


def test_find_bundled_prefers_home_bin(tmp_path: Path):
    import sys

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    name = "rg.exe" if sys.platform == "win32" else "rg"
    fake = bin_dir / name
    fake.write_bytes(b"MZ" if sys.platform == "win32" else b"#!/bin/sh\n")
    found = find_bundled_rg(tmp_path)
    assert found is not None
    assert found.name == name


def test_version_pin():
    assert RG_VERSION
    assert RG_VERSION[0].isdigit()


def test_system_rg_optional():
    # May or may not exist on the machine, but the answer has to be usable:
    # either a path that is really there, or nothing.
    found = find_system_rg()
    assert found is None or found == "" or Path(found).is_file(), (
        f"find_system_rg returned {found!r}, which is not a file"
    )
