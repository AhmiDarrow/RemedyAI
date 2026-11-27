# Signs one artifact with the existing minisign secret.
# Reads REMEDY_UPDATE_KEY (a path) or TAURI_SIGNING_PRIVATE_KEY
# (a path, the raw minisign secret text, or the base64 of that text).
# Password: REMEDY_UPDATE_KEY_PASSWORD or TAURI_SIGNING_PRIVATE_KEY_PASSWORD.
# Does not print key material. If no secret is set, exits 0 and leaves the file unsigned.
param(
    [Parameter(Mandatory = $true)]
    [string]$File
)

$ErrorActionPreference = "Stop"

function Write-NoSecret {
    Write-Output "unsigned: no minisign secret in the environment ($([IO.Path]::GetFileName($File)))"
    exit 0
}

if (-not (Test-Path -LiteralPath $File)) {
    Write-Error "file to sign does not exist"
    exit 1
}

$tempKey = $null
$keyPath = $null
$password = $null

if ($env:REMEDY_UPDATE_KEY) {
    if (-not (Test-Path -LiteralPath $env:REMEDY_UPDATE_KEY)) {
        Write-Error "REMEDY_UPDATE_KEY is set but the file is missing"
        exit 1
    }
    $keyPath = $env:REMEDY_UPDATE_KEY
    $password = $env:REMEDY_UPDATE_KEY_PASSWORD
} elseif ($env:TAURI_SIGNING_PRIVATE_KEY) {
    $raw = $env:TAURI_SIGNING_PRIVATE_KEY
    $password = $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD
    if (Test-Path -LiteralPath $raw) {
        $keyPath = $raw
    } else {
        $text = $raw
        if ($text -notmatch "untrusted comment") {
            try {
                $bytes = [Convert]::FromBase64String(($raw -replace '\s', ''))
                $text = [Text.Encoding]::UTF8.GetString($bytes)
            } catch {
                Write-NoSecret
            }
        }
        if ($text -notmatch "untrusted comment") {
            Write-NoSecret
        }
        $tempKey = Join-Path ([IO.Path]::GetTempPath()) ("remedy-minisign-" + [guid]::NewGuid().ToString("n") + ".key")
        $utf8 = New-Object System.Text.UTF8Encoding $false
        [IO.File]::WriteAllText($tempKey, $text, $utf8)
        $keyPath = $tempKey
    }
} else {
    Write-NoSecret
}

$sig = "$File.sig"
$minisign = Get-Command minisign -ErrorAction SilentlyContinue
if (-not $minisign) {
    if ($tempKey) { Remove-Item -LiteralPath $tempKey -Force -ErrorAction SilentlyContinue }
    Write-Error "minisign is not on PATH"
    exit 1
}

try {
    $baseArgs = @("-S", "-s", $keyPath, "-m", $File, "-x", $sig, "-c", "Remedy Desktop update", "-t", ([IO.Path]::GetFileName($File)))
    # The password goes in on stdin, also when it is empty: a key made with an empty
    # password is still an encrypted key, and minisign asks for it. Only a key written
    # with no encryption at all takes -W, which is tried when the first way fails.
    $attempts = @(, $baseArgs)
    if ([string]::IsNullOrEmpty($password)) { $attempts += , (@("-W") + $baseArgs) }
    foreach ($argList in $attempts) {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $minisign.Source
        $psi.UseShellExecute = $false
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $psi.Arguments = ($argList | ForEach-Object {
            if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
        }) -join " "
        $proc = [Diagnostics.Process]::Start($psi)
        if ($argList[0] -ne "-W") {
            $proc.StandardInput.WriteLine([string]$password)
        }
        $proc.StandardInput.Close()
        $stdout = $proc.StandardOutput.ReadToEnd()
        $stderr = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()
        if ($proc.ExitCode -eq 0 -and (Test-Path -LiteralPath $sig)) { break }
    }
    if ($proc.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $sig)) {
        Write-Output "minisign failed (exit $($proc.ExitCode)). The key was not printed."
        if ($stderr) { Write-Output $stderr }
        if ($stdout) { Write-Output $stdout }
        exit 1
    }
    Write-Output "signed $([IO.Path]::GetFileName($File)) -> $([IO.Path]::GetFileName($sig))"
} finally {
    if ($tempKey) { Remove-Item -LiteralPath $tempKey -Force -ErrorAction SilentlyContinue }
}
