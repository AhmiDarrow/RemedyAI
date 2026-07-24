#Requires -Version 5.1
# Uninstall options dialog for Remedy Desktop (NSIS PREUNINSTALL).
# ASCII-only UI strings so Windows codepages never show mojibake next to labels.
param(
  [switch]$SilentKeepData,
  [switch]$ForceFull
)

$ErrorActionPreference = 'Continue'
$outFile = Join-Path $env:TEMP 'RemedyDesktop-UninstallChoices.txt'
$logFile = Join-Path $env:TEMP 'RemedyDesktop-UninstallOptions.log'

function Log([string]$m) {
  try {
    $line = '{0:u} {1}' -f (Get-Date), $m
    Add-Content -LiteralPath $logFile -Value $line -ErrorAction SilentlyContinue
  } catch {}
}

function Write-Choices([int]$Config, [int]$Skills, [int]$Full) {
  $lines = @(
    ("config={0}" -f $Config),
    ("skills={0}" -f $Skills),
    ("full={0}" -f $Full)
  )
  try {
    [System.IO.File]::WriteAllLines($outFile, $lines)
    Log ("Wrote choices config={0} skills={1} full={2}" -f $Config, $Skills, $Full)
  } catch {
    Log ("WARN write choices failed: {0}" -f $_.Exception.Message)
  }
}

try {
  if ($ForceFull) {
    Write-Choices 1 1 1
    exit 0
  }

  if ($SilentKeepData) {
    Write-Choices 0 0 0
    exit 0
  }

  try {
    Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
    Add-Type -AssemblyName System.Drawing -ErrorAction Stop
  } catch {
    Log ("WinForms unavailable: {0} - keep data, soft-fail" -f $_.Exception.Message)
    Write-Choices 0 0 0
    exit 2
  }

  [System.Windows.Forms.Application]::EnableVisualStyles()
  $uiFont = [System.Drawing.SystemFonts]::MessageBoxFont
  if (-not $uiFont) {
    $uiFont = New-Object System.Drawing.Font('Segoe UI', 9.0)
  }

  $form = New-Object System.Windows.Forms.Form
  $form.Text = 'Remedy Desktop - Uninstall'
  $form.Size = New-Object System.Drawing.Size(500, 340)
  $form.StartPosition = 'CenterScreen'
  $form.FormBorderStyle = 'FixedDialog'
  $form.MaximizeBox = $false
  $form.MinimizeBox = $false
  $form.TopMost = $true
  $form.ShowInTaskbar = $true
  $form.Font = $uiFont
  $form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Font

  $label = New-Object System.Windows.Forms.Label
  $label.Location = New-Object System.Drawing.Point(16, 16)
  $label.Size = New-Object System.Drawing.Size(450, 48)
  $label.Font = $uiFont
  $label.Text = "Remove Remedy Desktop from this PC.`r`nOptionally delete user data under your profile (.remedy):"
  $form.Controls.Add($label)

  $cbConfig = New-Object System.Windows.Forms.CheckBox
  $cbConfig.Location = New-Object System.Drawing.Point(24, 76)
  $cbConfig.Size = New-Object System.Drawing.Size(440, 28)
  $cbConfig.Font = $uiFont
  $cbConfig.UseVisualStyleBackColor = $true
  $cbConfig.Text = 'Remove configuration (config, desktop prefs, API keys / auth)'
  $form.Controls.Add($cbConfig)

  $cbSkills = New-Object System.Windows.Forms.CheckBox
  $cbSkills.Location = New-Object System.Drawing.Point(24, 110)
  $cbSkills.Size = New-Object System.Drawing.Size(440, 28)
  $cbSkills.Font = $uiFont
  $cbSkills.UseVisualStyleBackColor = $true
  $cbSkills.Text = 'Remove skills (custom and learned skills)'
  $form.Controls.Add($cbSkills)

  $cbFull = New-Object System.Windows.Forms.CheckBox
  $cbFull.Location = New-Object System.Drawing.Point(24, 144)
  $cbFull.Size = New-Object System.Drawing.Size(440, 40)
  $cbFull.Font = $uiFont
  $cbFull.UseVisualStyleBackColor = $true
  $cbFull.Text = 'Full wipe - delete entire .remedy folder and leftovers'
  $form.Controls.Add($cbFull)

  $hint = New-Object System.Windows.Forms.Label
  $hint.Location = New-Object System.Drawing.Point(24, 192)
  $hint.Size = New-Object System.Drawing.Size(440, 40)
  $hint.Font = $uiFont
  $hint.ForeColor = [System.Drawing.SystemColors]::GrayText
  $hint.Text = 'Leave all unchecked to keep your data. Full wipe includes config, skills, memory, and sessions.'
  $form.Controls.Add($hint)

  $cbFull.Add_CheckedChanged({
    if ($cbFull.Checked) {
      $cbConfig.Checked = $true
      $cbSkills.Checked = $true
      $cbConfig.Enabled = $false
      $cbSkills.Enabled = $false
    } else {
      $cbConfig.Enabled = $true
      $cbSkills.Enabled = $true
    }
  })

  $btnOk = New-Object System.Windows.Forms.Button
  $btnOk.Text = 'Continue uninstall'
  $btnOk.Location = New-Object System.Drawing.Point(250, 250)
  $btnOk.Size = New-Object System.Drawing.Size(140, 30)
  $btnOk.Font = $uiFont
  $btnOk.DialogResult = [System.Windows.Forms.DialogResult]::OK
  $btnOk.UseVisualStyleBackColor = $true
  $form.Controls.Add($btnOk)
  $form.AcceptButton = $btnOk

  $btnCancel = New-Object System.Windows.Forms.Button
  $btnCancel.Text = 'Cancel'
  $btnCancel.Location = New-Object System.Drawing.Point(140, 250)
  $btnCancel.Size = New-Object System.Drawing.Size(90, 30)
  $btnCancel.Font = $uiFont
  $btnCancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
  $btnCancel.UseVisualStyleBackColor = $true
  $form.Controls.Add($btnCancel)
  $form.CancelButton = $btnCancel

  [void]$form.Handle
  $form.Add_Shown({ $form.Activate() })

  $result = $form.ShowDialog()
  if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    Log 'User cancelled uninstall options'
    Write-Choices 0 0 0
    exit 1
  }

  $full = [int]$cbFull.Checked
  if ($full -eq 1) {
    $config = 1
    $skills = 1
  } else {
    $config = [int]$cbConfig.Checked
    $skills = [int]$cbSkills.Checked
  }
  Write-Choices $config $skills $full
  Log ("User continued: config={0} skills={1} full={2}" -f $config, $skills, $full)
  exit 0
} catch {
  Log ("FATAL: {0}" -f $_.Exception.Message)
  try { Write-Choices 0 0 0 } catch {}
  exit 2
}
