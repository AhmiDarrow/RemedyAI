# Remedy Desktop - update progress host (I-A helper process)
# Lives in TEMP; does not lock install-dir EXEs. Polls status JSON written by the updater.
# Usage: powershell -File remedy-update-ui.ps1 [-StatusPath path] [-From v] [-To v]
#
# IMPORTANT: ASCII-only strings (Windows PowerShell 5.1 may load .ps1 as system ANSI
# when no BOM is present). Keep the host console hidden; only the WinForms UI shows.

param(
  [string]$StatusPath = "",
  [string]$From = "?",
  [string]$To = "?"
)

$ErrorActionPreference = 'SilentlyContinue'
if (-not $StatusPath) {
  $StatusPath = Join-Path $env:TEMP 'RemedyDesktop-Update-status.json'
}

# WinForms requires STA (caller should pass -STA; re-enter hidden if not).
if ([System.Threading.Thread]::CurrentThread.GetApartmentState() -ne 'STA') {
  $argList = @(
    '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass',
    '-WindowStyle', 'Hidden',
    '-File', $MyInvocation.MyCommand.Path,
    '-StatusPath', $StatusPath,
    '-From', $From,
    '-To', $To
  )
  # Hidden console; WinForms form still appears from the STA child.
  Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -WindowStyle Hidden | Out-Null
  exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Remedy Install Progress'
$form.Size = New-Object System.Drawing.Size(460, 230)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $true
$form.TopMost = $true
$form.ShowInTaskbar = $true
$form.BackColor = [System.Drawing.Color]::FromArgb(18, 22, 28)
$form.ForeColor = [System.Drawing.Color]::FromArgb(230, 236, 242)

$title = New-Object System.Windows.Forms.Label
$title.Text = 'Remedy Install'
$title.Font = New-Object System.Drawing.Font('Segoe UI', 16, [System.Drawing.FontStyle]::Bold)
$title.ForeColor = [System.Drawing.Color]::FromArgb(56, 189, 248)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(24, 18)
$form.Controls.Add($title)

$ver = New-Object System.Windows.Forms.Label
$ver.Text = ("v{0}  ->  v{1}" -f $From, $To)
$ver.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$ver.ForeColor = [System.Drawing.Color]::FromArgb(160, 174, 192)
$ver.AutoSize = $true
$ver.Location = New-Object System.Drawing.Point(26, 52)
$form.Controls.Add($ver)

$phaseLbl = New-Object System.Windows.Forms.Label
$phaseLbl.Text = 'Preparing...'
$phaseLbl.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$phaseLbl.ForeColor = [System.Drawing.Color]::White
$phaseLbl.AutoSize = $false
$phaseLbl.Size = New-Object System.Drawing.Size(380, 24)
$phaseLbl.Location = New-Object System.Drawing.Point(26, 82)
$form.Controls.Add($phaseLbl)

$bar = New-Object System.Windows.Forms.ProgressBar
$bar.Style = 'Continuous'
$bar.Minimum = 0
$bar.Maximum = 100
$bar.Value = 2
$bar.Size = New-Object System.Drawing.Size(380, 18)
$bar.Location = New-Object System.Drawing.Point(26, 112)
$form.Controls.Add($bar)

$msg = New-Object System.Windows.Forms.Label
$msg.Text = 'Please keep this window open until Remedy restarts.'
$msg.Font = New-Object System.Drawing.Font('Segoe UI', 8.5)
$msg.ForeColor = [System.Drawing.Color]::FromArgb(140, 155, 170)
$msg.AutoSize = $false
$msg.Size = New-Object System.Drawing.Size(380, 36)
$msg.Location = New-Object System.Drawing.Point(26, 140)
$form.Controls.Add($msg)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 400
$script:done = $false
$script:idleTicks = 0
$script:From = $From
$script:To = $To

$timer.Add_Tick({
  if (-not (Test-Path -LiteralPath $StatusPath)) {
    $script:idleTicks++
    # Keep window visible even if status file is late (app just closed).
    if ($script:idleTicks -eq 1) {
      $phaseLbl.Text = 'Waiting for installer...'
      $msg.Text = 'Remedy closed - install continues. Leave this window open.'
    }
    return
  }
  $script:idleTicks = 0
  try {
    $raw = Get-Content -LiteralPath $StatusPath -Raw -Encoding UTF8 -ErrorAction Stop
    $j = $raw | ConvertFrom-Json
  } catch { return }

  $p = [string]$j.phase
  $pct = 0
  try { $pct = [int]$j.percent } catch { $pct = 0 }
  if ($pct -lt 0) { $pct = 0 }
  if ($pct -gt 100) { $pct = 100 }
  $bar.Value = $pct
  if ($j.message) { $msg.Text = [string]$j.message }
  if ($j.from) { $script:From = [string]$j.from }
  if ($j.to) { $script:To = [string]$j.to }
  $ver.Text = ("v{0}  ->  v{1}" -f $script:From, $script:To)

  switch ($p) {
    'downloading' { $phaseLbl.Text = 'Downloading update...' }
    'closing'     { $phaseLbl.Text = 'Closing Remedy...' }
    'installing'  { $phaseLbl.Text = 'Installing update...' }
    'verifying'   { $phaseLbl.Text = 'Verifying install...' }
    'relaunch'    { $phaseLbl.Text = 'Relaunching...' }
    'done' {
      $phaseLbl.Text = 'Update complete'
      $bar.Value = 100
      $script:done = $true
      $timer.Stop()
      Start-Sleep -Milliseconds 1400
      $form.Close()
    }
    'error' {
      $phaseLbl.Text = 'Update failed'
      $phaseLbl.ForeColor = [System.Drawing.Color]::FromArgb(248, 113, 113)
      $script:done = $true
      $timer.Stop()
    }
    default {
      if ($p) { $phaseLbl.Text = $p }
    }
  }
})

$form.Add_Shown({
  $form.Activate()
  $form.BringToFront()
  $timer.Start()
})
$form.Add_FormClosed({ $timer.Stop() })
[void]$form.ShowDialog()
