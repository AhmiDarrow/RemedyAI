"""Phase 3: Zig ``translate_posix_to_host`` matches translate_cmd fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remedy.core.computer import host_binding as hb
from remedy.execution.host.translate import translate_posix_to_host
from remedy.runtime import native_runtime

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "host_ir" / "translate_cmd.json"


def _load_cases() -> list[dict]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return list(raw["cases"])


@pytest.fixture(scope="module")
def _require_abi4_core():
    if native_runtime._core_library_path() is None:
        pytest.skip("remedy_core is not built in this checkout")
    library = native_runtime.core_library()
    assert int(library.remedy_core_abi_version()) == 5
    assert hasattr(library, "remedy_core_translate_posix_to_host")


@pytest.mark.usefixtures("_require_abi4_core")
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_translate_posix_matches_fixture(case: dict) -> None:
    inp = case["input"]
    expected = case["expected"]
    fake_rg = r"C:\tools\rg.exe"
    rg = fake_rg if expected.get("rg_placeholder") else ""
    got = hb.translate_posix_to_host(
        inp["command"],
        host=inp.get("host"),
        rg_path=rg or None,
    )
    exp_text = expected["text"]
    if expected.get("rg_placeholder"):
        exp_text = exp_text.replace("<RG>", fake_rg)
    assert got["text"] == exp_text
    assert bool(got.get("changed")) == bool(expected["changed"])
    assert bool(got.get("untranslatable")) == bool(expected["untranslatable"])
    assert bool(got.get("noop")) == bool(expected["noop"])


@pytest.mark.usefixtures("_require_abi4_core")
def test_translate_posix_to_host_routes_through_zig() -> None:
    """Production wrapper must call Zig (no Python rewrite twin)."""
    got = translate_posix_to_host("mkdir -p a", host="cmd")
    assert got.changed
    assert "if not exist" in got.text
    assert 'mkdir "a"' in got.text
