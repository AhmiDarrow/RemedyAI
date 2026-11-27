"""Third-party notices generator: SPDX fallback, Go licence detection, outputs."""

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


def test_binding_license_is_apache_2() -> None:
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in text and "Version 2.0, January 2004" in text
    assert "Limitation of Liability" in text
    assert "Redistribution" in text
    assert (ROOT / "NOTICE").is_file()
    assert not (ROOT / "COMMERCIAL.md").exists()



def test_detect_license_ids_from_text(gen) -> None:
    mit = "Permission is hereby granted, free of charge, to any person obtaining a copy"
    apache = "Apache License Version 2.0, January 2004"
    bsd3 = "Redistribution and use in source and binary forms ... Neither the name of"
    bsd2 = "Redistribution and use in source and binary forms, with or without"
    assert gen.detect_license_ids([mit]) == "MIT"
    assert gen.detect_license_ids([apache]) == "Apache-2.0"
    assert gen.detect_license_ids([bsd3]) == "BSD-3-Clause"
    assert gen.detect_license_ids([bsd2]) == "BSD-2-Clause"
    assert gen.detect_license_ids([mit, apache]) == "MIT AND Apache-2.0"
    assert gen.detect_license_ids(["unknown terms"]) == ""


def test_outputs_and_version_source(gen) -> None:
    assert [p.name for p in gen.OUTS] == ["THIRD_PARTY_NOTICES.txt"] * 2
    assert gen.OUTS[1].parent == ROOT / "desktop" / "remedyui" / "assets"
    assert gen.LICENSE_OUTS[1].parent == gen.OUTS[1].parent
    assert gen.VERSION_FILE == ROOT / "desktop" / "remedyui" / "Cargo.toml"
    assert gen._version().count(".") == 2


def test_render_has_sections_and_no_python(gen) -> None:
    comps = [
        gen.Component("Go module", "example.com/m", "v1.0.0", "MIT", "", ["MIT text"], ""),
        gen.Component("Go module", "example.com/n", "v1.0.0", "", "", [], "licence text not found"),
        gen.Component("npm package", "left-pad", "1.0.0", "MIT", "", ["MIT text"], ""),
    ]
    out = gen.render(comps, {"remedy-ui 1.1.1"})
    assert "GO MODULES (RUNTIME)" in out
    assert "NPM PACKAGES (WEB UI" in out
    assert "First-party components" in out and "remedy-ui 1.1.1" in out
    assert "licence text not found" in out
    assert "Python" not in out
