# Installs the Remedy runtime and its `remedy` command on Windows.
#
# For a machine without the desktop app. It downloads the runtime for this
# computer from the GitHub release, checks both files against the release's
# SHA-256 list, and installs them for the current user. Nothing is installed
# system-wide and nothing needs administrator rights.
#
#   .\install.ps1                  # latest release
#   .\install.ps1 -Version 2.1.1   # a given version
#   .\install.ps1 -NoPath          # do not add the folder to the user PATH
param(
    [string]$Version = "",
    [string]$InstallDir = "",
    [switch]$NoPath
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$repo = "AhmiDarrow/RemedyAI"
$releases = "https://github.com/$repo/releases"
$sums = "remedy-native-SHA256SUMS.txt"

switch ($env:PROCESSOR_ARCHITECTURE) {
    "AMD64" { $arch = "x86_64" }
    "ARM64" { $arch = "aarch64" }
    default { throw "No Remedy runtime is published for $($env:PROCESSOR_ARCHITECTURE)." }
}
$triple = "$arch-pc-windows-msvc"
$runtimeAsset = "remedy-runtime-$triple.exe"
$coreAsset = "remedy_core-$triple.dll"

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("remedy-install-" + [guid]::NewGuid().ToString("n"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    if (-not $Version) {
        $latest = Join-Path $tmp "latest.json"
        Invoke-WebRequest -UseBasicParsing -Uri "$releases/latest/download/latest.json" -OutFile $latest
        $Version = (Get-Content -Raw -LiteralPath $latest | ConvertFrom-Json).version
    }
    $Version = "$Version".TrimStart("v")
    if ($Version -notmatch '^[0-9A-Za-z.+-]+$') { throw "That does not look like a version: '$Version'." }

    $base = "$releases/download/runtime-v$Version"
    Write-Output "Remedy $Version for $triple"
    $sumsPath = Join-Path $tmp $sums
    Invoke-WebRequest -UseBasicParsing -Uri "$base/$sums" -OutFile $sumsPath
    $listed = @{}
    foreach ($line in Get-Content -LiteralPath $sumsPath) {
        $parts = $line.Trim() -split '\s+', 2
        if ($parts.Count -eq 2) { $listed[$parts[1].TrimStart('*')] = $parts[0].ToLowerInvariant() }
    }
    foreach ($asset in @($runtimeAsset, $coreAsset)) {
        $path = Join-Path $tmp $asset
        Invoke-WebRequest -UseBasicParsing -Uri "$base/$asset" -OutFile $path
        if (-not $listed.ContainsKey($asset)) { throw "$asset is not in the release's checksum list; not installing it." }
        $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
        if ($got -ne $listed[$asset]) { throw "$asset does not match its checksum; not installing it." }
    }
    Write-Output "Checksums match."

    if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA "Remedy\cli" }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    # The command is the runtime under the name people type. Its core library sits beside it.
    Copy-Item -Force -LiteralPath (Join-Path $tmp $runtimeAsset) -Destination (Join-Path $InstallDir "remedy.exe")
    Copy-Item -Force -LiteralPath (Join-Path $tmp $coreAsset) -Destination (Join-Path $InstallDir "remedy_core.dll")
    Write-Output "Installed: $(Join-Path $InstallDir 'remedy.exe')"

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $onPath = ($userPath -split ';') -contains $InstallDir
    if (-not $onPath -and -not $NoPath) {
        $newPath = if ([string]::IsNullOrEmpty($userPath)) { $InstallDir } else { "$userPath;$InstallDir" }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-Output "Added $InstallDir to your user PATH. Open a new terminal to use it."
    } elseif (-not $onPath) {
        Write-Output "Add $InstallDir to your PATH to run 'remedy' from anywhere."
    }
    Write-Output "Start with:  remedy setup"
    Write-Output "To update later, run this script again."
} finally {
    Remove-Item -Recurse -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
}
