; Auto-update pipeline + uninstall data options.
; productName "Remedy Desktop" → main binary is typically "Remedy Desktop.exe"
; (some builds still ship as app.exe). Sidecar is remedy-desktop.exe.
;
; Uninstall UI: config / skills / full wipe checkboxes via PowerShell dialog
; (scripts bundled as resources and run from %TEMP% during uninstall).

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
  ; Second pass — Windows can take a moment to release file handles.
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

!macro NSIS_HOOK_PREINSTALL
  !insertmacro _REMEDY_KILL_ALL
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; Scrub legacy HKCU Run keys from older builds (Defender Persistence.A!ml false positive).
  ; Autostart (if user enables) uses Startup folder only — never registry Run.
  DetailPrint "Removing legacy autostart registry entries if present..."
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach ($n in @(''RemedyDesktop'',''Remedy Desktop'',''remedy-desktop'')) { Remove-ItemProperty -Path ''HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'' -Name $n -ErrorAction SilentlyContinue }"'
  ; One-click update: relaunch after files are written.
  ; Use `cmd /c start` so launch is independent of the installer process tree
  ; (silent /S installs sometimes tear down children when NSIS exits).
  DetailPrint "Launching Remedy Desktop after install/update..."
  IfFileExists "$INSTDIR\Remedy Desktop.exe" 0 try_app_exe
    nsExec::ExecToLog 'cmd /c start "" "$INSTDIR\Remedy Desktop.exe"'
    Goto launch_done
  try_app_exe:
  IfFileExists "$INSTDIR\app.exe" 0 launch_done
    nsExec::ExecToLog 'cmd /c start "" "$INSTDIR\app.exe"'
  launch_done:
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro _REMEDY_KILL_ALL
  ; Remove optional Startup shortcut + any leftover Run keys (always on uninstall)
  Delete "$APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Remedy Desktop.lnk"
  nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach ($n in @(''RemedyDesktop'',''Remedy Desktop'',''remedy-desktop'')) { Remove-ItemProperty -Path ''HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'' -Name $n -ErrorAction SilentlyContinue }"'

  ; --- Uninstall data options (config / skills / full) ---
  ; Skip UI during silent / auto-update uninstalls so updates keep user data.
  ${GetOptions} $CMDLINE "/UPDATE" $R9
  ${IfNot} ${Errors}
    DetailPrint "Update-mode uninstall: keeping user data (config/skills)."
    nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Content -LiteralPath (Join-Path $env:TEMP ''RemedyDesktop-UninstallChoices.txt'') -Value @(''config=0'',''skills=0'',''full=0'')"'
    Goto uninstall_options_done
  ${EndIf}
  ${If} ${Silent}
    DetailPrint "Silent uninstall: keeping user data (use interactive uninstall for wipe options)."
    nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Content -LiteralPath (Join-Path $env:TEMP ''RemedyDesktop-UninstallChoices.txt'') -Value @(''config=0'',''skills=0'',''full=0'')"'
    Goto uninstall_options_done
  ${EndIf}

  ; Copy bundled scripts to TEMP (still available before $INSTDIR is wiped).
  CreateDirectory "$TEMP\RemedyDesktop-Uninstall"
  ; Resources may be at $INSTDIR\windows\ or $INSTDIR\resources\windows\
  IfFileExists "$INSTDIR\windows\uninstall_options.ps1" 0 try_res_opt
    CopyFiles /SILENT "$INSTDIR\windows\uninstall_options.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_options.ps1"
    CopyFiles /SILENT "$INSTDIR\windows\uninstall_wipe.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1"
    Goto run_options_dialog
  try_res_opt:
  IfFileExists "$INSTDIR\resources\windows\uninstall_options.ps1" 0 try_hooks_dir
    CopyFiles /SILENT "$INSTDIR\resources\windows\uninstall_options.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_options.ps1"
    CopyFiles /SILENT "$INSTDIR\resources\windows\uninstall_wipe.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1"
    Goto run_options_dialog
  try_hooks_dir:
  ; Fallback: scripts may live next to uninstall.exe if resources map failed
  IfFileExists "$INSTDIR\uninstall_options.ps1" 0 skip_options_missing
    CopyFiles /SILENT "$INSTDIR\uninstall_options.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_options.ps1"
    CopyFiles /SILENT "$INSTDIR\uninstall_wipe.ps1" "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1"
    Goto run_options_dialog
  skip_options_missing:
    DetailPrint "Uninstall options scripts not found — keeping user data."
    nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "Set-Content -LiteralPath (Join-Path $env:TEMP ''RemedyDesktop-UninstallChoices.txt'') -Value @(''config=0'',''skills=0'',''full=0'')"'
    Goto uninstall_options_done

  run_options_dialog:
    DetailPrint "Asking which user data to remove (config / skills / full)..."
    ; Interactive dialog. Exit code 1 = user cancelled → abort uninstall.
    ; -STA required for WinForms.
    ExecWait 'powershell -NoProfile -ExecutionPolicy Bypass -STA -File "$TEMP\RemedyDesktop-Uninstall\uninstall_options.ps1"' $0
    ${If} $0 <> 0
      DetailPrint "Uninstall cancelled by user (or options dialog failed)."
      Abort
    ${EndIf}

  uninstall_options_done:
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; Apply data wipe after app files are gone (so full wipe is clean).
  DetailPrint "Applying uninstall data options..."
  IfFileExists "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1" 0 try_wipe_inline
    nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -File "$TEMP\RemedyDesktop-Uninstall\uninstall_wipe.ps1"'
    Goto wipe_done
  try_wipe_inline:
    ; If wipe script missing, still honor a choices file if present
    IfFileExists "$TEMP\RemedyDesktop-UninstallChoices.txt" 0 wipe_done
      nsExec::ExecToLog 'powershell -NoProfile -ExecutionPolicy Bypass -Command "& { $c=0;$s=0;$f=0; Get-Content (Join-Path $env:TEMP ''RemedyDesktop-UninstallChoices.txt'') | %% { if ($_ -match ''^config=(\d)''){$c=[int]$Matches[1]}; if ($_ -match ''^skills=(\d)''){$s=[int]$Matches[1]}; if ($_ -match ''^full=(\d)''){$f=[int]$Matches[1]} }; $h=Join-Path $env:USERPROFILE ''.remedy''; if ($f -eq 1) { Remove-Item -LiteralPath $h -Recurse -Force -EA SilentlyContinue; Remove-Item -LiteralPath (Join-Path $env:APPDATA ''com.remedy.desktop'') -Recurse -Force -EA SilentlyContinue; Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA ''com.remedy.desktop'') -Recurse -Force -EA SilentlyContinue } else { if ($c -eq 1) { @(''config.toml'',''desktop.json'') | %% { Remove-Item (Join-Path $h $_) -Force -EA SilentlyContinue }; Remove-Item (Join-Path $h ''auth'') -Recurse -Force -EA SilentlyContinue }; if ($s -eq 1) { Remove-Item (Join-Path $h ''skills'') -Recurse -Force -EA SilentlyContinue } } }"'
  wipe_done:
  ; Cleanup temp uninstall helpers (keep wipe log for support)
  RMDir /r "$TEMP\RemedyDesktop-Uninstall"
!macroend
