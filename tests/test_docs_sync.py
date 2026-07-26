"""Smoke tests for the documentation sync pipeline.

The full aggregator (same gate as CI) is exercised once here; pure parsers
are covered by loading scripts/check_docs.py as a module.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_docs.py"


def _load_check_docs():
    """Load scripts/check_docs.py without requiring it to be a package."""
    name = "remedy_check_docs"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve the module namespace.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_check_docs_script_exists() -> None:
    assert SCRIPT.is_file()


def test_parse_builtin_commands() -> None:
    mod = _load_check_docs()
    cmds = mod._parse_builtin_commands()
    names = {c["name"] for c in cmds}
    assert "/help" in names
    assert "/compact" in names
    assert "/import-session" in names


def test_parse_hotkeys() -> None:
    mod = _load_check_docs()
    keys = mod._parse_hotkey_keys()
    assert "Ctrl+N" in keys
    assert "F1" in keys
    assert "Enter" in keys


def test_parse_catalog_ids() -> None:
    mod = _load_check_docs()
    ids = mod._parse_catalog_ids()
    assert "00-overview" in ids
    assert "11-reference-commands" in ids


def test_check_docs_full_passes() -> None:
    """Full aggregator must exit 0 on a clean tree (same gate as CI)."""
    import os

    env = os.environ.copy()
    # Windows runners/local consoles may default to cp1252; script also
    # reconfigures stdio, but force UTF-8 for captured output stability.
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
