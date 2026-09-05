"""Guardrails: production installer must not embed multi-GB local models."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI = ROOT / "desktop" / "src-tauri"


def test_tauri_does_not_embed_resources_local():
    conf_path = TAURI / "tauri.conf.json"
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


def test_tauri_external_bin_is_remedy_runtime_win_and_linux() -> None:
    """Packaged Desktop launches Go remedy-runtime only (no remedy-desktop)."""
    for name in ("tauri.conf.json", "tauri.linux.conf.json"):
        conf = json.loads((TAURI / name).read_text(encoding="utf-8"))
        external = conf.get("bundle", {}).get("externalBin") or []
        assert external == ["../bin/remedy-runtime"], f"{name}: {external!r}"
        blob = json.dumps(conf).lower()
        assert "remedy-desktop" not in blob, f"{name} still references remedy-desktop"

    win = json.loads((TAURI / "tauri.windows.conf.json").read_text(encoding="utf-8"))
    resources = win.get("bundle", {}).get("resources") or {}
    res_blob = json.dumps(resources).lower()
    assert "remedy-desktop" not in res_blob
    assert "remedy_core.dll" in res_blob


def test_lib_rs_launch_path_never_joins_remedy_desktop() -> None:
    """Fail-closed: packaged/dev lookup must not prefer legacy sidecar paths."""
    text = (TAURI / "src" / "lib.rs").read_text(encoding="utf-8")
    assert 'join("remedy-desktop' not in text
    assert 'join("remedy-desktop.exe")' not in text
    assert 'join("remedy-runtime' in text
    assert "no Python dual-serve" in text or "no soft Python fallback" in text


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
