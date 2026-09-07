"""Sanitization gate stays wired and refuse-shaped."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "check_sanitize.py"
    spec = importlib.util.spec_from_file_location("remedy_check_sanitize", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_sanitize_script_exits_clean_on_current_tree() -> None:
    # Windows CI can emit non-UTF8 bytes (e.g. em dash 0x97) on stdout; decode
    # lossily so a clean exit is not masked by a reader-thread UnicodeError.
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_sanitize.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    assert proc.returncode == 0, out + err
    assert "sanitize: OK" in out


def test_public_docs_allowlist_matches_gitignore() -> None:
    mod = _load()
    gi = mod._gitignore_doc_allowlist()
    assert gi == mod.PUBLIC_TOP_LEVEL_DOCS


def test_unreleased_body_empty_when_blank() -> None:
    mod = _load()
    assert mod._unreleased_body("## [Unreleased]\n\n## [0.1.0]\n- x\n") == ""
    assert mod._unreleased_body("## [Unreleased]\n\n- pending\n\n## [0.1.0]\n") == "- pending"


def test_fake_hint_skips_unit_test_tokens() -> None:
    mod = _load()
    assert mod._FAKE_HINT.search('APIKey: "sk-poe-unit-test-not-real"')
    assert mod._FAKE_HINT.search("signature-not-real-for-unit-test")
