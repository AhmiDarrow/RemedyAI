; Auto-update pipeline + uninstall data options.
; productName "Remedy Desktop" -> main binary is typically "Remedy Desktop.exe"
; (some builds still ship as app.exe). Sidecar is remedy-desktop.exe.
;
; Uninstall UI: config / skills / full wipe checkboxes via PowerShell dialog
; (scripts bundled as resources under $INSTDIR\windows\ and run from %TEMP%).
;
; CRITICAL: options-dialog failures must NEVER abort uninstall of the app.
; Exit codes from uninstall_options.ps1:
;   0 = continue (choices written)
;   1 = user cancelled -> Abort uninstall
;   2+= dialog/script error -> keep user data and continue uninstalling the app

!macro _REMEDY_KILL_ALL
  DetailPrint "Closing running Remedy processes so files can be replaced..."
  ; Tree-kill every known image name (main app + sidecar variants).
  nsExec::ExecToLog 'taskkill /F /T /IM "Remedy Desktop.exe"'
  nsExec::ExecToLog 'taskkill /F /T /IM "app.exe"'
  nsExec::ExecToLog 'taskkill /F /T /IM "remedy-desktop.exe"'
  nsExec::ExecToLog 'taskkill /F /T /IM "remedy-desktop-x86_64-pc-windows-msvc.exe"'
  nsExec::ExecToLog 'taskkill /F /T /IM "remedy-desktop-amd64-pc-windows-msvc.exe"'
  ; Anything still listening on the sidecar port (stale Python/uvicorn).
  nsExec::ExecToLog 'cmd /c for /f "tokens=5" %a in (''netstat -ano ^| findstr :7400 ^| findstr LISTENING'') do taskkill /F /PID %a'
  ; PowerShell belt-and-suspenders by process name substring.
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match ''^(app|remedy-desktop|Remedy Desktop)$'' -or ($_.Path -and $_.Path -like ''*Remedy Desktop*'') } | Stop-Process -Force -ErrorAction SilentlyContinue"'
  Sleep 2000
  ; Second pass - Windows can take a moment to release file handles.
  nsExec::ExecToLog 'taskkill /F /T /IM "remedy-desktop.exe"'
  nsExec::ExecToLog 'taskkill /F /T /IM "app.exe"'
  nsExec::ExecToLog 'taskkill /F /T /IM "Remedy Desktop.exe"'
  Sleep 1500
  ; Best-effort delete of locked sidecar so NSIS can recreate it.
  Delete /REBOOTOK "$INSTDIR\remedy-desktop.exe"
  Delete /REBOOTOK "$INSTDIR\remedy-desktop-x86_64-pc-windows-msvc.exe"
  Delete /REBOOTOK "$INSTDIR\app.exe"
  Sleep 500
!macroend

!macro _REMEDY_WRITE_KEEP_CHOICES
  ; Always leave a valid choices file so POSTUNINSTALL never errors.
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $p=Join-Path $env:TEMP ''RemedyDesktop-UninstallChoices.txt''; @(''config=0'',''skills=0'',''full=0'') | Set-Content -LiteralPath $p -Encoding ASCII } catch {}"'
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro _REMEDY_KILL_ALL
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; Scrub legacy HKCU Run keys from older builds (Defender Persistence.A!ml).
  ; Use NSIS DeleteRegValue - not PowerShell Bypass (ML treats hidden PS+Run as suspicious).
  ; Autostart (if user enables) uses Startup folder only - never registry Run.
  DetailPrint "Removing legacy autostart registry entries if present..."
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "RemedyDesktop"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Remedy Desktop"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "remedy-desktop"

  ; ---- Launch policy ----
  ; Interactive GUI installers show the finish page with:
  ;   - "Create desktop shortcut"
  ;   - "Run Remedy Desktop"
  ; Never pre-launch there - it races the finish page and confuses users.
  ;
  ; Silent (/S), passive (/P), and update (/UPDATE) installs skip the finish page,
  ; so we relaunch here UNLESS the in-app updater owns relaunch.
  ;
  ; Double-launch root cause (pre-0.14.1): POSTINSTALL + updater script both started
  ; the app. Prefer a TEMP marker file (reliable even when GetOptions/CMDLINE fail)
  ; and keep /NOAUTOLAUNCH as a second signal.
  ;
  ; Marker: %TEMP%\RemedyDesktop-UpdaterOwnsRelaunch.flag (written by lib.rs update path)
  IfFileExists "$TEMP\RemedyDesktop-UpdaterOwnsRelaunch.flag" 0 check_noautolaunch_opt
    DetailPrint "UpdaterOwnsRelaunch marker: single relaunch deferred to update script."
    Delete "$TEMP\RemedyDesktop-UpdaterOwnsRelaunch.flag"
    Goto skip_auto_launch
  check_noautolaunch_opt:
  ; Secondary: /NOAUTOLAUNCH on the installer command line (FileFunc GetOptions).
  ClearErrors
  ${GetOptions} $CMDLINE "/NOAUTOLAUNCH" $R9
  ${IfNot} ${Errors}
    DetailPrint "NOAUTOLAUNCH (GetOptions): deferring start to updater script."
    Goto skip_auto_launch
  ${EndIf}

  StrCpy $R7 0
  StrCmp $PassiveMode "1" 0 +2
    StrCpy $R7 1
  StrCmp $UpdateMode "1" 0 +2
    StrCpy $R7 1
  IfSilent 0 +2
    StrCpy $R7 1
  StrCmp $R7 "1" 0 skip_auto_launch

  DetailPrint "Silent/passive/update install: launching Remedy Desktop..."
  ; Exec (not cmd /c start) avoids a visible black console flash.
  IfFileExists "$INSTDIR\Remedy Desktop.exe" 0 try_app_exe
    Exec '"$INSTDIR\Remedy Desktop.exe"'
    Goto launch_done
  try_app_exe:
  IfFileExists "$INSTDIR\app.exe" 0 launch_done
    Exec '"$INSTDIR\app.exe"'
  Goto launch_done

  skip_auto_launch:
  DetailPrint "Interactive install or updater-owned relaunch: launch deferred."
  launch_done:
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro _REMEDY_KILL_ALL
  ; Remove optional Startup shortcut + any leftover Run keys (always on uninstall)
  Delete "$APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Remedy Desktop.lnk"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "RemedyDesktop"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Remedy Desktop"
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "remedy-desktop"

  ; Default: keep user data unless the options dialog successfully says otherwise.
  !insertmacro _REMEDY_WRITE_KEEP_CHOICES

  ; --- Uninstall data options (config / skills / full) ---
  ; Skip UI during silent / auto-update uninstalls so updates keep user data.
  ClearErrors
  ${GetOptions} $CMDLINE "/UPDATE" $R9
  ${IfNot} ${Errors}
    DetailPrint "Update-mode uninstall: keeping user data (config/skills)."
    Goto uninstall_options_done
  ${EndIf}

  ; Silent (/S) or passive - no UI; keep data.
  IfSilent 0 not_silent_uninstall
    DetailPrint "Silent uninstall: keeping user data (use interactive uninstall for wipe options)."
    Goto uninstall_options_done
  not_silent_uninstall:

  ; Copy bundled scripts to TEMP *before* $INSTDIR is wiped by the Uninstall section.
  CreateDirectory "$TEMP\RemedyDesktop-Uninstall"
  StrCpy $R8 ""
  IfFileExists "$INSTDIR\windows\uninstall_options.ps1" 0 try_res_opt
    CopyFiles /SILENT "$INSTDIR\windows\uninstall_options.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_options.ps1"
    CopyFiles /SILENT "$INSTDIR\windows\uninstall_wipe.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1"
    StrCpy $R8 "$TEMP\RemedyDesktop-Uninstall\uninstall_options.ps1"
    Goto run_options_dialog
  try_res_opt:
  IfFileExists "$INSTDIR\resources\windows\uninstall_options.ps1" 0 try_hooks_dir
    CopyFiles /SILENT "$INSTDIR\resources\windows\uninstall_options.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_options.ps1"
    CopyFiles /SILENT "$INSTDIR\resources\windows\uninstall_wipe.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1"
    StrCpy $R8 "$TEMP\RemedyDesktop-Uninstall\uninstall_options.ps1"
    Goto run_options_dialog
  try_hooks_dir:
  IfFileExists "$INSTDIR\uninstall_options.ps1" 0 skip_options_missing
    CopyFiles /SILENT "$INSTDIR\uninstall_options.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_options.ps1"
    CopyFiles /SILENT "$INSTDIR\uninstall_wipe.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1"
    StrCpy $R8 "$TEMP\RemedyDesktop-Uninstall\uninstall_options.ps1"
    Goto run_options_dialog
  skip_options_missing:
    DetailPrint "Uninstall options scripts not found - keeping user data and continuing uninstall."
    Goto uninstall_options_done

  run_options_dialog:
    DetailPrint "Asking which user data to remove (config / skills / full)..."
    ; -STA required for WinForms. Quote path for spaces in TEMP.
    ; Exit 0 = ok, 1 = user cancelled, other = soft-fail (keep data, still uninstall app).
    ClearErrors
    ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Normal -File "$R8"' $0
    DetailPrint "Uninstall options dialog exit code: $0"
    ; LogicLib: only Abort on intentional cancel (1). Any other non-zero soft-fails.
    StrCmp $0 "1" 0 options_not_cancel
      DetailPrint "Uninstall cancelled by user."
      Abort
    options_not_cancel:
    StrCmp $0 "0" options_ok options_soft_fail
    options_soft_fail:
      DetailPrint "Options dialog failed (exit $0) - keeping user data and continuing uninstall."
      !insertmacro _REMEDY_WRITE_KEEP_CHOICES
      Goto options_handled
    options_ok:
      DetailPrint "Uninstall options recorded."
    options_handled:
    ; Exit 0: choices file already written by the script.

  uninstall_options_done:
  ; Ensure wipe script is in TEMP even if options dialog was skipped.
  IfFileExists "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1" 0 ensure_wipe_from_inst
    Goto wipe_script_ready
  ensure_wipe_from_inst:
  IfFileExists "$INSTDIR\windows\uninstall_wipe.ps1" 0 wipe_script_ready
    CreateDirectory "$TEMP\RemedyDesktop-Uninstall"
    CopyFiles /SILENT "$INSTDIR\windows\uninstall_wipe.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1"
  wipe_script_ready:
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; Apply data wipe after app files are gone (so full wipe is clean).
  ; Never fail the uninstaller if wipe has issues.
  DetailPrint "Applying uninstall data options..."
  IfFileExists "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1" 0 try_wipe_inline
    nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1"'
    Goto wipe_done
  try_wipe_inline:
    IfFileExists "$TEMP\RemedyDesktop-UninstallChoices.txt" 0 wipe_done
      nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $c=0;$s=0;$f=0; $cf=Join-Path $env:TEMP ''RemedyDesktop-UninstallChoices.txt''; if (Test-Path -LiteralPath $cf) { Get-Content -LiteralPath $cf | ForEach-Object { if ($_ -match ''^config=(\d)''){$c=[int]$Matches[1]}; if ($_ -match ''^skills=(\d)''){$s=[int]$Matches[1]}; if ($_ -match ''^full=(\d)''){$f=[int]$Matches[1]} } }; $h=Join-Path $env:USERPROFILE ''.remedy''; if ($f -eq 1) { if (Test-Path -LiteralPath $h) { Remove-Item -LiteralPath $h -Recurse -Force -EA SilentlyContinue }; Remove-Item -LiteralPath (Join-Path $env:APPDATA ''com.remedy.desktop'') -Recurse -Force -EA SilentlyContinue; Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA ''com.remedy.desktop'') -Recurse -Force -EA SilentlyContinue } else { if ($c -eq 1 -and (Test-Path -LiteralPath $h)) { @(''config.toml'',''desktop.json'') | ForEach-Object { Remove-Item (Join-Path $h $_) -Force -EA SilentlyContinue }; Remove-Item (Join-Path $h ''auth'') -Recurse -Force -EA SilentlyContinue }; if ($s -eq 1) { Remove-Item (Join-Path $h ''skills'') -Recurse -Force -EA SilentlyContinue } } } catch {}"'
  wipe_done:
  ; Cleanup temp uninstall helpers (keep wipe log for support)
  RMDir /r "$TEMP\RemedyDesktop-Uninstall"
!macroend
