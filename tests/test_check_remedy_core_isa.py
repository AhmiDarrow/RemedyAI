"""ISA gate for remedy_core — refuse Intel SHA-NI bake from host-native Zig builds."""

from __future__ import annotations

from pathlib import Path

import scripts.check_remedy_core_isa as isa

ROOT = Path(__file__).resolve().parents[1]


def test_sha_ni_opcode_names_are_the_comet_lake_killer() -> None:
    assert "sha256msg1" in isa._SHA_NI
    assert "sha256rnds2" in isa._SHA_NI


def test_check_lib_ok_on_baseline_build_when_present() -> None:
    dll = ROOT / "native" / "zig" / "zig-out" / "bin" / "remedy_core.dll"
    so = ROOT / "native" / "zig" / "zig-out" / "lib" / "libremedy_core.so"
    lib = dll if dll.is_file() else so if so.is_file() else None
    if lib is None:
        return
    assert isa.check_lib(lib) == []
