"""E2E: enqueue rail navigates and wait for rust-host complete (<3s target).

Run while feature/computer-use desktop is up (tauri:dev).
  uv run python scripts/computer_rail_e2e.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from remedy.core.computer.host_bridge import ComputerHostBridge, canonical_home  # noqa: E402


URLS = [
    "https://www.google.com",
    "https://en.wikipedia.org/wiki/Grand_Theft_Auto_V",
    "https://en.wikipedia.org/wiki/Baldur%27s_Gate",
    "https://www.google.com",
    "https://en.wikipedia.org/wiki/Grand_Theft_Auto_V",
    "https://www.google.com",
]


def one(bridge: ComputerHostBridge, url: str, timeout: float = 6.0) -> tuple[bool, float, str]:
    t0 = time.perf_counter()
    job = bridge.enqueue(
        "navigate",
        {"url": url, "ui": {"open_browser": True}},
    )
    finished = bridge.wait(job.id, timeout_s=timeout, poll_s=0.05, unclaimed_timeout_s=None)
    dt = time.perf_counter() - t0
    ok = finished.status == "done" and bool((finished.result or {}).get("ok", True))
    via = ""
    if finished.result:
        via = str(finished.result.get("via") or "")
    msg = finished.error or (finished.result or {}).get("message") or finished.status
    return ok, dt, f"{msg} via={via}"


def main() -> int:
    home = canonical_home()
    bridge = ComputerHostBridge(home_dir=home)
    print(f"jobs_root={bridge.root}")
    print(f"home={home}")
    fails = 0
    for i, url in enumerate(URLS, 1):
        print(f"\n[{i}/{len(URLS)}] navigate {url}")
        ok, dt, detail = one(bridge, url)
        status = "OK" if ok and dt < 5.0 else ("SLOW" if ok else "FAIL")
        print(f"  {status} in {dt:.2f}s — {detail[:160]}")
        if not ok or dt >= 5.0:
            fails += 1
        time.sleep(0.4)
    print(f"\n{'PASS' if fails == 0 else 'FAIL'}: {len(URLS) - fails}/{len(URLS)} smooth")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
