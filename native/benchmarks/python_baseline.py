"""Equivalent Python kernel baselines; not an end-to-end product comparison."""

from __future__ import annotations

import json
import os
import statistics
import struct
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

HEADER = struct.Struct("<4sHHII16s")
PAYLOAD = bytes(1024)
FRAME = HEADER.pack(b"RMDY", 1, 2, 0, len(PAYLOAD), bytes(16)) + PAYLOAD


def measure(name: str, operation: Callable[[], object], iterations: int) -> None:
    samples: list[float] = []
    for _ in range(5):
        started = time.perf_counter_ns()
        for _ in range(iterations):
            operation()
        samples.append((time.perf_counter_ns() - started) / iterations)
    print(f"{name}: {statistics.median(samples):.1f} ns/op ({iterations} iterations)")


def frame_round_trip() -> tuple[tuple[object, ...], bytes]:
    return HEADER.unpack(FRAME[: HEADER.size]), bytes(FRAME[HEADER.size :])


def main() -> None:
    measure("PythonFrame1KiBRoundTrip", frame_round_trip, 200_000)
    value = {"kind": "ToolResult", "payload": "x" * 1024, "ok": True}
    measure("PythonJSON1KiBRoundTrip", lambda: json.loads(json.dumps(value)), 20_000)
    records = [f"key-{index} native retrieval benchmark" for index in range(1_000)]
    measure("PythonMemorySearch1000", lambda: [row for row in records if "retrieval" in row][:20], 5_000)

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory, "sample.bin")
        path.write_bytes(PAYLOAD)
        measure("PythonFilesystemStat", path.stat, 20_000)

    command = ["cmd.exe", "/d", "/c", "exit", "0"] if os.name == "nt" else ["/bin/true"]
    measure(
        "PythonProcessSpawn",
        lambda: subprocess.run(command, check=True, capture_output=True),
        25,
    )


if __name__ == "__main__":
    main()
