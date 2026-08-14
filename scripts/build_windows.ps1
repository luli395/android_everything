[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $ProjectRoot ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$DistDir = Join-Path $ProjectRoot "dist"
$ExePath = Join-Path $DistDir "AndroidEverything.exe"
$ChecksumPath = Join-Path $DistDir "AndroidEverything-v0.1.0-SHA256.txt"
$VersionFile = Join-Path $ProjectRoot "packaging\windows-version-info.txt"

Push-Location $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath $Python)) {
        python -m venv $VenvDir
    }

    if (-not $SkipInstall) {
        & $Python -m pip install --disable-pip-version-check --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE" }
        & $Python -m pip install --disable-pip-version-check "pyinstaller==6.15.0"
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed with exit code $LASTEXITCODE" }
    }

    Remove-Item -LiteralPath $ExePath, $ChecksumPath -Force -ErrorAction SilentlyContinue

    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name AndroidEverything `
        --specpath build `
        --version-file $VersionFile `
        main.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "Build completed without producing $ExePath"
    }

    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash.ToLowerInvariant()
    "$Hash  AndroidEverything.exe" | Set-Content -LiteralPath $ChecksumPath -Encoding ascii

    Write-Host "Built: $ExePath"
    Write-Host "SHA-256: $Hash"
    Write-Host "Checksum: $ChecksumPath"
}
finally {
    Pop-Location
}
