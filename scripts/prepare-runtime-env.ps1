[CmdletBinding()]
param(
    [string]$BaseEnvFile,
    [string]$RuntimeEnvFile
)

$ErrorActionPreference = 'Stop'
$TokenKey = 'PANDOCR_MODEL_CONTROLLER_TOKEN'
$ChinesePlaceholder = -join (@(0x8BF7, 0x66FF, 0x6362, 0x4E3A, 0x968F, 0x673A, 0x957F, 0x503C) | ForEach-Object { [char]$_ })
$UnsafeTokens = @(
    'pandocr-internal-controller-v1',
    'change-this-to-a-random-long-value',
    $ChinesePlaceholder
)

function Resolve-AbsolutePath {
    param([Parameter(Mandatory = $true)][string]$PathValue)
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Assert-SafeControllerToken {
    param(
        [AllowEmptyString()][string]$Token,
        [Parameter(Mandatory = $true)][string]$Source
    )
    if ([string]::IsNullOrWhiteSpace($Token)) {
        throw "$Source contains an empty $TokenKey."
    }
    if ($UnsafeTokens -contains $Token.Trim()) {
        throw "$Source contains an unsafe placeholder $TokenKey."
    }
    if ($Token -cne $Token.Trim()) {
        throw "$Source contains leading or trailing whitespace in $TokenKey."
    }
    if ($Token -notmatch '^[A-Za-z0-9._~-]{32,256}$') {
        throw "$Source must use 32-256 URL-safe ASCII characters for $TokenKey."
    }
    if ($Token.Contains("`r") -or $Token.Contains("`n")) {
        throw "$Source contains an invalid multi-line $TokenKey."
    }
}

try {
    $ProjectDir = Split-Path -Parent $PSScriptRoot
    if ([string]::IsNullOrWhiteSpace($BaseEnvFile)) {
        $BaseEnvFile = Join-Path $ProjectDir 'env.txt'
    }
    if ([string]::IsNullOrWhiteSpace($RuntimeEnvFile)) {
        $RuntimeEnvFile = Join-Path $ProjectDir 'tmp\pandocr-runtime.env'
    }
    $BaseEnvFile = Resolve-AbsolutePath $BaseEnvFile
    $RuntimeEnvFile = Resolve-AbsolutePath $RuntimeEnvFile

    if (-not (Test-Path -LiteralPath $BaseEnvFile -PathType Leaf)) {
        throw "base env file not found: $BaseEnvFile"
    }

    $RuntimeToken = $null
    if (Test-Path -LiteralPath $RuntimeEnvFile) {
        if (-not (Test-Path -LiteralPath $RuntimeEnvFile -PathType Leaf)) {
            throw "runtime env path must be a regular file: $RuntimeEnvFile"
        }
        $TokenLines = @(Get-Content -LiteralPath $RuntimeEnvFile -Encoding UTF8 | Where-Object {
            $_.StartsWith("$TokenKey=", [System.StringComparison]::Ordinal)
        })
        if ($TokenLines.Count -ne 1) {
            throw "$RuntimeEnvFile must contain exactly one $TokenKey entry."
        }
        $RuntimeToken = $TokenLines[0].Substring($TokenKey.Length + 1)
        Assert-SafeControllerToken -Token $RuntimeToken -Source $RuntimeEnvFile
    }

    $ProcessToken = [Environment]::GetEnvironmentVariable($TokenKey, 'Process')
    if ($null -ne $ProcessToken) {
        Assert-SafeControllerToken -Token $ProcessToken -Source 'process environment'
        if ($null -ne $RuntimeToken -and $ProcessToken -cne $RuntimeToken) {
            throw "process $TokenKey differs from the persisted runtime token; remove it or restore the persisted value."
        }
    }

    if ($null -eq $RuntimeToken) {
        if ($null -ne $ProcessToken) {
            $RuntimeToken = $ProcessToken
        } else {
            $RuntimeToken = [Guid]::NewGuid().ToString('N') + [Guid]::NewGuid().ToString('N')
        }
        Assert-SafeControllerToken -Token $RuntimeToken -Source 'generated token'

        $RuntimeDir = Split-Path -Parent $RuntimeEnvFile
        [System.IO.Directory]::CreateDirectory($RuntimeDir) | Out-Null
        $TempFile = Join-Path $RuntimeDir ('.pandocr-runtime.' + [Guid]::NewGuid().ToString('N') + '.tmp')
        try {
            $Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
            [System.IO.File]::WriteAllText($TempFile, "$TokenKey=$RuntimeToken`n", $Utf8NoBom)
            Move-Item -LiteralPath $TempFile -Destination $RuntimeEnvFile -Force
        } finally {
            if (Test-Path -LiteralPath $TempFile) {
                Remove-Item -LiteralPath $TempFile -Force
            }
        }
    }

    Write-Output $RuntimeEnvFile
} catch {
    [Console]::Error.WriteLine("ERROR: {0}" -f $_.Exception.Message)
    exit 1
}
