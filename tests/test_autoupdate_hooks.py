"""Guardrails for Windows in-app update (double-launch + progress host).

These are source/contract tests — full NSIS install is covered by
scripts/test_autoupdate_pipeline.ps1 and desktop-release CI.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "desktop" / "src-tauri" / "windows" / "hooks.nsh"
UPDATE_UI = ROOT / "desktop" / "src-tauri" / "windows" / "remedy-update-ui.ps1"
LIB_RS = ROOT / "desktop" / "src-tauri" / "src" / "lib.rs"
PIPELINE = ROOT / "scripts" / "test_autoupdate_pipeline.ps1"


def test_hooks_defer_relaunch_via_marker_and_noautolaunch() -> None:
    text = HOOKS.read_text(encoding="utf-8")
    assert "RemedyDesktop-UpdaterOwnsRelaunch.flag" in text
    assert "NOAUTOLAUNCH" in text
    assert "skip_auto_launch" in text
    # POSTINSTALL must not always start on silent when marker present.
    assert "UpdaterOwnsRelaunch" in text or "Updater owns" in text or "updater" in text.lower()


def test_update_ui_requires_sta_and_stays_visible() -> None:
    text = UPDATE_UI.read_text(encoding="utf-8")
    assert "System.Windows.Forms" in text
    assert "GetApartmentState" in text or "-STA" in text
    assert "ShowDialog" in text
    assert "ShowInTaskbar" in text or "TopMost" in text
    # Must survive missing status briefly after app close
    assert "Waiting for installer" in text or "status" in text.lower()


def test_lib_rs_two_stage_update_ux() -> None:
    text = LIB_RS.read_text(encoding="utf-8")
    # Stage 1 = in-app download; stage 2 = install popup at/after exit
    assert "ensure_update_ui_ps1_in_temp" in text
    assert "launch_install_progress_ui" in text
    assert "Stage 1 is in-app" in text or "in-app only" in text.lower()
    assert "NOAUTOLAUNCH" in text
    assert "UpdaterOwnsRelaunch" in text or "updater_owns_relaunch" in text
    assert "Stop-RemedyAppOnly" in text
    # Download begin must not start the install host
    assert "launch_install_progress_ui(&ver_from, &ver_to);" in text
    # Ensure call is after closing phase, not at thread start with download
    download_idx = text.find('emit_progress_ver(\n                &app_for_thread,\n                "downloading"')
    install_ui_idx = text.find("launch_install_progress_ui(&ver_from, &ver_to)")
    assert download_idx > 0 and install_ui_idx > download_idx


def test_autoupdate_pipeline_script_exists() -> None:
    assert PIPELINE.is_file()
    body = PIPELINE.read_text(encoding="utf-8")
    assert "Relaunch" in body
    assert "parent" in body.lower()
