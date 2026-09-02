[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Fail-ImagePptgenDispatcher {
    param([Parameter(Mandatory = $true)][string]$Message)

    $payload = @{ error = 'platform_unavailable'; message = $Message } | ConvertTo-Json -Compress
    [Console]::Error.WriteLine($payload)
    exit 3
}

function Test-ImagePptgenAbsolutePath {
    param([AllowEmptyString()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }

    # Windows PowerShell 5.1 lacks the newer .NET absolute-path helper.  The
    # dispatcher contract accepts ordinary drive-qualified Windows paths only;
    # UNC and device paths remain outside this minimal compatibility fix.
    return $Path -match '^[A-Za-z]:\\'
}

if (-not [string]::IsNullOrWhiteSpace($env:IMAGE_PPTGEN_CLI)) {
    $cli = $env:IMAGE_PPTGEN_CLI
    if (-not (Test-ImagePptgenAbsolutePath $cli)) {
        Fail-ImagePptgenDispatcher 'IMAGE_PPTGEN_CLI must be an absolute executable path.'
    }
} elseif (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    $cli = Join-Path $env:LOCALAPPDATA 'ImagePPTGen\bin\image-pptgen.cmd'
} else {
    Fail-ImagePptgenDispatcher 'Image PPTGen CLI is unavailable; install it or set IMAGE_PPTGEN_CLI to an absolute executable path.'
}

if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
    Fail-ImagePptgenDispatcher 'Image PPTGen CLI is unavailable; install it or set IMAGE_PPTGEN_CLI to an absolute executable path.'
}

& $cli @Arguments
exit $LASTEXITCODE
