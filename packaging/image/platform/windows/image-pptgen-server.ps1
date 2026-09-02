$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Stop-ServerWrapper([string]$Message) {
    $FailureJson = [ordered]@{
        error = 'platform_unavailable'
        message = "Image PPTGen runtime unavailable: $Message"
    } | ConvertTo-Json -Compress
    [Console]::Error.WriteLine($FailureJson)
    exit 3
}

$InstallRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$StatePath = Join-Path $InstallRoot 'state\windows-install-state.json'
if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
    Stop-ServerWrapper 'active install state is missing'
}
try {
    $State = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $ReleaseRoot = [IO.Path]::GetFullPath([string]$State.active.release_root)
    $VenvRoot = [IO.Path]::GetFullPath([string]$State.active.venv_root)
} catch {
    Stop-ServerWrapper 'active install state is unreadable'
}
$ReleasePrefix = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'releases')).TrimEnd('\') + '\'
$VenvPrefix = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'venvs')).TrimEnd('\') + '\'
if (-not $ReleaseRoot.StartsWith($ReleasePrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not $VenvRoot.StartsWith($VenvPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    Stop-ServerWrapper 'active install paths leave the user install root'
}
$Python = Join-Path $VenvRoot 'Scripts\python.exe'
$Launcher = Join-Path $ReleaseRoot 'app\image-launcher.py'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -or
    -not (Test-Path -LiteralPath $Launcher -PathType Leaf)) {
    Stop-ServerWrapper 'active runtime files are missing'
}
$env:IMAGE_PPTGEN_PYTHON = $Python
if ([string]::IsNullOrWhiteSpace($env:PPTGEN_CODEX_INHERIT_USER_CONFIG)) {
    $env:PPTGEN_CODEX_INHERIT_USER_CONFIG = '1'
}
$env:IMAGE_PPTGEN_BASE_URL = 'http://127.0.0.1:3130'
$env:IMAGE_PPTGEN_HOST = '127.0.0.1'
$env:IMAGE_PPTGEN_PORT = '3130'
$env:IMAGE_PPTGEN_DATA_ROOT = $InstallRoot
$env:PPTGEN_BASE_URL = 'http://127.0.0.1:3130'
$env:PPTGEN_DATA_ROOT = $InstallRoot
$env:PPTGEN_INSTANCE_ID_PATH = Join-Path $InstallRoot 'state\runtime-instance.json'
$env:PPTGEN_RELEASE_IDENTITY_PATH = Join-Path $ReleaseRoot 'app\release-identity.json'
$env:PPTGEN_RELEASE_ROOT = $ReleaseRoot
$env:PPTGEN_PUBLIC_DATA_DIR = Join-Path $InstallRoot 'state\data'
$env:PPTGEN_HISTORICAL_DATA_DIR = Join-Path $InstallRoot 'state\data\historical-data'
$env:PPTGEN_IMAGE_RUNTIME_MODE = 'installed'
$env:PPT_DB_PATH = Join-Path $InstallRoot 'state\data\ppt.db'
$env:PPT_ARTIFACTS_DIR = Join-Path $InstallRoot 'state\data\artifacts'
$env:PPTGEN_HOST = '127.0.0.1'
$env:PPTGEN_PORT = '3130'
$env:PORT = '3130'
& $Python $Launcher @args
exit $LASTEXITCODE
