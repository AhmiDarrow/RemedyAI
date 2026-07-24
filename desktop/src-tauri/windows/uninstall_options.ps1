#Requires -Version 5.1
# Uninstall options dialog for Remedy Desktop (NSIS PREUNINSTALL).
#
# Checkboxes: config / skills / full wipe under %USERPROFILE%\.remedy
# Writes: %TEMP%\RemedyDesktop-UninstallChoices.txt  (config=0|1, skills=0|1, full=0|1)
#
# Exit codes (NSIS PREUNINSTALL must honor these):
#   0  continue uninstall (choices written)
#   1  user cancelled -> Abort uninstall only for intentional cancel
#   2  dialog/script error -> keep data and STILL uninstall the app
#
# Silent /UPDATE uninstalls skip UI and keep data (safe for auto-update).
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

  $form = New-Object System.Windows.Forms.Form
  $form.Text = 'Remedy Desktop - Uninstall options'
  $form.Size = New-Object System.Drawing.Size(480, 320)
  $form.StartPosition = 'CenterScreen'
  $form.FormBorderStyle = 'FixedDialog'
  $form.MaximizeBox = $false
  $form.MinimizeBox = $false
  $form.TopMost = $true
  $form.ShowInTaskbar = $true

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
  $cbSkills.Text = 'Remove skills (custom and learned skills under .remedy\skills)'
  $form.Controls.Add($cbSkills)

  $cbFull = New-Object System.Windows.Forms.CheckBox
  $cbFull.Location = New-Object System.Drawing.Point(24, 144)
  $cbFull.Size = New-Object System.Drawing.Size(420, 40)
  $cbFull.Text = 'Full wipe - delete entire .remedy folder + leftovers (clean reinstall)'
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
