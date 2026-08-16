"""Guardrails: production installer must not embed multi-GB local models."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tauri_does_not_embed_resources_local():
    conf_path = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
    conf = json.loads(conf_path.read_text(encoding="utf-8"))
    resources = conf.get("bundle", {}).get("resources") or {}
    # Map of source path -> dest name (or list in older formats)
    blob = json.dumps(resources).lower()
    assert "resources/local" not in blob.replace("\\\\", "/")
    assert '"local"' not in blob or "uninstall" in blob  # allow unrelated keys
    # Explicit: no dest named exactly "local" for model tree
    if isinstance(resources, dict):
        for src, dest in resources.items():
            assert "resources/local" not in str(src).replace("\\", "/")
            assert str(dest).replace("\\", "/").strip("/") != "local"


def test_local_resources_readme_documents_first_run_download():
    readme = ROOT / "desktop" / "resources" / "local" / "README.md"
    text = readme.read_text(encoding="utf-8").lower()
    assert "first-run" in text or "first run" in text
    assert "does not" in text or "not package" in text or "not" in text
    assert "smolvlm2" in text or "smolvlm" in text


def test_no_gguf_committed_under_resources_local():
    local = ROOT / "desktop" / "resources" / "local"
    if not local.is_dir():
        return
    ggufs = list(local.rglob("*.gguf"))
    assert ggufs == [], f"Do not commit GGUF files: {ggufs}"
