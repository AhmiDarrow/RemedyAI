"""Refuse remedy_core shared libs that bake host ISA into crypto.

GitHub windows-latest enables SHA-NI. Zig's HMAC-SHA256 then emits
``sha256msg1`` / ``sha256rnds2``. Owners on Comet Lake (no SHA-NI) crash in
``PolicyHashArgv`` with STATUS_ILLEGAL_INSTRUCTION before :7400 listens —
Desktop stuck on "connecting to local server".

``native/zig/build.zig`` defaults to ``cpu_model = .baseline``. This gate
scans the built DLL/SO for Intel SHA-NI mnemonics so a native override cannot
quietly ship again.

Usage:
    python scripts/check_remedy_core_isa.py
    python scripts/check_remedy_core_isa.py path/to/remedy_core.dll
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Intel SHA extensions — illegal on many desktop CPUs still in use.
_SHA_NI = (
    "sha256msg1",
    "sha256msg2",
    "sha256rnds2",
    "sha1msg1",
    "sha1msg2",
    "sha1nexte",
    "sha1rnds4",
)

_CANDIDATES = (
    ROOT / "native" / "zig" / "zig-out" / "bin" / "remedy_core.dll",
    ROOT / "native" / "zig" / "zig-out" / "lib" / "libremedy_core.so",
    ROOT / "desktop" / "bin" / "remedy_core.dll",
    ROOT / "desktop" / "bin" / "libremedy_core.so",
)


def _libs(explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(p) for p in explicit]
    found = [p for p in _CANDIDATES if p.is_file()]
    return found


def _objdump(path: Path) -> str:
    tool = shutil.which("llvm-objdump") or shutil.which("objdump")
    if not tool:
        print(
            "check_remedy_core_isa: llvm-objdump/objdump not on PATH; "
            "skipping disassembly (build.zig baseline still required)",
            file=sys.stderr,
        )
        return ""
    proc = subprocess.run(
        [tool, "-d", "--no-show-raw-insn", str(path)],
        check=False,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if proc.returncode != 0:
        print(
            f"check_remedy_core_isa: objdump failed for {path}: {proc.stderr[:400]}",
            file=sys.stderr,
        )
        return ""
    return proc.stdout.lower()


def check_lib(path: Path) -> list[str]:
    text = _objdump(path)
    if not text:
        return []
    hits = sorted({name for name in _SHA_NI if name in text})
    return hits


def main(argv: list[str]) -> int:
    libs = _libs(argv[1:])
    if not libs:
        print("check_remedy_core_isa: no remedy_core library found to scan", file=sys.stderr)
        return 1

    failed = False
    for lib in libs:
        hits = check_lib(lib)
        if hits:
            failed = True
            print(
                f"FAIL {lib}: contains Intel SHA-NI opcodes ({', '.join(hits)}). "
                "Rebuild with zig baseline CPU (build.zig default); "
                "do not ship -Dcpu=native.",
                file=sys.stderr,
            )
        else:
            print(f"ok {lib}: no SHA-NI opcodes")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
