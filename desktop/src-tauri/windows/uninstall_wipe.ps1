#Requires -Version 5.1
<#
.SYNOPSIS
  Apply uninstall data wipe based on choices file from uninstall_options.ps1.

.DESCRIPTION
  Reads %TEMP%\RemedyDesktop-UninstallChoices.txt and removes:
    config  → selected config files under ~/.remedy (keeps skills/memory unless also selected)
    skills  → ~/.remedy/skills
    full    → entire ~/.remedy + known leftovers so a reinstall is completely clean
#>
param(
  [string]$ChoicesFile = $(Join-Path $env:TEMP 'RemedyDesktop-UninstallChoices.txt')
)

$ErrorActionPreference = 'SilentlyContinue'
$homeRem = Join-Path $env:USERPROFILE '.remedy'
$log = Join-Path $env:TEMP 'RemedyDesktop-UninstallWipe.log'

function Log([string]$m) {
  $line = '{0:u} {1}' -f (Get-Date), $m
  Add-Content -LiteralPath $log -Value $line -ErrorAction SilentlyContinue
}

$config = 0; $skills = 0; $full = 0
if (Test-Path -LiteralPath $ChoicesFile) {
  Get-Content -LiteralPath $ChoicesFile | ForEach-Object {
    if ($_ -match '^config=(\d)') { $config = [int]$Matches[1] }
    if ($_ -match '^skills=(\d)') { $skills = [int]$Matches[1] }
    if ($_ -match '^full=(\d)') { $full = [int]$Matches[1] }
  }
}
Log "Choices: config=$config skills=$skills full=$full home=$homeRem"

function Remove-PathSafe([string]$p) {
  if (-not $p) { return }
  if (-not (Test-Path -LiteralPath $p)) { return }
  try {
    if (Test-Path -LiteralPath $p -PathType Container) {
      Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop
    } else {
      Remove-Item -LiteralPath $p -Force -ErrorAction Stop
    }
    Log "Removed: $p"
  } catch {
    Log "WARN remove failed: $p :: $($_.Exception.Message)"
  }
}

if ($full -eq 1) {
  # Full wipe: nothing left for a fresh install.
  Remove-PathSafe $homeRem
  # Tauri / app leftovers
  Remove-PathSafe (Join-Path $env:APPDATA 'com.remedy.desktop')
  Remove-PathSafe (Join-Path $env:LOCALAPPDATA 'com.remedy.desktop')
  Remove-PathSafe (Join-Path $env:LOCALAPPDATA 'Remedy Desktop')
  Remove-PathSafe (Join-Path $env:LOCALAPPDATA 'Programs\Remedy Desktop')
  # Startup shortcut
  Remove-PathSafe (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Remedy Desktop.lnk')
  # Start Menu / Desktop shortcuts (best-effort)
  Remove-PathSafe (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Remedy Desktop')
  Remove-PathSafe (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Remedy Desktop.lnk')
  Remove-PathSafe (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Remedy Desktop.lnk')
  # Temp update / uninstall artifacts
  Remove-PathSafe (Join-Path $env:TEMP 'RemedyDesktop-Update.log')
  Get-ChildItem -Path $env:TEMP -Filter 'RemedyDesktop-Update*' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-PathSafe $_.FullName }
  Get-ChildItem -Path $env:TEMP -Filter 'RemedyDesktop-Uninstall*' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-PathSafe $_.FullName }
  # Legacy Run keys
  foreach ($n in @('RemedyDesktop', 'Remedy Desktop', 'remedy-desktop')) {
    Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name $n -ErrorAction SilentlyContinue
  }
  # Manufacturer key leftovers
  Remove-Item -Path 'HKCU:\Software\com.remedy.desktop' -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -Path 'HKCU:\Software\Remedy' -Recurse -Force -ErrorAction SilentlyContinue
  Log 'Full wipe complete'
  exit 0
}

if ($config -eq 1 -and (Test-Path -LiteralPath $homeRem)) {
  # Config-ish files — keep memory/skills unless skills also selected
  @(
    'config.toml', 'config.yaml', 'config.yml', 'desktop.json',
    'comfyui.json'
  ) | ForEach-Object { Remove-PathSafe (Join-Path $homeRem $_) }
  Remove-PathSafe (Join-Path $homeRem 'auth')
  Log 'Config wipe done'
}

if ($skills -eq 1) {
  Remove-PathSafe (Join-Path $homeRem 'skills')
  Log 'Skills wipe done'
}

Log 'Selective wipe finished'
exit 0
