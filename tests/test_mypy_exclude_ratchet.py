"""mypy exclude lock may only shrink."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mypy_exclude_script_passes_on_this_tree() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_mypy_exclude.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok:" in proc.stdout


def test_lock_lists_every_pyproject_exclude() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_mypy_exclude as ratchet

    current = set(ratchet._pyproject_exclude())
    allowed = set(ratchet._lock_paths())
    assert current <= allowed
    assert current == allowed
