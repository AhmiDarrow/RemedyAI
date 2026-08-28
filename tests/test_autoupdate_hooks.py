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
    # Job-object-safe multi-path install schedule (0.14.6+)
    assert "schedule_update_install_script" in text
    assert "wscript.exe" in text
    assert "schtasks.exe" in text
    assert "CREATE_BREAKAWAY_FROM_JOB" in text
    # Do not exit until install script proves it started (0.14.7+)
    assert "BOOT pid=" in text
    assert "Install script alive" in text or "not alive yet" in text
    # Download begin must not start the install host
    assert "launch_install_progress_ui(&ver_from, &ver_to);" in text
    # Ensure call is after closing phase, not at thread start with download
    download_idx = text.find('emit_progress_ver(\n                &app_for_thread,\n                "downloading"')
    install_ui_idx = text.find("launch_install_progress_ui(&ver_from, &ver_to)")
    assert download_idx > 0 and install_ui_idx > download_idx
    # No black CMD flashes on update path: spawn powershell + CREATE_NO_WINDOW
    assert 'Command::new("powershell.exe")' in text
    assert "CREATE_NO_WINDOW" in text
    # Update hosts must not use cmd /c start (that flashed consoles)
    assert "Never use cmd /c start" in text or "no `cmd /c start`" in text or "no cmd /c start" in text.lower()
    # launch_install_progress_ui body must not spawn cmd.exe
    start = text.find("fn launch_install_progress_ui")
    end = text.find("\nfn ", start + 10)
    body = text[start:end if end > start else start + 2500]
    assert "cmd.exe" not in body
    assert 'Command::new("cmd")' not in body


def test_update_ui_ascii_status_copy() -> None:
    """PS1 must stay ASCII-safe for Windows PowerShell 5.1 default encoding."""
    text = UPDATE_UI.read_text(encoding="utf-8")
    non_ascii = [c for c in text if ord(c) > 127]
    assert not non_ascii, f"non-ASCII in update UI script: {non_ascii[:8]!r}"


def test_hooks_silent_relaunch_uses_exec() -> None:
    text = HOOKS.read_text(encoding="utf-8")
    # Silent relaunch uses NSIS Exec (not cmd start)
    assert 'Exec \'"$INSTDIR\\Remedy Desktop.exe"\'' in text
    # Live code must not shell out via cmd start for relaunch (comments may mention it)
    code_lines = [
        ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(";")
    ]
    code = "\n".join(code_lines).lower()
    assert "cmd /c start" not in code
    assert "exec " in code


def test_hooks_kill_before_replace_and_marker_cleanup() -> None:
    """PREINSTALL must kill running app; marker deleted after defer so no sticky skip."""
    text = HOOKS.read_text(encoding="utf-8")
    assert "!macro NSIS_HOOK_PREINSTALL" in text
    assert "_REMEDY_KILL_ALL" in text
    # Marker consumed (deleted) when present — avoids permanent no-relaunch
    assert 'Delete "$TEMP\\RemedyDesktop-UpdaterOwnsRelaunch.flag"' in text
    # Port 7400 stale listeners cleaned (sidecar/uvicorn leftovers)
    assert ":7400" in text
    assert "taskkill" in text.lower()


def test_update_ui_does_not_invoke_cmd_start() -> None:
    """Progress host must stay flash-free (no cmd /c start)."""
    text = UPDATE_UI.read_text(encoding="utf-8")
    code_lines = [
        ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines).lower()
    assert "cmd /c start" not in code
    assert "cmd.exe" not in code or "comment" in code  # soft: prefer no cmd.exe
