#!/usr/bin/env python3
"""Basic smoke of a published Windows ARM64 remedy-runtime + remedy_core pair.

Used by Desktop Release's verify-native-assets job on windows-11-arm after
checksums match. The Go binary must run natively and report windows/arm64;
the Zig DLL must load and export remedy_core_abi_version.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: smoke_windows_arm_runtime.py DIRECTORY", file=sys.stderr)
        return 2
    directory = Path(argv[0])
    exe = directory / "remedy-runtime.exe"
    dll = directory / "remedy_core.dll"
    if not exe.is_file() or not dll.is_file():
        print(f"missing {exe.name if not exe.is_file() else dll.name} in {directory}", file=sys.stderr)
        return 1
    raw = subprocess.check_output([str(exe), "--probe"], text=True)
    probe = json.loads(raw)
    if probe.get("os") != "windows" or probe.get("arch") != "arm64":
        print(f"unexpected probe: {probe}", file=sys.stderr)
        return 1
    if probe.get("status") != "ready":
        print(f"probe not ready: {probe}", file=sys.stderr)
        return 1
    print(f"probe ok {probe}")
    lib = ctypes.WinDLL(str(dll))
    lib.remedy_core_abi_version.restype = ctypes.c_uint32
    abi = int(lib.remedy_core_abi_version())
    if abi <= 0:
        print(f"remedy_core_abi_version returned {abi}", file=sys.stderr)
        return 1
    print(f"dll ok abi={abi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
