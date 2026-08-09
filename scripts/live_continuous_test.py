#!/usr/bin/env python3
"""Run continuous Remedy live tests until killed (Ctrl+C / process stop).

Cycles: security soak → stress suite → PA/google probe → vision → tools.
Logs a summary line per cycle. Exit 0 only on clean KeyboardInterrupt.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PY = sys.executable


def run_step(name: str, args: list[str], timeout: int) -> tuple[bool, float, str]:
    env = {
        **dict(**dict(__import__("os").environ.items())),
        "PYTHONPATH": str(SRC),
        "REMEDY_HOME": str(Path.home() / ".remedy"),
    }
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            [PY, *args],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        dt = time.perf_counter() - t0
        tail = (p.stdout or "")[-400:] + (p.stderr or "")[-200:]
        return p.returncode == 0, dt, tail.replace("\n", " ")[:300]
    except subprocess.TimeoutExpired:
        dt = time.perf_counter() - t0
        return False, dt, f"TIMEOUT after {timeout}s"
    except Exception as e:
        dt = time.perf_counter() - t0
        return False, dt, str(e)[:200]


def wait_for_api(timeout_s: float = 120.0) -> bool:
    import urllib.error
    import urllib.request

    try:
        scripts = Path(__file__).resolve().parent
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from lib_local_token import resolve_local_api_token

        token = resolve_local_api_token(base="http://127.0.0.1:7400")
    except Exception as exc:
        print(f"no usable API token: {exc}", flush=True)
        return False
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:7400/api/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(1.5)
    return False


def main() -> int:
    cycle = 0
    ok_n = fail_n = 0
    print(f"=== Continuous Remedy test @ {datetime.now(UTC).isoformat()} ===", flush=True)
    print(f"root={ROOT}", flush=True)
    print("Waiting for API on :7400 ...", flush=True)
    if not wait_for_api(180):
        print("API not reachable — abort continuous test", flush=True)
        return 2
    print("API ready", flush=True)
    try:
        while True:
            cycle += 1
            print(f"\n######## CYCLE {cycle} {datetime.now(UTC).strftime('%H:%M:%S')}Z ########", flush=True)
            if not wait_for_api(30):
                print("  [WAIT] API dropped — waiting for recovery", flush=True)
                if not wait_for_api(120):
                    fail_n += 1
                    print("  [FAIL] API still down", flush=True)
                    time.sleep(10)
                    continue
            steps = [
                ("soak", ["scripts/live_soak_security_chat.py"], 180),
                ("stress", ["scripts/live_stress_remedy.py", "--loops", "1"], 240),
            ]
            # Lighter PA probe every cycle (no hang if Gmail disabled)
            steps.append(
                (
                    "google-apis",
                    [
                        "-c",
                        (
                            "from pathlib import Path; "
                            "from remedy.assistant.google_oauth import public_status; "
                            "s=public_status(Path.home()/'.remedy'); "
                            "print('connected', s.get('connected'), 'apis', s.get('apis')); "
                            "raise SystemExit(0 if s.get('connected') else 1)"
                        ),
                    ],
                    60,
                )
            )
            for name, args, timeout in steps:
                ok, dt, tail = run_step(name, args, timeout)
                if ok:
                    ok_n += 1
                    print(f"  [OK]   {name} {dt:.1f}s", flush=True)
                else:
                    fail_n += 1
                    print(f"  [FAIL] {name} {dt:.1f}s — {tail[:180]}", flush=True)
            print(
                f"  -- totals ok={ok_n} fail={fail_n} cycles={cycle}",
                flush=True,
            )
            time.sleep(5)
    except KeyboardInterrupt:
        print(f"\nStopped. cycles={cycle} ok={ok_n} fail={fail_n}", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
