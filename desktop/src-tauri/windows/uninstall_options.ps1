#Requires -Version 5.1
<#
.SYNOPSIS
  Uninstall options dialog for Remedy Desktop (NSIS PREUNINSTALL).

.DESCRIPTION
  Shows checkboxes:
    - Remove configuration (config.toml, desktop.json, auth keys, …)
    - Remove skills (~/.remedy/skills)
    - Full wipe (entire ~/.remedy + app leftovers — nothing left for a fresh install)

  Writes a result file consumed by the NSIS uninstaller:
    %TEMP%\RemedyDesktop-UninstallChoices.txt
  Lines: config=0|1, skills=0|1, full=0|1

  Silent /UPDATE uninstalls skip the UI and keep data (safe for auto-update).
#>
param(
  [switch]$SilentKeepData,
  [switch]$ForceFull
)

$ErrorActionPreference = 'Stop'
$outFile = Join-Path $env:TEMP 'RemedyDesktop-UninstallChoices.txt'

function Write-Choices([int]$Config, [int]$Skills, [int]$Full) {
  $lines = @(
    "config=$Config",
    "skills=$Skills",
    "full=$Full"
  )
  [System.IO.File]::WriteAllLines($outFile, $lines)
}

if ($ForceFull) {
  Write-Choices 1 1 1
  exit 0
}

if ($SilentKeepData) {
  Write-Choices 0 0 0
  exit 0
}

# GUI dialog (WinForms)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Remedy Desktop — Uninstall options'
$form.Size = New-Object System.Drawing.Size(480, 320)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true

$label = New-Object System.Windows.Forms.Label
$label.Location = New-Object System.Drawing.Point(16, 16)
$label.Size = New-Object System.Drawing.Size(430, 48)
$label.Text = "The app will be removed from this PC.`r`nChoose what user data to delete under your profile (.remedy):"
$form.Controls.Add($label)

$cbConfig = New-Object System.Windows.Forms.CheckBox
$cbConfig.Location = New-Object System.Drawing.Point(24, 76)
$cbConfig.Size = New-Object System.Drawing.Size(420, 28)
$cbConfig.Text = 'Remove configuration (config, desktop prefs, API keys / auth)'
$form.Controls.Add($cbConfig)

$cbSkills = New-Object System.Windows.Forms.CheckBox
$cbSkills.Location = New-Object System.Drawing.Point(24, 110)
$cbSkills.Size = New-Object System.Drawing.Size(420, 28)
$cbSkills.Text = 'Remove skills (custom & learned skills under .remedy\skills)'
$form.Controls.Add($cbSkills)

$cbFull = New-Object System.Windows.Forms.CheckBox
$cbFull.Location = New-Object System.Drawing.Point(24, 144)
$cbFull.Size = New-Object System.Drawing.Size(420, 40)
$cbFull.Text = 'Full wipe — delete entire .remedy folder + leftovers (fresh install will be clean)'
$form.Controls.Add($cbFull)

$hint = New-Object System.Windows.Forms.Label
$hint.Location = New-Object System.Drawing.Point(24, 190)
$hint.Size = New-Object System.Drawing.Size(420, 36)
$hint.ForeColor = [System.Drawing.Color]::DimGray
$hint.Text = 'Leave all unchecked to keep your data. Full wipe implies config + skills and all memory/sessions.'
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
$btnOk.Location = New-Object System.Drawing.Point(230, 240)
$btnOk.Size = New-Object System.Drawing.Size(140, 28)
$btnOk.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($btnOk)
$form.AcceptButton = $btnOk

$btnCancel = New-Object System.Windows.Forms.Button
$btnCancel.Text = 'Cancel'
$btnCancel.Location = New-Object System.Drawing.Point(120, 240)
$btnCancel.Size = New-Object System.Drawing.Size(90, 28)
$btnCancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($btnCancel)
$form.CancelButton = $btnCancel

$result = $form.ShowDialog()
if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
  # Cancel uninstall — signal NSIS with exit code 1
  Write-Choices 0 0 0
  exit 1
}

$full = [int]$cbFull.Checked
$config = if ($full -eq 1) { 1 } else { [int]$cbConfig.Checked }
$skills = if ($full -eq 1) { 1 } else { [int]$cbSkills.Checked }
Write-Choices $config $skills $full
exit 0
