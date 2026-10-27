param(
    [ValidateSet("auto", "sglang", "transformers")]
    [string]$Backend = "auto",
    [string]$EnvFile = "",
    [int]$GpuId = -1,
    [int]$TimeoutSeconds = 1800,
    [switch]$DryRun,
    [switch]$SkipPull,
    [switch]$SkipBuild,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$options = @{
    Model = "unlimited-ocr"
    UnlimitedOcrBackend = $(if ($Backend -eq 'auto') { '' } else { $Backend })
    EnvFile = $EnvFile
    GpuId = $GpuId
    TimeoutSeconds = $TimeoutSeconds
    DryRun = $DryRun
    SkipPull = $SkipPull
    SkipBuild = $SkipBuild
    SkipClean = $true
    ReuseManagedGpu = $true
    AutoUnlimitedOcrHardware = $true
    NoOpen = $NoOpen
}

# Reuse model discovery, persistent credentials, GPU release checks and startup
# readiness from the existing launcher. Other models keep their original entry.
& (Join-Path $PSScriptRoot "windows-one-click.ps1") @options
exit $LASTEXITCODE
