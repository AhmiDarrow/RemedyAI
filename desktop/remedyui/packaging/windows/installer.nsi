; Remedy Desktop 1.0 NSIS installer.
;
; This is the payload 0.70.5 (and later) clients launch. It must stay a real
; NSIS exe. Inno Setup does not understand these flags.
;
; Accepted flags, same as desktop/src-tauri:
;   /S            NSIS silent
;   /NCRC         skip CRC (NSIS built-in; clients pass it)
;   /P            passive
;   /UPDATE       keep user data, record UpdateMode for the hooks
;   /NOAUTOLAUNCH installer must not start the app (updater script relaunches)
;   /D=<dir>      install directory, last argument, unquoted (NSIS rule)
;
; Default directory is %LOCALAPPDATA%\Remedy Desktop, unless the registry
; already has Software\Remedy\Remedy Desktop from a previous install.
; Uninstall key: HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Remedy Desktop
; Product id written into the version resource: com.remedy.desktop
;
; This script never deletes the per-user data directory. The included hooks
; delete that tree only when an interactive uninstall's options dialog sets
; full=1. /S and /UPDATE skip that dialog and write keep-choices.
;
; An upgrade runs the previous uninstaller first, with /S /UPDATE, and waits
; for it (_?= so NSIS does not return early). That removes the old app files
; and keeps the per-user data directory.
;
; Hooks live in the Tauri tree and must be included, not rewritten:
;   desktop/remedyui/packaging/windows/hooks.nsh

Unicode true
ManifestDPIAware true
; Per-user, same as 0.70.5. The installed exe is asInvoker so system
; shortcuts (Win+Shift+S) work while the app has focus. Keep this `user`
; so silent /S /UPDATE installs into %LOCALAPPDATA% are not blocked by a
; UAC prompt.
RequestExecutionLevel user
SetCompressor /SOLID lzma
CRCCheck on

!ifndef VERSION
  !define VERSION "2.1.1"
!endif
!ifndef PRODUCTNAME
  !define PRODUCTNAME "Remedy Desktop"
!endif
!define MAINBINARYNAME "Remedy Desktop.exe"
!define BUNDLEID "com.remedy.desktop"
!define MANUFACTURER "Remedy"
!ifndef OUTFILE
  !define OUTFILE "Remedy.Desktop_${VERSION}_x64-setup.exe"
!endif
!ifndef MAINBINARYSRCPATH
  !error "Pass /DMAINBINARYSRCPATH=path\to\remedy-desktop.exe"
!endif
!ifndef RUNTIMESRCPATH
  !error "Pass /DRUNTIMESRCPATH=path\to\remedy-runtime.exe"
!endif
!ifndef IROHSRCPATH
  !error "Pass /DIROHSRCPATH=path\to\remedy-iroh.exe"
!endif
!ifndef VOICESRCPATH
  !error "Pass /DVOICESRCPATH=path\to\remedy-voice.exe"
!endif
!ifndef CORESRCPATH
  !error "Pass /DCORESRCPATH=path\to\remedy_core.dll"
!endif
!ifndef WEBUISRCPATH
  !error "Pass /DWEBUISRCPATH=path\to\webui (folder with index.html)"
!endif

!define PLACEHOLDER_INSTALL_DIR "C:\__REMEDY_PLACEHOLDER_DO_NOT_INSTALL__"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCTNAME}"
!define MANUPRODUCTKEY "Software\${MANUFACTURER}\${PRODUCTNAME}"
; Anchored at this script. Windows makensis does not open forward-slash
; paths for !include or File. package-windows.ps1 passes absolute backslash
; /D defines so the compile does not depend on the working directory.
!ifndef HOOKS
  !define HOOKS "${__FILEDIR__}\hooks.nsh"
!endif
!ifndef UNINSTALL_OPTIONS
  !define UNINSTALL_OPTIONS "${__FILEDIR__}\uninstall_options.ps1"
!endif
!ifndef UNINSTALL_WIPE
  !define UNINSTALL_WIPE "${__FILEDIR__}\uninstall_wipe.ps1"
!endif
!ifndef INSTALLERICON
  !define INSTALLERICON "${__FILEDIR__}\..\..\assets\icons\icon.ico"
!endif

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

; hooks.nsh reads these. Declare them before the include.
Var PassiveMode
Var UpdateMode
Var CmdLineInstallDir
Var DeleteAppDataCheckboxState

!include "${HOOKS}"

!define MUI_ICON "${INSTALLERICON}"
!define MUI_UNICON "${INSTALLERICON}"
!define MUI_ABORTWARNING

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION RunMainBinary
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_FINISH
!undef MUI_PAGE_CUSTOMFUNCTION_PRE

; Skip the confirm page on /S, /P, and /UPDATE. Do not skip the instfiles page.
!define MUI_PAGE_CUSTOMFUNCTION_PRE un.SkipIfPassive
!insertmacro MUI_UNPAGE_CONFIRM
!undef MUI_PAGE_CUSTOMFUNCTION_PRE
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Name "${PRODUCTNAME}"
BrandingText "${PRODUCTNAME}"
OutFile "${OUTFILE}"
InstallDir "${PLACEHOLDER_INSTALL_DIR}"
InstallDirRegKey HKCU "${MANUPRODUCTKEY}" ""

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${PRODUCTNAME}"
VIAddVersionKey "CompanyName" "${MANUFACTURER}"
VIAddVersionKey "FileDescription" "${PRODUCTNAME} setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "${MANUFACTURER}"
VIAddVersionKey "InternalName" "${BUNDLEID}"
VIAddVersionKey "OriginalFilename" "Remedy.Desktop_${VERSION}_x64-setup.exe"

Function .onInit
  SetShellVarContext current
  StrCpy $PassiveMode 0
  StrCpy $UpdateMode 0
  StrCpy $DeleteAppDataCheckboxState 0
  StrCpy $CmdLineInstallDir ""

  ${GetOptions} $CMDLINE "/P" $R0
  ${IfNot} ${Errors}
    StrCpy $PassiveMode 1
  ${EndIf}
  ClearErrors
  ${GetOptions} $CMDLINE "/UPDATE" $R0
  ${IfNot} ${Errors}
    StrCpy $UpdateMode 1
  ${EndIf}

  ; /D= is applied by NSIS before .onInit. Anything other than the placeholder
  ; is that override and must survive the default-dir logic below.
  ${If} $INSTDIR != "${PLACEHOLDER_INSTALL_DIR}"
    StrCpy $CmdLineInstallDir $INSTDIR
  ${Else}
    StrCpy $INSTDIR "$LOCALAPPDATA\${PRODUCTNAME}"
    Call RestorePreviousInstallLocation
  ${EndIf}
FunctionEnd

Function un.onInit
  SetShellVarContext current
  StrCpy $PassiveMode 0
  StrCpy $UpdateMode 0
  StrCpy $DeleteAppDataCheckboxState 0
  ${GetOptions} $CMDLINE "/P" $R0
  ${IfNot} ${Errors}
    StrCpy $PassiveMode 1
  ${EndIf}
  ClearErrors
  ${GetOptions} $CMDLINE "/UPDATE" $R0
  ${IfNot} ${Errors}
    StrCpy $UpdateMode 1
    StrCpy $PassiveMode 1
  ${EndIf}
FunctionEnd

Function RestorePreviousInstallLocation
  ${If} $CmdLineInstallDir != ""
    StrCpy $INSTDIR $CmdLineInstallDir
    Return
  ${EndIf}
  ReadRegStr $R0 HKCU "${MANUPRODUCTKEY}" ""
  ${If} $R0 != ""
    StrCpy $INSTDIR $R0
  ${EndIf}
FunctionEnd

Function SkipIfPassive
  ${If} $PassiveMode = 1
  ${OrIf} $UpdateMode = 1
    Abort
  ${EndIf}
FunctionEnd

Function un.SkipIfPassive
  ${If} $PassiveMode = 1
  ${OrIf} $UpdateMode = 1
    Abort
  ${EndIf}
FunctionEnd

Function RunMainBinary
  Exec '"$INSTDIR\${MAINBINARYNAME}"'
FunctionEnd

; What an older install left that this one no longer ships. The old
; uninstaller is not run: its hook stops every process under the install
; folder, itself included, so it never removed anything. Per-user data lives
; elsewhere and is not touched.
Function RemoveStaleFiles
  RMDir /r "$INSTDIR\webui"
  RMDir /r "$INSTDIR\windows"
  Delete "$INSTDIR\app.exe"
  Delete "$INSTDIR\remedy-desktop.exe"
  Delete "$INSTDIR\remedy-runtime-x86_64-pc-windows-msvc.exe"
  ; The Python worker older versions shipped. Nothing runs it now.
  Delete "$INSTDIR\rmdy_tool_worker.pyz"
  RMDir /r "$INSTDIR\rmdy-deps"
  ; Older installs put the Start Menu shortcut beside the folder this one uses.
  Delete "$SMPROGRAMS\${PRODUCTNAME}.lnk"
FunctionEnd

Section Install
  SetOutPath "$INSTDIR"

  ; Running copies are closed first, so nothing below is locked.
  !insertmacro NSIS_HOOK_PREINSTALL
  Call RemoveStaleFiles

  ; Installed name stays "Remedy Desktop.exe" even when the cargo bin is remedy-desktop.exe.
  File "/oname=${MAINBINARYNAME}" "${MAINBINARYSRCPATH}"
  ; Local API sidecar. The shell looks for remedy-runtime.exe next to the app.
  File "/oname=remedy-runtime.exe" "${RUNTIMESRCPATH}"
  ; Her voice. remedy-runtime starts it, from beside itself, on first use.
  File "/oname=remedy-voice.exe" "${VOICESRCPATH}"
  ; The away-from-home path for a paired phone. Off until the owner turns it on.
  File "/oname=remedy-iroh.exe" "${IROHSRCPATH}"
  File "/oname=remedy_core.dll" "${CORESRCPATH}"

  ; Chat Web UI. remedy-runtime serves this from a `webui` folder next to the exe.
  SetOutPath "$INSTDIR\webui"
  File /r "${WEBUISRCPATH}\*.*"
  SetOutPath "$INSTDIR"

  SetOutPath "$INSTDIR\windows"
  File "/oname=uninstall_options.ps1" "${UNINSTALL_OPTIONS}"
  File "/oname=uninstall_wipe.ps1" "${UNINSTALL_WIPE}"
  SetOutPath "$INSTDIR"

  WriteUninstaller "$INSTDIR\uninstall.exe"

  WriteRegStr HKCU "${MANUPRODUCTKEY}" "" "$INSTDIR"

  ReadRegStr $R1 HKCU "${UNINSTKEY}" "MainBinaryName"
  ${If} $R1 != ""
  ${AndIf} $R1 != "${MAINBINARYNAME}"
    Delete "$INSTDIR\$R1"
  ${EndIf}

  WriteRegStr HKCU "${UNINSTKEY}" "MainBinaryName" "${MAINBINARYNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${PRODUCTNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$\"$INSTDIR\${MAINBINARYNAME}$\""
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "${MANUFACTURER}"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$\"$INSTDIR$\""
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1

  CreateDirectory "$SMPROGRAMS\${PRODUCTNAME}"
  CreateShortcut "$SMPROGRAMS\${PRODUCTNAME}\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}"
  ; A first install puts a shortcut on the desktop. An update leaves the
  ; desktop as the person arranged it.
  ${If} $UpdateMode <> 1
    ${If} $PassiveMode = 1
    ${OrIf} ${Silent}
      CreateShortcut "$DESKTOP\${PRODUCTNAME}.lnk" "$INSTDIR\${MAINBINARYNAME}"
    ${EndIf}
  ${EndIf}

  !insertmacro NSIS_HOOK_POSTINSTALL

  ${If} $PassiveMode = 1
  ${OrIf} ${Silent}
  ${OrIf} $UpdateMode = 1
    SetAutoClose true
  ${EndIf}
SectionEnd

Section Uninstall
  !insertmacro NSIS_HOOK_PREUNINSTALL

  Delete "$INSTDIR\${MAINBINARYNAME}"
  Delete "$INSTDIR\app.exe"
  Delete "$INSTDIR\remedy-desktop.exe"
  Delete "$INSTDIR\remedy-runtime.exe"
  Delete "$INSTDIR\remedy-voice.exe"
  Delete "$INSTDIR\remedy_core.dll"
  RMDir /r "$INSTDIR\webui"
  Delete "$INSTDIR\rmdy_tool_worker.pyz"
  RMDir /r "$INSTDIR\rmdy-deps"
  Delete "$INSTDIR\remedy-runtime-x86_64-pc-windows-msvc.exe"
  Delete "$INSTDIR\remedy-iroh.exe"
  Delete "$INSTDIR\windows\uninstall_options.ps1"
  Delete "$INSTDIR\windows\uninstall_wipe.ps1"
  RMDir "$INSTDIR\windows"
  Delete "$INSTDIR\uninstall.exe"

  ${If} $UpdateMode <> 1
    Delete "$SMPROGRAMS\${PRODUCTNAME}\${PRODUCTNAME}.lnk"
    RMDir "$SMPROGRAMS\${PRODUCTNAME}"
    Delete "$DESKTOP\${PRODUCTNAME}.lnk"
    DeleteRegKey HKCU "${UNINSTKEY}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCTNAME}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "RemedyDesktop"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "remedy-desktop"
  ${EndIf}

  ; App-data checkbox from the Tauri template. This script never sets it.
  ; The per-user data directory is not under $INSTDIR and is not removed here.
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    DeleteRegKey HKCU "${MANUPRODUCTKEY}"
    RMDir /r "$APPDATA\${BUNDLEID}"
    RMDir /r "$LOCALAPPDATA\${BUNDLEID}"
  ${EndIf}

  RMDir "$INSTDIR"

  !insertmacro NSIS_HOOK_POSTUNINSTALL

  ${If} $PassiveMode = 1
  ${OrIf} $UpdateMode = 1
    SetAutoClose true
  ${EndIf}
SectionEnd
