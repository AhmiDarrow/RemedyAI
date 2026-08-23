"""SPDX fallback for packages that name a licence but ship no file."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gen_third_party_notices.py"


def _load():
    spec = importlib.util.spec_from_file_location("gen_third_party_notices", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gen():
    return _load()


def test_spdx_ids_split_expressions(gen) -> None:
    assert gen.spdx_ids("MIT") == ["MIT"]
    assert gen.spdx_ids("MIT/Apache-2.0") == ["MIT", "Apache-2.0"]
    assert gen.spdx_ids("MIT OR Apache-2.0") == ["MIT", "Apache-2.0"]
    assert gen.spdx_ids("MPL-2.0") == ["MPL-2.0"]
    assert gen.spdx_ids("BSD-3-Clause") == ["BSD-3-Clause"]
    assert gen.spdx_ids("see project") == []


def test_mpl_fallback_attaches_standard_text(gen) -> None:
    c = gen.Component("Rust crate", "selectors", "0.36.1", "MPL-2.0", "", [], "")
    gen.apply_spdx_fallback(c)
    assert c.texts
    assert "Mozilla Public License Version 2.0" in c.texts[0]
    assert "SPDX standard form" in c.note


def test_mit_fallback_uses_package_copyright_not_another_project(gen) -> None:
    c = gen.Component(
        "npm package",
        "@uiw/react-codemirror",
        "4.25.11",
        "MIT",
        "Copyright (c) uiwjs",
        [],
        "",
    )
    gen.apply_spdx_fallback(c)
    assert c.texts
    assert "Copyright (c) uiwjs" in c.texts[0]
    assert "Permission is hereby granted" in c.texts[0]
    assert "aio-libs" not in c.texts[0]


def test_fallback_does_not_replace_shipped_text(gen) -> None:
    shipped = "Copyright (c) Example\n\nPermission is hereby granted..."
    c = gen.Component("Rust crate", "seahash", "4.1.0", "MIT", "", [shipped], "")
    gen.apply_spdx_fallback(c)
    assert c.texts == [shipped]


def test_spdx_files_exist_for_fallback_ids(gen) -> None:
    for spdx_id in ("MIT", "Apache-2.0", "BSD-3-Clause", "MPL-2.0", "ISC"):
        assert (gen.SPDX_DIR / f"{spdx_id}.txt").is_file(), spdx_id


def test_binding_license_covers_use_notices_and_disclaimer() -> None:
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    for needle in (
        "By installing, copying, or using the Software, you agree",
        "THIRD_PARTY_NOTICES.txt",
        "Every action the Software takes",
        "robots.txt",
        'THE SOFTWARE IS PROVIDED "AS IS"',
        "defend, indemnify",
        "Nothing in this license prevents Ahmi Darrow from charging",
        "dual licensing",
    ):
        assert needle in text, needle
