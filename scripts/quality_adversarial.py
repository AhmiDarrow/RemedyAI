"""Drive the 2026-08-28 quality-fix tests on Windows and Linux (WSL).

Prints a JSON summary to stdout. Also writes clone-only
``docs/_quality_adversarial_results.json`` when that directory is writable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIN_FILES = [
    "tests/test_quality_adversarial.py",
    "tests/test_life_task_drive.py",
    "tests/test_casual_verify.py",
    "tests/test_build_drive.py",
    "tests/test_build_persist.py",
    "tests/test_build_apply_patch.py",
    "tests/test_build_verify_gate.py",
    "tests/test_hive.py",
    "tests/test_policy_engine.py",
    "tests/test_agent_llm_binding.py",
    "tests/test_catalog_routes.py",
    "tests/test_additive_followups.py",
    "tests/test_computer_linux_detect.py",
    "tests/test_in_app_fast_path.py",
    "tests/test_payment_checkpoint.py",
    "tests/test_closed_loop.py",
    "tests/test_life_goals.py",
]
LINUX_FILES = [
    "tests/test_quality_adversarial.py",
    "tests/test_computer_linux_detect.py",
    "tests/test_computer_use.py",
    "tests/test_web_fetch_ssrf.py",
]


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> dict:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-2000:],
        "ok": proc.returncode == 0,
    }


def main() -> int:
    summary: dict = {"ok": True, "steps": []}
    win = _run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", *WIN_FILES],
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": ""},
    )
    summary["steps"].append({"name": "windows_pytest", **win})
    if not win["ok"]:
        summary["ok"] = False

    wsl_bin = shutil.which("wsl") or shutil.which("wsl.exe")
    if not wsl_bin:
        if os.environ.get("QUALITY_ADVERSARIAL_ALLOW_NO_WSL") == "1":
            summary["steps"].append(
                {
                    "name": "wsl_pytest",
                    "ok": True,
                    "skipped": True,
                    "returncode": None,
                    "cmd": [],
                    "stdout": "",
                    "stderr": "wsl.exe not found; QUALITY_ADVERSARIAL_ALLOW_NO_WSL=1",
                }
            )
        else:
            summary["ok"] = False
            summary["steps"].append(
                {
                    "name": "wsl_pytest",
                    "ok": False,
                    "returncode": 1,
                    "cmd": [],
                    "stdout": "",
                    "stderr": "wsl.exe not found (set QUALITY_ADVERSARIAL_ALLOW_NO_WSL=1 to opt out)",
                }
            )
    else:
        wsl = _run(
            [
                wsl_bin,
                "-e",
                "bash",
                "-lc",
                "cd /mnt/c/Users/Administrator/Old-Remedy && "
                "UV_PROJECT_ENVIRONMENT=/tmp/remedy-wsl-venv uv run pytest -q --tb=short "
                + " ".join(LINUX_FILES),
            ]
        )
        summary["steps"].append({"name": "wsl_pytest", **wsl})
        if not wsl["ok"]:
            summary["ok"] = False

    print(json.dumps({"ok": summary["ok"], "steps": [
        {"name": s["name"], "ok": s["ok"], "returncode": s["returncode"]}
        for s in summary["steps"]
    ]}, indent=2))
    out = ROOT / "docs" / "_quality_adversarial_results.json"
    with suppress(OSError):
        out.write_text(json.dumps(summary, indent=2)[:200_000], encoding="utf-8")
    if not summary["ok"]:
        for s in summary["steps"]:
            if not s.get("ok"):
                sys.stderr.write(s.get("stdout") or "")
                sys.stderr.write(s.get("stderr") or "")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
