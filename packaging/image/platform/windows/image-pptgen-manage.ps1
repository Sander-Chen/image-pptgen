[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Doctor', 'Stop', 'Rollback')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Stop-Manage([string]$Code, [string]$Message) {
    $FailureJson = [ordered]@{
        ok = $false
        error = $Code
        message = $Message
        platform = 'windows-amd64'
    } | ConvertTo-Json -Compress
    [Console]::Error.WriteLine($FailureJson)
    exit 3
}

$InstallRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$StatePath = Join-Path $InstallRoot 'state\windows-install-state.json'
$Controller = Join-Path $PSScriptRoot 'windows_installer.py'
if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $Controller -PathType Leaf)) {
    Stop-Manage 'not_installed' 'Image PPTGen management state is missing.'
}
try {
    $State = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $VenvRoot = [IO.Path]::GetFullPath([string]$State.active.venv_root)
} catch {
    Stop-Manage 'state_invalid' 'Image PPTGen management state is invalid.'
}
$VenvPrefix = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'venvs')).TrimEnd('\') + '\'
if (-not $VenvRoot.StartsWith($VenvPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    Stop-Manage 'state_invalid' 'Active Python leaves the user install root.'
}
$Python = Join-Path $VenvRoot 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    Stop-Manage 'active_install_invalid' 'Active Scripts\python.exe is missing.'
}
& $Python $Controller $Action.ToLowerInvariant() --install-root $InstallRoot
exit $LASTEXITCODE
