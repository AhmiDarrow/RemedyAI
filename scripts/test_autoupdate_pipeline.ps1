#Requires -Version 5.1
<#
.SYNOPSIS
  Fake end-to-end auto-update pipeline test (mirrors desktop/src-tauri update path).

.DESCRIPTION
  Parent process schedules a detached update script then exits immediately.
  The update script must still: run a fake installer, replace the app binary
  (mtime advances), and relaunch the new app — proving install survives parent exit.

  Exit 0 = pass.
#>
$ErrorActionPreference = 'Stop'

$root = Join-Path $env:TEMP ('RemedyAutoupdateTest-' + [guid]::NewGuid().ToString('N'))
$installDir = Join-Path $root 'install'
$logPath = Join-Path $root 'update.log'
$fakeInstaller = Join-Path $root 'FakeInstaller.ps1'
$updateScript = Join-Path $root 'Update-Run.ps1'
$relaunchMarker = Join-Path $installDir 'RELAUNCHED.marker'
$versionFile = Join-Path $installDir 'VERSION.txt'
$appPath = Join-Path $installDir 'Remedy Desktop.cmd'

function Write-Utf8([string]$Path, [string]$Content) {
  $dir = Split-Path -Parent $Path
  if (-not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }
  [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

function Fail([string]$Msg) {
  Write-Host "FAIL: $Msg" -ForegroundColor Red
  if (Test-Path -LiteralPath $logPath) {
    Write-Host '--- update log ---' -ForegroundColor Yellow
    Get-Content -LiteralPath $logPath | ForEach-Object { Write-Host $_ }
  }
  Write-Host "Work dir: $root"
  exit 1
}

Write-Host '=== Fake auto-update pipeline test ===' -ForegroundColor Cyan
Write-Host "Work dir: $root"

# --- 1) Fake old install ---
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
Write-Utf8 $versionFile '0.10.26'
Write-Utf8 $appPath "@echo off`r`necho relaunched>%~dp0RELAUNCHED.marker`r`n"
(Get-Item -LiteralPath $appPath).LastWriteTimeUtc = (Get-Date).ToUniversalTime().AddHours(-2)
$oldTicks = (Get-Item -LiteralPath $appPath).LastWriteTimeUtc.Ticks
Write-Host "Old app ticks=$oldTicks version=$((Get-Content $versionFile -Raw).Trim())"

# --- 2) Fake installer (NSIS stand-in: /S /NCRC /D=path) ---
Write-Utf8 $fakeInstaller @'
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Rest)
$ErrorActionPreference = "Stop"
$dest = $null
foreach ($a in $Rest) { if ($a -like "/D=*") { $dest = $a.Substring(3) } }
if (-not $dest) { throw "FakeInstaller: missing /D=" }
if (-not (Test-Path -LiteralPath $dest)) { New-Item -ItemType Directory -Path $dest -Force | Out-Null }
$app = Join-Path $dest "Remedy Desktop.cmd"
$ver = Join-Path $dest "VERSION.txt"
$stub = "@echo off`r`necho relaunched>%~dp0RELAUNCHED.marker`r`necho NEW`r`n"
[System.IO.File]::WriteAllText($app, $stub)
[System.IO.File]::WriteAllText($ver, "0.10.29")
Start-Sleep -Milliseconds 80
(Get-Item -LiteralPath $app).LastWriteTimeUtc = (Get-Date).ToUniversalTime().AddMinutes(5)
exit 0
'@

# --- 3) Update orchestrator (same control flow as lib.rs; short sleeps) ---
# Build without nested-quote hell: use single-quoted fragments + -f format.
$updateLines = @(
  '$ErrorActionPreference = "Continue"'
  ('$log = "{0}"' -f $logPath.Replace('\', '\\'))
  'function Log($m) { Add-Content -LiteralPath $log -Value (("{0:u} {1}" -f (Get-Date), $m)) -EA SilentlyContinue }'
  'Log "Update script started (FAKE PIPELINE)"'
  ('Log "Installer: {0}"' -f $fakeInstaller.Replace('\', '\\'))
  ('Log "Prior exe: {0}"' -f $appPath.Replace('\', '\\'))
  ('Log "Prior dir: {0}"' -f $installDir.Replace('\', '\\'))
  'Start-Sleep -Seconds 1'
  ('$installer = "{0}"' -f $fakeInstaller.Replace('\', '\\'))
  'if (-not (Test-Path -LiteralPath $installer)) { Log "ERROR: installer missing"; exit 2 }'
  ('$priorExe = "{0}"' -f $appPath.Replace('\', '\\'))
  ('$priorDir = "{0}"' -f $installDir.Replace('\', '\\'))
  '$candidates = @($priorExe) | Select-Object -Unique'
  '$before = @{}'
  'foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { $before[$c] = (Get-Item -LiteralPath $c).LastWriteTimeUtc.Ticks } }'
  'Log ("Snapshot count: " + $before.Count)'
  '$argList = @("-NoProfile","-ExecutionPolicy","Bypass","-File",$installer,"/S","/NCRC",("/D=" + $priorDir))'
  'Log ("Starting fake installer -> " + $priorDir)'
  '$p = Start-Process -FilePath "powershell.exe" -ArgumentList $argList -PassThru -WindowStyle Hidden'
  'if (-not $p) { Log "ERROR: Start-Process null"; exit 3 }'
  'Wait-Process -Id $p.Id -Timeout 60 -ErrorAction SilentlyContinue'
  '$exitCode = 0; try { $exitCode = $p.ExitCode } catch { $exitCode = -1 }'
  'Log ("Installer exit code: " + $exitCode)'
  'if ($exitCode -ne 0) { Log "ERROR: installer non-zero"; exit 5 }'
  'Start-Sleep -Seconds 1'
  '$launch = $null'
  'foreach ($c in $candidates) {'
  '  if (-not (Test-Path -LiteralPath $c)) { continue }'
  '  $ticks = (Get-Item -LiteralPath $c).LastWriteTimeUtc.Ticks'
  '  $old = $before[$c]'
  '  if (-not $old -or $ticks -gt $old) { $launch = $c; Log ("Selected updated binary: " + $c); break }'
  '}'
  'if (-not $launch) { foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { $launch = $c; break } } }'
  'if ($launch) {'
  '  Log ("Relaunching: " + $launch)'
  '  Start-Process -FilePath $launch -WindowStyle Hidden'
  '  Log "Relaunch issued"'
  '  exit 0'
  '}'
  'Log "ERROR: no app binary found after install"'
  'exit 4'
)
Write-Utf8 $updateScript ($updateLines -join "`r`n")

# --- 4) Parent schedules detached update then exits (like app.exit) ---
# Avoid empty ArgumentList entries (PS rejects them). Use: start /MIN powershell ...
$parentLines = @(
  '$ErrorActionPreference = "Stop"'
  ('$updateScript = "{0}"' -f $updateScript.Replace('\', '\\'))
  # cmd /c start /MIN ...  (no empty title — PowerShell drops empty -ArgumentList items)
  '$args = @("/C","start","/MIN","powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File",$updateScript)'
  '$psi = Start-Process -FilePath "cmd.exe" -ArgumentList $args -PassThru -WindowStyle Hidden'
  'if (-not $psi) { throw "Failed to schedule update" }'
  'Start-Sleep -Milliseconds 500'
  'exit 0'
)
$parentPs1 = Join-Path $root 'Parent-App.ps1'
Write-Utf8 $parentPs1 ($parentLines -join "`r`n")

Write-Host 'Scheduling update and killing parent...'
$sched = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $parentPs1
) -PassThru -WindowStyle Hidden
if (-not $sched) { Fail 'Could not start parent simulator' }
Wait-Process -Id $sched.Id -Timeout 30
Write-Host "Parent exited (PID $($sched.Id)). Waiting for detached update..."

# --- 5) Assert ---
$deadline = (Get-Date).AddSeconds(40)
$okLog = $false
$okVer = $false
$okRelaunch = $false
while ((Get-Date) -lt $deadline) {
  if (Test-Path -LiteralPath $logPath) {
    $t = Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue
    if ($t -match 'Relaunch issued') { $okLog = $true }
  }
  if ((Test-Path -LiteralPath $versionFile) -and ((Get-Content $versionFile -Raw).Trim() -eq '0.10.29')) {
    $okVer = $true
  }
  if (Test-Path -LiteralPath $relaunchMarker) { $okRelaunch = $true }
  if ($okLog -and $okVer -and $okRelaunch) { break }
  Start-Sleep -Milliseconds 300
}

Write-Host ''
Write-Host '--- results ---' -ForegroundColor Cyan
Write-Host "Log has 'Relaunch issued': $okLog"
Write-Host "VERSION.txt is 0.10.29:     $okVer"
Write-Host "RELAUNCHED.marker present:  $okRelaunch"
if (Test-Path -LiteralPath $logPath) {
  Write-Host '--- update log ---' -ForegroundColor Yellow
  Get-Content -LiteralPath $logPath | ForEach-Object { Write-Host $_ }
}
if (Test-Path -LiteralPath $appPath) {
  $newTicks = (Get-Item -LiteralPath $appPath).LastWriteTimeUtc.Ticks
  Write-Host "App mtime advanced: $($newTicks -gt $oldTicks) ($oldTicks -> $newTicks)"
}

if (-not ($okLog -and $okVer -and $okRelaunch)) {
  Fail "Pipeline incomplete (log=$okLog ver=$okVer relaunch=$okRelaunch)"
}

Write-Host ''
Write-Host 'PASS: fake auto-update pipeline completed after parent exit.' -ForegroundColor Green
Write-Host "Work dir: $root"
exit 0
