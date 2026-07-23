; In-app update pipeline: silent NSIS (/S) + these hooks = download → install → relaunch.
; Tauri productName is "Remedy Desktop" → main exe is "Remedy Desktop.exe".

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Closing running instances of Remedy Desktop..."
  nsExec::ExecToLog 'taskkill /F /IM "Remedy Desktop.exe"'
  nsExec::ExecToLog 'taskkill /F /IM "app.exe"'
  nsExec::ExecToLog 'taskkill /F /IM "remedy-desktop.exe"'
  nsExec::ExecToLog 'taskkill /F /IM "remedy-desktop-x86_64-pc-windows-msvc.exe"'
  Sleep 1500
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; One-click update: after files are written, relaunch the app automatically.
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
  DetailPrint "Closing running instances of Remedy Desktop..."
  nsExec::ExecToLog 'taskkill /F /IM "Remedy Desktop.exe"'
  nsExec::ExecToLog 'taskkill /F /IM "app.exe"'
  nsExec::ExecToLog 'taskkill /F /IM "remedy-desktop.exe"'
  nsExec::ExecToLog 'taskkill /F /IM "remedy-desktop-x86_64-pc-windows-msvc.exe"'
  Sleep 1000
!macroend
