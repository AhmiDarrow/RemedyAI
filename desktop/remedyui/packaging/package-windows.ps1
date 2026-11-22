# Builds Remedy.Desktop_{version}_x64-setup.exe (NSIS) for the RemedyUI shell.
# Does not tag, publish, or run the installer.
# Usage:
#   ./package-windows.ps1
#   ./package-windows.ps1 -SkipBuild -Binary path\to\remedy-desktop.exe
param(
    [string]$Profile = "release",
    [switch]$SkipBuild,
    [string]$Binary = "",
    [int]$Jobs = 4
)

$ErrorActionPreference = "Stop"
try { (Get-Process -Id $PID).PriorityClass = "BelowNormal" } catch {}

$manifestDir = Split-Path -Parent $PSScriptRoot
Set-Location $manifestDir

$cargo = Get-Content -Raw -LiteralPath (Join-Path $manifestDir "Cargo.toml")
if ($cargo -notmatch '(?m)^version\s*=\s*"(\d+\.\d+\.\d+)"') {
    throw "Could not read the package version from Cargo.toml"
}
$version = $Matches[1]

if (-not $SkipBuild) {
    & cargo build --profile $Profile --jobs $Jobs --locked --manifest-path (Join-Path $manifestDir "Cargo.toml")
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed ($LASTEXITCODE)" }
}

if (-not $Binary) {
    $dirName = if ($Profile -eq "dev") { "debug" } else { $Profile }
    $Binary = Join-Path $manifestDir "target\$dirName\remedy-desktop.exe"
}
if (-not (Test-Path -LiteralPath $Binary)) {
    throw "Binary not found: $Binary"
}

$makensis = Get-Command makensis -ErrorAction SilentlyContinue
if (-not $makensis) {
    throw "makensis is not on PATH. Install NSIS 3 and leave the GUI tools closed."
}

$outDir = Join-Path $manifestDir "target\packaging"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$out = Join-Path $outDir "Remedy.Desktop_${version}_x64-setup.exe"
if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
$nsi = Join-Path $PSScriptRoot "windows\installer.nsi"

& $makensis.Source "/V2" "/DVERSION=$version" "/DOUTFILE=$out" "/DMAINBINARYSRCPATH=$Binary" $nsi
if ($LASTEXITCODE -ne 0) { throw "makensis failed ($LASTEXITCODE)" }
if (-not (Test-Path -LiteralPath $out)) { throw "makensis did not write $out" }

& (Join-Path $PSScriptRoot "sign-minisign.ps1") -File $out
if ($LASTEXITCODE -ne 0) { throw "signing failed ($LASTEXITCODE)" }

Write-Output $out
Write-Output "Not published. Copy the .sig text into packaging/latest.json when Joe cuts the 1.0.0 release."
