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

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "python is required to run packaging/check.py" }
& $py.Source (Join-Path $PSScriptRoot "check.py")
if ($LASTEXITCODE -ne 0) { throw "packaging check failed ($LASTEXITCODE)" }

if (-not $SkipBuild) {
    & cargo build --profile $Profile --jobs $Jobs --locked --manifest-path (Join-Path $manifestDir "Cargo.toml")
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed ($LASTEXITCODE)" }
}

if (-not $Binary) {
    # Honor CARGO_TARGET_DIR. CI leaves it unset and builds into the crate's target/.
    $targetRoot = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { Join-Path $manifestDir "target" }
    $dirName = if ($Profile -eq "dev") { "debug" } else { $Profile }
    $Binary = Join-Path $targetRoot "$dirName\remedy-desktop.exe"
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
$desktop = Split-Path -Parent $manifestDir
# Windows makensis rejects forward slashes in !include and File.
function To-NsisPath([string]$Path) {
    return (Resolve-Path -LiteralPath $Path).Path
}
$hooks = To-NsisPath (Join-Path $desktop "src-tauri\windows\hooks.nsh")
$options = To-NsisPath (Join-Path $desktop "src-tauri\windows\uninstall_options.ps1")
$wipe = To-NsisPath (Join-Path $desktop "src-tauri\windows\uninstall_wipe.ps1")
$icon = To-NsisPath (Join-Path $desktop "src-tauri\icons\icon.ico")
$binaryNsis = (Resolve-Path -LiteralPath $Binary).Path
$outNsis = $out

& $makensis.Source "/V2" "/DVERSION=$version" "/DOUTFILE=$outNsis" "/DMAINBINARYSRCPATH=$binaryNsis" "/DHOOKS=$hooks" "/DUNINSTALL_OPTIONS=$options" "/DUNINSTALL_WIPE=$wipe" "/DINSTALLERICON=$icon" $nsi
if ($LASTEXITCODE -ne 0) { throw "makensis failed ($LASTEXITCODE)" }
if (-not (Test-Path -LiteralPath $out)) { throw "makensis did not write $out" }

& (Join-Path $PSScriptRoot "sign-minisign.ps1") -File $out
if ($LASTEXITCODE -ne 0) { throw "signing failed ($LASTEXITCODE)" }

Write-Output $out
Write-Output "Not published. Copy the .sig text into packaging/latest.json when Joe cuts the 1.0.0 release."
