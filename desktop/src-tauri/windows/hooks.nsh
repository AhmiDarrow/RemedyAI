!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Closing running instances of Remedy Desktop..."
  nsExec::ExecToLog 'taskkill /F /IM "Remedy Desktop.exe"'
  nsExec::ExecToLog 'taskkill /F /IM "app.exe"'
  nsExec::ExecToLog 'taskkill /F /IM "remedy-desktop.exe"'
  Sleep 1000
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  DetailPrint "Closing running instances of Remedy Desktop..."
  nsExec::ExecToLog 'taskkill /F /IM "Remedy Desktop.exe"'
  nsExec::ExecToLog 'taskkill /F /IM "app.exe"'
  nsExec::ExecToLog 'taskkill /F /IM "remedy-desktop.exe"'
  Sleep 1000
!macroend
