$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Stop-Wrapper([string]$Code, [string]$Message) {
    $FailureJson = [ordered]@{
        error = 'platform_unavailable'
        message = "Image PPTGen runtime unavailable: ${Code}: ${Message}"
    } | ConvertTo-Json -Compress
    [Console]::Error.WriteLine($FailureJson)
    exit 3
}

$InstallRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$StatePath = Join-Path $InstallRoot 'state\windows-install-state.json'
if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) {
    Stop-Wrapper 'not_installed' 'active install state is missing'
}
try {
    $State = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $Active = $State.active
    $ReleaseRoot = [IO.Path]::GetFullPath([string]$Active.release_root)
    $VenvRoot = [IO.Path]::GetFullPath([string]$Active.venv_root)
} catch {
    Stop-Wrapper 'state_invalid' 'active install state is unreadable'
}
$ReleasePrefix = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'releases')).TrimEnd('\') + '\'
$VenvPrefix = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'venvs')).TrimEnd('\') + '\'
if (-not $ReleaseRoot.StartsWith($ReleasePrefix, [StringComparison]::OrdinalIgnoreCase) -or
    -not $VenvRoot.StartsWith($VenvPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    Stop-Wrapper 'state_invalid' 'active install paths leave the user install root'
}

$Python = Join-Path $VenvRoot 'Scripts\python.exe'
$RuntimeManager = Join-Path $ReleaseRoot 'app\runtime_manager.py'
$Cli = Join-Path $VenvRoot 'Scripts\image-pptgen.exe'
foreach ($RequiredPath in @($Python, $RuntimeManager, $Cli)) {
    if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
        Stop-Wrapper 'active_install_invalid' "required file is missing: $RequiredPath"
    }
}

$env:IMAGE_PPTGEN_PYTHON = $Python
$env:IMAGE_PPTGEN_DATA_ROOT = $InstallRoot
$env:PPTGEN_DATA_ROOT = $InstallRoot
if ([string]::IsNullOrWhiteSpace($env:IMAGE_PPTGEN_BASE_URL)) {
    $env:IMAGE_PPTGEN_BASE_URL = 'http://127.0.0.1:3130'
}
$EnsureReady = $true
for ($Index = 0; $Index -lt $args.Count; $Index++) {
    $Argument = [string]$args[$Index]
    if ($Argument -eq '-h' -or $Argument -eq '--help') {
        $EnsureReady = $false
    }
    if ($Argument.StartsWith('--base-url=')) {
        $ExplicitUrl = $Argument.Substring('--base-url='.Length).TrimEnd('/')
        if ($ExplicitUrl -ne 'http://127.0.0.1:3130') {
            $EnsureReady = $false
        }
    }
    if ($Argument -eq '--base-url' -and $Index + 1 -lt $args.Count) {
        $ExplicitUrl = ([string]$args[$Index + 1]).TrimEnd('/')
        if ($ExplicitUrl -ne 'http://127.0.0.1:3130') {
            $EnsureReady = $false
        }
    }
}
if ($env:IMAGE_PPTGEN_BASE_URL.TrimEnd('/') -ne 'http://127.0.0.1:3130') {
    $EnsureReady = $false
}
if ($EnsureReady) {
    & $Python $RuntimeManager ensure-ready --json --app-root (Join-Path $ReleaseRoot 'app') --data-root $InstallRoot | Out-Null
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
& $Cli @args
exit $LASTEXITCODE
