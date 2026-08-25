[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [string]$AdbPath = $env:ANDROID_EVERYTHING_ADB
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $ProjectRoot ".venv"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$DistDir = Join-Path $ProjectRoot "dist"
$ExePath = Join-Path $DistDir "AndroidEverything.exe"
$VersionSource = Get-Content -LiteralPath (Join-Path $ProjectRoot "version.py") -Raw
$VersionMatch = [regex]::Match($VersionSource, '__version__\s*=\s*"([^"]+)"')
if (-not $VersionMatch.Success) {
    throw "Could not read __version__ from version.py"
}
$Version = $VersionMatch.Groups[1].Value
$ChecksumPath = Join-Path $DistDir "AndroidEverything-v$Version-SHA256.txt"
$ZipPath = Join-Path $DistDir "AndroidEverything-v$Version-windows.zip"
$ZipChecksumPath = Join-Path $DistDir "AndroidEverything-v$Version-windows-SHA256.txt"
$WindowsVersionFile = Join-Path $ProjectRoot "packaging\windows-version-info.txt"

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

    Remove-Item -LiteralPath $ExePath, $ChecksumPath, $ZipPath, $ZipChecksumPath `
        -Force -ErrorAction SilentlyContinue

    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name AndroidEverything `
        --specpath build `
        --version-file $WindowsVersionFile `
        main.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "Build completed without producing $ExePath"
    }

    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath).Hash.ToLowerInvariant()
    "$Hash  AndroidEverything.exe" | Set-Content -LiteralPath $ChecksumPath -Encoding ascii

    if (-not $AdbPath) {
        $AdbCommand = Get-Command adb.exe -ErrorAction SilentlyContinue
        if ($AdbCommand) {
            $AdbPath = $AdbCommand.Source
        }
    }

    $PackageFiles = @($ExePath, $ChecksumPath)
    if ($AdbPath) {
        $ResolvedAdbPath = (Resolve-Path -LiteralPath $AdbPath).Path
        $AdbSourceDir = Split-Path -Parent $ResolvedAdbPath

        foreach ($Name in @("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll")) {
            $SourcePath = Join-Path $AdbSourceDir $Name
            if (-not (Test-Path -LiteralPath $SourcePath)) {
                throw "Required Android Platform Tools file is missing: $SourcePath"
            }

            $DestinationPath = Join-Path $DistDir $Name
            Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
            $PackageFiles += $DestinationPath
        }

        foreach ($Name in @("NOTICE.txt", "source.properties")) {
            $SourcePath = Join-Path $AdbSourceDir $Name
            if (Test-Path -LiteralPath $SourcePath) {
                $DestinationPath = Join-Path $DistDir $Name
                Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
                $PackageFiles += $DestinationPath
            }
        }
    }
    else {
        Write-Warning "ADB was not found; the Windows ZIP will require a separate ADB installation."
    }

    Compress-Archive -LiteralPath $PackageFiles -DestinationPath $ZipPath -CompressionLevel Optimal
    $ZipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash.ToLowerInvariant()
    "$ZipHash  AndroidEverything-v$Version-windows.zip" | `
        Set-Content -LiteralPath $ZipChecksumPath -Encoding ascii

    Write-Host "Built: $ExePath"
    Write-Host "SHA-256: $Hash"
    Write-Host "Checksum: $ChecksumPath"
    Write-Host "Package: $ZipPath"
    Write-Host "Package SHA-256: $ZipHash"
    Write-Host "Package checksum: $ZipChecksumPath"
}
finally {
    Pop-Location
}
