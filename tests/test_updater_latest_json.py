"""Contracts a 0.41.5 Desktop client still needs from current latest.json / CI."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "scripts" / "latest.json"
LIB_RS = ROOT / "desktop" / "src-tauri" / "src" / "lib.rs"
RELEASE_YML = ROOT / ".github" / "workflows" / "desktop-release.yml"
PYPROJECT = ROOT / "pyproject.toml"


def test_latest_json_uses_dot_asset_names_and_stable_platform_keys() -> None:
    data = json.loads(LATEST.read_text(encoding="utf-8"))
    platforms = data["platforms"]
    assert "windows-x86_64" in platforms
    assert "linux-x86_64" in platforms
    win = platforms["windows-x86_64"]["url"]
    linux = platforms["linux-x86_64"]["url"]
    assert "Remedy.Desktop_" in win
    assert "Remedy_Desktop_" not in win
    assert "Remedy Desktop_" not in win
    assert win.endswith("-setup.exe")
    assert "Remedy.Desktop_" in linux
    assert linux.endswith(".AppImage")


def test_rust_updater_still_accepts_0415_platform_keys() -> None:
    text = LIB_RS.read_text(encoding="utf-8")
    assert "windows-x86_64" in text
    assert "linux-x86_64" in text
    # Older aliases kept so a typo in a historic latest.json still resolves.
    assert "windows-x86-64" in text
    assert "linux-x86-64" in text
    assert "releases/latest/download/latest.json" in text
    # Unsigned GitHub API asset URLs must not be treated as installable.
    assert "never installable" in text.lower()


def test_release_workflow_publishes_native_cli_checksums() -> None:
    """The live workflow, or the apply-once patch if workflow scope blocked the push."""
    workflow = RELEASE_YML.read_text(encoding="utf-8")
    patch = ROOT / "scripts" / "desktop-release-native-assets.patch"
    text = workflow if "remedy-native-SHA256SUMS.txt" in workflow else patch.read_text(encoding="utf-8")
    assert "remedy-native-SHA256SUMS.txt" in text
    assert "dist-native/*" in text
    assert "remedy-runtime-x86_64-unknown-linux-gnu" in text
    assert "remedy_core-x86_64-pc-windows-msvc.dll" in text
    assert "libremedy_core-x86_64-unknown-linux-gnu.so" in text


def test_pyproject_license_is_apache_2() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'license = { text = "Apache-2.0" }' in text
    assert "LicenseRef-Proprietary" not in text
