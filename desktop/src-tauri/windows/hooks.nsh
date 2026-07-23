; Auto-update pipeline: silent NSIS (/S) + these hooks.
; productName "Remedy Desktop" → main binary is typically "Remedy Desktop.exe"
; (some builds still ship as app.exe). Sidecar is remedy-desktop.exe.

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
  ; One-click update: relaunch after files are written.
  DetailPrint "Launching Remedy Desktop after install/update..."
  IfFileExists "$INSTDIR\Remedy Desktop.exe" 0 try_app_exe
    Exec '"$INSTDIR\Remedy Desktop.exe"'
    Goto launch_done
  try_app_exe:
  IfFileExists "$INSTDIR\app.exe" 0 launch_done
    Exec '"$INSTDIR\app.exe"'
  launch_done:
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro _REMEDY_KILL_ALL
!macroend
