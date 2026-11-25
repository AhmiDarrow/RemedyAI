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

$desktop = Split-Path -Parent $manifestDir
$repoRoot = Split-Path -Parent $desktop
$outDirEarly = Join-Path $manifestDir "target\packaging"
New-Item -ItemType Directory -Force -Path $outDirEarly | Out-Null
$Runtime = Join-Path $outDirEarly "remedy-runtime.exe"
if (-not $SkipBuild -or -not (Test-Path -LiteralPath $Runtime)) {
    $go = Get-Command go -ErrorAction SilentlyContinue
    if (-not $go) { throw "go is required to build remedy-runtime.exe" }
    $goMod = Join-Path $repoRoot "native\go"
    Push-Location $goMod
    try {
        & $go.Source build -trimpath -ldflags "-s -w" -o $Runtime ./cmd/remedy-runtime
        if ($LASTEXITCODE -ne 0) { throw "go build remedy-runtime failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
}
# Her voice: the speech helper remedy-runtime starts the first time she speaks
# or listens. One file, with the speech runtime linked in.
$voiceManifest = Join-Path $repoRoot "native\voice\Cargo.toml"
$Voice = Join-Path $repoRoot "native\voice\target\release\remedy-voice.exe"
if (-not $SkipBuild -or -not (Test-Path -LiteralPath $Voice)) {
    & cargo build --release --jobs $Jobs --locked --manifest-path $voiceManifest
    if ($LASTEXITCODE -ne 0) { throw "cargo build remedy-voice failed ($LASTEXITCODE)" }
}
if (-not (Test-Path -LiteralPath $Voice)) { throw "remedy-voice.exe missing: $Voice" }
if (-not (Test-Path -LiteralPath $Runtime)) {
    throw "Runtime not found: $Runtime"
}

$Core = Join-Path $repoRoot "native\zig\zig-out\bin\remedy_core.dll"
if (-not $SkipBuild -or -not (Test-Path -LiteralPath $Core)) {
    $zig = Get-Command zig -ErrorAction SilentlyContinue
    if (-not $zig) { throw "zig is required to build remedy_core.dll" }
    $zigDir = Join-Path $repoRoot "native\zig"
    Push-Location $zigDir
    try {
        & $zig.Source build -Doptimize=ReleaseSafe
        if ($LASTEXITCODE -ne 0) { throw "zig build remedy_core failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
}
if (-not (Test-Path -LiteralPath $Core)) {
    throw "Core not found: $Core"
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
# Windows makensis rejects forward slashes in !include and File.
function To-NsisPath([string]$Path) {
    return (Resolve-Path -LiteralPath $Path).Path
}
$hooks = To-NsisPath (Join-Path $desktop "src-tauri\windows\hooks.nsh")
$options = To-NsisPath (Join-Path $desktop "src-tauri\windows\uninstall_options.ps1")
$wipe = To-NsisPath (Join-Path $desktop "src-tauri\windows\uninstall_wipe.ps1")
$icon = To-NsisPath (Join-Path $desktop "src-tauri\icons\icon.ico")
$binaryNsis = (Resolve-Path -LiteralPath $Binary).Path
$runtimeNsis = (Resolve-Path -LiteralPath $Runtime).Path
$voiceNsis = (Resolve-Path -LiteralPath $Voice).Path
$coreNsis = (Resolve-Path -LiteralPath $Core).Path
$outNsis = $out

# Build a matching chat Web UI (Vite app in desktop/). Packaged runtime serves
# $INSTDIR\webui next to remedy-runtime.exe. Always rebuild when desktop/src is
# newer than dist (or dist is incomplete) so index.html cannot reference a
# chunk that was never copied.
function Assert-WebUIComplete([string]$Root) {
    $index = Join-Path $Root "index.html"
    if (-not (Test-Path -LiteralPath $index)) {
        throw "Web UI is missing: expected $index"
    }
    $html = Get-Content -Raw -LiteralPath $index
    $refs = [System.Collections.Generic.List[string]]::new()
    foreach ($m in [regex]::Matches($html, '(?:src|href)\s*=\s*["'']([^"'']+)["'']')) {
        $rel = $m.Groups[1].Value
        if ($rel -match '^(https?:|data:|mailto:|#)') { continue }
        $rel = $rel -replace '[?#].*$', ''
        $rel = $rel.TrimStart('./').TrimStart('/')
        if ($rel) { $refs.Add($rel) }
    }
    $i = 0
    while ($i -lt $refs.Count) {
        $rel = $refs[$i]
        $i++
        $path = Join-Path $Root ($rel -replace '/', '\')
        if (-not (Test-Path -LiteralPath $path)) {
            throw "Web UI bundle is incomplete: $index references $rel but $path is missing"
        }
        if ($rel -notmatch '\.(js|mjs)$') { continue }
        $body = Get-Content -Raw -LiteralPath $path
        $base = Split-Path -Parent $rel
        foreach ($im in [regex]::Matches($body, '["''](\./[A-Za-z0-9_./@-]+\.(?:js|mjs|css|wasm))["'']')) {
            $imp = $im.Groups[1].Value
            if ($imp.StartsWith('./')) {
                if ([string]::IsNullOrEmpty($base) -or $base -eq '.') {
                    $joined = $imp.Substring(2)
                } else {
                    $joined = ($base.TrimEnd('/') + '/' + $imp.Substring(2))
                }
                $joined = $joined.Replace('\', '/')
                if (-not $refs.Contains($joined)) { $refs.Add($joined) }
            }
        }
    }
}

$webuiStage = Join-Path $outDir "webui"
if (Test-Path -LiteralPath $webuiStage) { Remove-Item -LiteralPath $webuiStage -Recurse -Force }
$dist = Join-Path $desktop "dist"
$indexPath = Join-Path $dist "index.html"
$needBuild = $true
if (Test-Path -LiteralPath $indexPath) {
    try {
        Assert-WebUIComplete $dist
        $distTime = (Get-Item -LiteralPath $indexPath).LastWriteTimeUtc
        $srcRoots = @(
            (Join-Path $desktop "src"),
            (Join-Path $desktop "index.html"),
            (Join-Path $desktop "vite.config.ts"),
            (Join-Path $desktop "package.json")
        )
        $newer = $false
        foreach ($src in $srcRoots) {
            if (-not (Test-Path -LiteralPath $src)) { continue }
            $items = Get-Item -LiteralPath $src
            if ($items.PSIsContainer) {
                $items = Get-ChildItem -LiteralPath $src -Recurse -File -ErrorAction SilentlyContinue
            }
            foreach ($item in @($items)) {
                if ($item.LastWriteTimeUtc -gt $distTime) { $newer = $true; break }
            }
            if ($newer) { break }
        }
        $needBuild = $newer
        if (-not $needBuild) { Write-Output "Reusing complete desktop/dist (up to date with desktop/src)" }
    } catch {
        Write-Output "Rebuilding Web UI: $($_.Exception.Message)"
        $needBuild = $true
    }
}
if ($needBuild) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { throw "npm is required to build the Web UI (desktop/). Install Node.js, or leave a built desktop/dist/index.html." }
    Push-Location $desktop
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $desktop "node_modules"))) {
            if (Test-Path -LiteralPath (Join-Path $desktop "package-lock.json")) {
                & $npm.Source ci
            } else {
                & $npm.Source install
            }
            if ($LASTEXITCODE -ne 0) { throw "npm install/ci failed ($LASTEXITCODE)" }
        }
        # npm 12 may skip esbuild's postinstall (allowScripts). Vite needs the binary.
        $esbuildInstall = Join-Path $desktop "node_modules\esbuild\install.js"
        if (Test-Path -LiteralPath $esbuildInstall) {
            & node $esbuildInstall
            if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "esbuild postinstall failed ($LASTEXITCODE)" }
        }
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build (Web UI) failed ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
}
Assert-WebUIComplete $dist
New-Item -ItemType Directory -Force -Path $webuiStage | Out-Null
Copy-Item -Path (Join-Path $dist "*") -Destination $webuiStage -Recurse -Force
Assert-WebUIComplete $webuiStage
$webuiNsis = (Resolve-Path -LiteralPath $webuiStage).Path

& $makensis.Source "/V2" "/DVERSION=$version" "/DOUTFILE=$outNsis" "/DMAINBINARYSRCPATH=$binaryNsis" "/DRUNTIMESRCPATH=$runtimeNsis" "/DVOICESRCPATH=$voiceNsis" "/DCORESRCPATH=$coreNsis" "/DWEBUISRCPATH=$webuiNsis" "/DHOOKS=$hooks" "/DUNINSTALL_OPTIONS=$options" "/DUNINSTALL_WIPE=$wipe" "/DINSTALLERICON=$icon" $nsi
if ($LASTEXITCODE -ne 0) { throw "makensis failed ($LASTEXITCODE)" }
if (-not (Test-Path -LiteralPath $out)) { throw "makensis did not write $out" }

& (Join-Path $PSScriptRoot "sign-minisign.ps1") -File $out
if ($LASTEXITCODE -ne 0) { throw "signing failed ($LASTEXITCODE)" }

Write-Output $out
Write-Output "Not published. Copy the .sig text into packaging/latest.json when Joe cuts the release."
