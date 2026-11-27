"""Clean-home smoke: first-run surfaces without ~/.remedy vision install.

Simulates a PC that has never downloaded the local model and never opened
a prior Remedy profile (isolated REMEDY_HOME).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def clean_home(tmp_path, monkeypatch):
    home = tmp_path / "remedy-home"
    home.mkdir()
    monkeypatch.setenv("REMEDY_HOME", str(home))
    # Do not inherit product bundle path from developer machine
    monkeypatch.delenv("REMEDY_LOCAL_BUNDLE", raising=False)
    monkeypatch.delenv("REMEDY_BUNDLE_DIR", raising=False)
    return home


def test_tauri_packaging_still_excludes_local_models():
    conf = json.loads(
        (Path(__file__).resolve().parents[1] / "desktop" / "src-tauri" / "tauri.conf.json")
        .read_text(encoding="utf-8")
    )
    resources = conf.get("bundle", {}).get("resources") or {}
    blob = json.dumps(resources).replace("\\", "/")
    assert "resources/local" not in blob
