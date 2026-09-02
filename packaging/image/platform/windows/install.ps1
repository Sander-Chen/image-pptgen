[CmdletBinding()]
param(
    [ValidateSet('Install', 'Doctor', 'Stop', 'Rollback')]
    [string]$Action = 'Install',
    [string]$OfficialPython,
    [string]$FallbackPythonRoot,
    [string]$FallbackAuthorizationFile,
    [string]$RuntimeSelectionReceipt,
    [string]$PayloadZip,
    [string]$PayloadSha256,
    [long]$PayloadSize,
    [string]$Version,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'ImagePPTGen'),
    [string]$SkillRoot = (Join-Path $env:USERPROFILE '.agents\skills')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Stop-Install([string]$Code, [string]$Message) {
    $FailureJson = [ordered]@{
        ok = $false
        error = $Code
        message = $Message
        platform = 'windows-amd64'
    } | ConvertTo-Json -Compress
    [Console]::Error.WriteLine($FailureJson)
    exit 3
}

function Test-UsablePython([string]$Candidate) {
    if ([string]::IsNullOrWhiteSpace($Candidate) -or -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
        return $false
    }
    & $Candidate -I -c "import sys,venv; raise SystemExit(0 if sys.version_info >= (3,11) else 4)" 2>$null
    return $LASTEXITCODE -eq 0
}

function Add-UserPathEntry([string]$Entry) {
    $ResolvedEntry = [IO.Path]::GetFullPath($Entry).TrimEnd('\')
    $UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $Segments = @()
    if (-not [string]::IsNullOrWhiteSpace($UserPath)) {
        $Segments = @($UserPath.Split(';') | ForEach-Object { $_.Trim().TrimEnd('\') })
    }
    foreach ($Segment in $Segments) {
        if ($Segment -eq $ResolvedEntry) {
            return $false
        }
    }
    $NextPath = if ([string]::IsNullOrWhiteSpace($UserPath)) {
        $ResolvedEntry
    } else {
        $UserPath.TrimEnd(';') + ';' + $ResolvedEntry
    }
    [Environment]::SetEnvironmentVariable('Path', $NextPath, 'User')
    return $true
}

$Architecture = if ($env:PROCESSOR_ARCHITEW6432) {
    $env:PROCESSOR_ARCHITEW6432
} else {
    $env:PROCESSOR_ARCHITECTURE
}
if ($Architecture -ne 'AMD64') {
    Stop-Install 'unsupported_platform' 'This installer supports Windows AMD64 only.'
}

if ($Action -ne 'Install') {
    $Manager = Join-Path $InstallRoot 'bin\image-pptgen-manage.ps1'
    if (-not (Test-Path -LiteralPath $Manager -PathType Leaf)) {
        Stop-Install 'not_installed' 'Image PPTGen management entry point is missing.'
    }
    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Manager -Action $Action
    exit $LASTEXITCODE
}

foreach ($RequiredValue in @($PayloadZip, $PayloadSha256, $Version)) {
    if ([string]::IsNullOrWhiteSpace($RequiredValue)) {
        Stop-Install 'install_argument_missing' 'PayloadZip, PayloadSha256, PayloadSize, and Version are required.'
    }
}
if ($PayloadSize -le 0) {
    Stop-Install 'install_argument_missing' 'PayloadSize must be greater than zero.'
}
if ([string]::IsNullOrWhiteSpace($RuntimeSelectionReceipt) -or
    -not (Test-Path -LiteralPath $RuntimeSelectionReceipt -PathType Leaf)) {
    Stop-Install 'runtime_selection_invalid' 'A bounded Runtime selection receipt is required.'
}

# The caller supplies both candidate locations.  Selection is deterministic:
# one valid official Codex Desktop Python wins immediately; fallback is only
# considered inside the one explicitly supplied local directory.
$SelectedPython = $null
$RuntimeSource = $null
if (Test-UsablePython $OfficialPython) {
    $SelectedPython = [IO.Path]::GetFullPath($OfficialPython)
    $RuntimeSource = 'official'
}
if (-not $SelectedPython -and -not [string]::IsNullOrWhiteSpace($FallbackPythonRoot)) {
    if ([string]::IsNullOrWhiteSpace($FallbackAuthorizationFile) -or
        -not (Test-Path -LiteralPath $FallbackAuthorizationFile -PathType Leaf)) {
        Stop-Install 'fallback_not_authorized' 'Fallback requires the bounded official-runtime failure receipt.'
    }
    try {
        $FallbackReceipt = Get-Content -LiteralPath $FallbackAuthorizationFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $Attempts = @($FallbackReceipt.official_attempts)
        $Approaches = @($Attempts | ForEach-Object { [string]$_.approach } | Select-Object -Unique)
        $FailedAttempts = @($Attempts | Where-Object { $_.result -eq 'failed' })
        $ReceiptRuntime = $FallbackReceipt.fallback_runtime
    } catch {
        Stop-Install 'fallback_not_authorized' 'Fallback authorization receipt is invalid.'
    }
    if ($FallbackReceipt.schema_version -ne 1 -or
        $FallbackReceipt.platform -ne 'windows-amd64' -or
        $FallbackReceipt.freeze_id -ne 'pbs-20260718-cp311-plus-cp312-v4' -or
        $FallbackReceipt.decision -ne 'fallback_authorized' -or
        $Attempts.Count -ne 2 -or
        $Approaches.Count -ne 2 -or
        $FailedAttempts.Count -ne 2 -or
        [string]::IsNullOrWhiteSpace($Approaches[0]) -or
        [string]::IsNullOrWhiteSpace($Approaches[1]) -or
        $ReceiptRuntime.archive_sha256 -ne 'a48c2dbe832319f61aa8557c9900caec70f7fed0cbee391a4c9ff9f98b50222d' -or
        $ReceiptRuntime.archive_bytes -ne 25678291) {
        Stop-Install 'fallback_not_authorized' 'Fallback requires two failed, approach-different official-runtime attempts.'
    }
    $FallbackRoot = [IO.Path]::GetFullPath($FallbackPythonRoot)
    $ReceiptRoot = [IO.Path]::GetFullPath([string]$ReceiptRuntime.extracted_root)
    $ExpectedFallbackPython = [IO.Path]::GetFullPath((Join-Path $FallbackRoot 'python.exe'))
    $ReceiptPython = [IO.Path]::GetFullPath([string]$ReceiptRuntime.python_path)
    if ($ReceiptRoot -ne $FallbackRoot -or $ReceiptPython -ne $ExpectedFallbackPython) {
        Stop-Install 'fallback_not_authorized' 'Fallback receipt does not bind the supplied frozen Runtime directory.'
    }
    if (Test-UsablePython $ReceiptPython) {
        $SelectedPython = $ReceiptPython
        $RuntimeSource = 'fallback'
    }
}
if (-not $SelectedPython) {
    Stop-Install 'python_unavailable' 'Neither the supplied official Python nor the supplied local fallback is usable.'
}

$Controller = Join-Path $PSScriptRoot 'windows_installer.py'
if (-not (Test-Path -LiteralPath $Controller -PathType Leaf)) {
    Stop-Install 'platform_tool_missing' 'Windows installer controller is missing.'
}

$ControllerOutput = & $SelectedPython $Controller install `
    --install-root $InstallRoot `
    --skill-root $SkillRoot `
    --payload $PayloadZip `
    --payload-sha256 $PayloadSha256 `
    --payload-size $PayloadSize `
    --version $Version `
    --base-python $SelectedPython `
    --runtime-source $RuntimeSource `
    --runtime-selection-receipt $RuntimeSelectionReceipt `
    --platform-root $PSScriptRoot | Out-String
$ControllerExit = $LASTEXITCODE
if ($ControllerExit -ne 0) {
    if (-not [string]::IsNullOrWhiteSpace($ControllerOutput)) {
        $ControllerOutput.Trim() | Write-Output
    }
    exit $ControllerExit
}
try {
    $InstallResult = $ControllerOutput | ConvertFrom-Json
    $PathUpdated = Add-UserPathEntry (Join-Path $InstallRoot 'bin')
    $InstallResult | Add-Member -NotePropertyName user_path_updated -NotePropertyValue $PathUpdated -Force
    $InstallResult | Add-Member -NotePropertyName desktop_restart_required -NotePropertyValue $true -Force
} catch {
    Stop-Install 'path_update_failed' 'Image PPTGen installed, but its user PATH entry could not be persisted.'
}
$InstallResult | ConvertTo-Json -Compress -Depth 8
exit 0
