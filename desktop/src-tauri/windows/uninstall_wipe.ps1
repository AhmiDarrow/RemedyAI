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

function Stop-VisionDecoder {
  # Stop local llama-server so vision/ files are not locked during wipe.
  try {
    Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue |
      ForEach-Object {
        Log "Stopping vision process pid=$($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
      }
  } catch {
    Log "WARN stop llama-server: $($_.Exception.Message)"
  }
  # Best-effort free vision port
  try {
    $conns = Get-NetTCPConnection -LocalPort 8740 -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
      if ($c.OwningProcess) {
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        Log "Stopped pid on :8740 = $($c.OwningProcess)"
      }
    }
  } catch {}
}

function Remove-VisionTree {
  Stop-VisionDecoder
  Start-Sleep -Milliseconds 400
  Remove-PathSafe (Join-Path $homeRem 'vision')
  # Stray download/cache names if any lived outside vision/ (legacy)
  Remove-PathSafe (Join-Path $homeRem 'llama-server')
  Remove-PathSafe (Join-Path $homeRem 'llama.cpp')
  Log 'Vision decoder (llama.cpp + models) removed'
}

if ($full -eq 1) {
  # Full wipe: nothing left for a fresh install.
  Stop-VisionDecoder
  # Do NOT delete the live install directory from this process — NSIS already
  # removed app files; wiping Programs\Remedy Desktop while uninstall.exe still
  # runs can fail or leave a half-deleted folder. Best-effort only after a delay.
  Remove-PathSafe $homeRem
  # Tauri / app leftovers (user data dirs — safe)
  Remove-PathSafe (Join-Path $env:APPDATA 'com.remedy.desktop')
  Remove-PathSafe (Join-Path $env:LOCALAPPDATA 'com.remedy.desktop')
  # Startup shortcut
  Remove-PathSafe (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Remedy Desktop.lnk')
  # Start Menu / Desktop shortcuts (best-effort)
  Remove-PathSafe (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Remedy Desktop')
  Remove-PathSafe (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Remedy Desktop.lnk')
  try {
    Remove-PathSafe (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Remedy Desktop.lnk')
  } catch {}
  # Temp update artifacts (not the active UninstallChoices / this script folder mid-run)
  Remove-PathSafe (Join-Path $env:TEMP 'RemedyDesktop-Update.log')
  Get-ChildItem -Path $env:TEMP -Filter 'RemedyDesktop-Update*' -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-PathSafe $_.FullName }
  # Legacy Run keys
  foreach ($n in @('RemedyDesktop', 'Remedy Desktop', 'remedy-desktop')) {
    Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name $n -ErrorAction SilentlyContinue
  }
  # Manufacturer key leftovers
  Remove-Item -Path 'HKCU:\Software\com.remedy.desktop' -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -Path 'HKCU:\Software\Remedy' -Recurse -Force -ErrorAction SilentlyContinue
  # Optional: schedule install-dir cleanup after uninstaller exits (no hard fail)
  $installCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Remedy Desktop'),
    (Join-Path $env:LOCALAPPDATA 'Remedy Desktop')
  )
  foreach ($dir in $installCandidates) {
    if (-not (Test-Path -LiteralPath $dir)) { continue }
    # Skip if our own process is running from that tree
    $myPath = $PSCommandPath
    if ($myPath -and $myPath.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase)) {
      Log "Skip live install dir (self): $dir"
      continue
    }
    try {
      # Best-effort delayed delete via cmd so NSIS uninstaller can exit first
      $cmd = "ping -n 3 127.0.0.1 >nul & rmdir /s /q `"$dir`""
      Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $cmd) -WindowStyle Hidden -ErrorAction SilentlyContinue
      Log "Scheduled delayed rmdir: $dir"
    } catch {
      Log "WARN schedule rmdir $dir :: $($_.Exception.Message)"
    }
  }
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
  # Local visual decoder: llama-server binary + Qwen GGUF/mmproj (~GBs)
  Remove-VisionTree
  Log 'Config wipe done (includes vision decoder / llama.cpp)'
}

if ($skills -eq 1) {
  Remove-PathSafe (Join-Path $homeRem 'skills')
  # Durable skill execution stats live next to skills, not inside the tree
  Remove-PathSafe (Join-Path $homeRem 'skill_stats.json')
  Log 'Skills wipe done'
}

Log 'Selective wipe finished'
exit 0
