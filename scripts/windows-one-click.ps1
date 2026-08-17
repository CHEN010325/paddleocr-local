param(
    [string]$EnvFile = "",
    [int]$GpuId = -1,
    [int]$TimeoutSeconds = 1800,
    [string]$Model = "",
    [string]$Models = "",
    [string]$ActiveModel = "",
    [Alias("Backend")]
    [string]$UnlimitedOcrBackend = "",
    [switch]$DryRun,
    [switch]$SkipPull,
    [switch]$SkipBuild,
    [switch]$SkipClean,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:RequestedModel = $Model
$script:RequestedModels = $Models
$script:RequestedActiveModel = $ActiveModel
$script:RequestedUnlimitedOcrBackend = $UnlimitedOcrBackend
$script:RuntimeEnv = ""
$script:DiagnosticsShown = $false
$script:ActiveModel = "paddleocr-vl-1.6"
$script:EnableUnlimitedOcr = $false
$script:EnableOvisOcr2 = $false
$script:EnableHpdParsing = $false
$script:UnlimitedOcrBackend = "transformers"
$script:UnlimitedOcrBackendExplicit = $false
$script:DeployModelIds = @("paddleocr-vl-1.6")
$script:ModelCatalogIds = @("paddleocr-vl-1.6", "pp-ocrv6", "unlimited-ocr", "ovisocr2", "hpd-parsing")
Set-Location $script:RepoRoot

function Write-Section {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Invoke-Checked {
    param(
        [string]$File,
        [string[]]$Arguments,
        [string]$Description
    )

    Write-Section $Description
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Get-RequiredCommand {
    param([string]$Name, [string]$InstallHint)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found. $InstallHint"
    }
}

function Get-GpuList {
    $args = @(
        "--query-gpu=index,name,compute_cap,memory.total,memory.free",
        "--format=csv,noheader,nounits"
    )
    $output = & nvidia-smi @args
    if ($LASTEXITCODE -ne 0) {
        $args = @(
            "--query-gpu=index,name,memory.total,memory.free",
            "--format=csv,noheader,nounits"
        )
        $output = & nvidia-smi @args
        if ($LASTEXITCODE -ne 0) {
            throw "nvidia-smi failed. Please install/update the NVIDIA driver first."
        }
    }

    $gpus = @()
    foreach ($line in $output) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        $parts = $line -split ","
        if ($parts.Count -lt 4) {
            throw "Unexpected nvidia-smi output: $line"
        }

        $hasComputeCapability = $parts.Count -ge 5
        $computeCapability = $null
        $memoryOffset = 2
        if ($hasComputeCapability) {
            $computeText = $parts[2].Trim()
            if (-not [string]::IsNullOrWhiteSpace($computeText) -and $computeText -ne "[N/A]") {
                $computeCapability = [double]$computeText
            }
            $memoryOffset = 3
        }

        $gpus += [pscustomobject]@{
            Index = [int]($parts[0].Trim())
            Name = $parts[1].Trim()
            ComputeCapability = $computeCapability
            TotalMiB = [int]([double]($parts[$memoryOffset].Trim()))
            FreeMiB = [int]([double]($parts[$memoryOffset + 1].Trim()))
        }
    }

    if ($gpus.Count -eq 0) {
        throw "No NVIDIA GPU was detected by nvidia-smi."
    }

    return @($gpus)
}

function Test-IsBlackwellGpu {
    param([string]$Name)

    $normalized = $Name.ToLowerInvariant()
    return ($normalized -match "blackwell" -or $normalized -match "rtx\s+50(50|60|70|80|90)\b")
}

function Select-Gpu {
    param([object[]]$Gpus, [int]$RequestedGpuId)

    Write-Section "Detected NVIDIA GPUs"
    foreach ($gpu in $Gpus) {
        $compute = Format-ComputeCapability $gpu.ComputeCapability
        Write-Host ("GPU {0}: {1} | {2} | total={3} MiB free={4} MiB" -f $gpu.Index, $gpu.Name, $compute, $gpu.TotalMiB, $gpu.FreeMiB)
    }

    if ($RequestedGpuId -ge 0) {
        $requested = @($Gpus | Where-Object { $_.Index -eq $RequestedGpuId })
        if ($requested.Count -eq 0) {
            throw "Requested GPU $RequestedGpuId was not found."
        }
        return $requested[0]
    }

    return @($Gpus | Sort-Object -Property FreeMiB -Descending)[0]
}

function Format-ComputeCapability {
    param($ComputeCapability)

    if ($null -eq $ComputeCapability) {
        return "sm=unknown"
    }

    $capability = [double]$ComputeCapability
    $major = [int][Math]::Floor($capability)
    $minor = [int][Math]::Round(($capability - $major) * 10)
    return "sm$major$minor"
}

function Test-GpuSupportsSglang {
    param([object]$Gpu)

    if ($null -eq $Gpu.ComputeCapability) {
        Write-Warn "Could not detect GPU compute capability. SGLang requires sm75 or newer; deployment will continue and may fail if the GPU is older."
        return
    }

    if ([double]$Gpu.ComputeCapability -lt 7.5) {
        $compute = Format-ComputeCapability $Gpu.ComputeCapability
        throw "Unlimited-OCR SGLang requires NVIDIA compute capability sm75 or newer. GPU $($Gpu.Index) ($($Gpu.Name)) is $compute. Use -UnlimitedOcrBackend transformers on this GPU."
    }
}

function Resolve-BaseEnvFile {
    param([object]$Gpu, [string]$RequestedEnvFile)

    if (-not [string]::IsNullOrWhiteSpace($RequestedEnvFile)) {
        if ([System.IO.Path]::IsPathRooted($RequestedEnvFile)) {
            $path = $RequestedEnvFile
        }
        else {
            $path = Join-Path $script:RepoRoot $RequestedEnvFile
        }
        if (-not (Test-Path $path)) {
            throw "Env file not found: $RequestedEnvFile"
        }
        return (Resolve-Path $path).Path
    }

    if (Test-IsBlackwellGpu $Gpu.Name) {
        return (Resolve-Path (Join-Path $script:RepoRoot "env.txt")).Path
    }

    return (Resolve-Path (Join-Path $script:RepoRoot "env.docker")).Path
}

function Set-EnvLine {
    param(
        [string[]]$Lines,
        [string]$Key,
        [string]$Value
    )

    $updated = New-Object System.Collections.Generic.List[string]
    $found = $false
    $pattern = "^\s*" + [regex]::Escape($Key) + "\s*="

    foreach ($line in $Lines) {
        if ($line -match $pattern) {
            $updated.Add("$Key=$Value")
            $found = $true
        }
        else {
            $updated.Add($line)
        }
    }

    if (-not $found) {
        $updated.Add("$Key=$Value")
    }

    return [string[]]$updated.ToArray()
}

function Ensure-EnvLine {
    param(
        [string[]]$Lines,
        [string]$Key,
        [string]$Value
    )

    $pattern = "^\s*" + [regex]::Escape($Key) + "\s*="
    foreach ($line in $Lines) {
        if ($line -match $pattern) {
            return $Lines
        }
    }

    return [string[]]($Lines + "$Key=$Value")
}

function Get-EnvLineValue {
    param(
        [string[]]$Lines,
        [string]$Key,
        [string]$DefaultValue
    )

    $pattern = "^\s*" + [regex]::Escape($Key) + "\s*=\s*(.*)\s*$"
    foreach ($line in $Lines) {
        if ($line -match $pattern) {
            return $Matches[1].Trim()
        }
    }

    return $DefaultValue
}

function Test-EnabledValue {
    param([string]$Value)
    $normalized = $Value.Trim().ToLowerInvariant()
    return ($normalized -in @("1", "true", "yes", "on"))
}

function Normalize-UnlimitedOcrBackend {
    param([string]$Value)
    $normalized = $Value.Trim().ToLowerInvariant()
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return "transformers"
    }
    if ($normalized -in @("transformers", "sglang")) {
        return $normalized
    }
    throw "Unsupported Unlimited-OCR backend '$Value'. Use transformers or sglang."
}

function Add-DeploymentModel {
    param(
        [System.Collections.Generic.List[string]]$Models,
        [string]$ModelId
    )
    if (-not $Models.Contains($ModelId)) {
        $Models.Add($ModelId)
    }
}

function Resolve-ModelId {
    param([string]$Value)

    $normalized = $Value.Trim().ToLowerInvariant()
    switch ($normalized) {
        { $_ -in @("1", "vl", "paddleocr-vl", "paddleocr-vl-1.6", "paddleocrvl") } { return "paddleocr-vl-1.6" }
        { $_ -in @("2", "ppocr", "ppocrv6", "pp-ocrv6", "ocr") } { return "pp-ocrv6" }
        { $_ -in @("3", "unlimited", "unlimited-ocr", "uow") } { return "unlimited-ocr" }
        { $_ -in @("4", "ovis", "ovisocr", "ovisocr2", "ovis-ocr2") } { return "ovisocr2" }
        { $_ -in @("5", "hpd", "hpd-parsing", "hpdparsing") } { return "hpd-parsing" }
        default { throw "Unknown model '$Value'. Use paddleocr-vl-1.6, pp-ocrv6, unlimited-ocr, ovisocr2, or hpd-parsing." }
    }
}

function Get-ModelDisplayName {
    param([string]$ModelId)
    switch ($ModelId) {
        "paddleocr-vl-1.6" { return "PaddleOCR-VL 1.6" }
        "pp-ocrv6" { return "PP-OCRv6" }
        "unlimited-ocr" { return "Unlimited-OCR" }
        "ovisocr2" { return "OvisOCR2" }
        "hpd-parsing" { return "HPD-Parsing" }
        default { return $ModelId }
    }
}

function Get-ModelGpuRequirement {
    param([string]$ModelId)

    switch ($ModelId) {
        "paddleocr-vl-1.6" { return [pscustomobject]@{ TotalMiB = 11264; FreeMiB = 6656 } }
        "pp-ocrv6" { return [pscustomobject]@{ TotalMiB = 4096; FreeMiB = 4096 } }
        "unlimited-ocr" { return [pscustomobject]@{ TotalMiB = 7680; FreeMiB = 6656 } }
        "ovisocr2" { return [pscustomobject]@{ TotalMiB = 7680; FreeMiB = 6656 } }
        "hpd-parsing" { return [pscustomobject]@{ TotalMiB = 7680; FreeMiB = 6656 } }
        default { throw "No GPU requirement is defined for model '$ModelId'." }
    }
}

function Get-DeploymentGpuRequirement {
    param([string[]]$ModelIds)

    if (-not $ModelIds -or $ModelIds.Count -eq 0) {
        throw "At least one deployment model is required for GPU validation."
    }

    $requirements = @($ModelIds | ForEach-Object { Get-ModelGpuRequirement $_ })
    return [pscustomobject]@{
        TotalMiB = [int](($requirements | Measure-Object -Property TotalMiB -Maximum).Maximum)
        FreeMiB = [int](($requirements | Measure-Object -Property FreeMiB -Maximum).Maximum)
    }
}

function Read-FriendlyDeploymentSelection {
    Write-Section "Choose the model to start first"
    Write-Host "Only this model is downloaded and started by default."
    Write-Host ""
    Write-Host "  1) PaddleOCR-VL 1.6   Full document parsing (recommended default)"
    Write-Host "  2) PP-OCRv6           Fast text OCR"
    Write-Host "  3) Unlimited-OCR      Long-document parsing"
    Write-Host "  4) OvisOCR2           Document parsing with vLLM"
    Write-Host "  5) HPD-Parsing        High-throughput hierarchical document parsing"
    Write-Host ""

    $answer = Read-Host "Select a model [1]"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        $answer = "1"
    }
    $initialModel = Resolve-ModelId $answer
    $selected = New-Object System.Collections.Generic.List[string]
    Add-DeploymentModel -Models $selected -ModelId $initialModel
    Write-Ok "First model: $(Get-ModelDisplayName $initialModel)"

    Write-Host ""
    Write-Host "Optional: prepare other models now (they will remain stopped)."
    Write-Host "Enter model numbers separated by commas, or press Enter to skip."
    $additional = Read-Host "Additional models"
    if (-not [string]::IsNullOrWhiteSpace($additional)) {
        $tokens = @($additional -split "[,\s;]+" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        foreach ($token in $tokens) {
            if ($token.Trim().ToLowerInvariant() -eq "all") {
                foreach ($modelId in $script:ModelCatalogIds) {
                    Add-DeploymentModel -Models $selected -ModelId $modelId
                }
                continue
            }
            Add-DeploymentModel -Models $selected -ModelId (Resolve-ModelId $token)
        }
    }

    return [pscustomobject]@{
        ModelIds = [string[]]$selected.ToArray()
        InitialModel = $initialModel
        UnlimitedOcrBackend = "transformers"
        UnlimitedOcrBackendExplicit = $false
    }
}

function Resolve-DeploymentSelection {
    param(
        [string]$RequestedModels,
        [string]$RequestedBackend
    )

    if ([string]::IsNullOrWhiteSpace($RequestedModels)) {
        throw "No models were provided to the advanced -Models option."
    }

    $rawSelection = $RequestedModels
    $backendExplicit = -not [string]::IsNullOrWhiteSpace($RequestedBackend)
    $backend = Normalize-UnlimitedOcrBackend $RequestedBackend
    $selected = New-Object System.Collections.Generic.List[string]
    $tokens = @($rawSelection -split "[,\s;]+" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })

    foreach ($token in $tokens) {
        $normalized = $token.Trim().ToLowerInvariant()
        switch ($normalized) {
            { $_ -in @("1", "vl", "paddleocr-vl", "paddleocr-vl-1.6", "paddleocrvl") } {
                Add-DeploymentModel -Models $selected -ModelId "paddleocr-vl-1.6"
                continue
            }
            { $_ -in @("2", "ppocr", "ppocrv6", "pp-ocrv6", "ocr") } {
                Add-DeploymentModel -Models $selected -ModelId "pp-ocrv6"
                continue
            }
            { $_ -in @("3", "unlimited", "unlimited-ocr", "uow", "unlimited-ocr-transformers") } {
                Add-DeploymentModel -Models $selected -ModelId "unlimited-ocr"
                if (-not $backendExplicit) {
                    $backend = "transformers"
                }
                continue
            }
            { $_ -in @("4", "sglang", "unlimited-sglang", "unlimited-ocr-sglang") } {
                Add-DeploymentModel -Models $selected -ModelId "unlimited-ocr"
                $backend = "sglang"
                continue
            }
            { $_ -in @("5", "core", "paddleocr", "paddleocr-core") } {
                Add-DeploymentModel -Models $selected -ModelId "paddleocr-vl-1.6"
                Add-DeploymentModel -Models $selected -ModelId "pp-ocrv6"
                continue
            }
            { $_ -in @("6", "three", "first-three") } {
                Add-DeploymentModel -Models $selected -ModelId "paddleocr-vl-1.6"
                Add-DeploymentModel -Models $selected -ModelId "pp-ocrv6"
                Add-DeploymentModel -Models $selected -ModelId "unlimited-ocr"
                if (-not $backendExplicit) {
                    $backend = "transformers"
                }
                continue
            }
            { $_ -in @("7", "ovis", "ovisocr", "ovisocr2", "ovis-ocr2") } {
                Add-DeploymentModel -Models $selected -ModelId "ovisocr2"
                continue
            }
            { $_ -in @("8", "all", "four", "full") } {
                Add-DeploymentModel -Models $selected -ModelId "paddleocr-vl-1.6"
                Add-DeploymentModel -Models $selected -ModelId "pp-ocrv6"
                Add-DeploymentModel -Models $selected -ModelId "unlimited-ocr"
                Add-DeploymentModel -Models $selected -ModelId "ovisocr2"
                if (-not $backendExplicit) {
                    $backend = "transformers"
                }
                continue
            }
            { $_ -in @("9", "all-sglang", "full-sglang") } {
                Add-DeploymentModel -Models $selected -ModelId "paddleocr-vl-1.6"
                Add-DeploymentModel -Models $selected -ModelId "pp-ocrv6"
                Add-DeploymentModel -Models $selected -ModelId "unlimited-ocr"
                Add-DeploymentModel -Models $selected -ModelId "ovisocr2"
                $backend = "sglang"
                continue
            }
            { $_ -in @("10", "hpd", "hpd-parsing", "hpdparsing") } {
                Add-DeploymentModel -Models $selected -ModelId "hpd-parsing"
                continue
            }
            { $_ -in @("11", "all-five", "full-five") } {
                foreach ($modelId in $script:ModelCatalogIds) {
                    Add-DeploymentModel -Models $selected -ModelId $modelId
                }
                if (-not $backendExplicit) {
                    $backend = "transformers"
                }
                continue
            }
            default {
                throw "Unknown model selection '$token'. Use 1-11 or model ids such as paddleocr-vl-1.6, pp-ocrv6, unlimited-ocr, ovisocr2, hpd-parsing."
            }
        }
    }

    if ($selected.Count -eq 0) {
        Add-DeploymentModel -Models $selected -ModelId "paddleocr-vl-1.6"
    }

    return [pscustomobject]@{
        ModelIds = [string[]]$selected.ToArray()
        UnlimitedOcrBackend = $backend
        UnlimitedOcrBackendExplicit = $backendExplicit
    }
}

function Resolve-SingleDeploymentSelection {
    param(
        [string]$RequestedModel,
        [string]$RequestedBackend
    )

    $modelId = Resolve-ModelId $RequestedModel
    return [pscustomobject]@{
        ModelIds = [string[]]@($modelId)
        InitialModel = $modelId
        UnlimitedOcrBackend = Normalize-UnlimitedOcrBackend $RequestedBackend
        UnlimitedOcrBackendExplicit = -not [string]::IsNullOrWhiteSpace($RequestedBackend)
    }
}

function Read-UnlimitedOcrBackendSelection {
    param([object]$Gpu)

    $recommended = if (Test-IsBlackwellGpu $Gpu.Name) { "SGLang" } else { "Transformers" }
    Write-Section "Choose the Unlimited-OCR backend"
    Write-Host "  1) Auto          $recommended is recommended for this GPU"
    Write-Host "  2) Transformers  Simpler runtime"
    Write-Host "  3) SGLang        CUDA optimized, requires sm75 or newer"
    Write-Host ""
    $answer = Read-Host "Select a backend [1]"
    if ([string]::IsNullOrWhiteSpace($answer) -or $answer -eq "1") {
        return
    }
    if ($answer -eq "2") {
        $script:UnlimitedOcrBackend = "transformers"
        $script:UnlimitedOcrBackendExplicit = $true
        return
    }
    if ($answer -eq "3") {
        $script:UnlimitedOcrBackend = "sglang"
        $script:UnlimitedOcrBackendExplicit = $true
        return
    }
    throw "Unknown backend selection '$answer'. Use 1, 2, or 3."
}

function Confirm-DeploymentPlan {
    Write-Section "Review deployment"
    Write-Host "GPU:                 $($script:SelectedGpu.Index) - $($script:SelectedGpu.Name)"
    Write-Host "Starts first:        $(Get-ModelDisplayName $script:ActiveModel)"
    Write-Host "Models prepared now: $((@($script:DeployModelIds | ForEach-Object { Get-ModelDisplayName $_ })) -join ', ')"
    if ($script:EnableUnlimitedOcr) {
        Write-Host "Unlimited backend:   $script:UnlimitedOcrBackend"
    }
    Write-Host "Other models remain visible in the WebUI and can be deployed later."
    Write-Host ""
    $answer = Read-Host "Start downloading and deploying? [Y/n]"
    return ([string]::IsNullOrWhiteSpace($answer) -or $answer.Trim().ToLowerInvariant() -in @("y", "yes"))
}

function Resolve-ActiveModel {
    param(
        [string]$RequestedActiveModel,
        [string[]]$SelectedModels
    )

    if ([string]::IsNullOrWhiteSpace($RequestedActiveModel)) {
        return $SelectedModels[0]
    }

    $resolved = Resolve-ModelId $RequestedActiveModel

    if ($SelectedModels -notcontains $resolved) {
        throw "Active model '$resolved' is not included in the deployment selection."
    }
    return $resolved
}

function Apply-GpuSpecificBackendDefaults {
    param([object]$Gpu)

    if (-not $script:EnableUnlimitedOcr) {
        return
    }

    if (-not (Test-IsBlackwellGpu $Gpu.Name)) {
        return
    }

    if (-not $script:UnlimitedOcrBackendExplicit -and $script:UnlimitedOcrBackend -eq "transformers") {
        $script:UnlimitedOcrBackend = "sglang"
        Write-Warn "RTX 50 / Blackwell GPU detected. Using Unlimited-OCR SGLang by default because the current Transformers CUDA 12.6 wheel does not execute on sm120 GPUs."
        return
    }

    if ($script:UnlimitedOcrBackend -eq "transformers") {
        Write-Warn "RTX 50 / Blackwell GPU detected. Unlimited-OCR Transformers was explicitly selected, but the current CUDA 12.6 PyTorch wheel may load and then fail during inference on sm120 GPUs. Use -UnlimitedOcrBackend sglang if that happens."
    }
}

function Get-DeployedModelServices {
    $services = New-Object System.Collections.Generic.List[string]
    if ($script:DeployModelIds -contains "paddleocr-vl-1.6") {
        $services.Add("paddleocr-vlm-server")
        $services.Add("paddleocr-vl-api")
    }
    if ($script:DeployModelIds -contains "pp-ocrv6") {
        $services.Add("paddleocr-ocr-api")
    }
    foreach ($service in (Get-UnlimitedOcrServices)) {
        if (-not $services.Contains($service)) {
            $services.Add($service)
        }
    }
    if ($script:EnableOvisOcr2) {
        $services.Add("ovisocr2-api")
    }
    if ($script:EnableHpdParsing) {
        $services.Add("hpd-parsing-server")
        $services.Add("hpd-parsing-api")
    }
    return [string[]]$services.ToArray()
}

function Get-DeploymentServiceList {
    $services = New-Object System.Collections.Generic.List[string]
    $services.Add("pandocr-controller")
    $services.Add("pandocr-office-converter")
    $services.Add("pandocr-web")
    foreach ($service in (Get-DeployedModelServices)) {
        if (-not $services.Contains($service)) {
            $services.Add($service)
        }
    }
    return [string[]]$services.ToArray()
}

function Get-GpuCheckService {
    if ($script:DeployModelIds -contains "paddleocr-vl-1.6") {
        return "paddleocr-vlm-server"
    }
    if ($script:DeployModelIds -contains "pp-ocrv6") {
        return "paddleocr-ocr-api"
    }
    if ($script:DeployModelIds -contains "unlimited-ocr") {
        return "unlimited-ocr-api"
    }
    if ($script:EnableOvisOcr2) {
        return "ovisocr2-api"
    }
    if ($script:EnableHpdParsing) {
        return "hpd-parsing-server"
    }
    return "pandocr-web"
}

function Get-UnlimitedOcrServices {
    if (-not $script:EnableUnlimitedOcr) {
        return @()
    }
    if ($script:UnlimitedOcrBackend -eq "sglang") {
        return @("unlimited-ocr-sglang", "unlimited-ocr-api")
    }
    return @("unlimited-ocr-api")
}

function New-RuntimeEnvFile {
    param([string]$BaseEnvFile, [object]$Gpu)

    $tmpDir = Join-Path $script:RepoRoot "tmp"
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

    $runtimeEnv = Join-Path $tmpDir "windows-one-click.env"
    $controllerRuntimeEnv = Join-Path $tmpDir "pandocr-runtime.env"
    $controllerRuntimeScript = Join-Path $script:RepoRoot "scripts\prepare-runtime-env.ps1"
    $null = & $controllerRuntimeScript -BaseEnvFile $BaseEnvFile -RuntimeEnvFile $controllerRuntimeEnv
    $controllerRuntimePrepared = $?
    if (-not $controllerRuntimePrepared -or -not (Test-Path -LiteralPath $controllerRuntimeEnv -PathType Leaf)) {
        throw "Failed to prepare the persistent model-controller credential."
    }
    $controllerLines = [string[]](Get-Content -LiteralPath $controllerRuntimeEnv -Encoding UTF8)
    $controllerToken = Get-EnvLineValue -Lines $controllerLines -Key "PANDOCR_MODEL_CONTROLLER_TOKEN" -DefaultValue ""
    if ([string]::IsNullOrWhiteSpace($controllerToken)) {
        throw "Persistent model-controller credential is empty."
    }

    $lines = [string[]](Get-Content -Path $BaseEnvFile -Encoding UTF8)
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_GPU_DEVICE_ID" -Value ([string]$Gpu.Index)
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_APP_VERSION" -Value "0.2.0"
    $gitCommit = ""
    try {
        $gitCommitOutput = & git -C $script:RepoRoot rev-parse --short HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($gitCommitOutput)) {
            $gitCommit = ([string]$gitCommitOutput).Trim()
        }
    }
    catch {
        $gitCommit = ""
    }
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_GIT_COMMIT" -Value $gitCommit
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_MODEL_CONTROL" -Value "docker"
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_MODEL_CONTROLLER_TOKEN" -Value $controllerToken
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_ACTIVE_MODEL_ON_START" -Value $script:ActiveModel
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_MODEL_CATALOG" -Value ($script:ModelCatalogIds -join ",")
    $lines = Ensure-EnvLine -Lines $lines -Key "UNLIMITED_OCR_MODEL_NAME" -Value "baidu/Unlimited-OCR"
    $lines = Set-EnvLine -Lines $lines -Key "UNLIMITED_OCR_BACKEND" -Value $script:UnlimitedOcrBackend
    $lines = Ensure-EnvLine -Lines $lines -Key "UNLIMITED_OCR_PRELOAD" -Value "1"
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_ENABLE_UNLIMITED_OCR" -Value "1"
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_ENABLE_OVISOCR2" -Value "1"
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_ENABLE_HPD_PARSING" -Value "1"
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_MODEL_SWITCH_TIMEOUT" -Value "1200"
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_MAX_UPLOAD_MB" -Value "512"
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_MAX_CONCURRENT_OCR" -Value "1"
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_ENFORCE_ORIGIN_CHECK" -Value "1"
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_API_TOKEN" -Value ""
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_ENABLE_API_DOCS" -Value "0"
    $script:ActiveModel = Get-EnvLineValue -Lines $lines -Key "PANDOCR_ACTIVE_MODEL_ON_START" -DefaultValue $script:ActiveModel
    $script:EnableUnlimitedOcr = $script:DeployModelIds -contains "unlimited-ocr"
    $script:EnableOvisOcr2 = $script:DeployModelIds -contains "ovisocr2"
    $script:EnableHpdParsing = $script:DeployModelIds -contains "hpd-parsing"
    $script:UnlimitedOcrBackend = (Get-EnvLineValue -Lines $lines -Key "UNLIMITED_OCR_BACKEND" -DefaultValue "transformers").Trim().ToLowerInvariant()
    Set-Content -Path $runtimeEnv -Value $lines -Encoding ASCII

    return (Resolve-Path $runtimeEnv).Path
}

function Set-UnlimitedOcrRuntimeSetting {
    if (-not $script:EnableUnlimitedOcr) {
        return
    }

    $dataDir = Join-Path $script:RepoRoot "data"
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $settingsPath = Join-Path $dataDir "runtime-settings.json"
    $settings = @{}

    if (Test-Path $settingsPath) {
        try {
            $raw = Get-Content -Path $settingsPath -Raw -Encoding UTF8
            if (-not [string]::IsNullOrWhiteSpace($raw)) {
                $parsed = $raw | ConvertFrom-Json
                foreach ($property in $parsed.PSObject.Properties) {
                    $settings[$property.Name] = $property.Value
                }
            }
        }
        catch {
            Write-Warn "Could not read existing runtime settings. Rewriting $settingsPath."
        }
    }

    $settings["unlimitedOcrBackend"] = $script:UnlimitedOcrBackend
    $settings | ConvertTo-Json -Depth 6 | Set-Content -Path $settingsPath -Encoding UTF8
    Write-Ok "Persisted Unlimited-OCR backend: $script:UnlimitedOcrBackend"
}

function Get-ComposeArgs {
    param(
        [string[]]$Arguments,
        [switch]$IncludeOptionalProfiles
    )
    $args = @("compose", "--env-file", $script:RuntimeEnv)
    if ($script:DeployModelIds -contains "paddleocr-vl-1.6" -or $IncludeOptionalProfiles) {
        $args += @("--profile", "paddleocr-vl")
    }
    if ($script:DeployModelIds -contains "pp-ocrv6" -or $IncludeOptionalProfiles) {
        $args += @("--profile", "pp-ocrv6")
    }
    if ($script:EnableUnlimitedOcr -or $IncludeOptionalProfiles) {
        $args += @("--profile", "unlimited-ocr")
    }
    if ($script:EnableOvisOcr2 -or $IncludeOptionalProfiles) {
        $args += @("--profile", "ovisocr2")
    }
    if ($script:EnableHpdParsing -or $IncludeOptionalProfiles) {
        $args += @("--profile", "hpd-parsing")
    }
    return $args + $Arguments
}

function Test-HttpOk {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300)
    }
    catch {
        return $false
    }
}

function Get-ModelRuntimePayload {
    try {
        return Invoke-RestMethod -Uri "http://localhost:8000/api/model-runtime" -UseBasicParsing -TimeoutSec 5
    }
    catch {
        return $null
    }
}

function Get-RuntimeModelStatus {
    param([object]$Runtime, [string]$ModelId)

    if (-not $Runtime -or -not $Runtime.models) {
        return $null
    }

    $property = $Runtime.models.PSObject.Properties[$ModelId]
    if (-not $property) {
        return $null
    }

    return $property.Value
}

function Get-ContainerStatus {
    param([string]$Name)

    $format = "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}"
    try {
        $output = & docker inspect --format $format $Name 2>$null
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
            return "missing|none"
        }
    }
    catch {
        return "missing|none"
    }

    return $output.Trim()
}

function Test-ContainerStatusRunning {
    param([string]$Status)

    return $Status -match "^running\|"
}

function Get-RunningLogicalModels {
    param(
        [string]$VlmStatus,
        [string]$VlApiStatus,
        [string]$PpOcrStatus,
        [string]$UnlimitedSglangStatus,
        [string]$UnlimitedApiStatus,
        [string]$OvisStatus,
        [string]$HpdServerStatus,
        [string]$HpdApiStatus
    )

    $running = New-Object System.Collections.Generic.List[string]
    if ((Test-ContainerStatusRunning $VlmStatus) -or (Test-ContainerStatusRunning $VlApiStatus)) {
        $running.Add("paddleocr-vl-1.6")
    }
    if (Test-ContainerStatusRunning $PpOcrStatus) {
        $running.Add("pp-ocrv6")
    }
    if ((Test-ContainerStatusRunning $UnlimitedSglangStatus) -or (Test-ContainerStatusRunning $UnlimitedApiStatus)) {
        $running.Add("unlimited-ocr")
    }
    if (Test-ContainerStatusRunning $OvisStatus) {
        $running.Add("ovisocr2")
    }
    if ((Test-ContainerStatusRunning $HpdServerStatus) -or (Test-ContainerStatusRunning $HpdApiStatus)) {
        $running.Add("hpd-parsing")
    }
    return [string[]]$running.ToArray()
}

function Show-Diagnostics {
    if ($script:DiagnosticsShown -or [string]::IsNullOrWhiteSpace($script:RuntimeEnv)) {
        return
    }

    $script:DiagnosticsShown = $true
    Write-Section "Service status"
    $statusArgs = Get-ComposeArgs @("ps", "-a")
    & docker @statusArgs

    $services = Get-DeploymentServiceList

    foreach ($service in $services) {
        Write-Section "Recent logs: $service"
        $logArgs = Get-ComposeArgs @("logs", "--tail=160", $service)
        & docker @logArgs
    }
}

function Wait-ForServices {
    param([int]$Timeout)

    Write-Section "Waiting for WebUI runtime and active model ($script:ActiveModel)"
    $deadline = (Get-Date).AddSeconds($Timeout)
    $lastLine = ""

    while ((Get-Date) -lt $deadline) {
        $vlm = Get-ContainerStatus "paddleocr-vlm-server"
        $api = Get-ContainerStatus "paddleocr-vl-api"
        $ocr = Get-ContainerStatus "paddleocr-ocr-api"
        $uow = Get-ContainerStatus "unlimited-ocr-sglang"
        $uowApi = Get-ContainerStatus "unlimited-ocr-api"
        $ovis = Get-ContainerStatus "ovisocr2-api"
        $hpdServer = Get-ContainerStatus "hpd-parsing-server"
        $hpdApi = Get-ContainerStatus "hpd-parsing-api"
        $web = Get-ContainerStatus "pandocr-web"
        $apiOk = Test-HttpOk "http://localhost:8081/health"
        $ocrOk = Test-HttpOk "http://localhost:8082/health"
        $uowOk = if ($script:EnableUnlimitedOcr) { Test-HttpOk "http://localhost:8083/health" } else { $false }
        $ovisOk = if ($script:EnableOvisOcr2) { Test-HttpOk "http://localhost:8084/health" } else { $false }
        $hpdOk = if ($script:EnableHpdParsing) { Test-HttpOk "http://localhost:8085/health" } else { $false }
        $webOk = Test-HttpOk "http://localhost:8000/"
        $runtime = if ($webOk) { Get-ModelRuntimePayload } else { $null }
        $activeRuntimeStatus = Get-RuntimeModelStatus -Runtime $runtime -ModelId $script:ActiveModel
        $runtimeReady = [bool]($activeRuntimeStatus -and $activeRuntimeStatus.ready)
        $runtimeState = if ($activeRuntimeStatus) { [string]$activeRuntimeStatus.state } else { "unavailable" }
        $operationState = if ($runtime -and $runtime.operation) { [string]$runtime.operation.state } else { "unavailable" }
        $operationTarget = if ($runtime -and $runtime.operation) { [string]$runtime.operation.targetModelId } else { "" }
        $runningLogicalModels = @(Get-RunningLogicalModels `
            -VlmStatus $vlm `
            -VlApiStatus $api `
            -PpOcrStatus $ocr `
            -UnlimitedSglangStatus $uow `
            -UnlimitedApiStatus $uowApi `
            -OvisStatus $ovis `
            -HpdServerStatus $hpdServer `
            -HpdApiStatus $hpdApi)

        $activeStatuses = @()
        if ($script:ActiveModel -eq "pp-ocrv6") {
            $activeStatuses = @($ocr, $web)
        }
        elseif ($script:ActiveModel -eq "unlimited-ocr") {
            $activeStatuses = if ($script:UnlimitedOcrBackend -eq "sglang") { @($uow, $uowApi, $web) } else { @($uowApi, $web) }
        }
        elseif ($script:ActiveModel -eq "ovisocr2") {
            $activeStatuses = @($ovis, $web)
        }
        elseif ($script:ActiveModel -eq "hpd-parsing") {
            $activeStatuses = @($hpdServer, $hpdApi, $web)
        }
        else {
            $activeStatuses = @($vlm, $api, $web)
        }

        if ($runtime -and -not $runtime.controlAvailable) {
            Show-Diagnostics
            throw "WebUI is running, but Docker model runtime control is not available."
        }

        if ($runningLogicalModels.Count -gt 1) {
            Show-Diagnostics
            throw "Single-model invariant violated. Running logical models: $($runningLogicalModels -join ', ')."
        }

        if ($runtimeReady -and $webOk) {
            if ($runningLogicalModels.Count -ne 1 -or $runningLogicalModels[0] -ne $script:ActiveModel) {
                Show-Diagnostics
                $actual = if ($runningLogicalModels.Count -eq 0) { "none" } else { $runningLogicalModels -join ", " }
                throw "Runtime reported $script:ActiveModel ready, but running logical models were: $actual."
            }
            Write-Ok "WebUI runtime reports only $script:ActiveModel is running and ready."
            return
        }

        if ($operationState -eq "error" -and ($operationTarget -eq "" -or $operationTarget -eq $script:ActiveModel)) {
            Show-Diagnostics
            $message = if ($runtime.operation.message) { [string]$runtime.operation.message } else { "Model runtime reported an error." }
            throw $message
        }

        foreach ($status in $activeStatuses) {
            if ($status -match "^exited\|") {
                Show-Diagnostics
                throw "An active service exited before becoming healthy."
            }
        }

        $line = "vlm=$vlm api=$api ocr=$ocr uow=$uow uowApi=$uowApi ovis=$ovis hpdServer=$hpdServer hpdApi=$hpdApi web=$web apiHttp=$apiOk ocrHttp=$ocrOk uowHttp=$uowOk ovisHttp=$ovisOk hpdHttp=$hpdOk webHttp=$webOk runtime=$runtimeState operation=$operationState"
        if ($line -ne $lastLine) {
            Write-Host $line
            $lastLine = $line
        }

        Start-Sleep -Seconds 15
    }

    Show-Diagnostics
    throw "Timed out after $Timeout seconds while waiting for WebUI and $script:ActiveModel."
}

try {
    Write-Section "PaddleOCR Local Windows one-click deployment"
    Write-Host "Repository: $script:RepoRoot"

    Get-RequiredCommand -Name "docker" -InstallHint "Please install Docker Desktop and start it."
    Get-RequiredCommand -Name "nvidia-smi" -InstallHint "Please install/update the NVIDIA driver."

    Invoke-Checked -File "docker" -Arguments @("info", "--format", "{{.ServerVersion}}") -Description "Checking Docker Desktop"
    Invoke-Checked -File "docker" -Arguments @("compose", "version") -Description "Checking Docker Compose"

    if (-not [string]::IsNullOrWhiteSpace($script:RequestedModel) -and -not [string]::IsNullOrWhiteSpace($script:RequestedModels)) {
        throw "Use either -Model for the simple single-model flow or -Models for the advanced multi-model flow, not both."
    }

    $interactiveSelection = [string]::IsNullOrWhiteSpace($script:RequestedModel) -and [string]::IsNullOrWhiteSpace($script:RequestedModels)
    if ($interactiveSelection) {
        $selection = Read-FriendlyDeploymentSelection
    }
    elseif (-not [string]::IsNullOrWhiteSpace($script:RequestedModel)) {
        $selection = Resolve-SingleDeploymentSelection -RequestedModel $script:RequestedModel -RequestedBackend $script:RequestedUnlimitedOcrBackend
    }
    else {
        $selection = Resolve-DeploymentSelection -RequestedModels $script:RequestedModels -RequestedBackend $script:RequestedUnlimitedOcrBackend
    }

    $script:DeployModelIds = @($selection.ModelIds)
    $script:ActiveModel = Resolve-ActiveModel -RequestedActiveModel $script:RequestedActiveModel -SelectedModels $script:DeployModelIds
    $script:EnableUnlimitedOcr = $script:DeployModelIds -contains "unlimited-ocr"
    $script:EnableOvisOcr2 = $script:DeployModelIds -contains "ovisocr2"
    $script:EnableHpdParsing = $script:DeployModelIds -contains "hpd-parsing"
    $script:UnlimitedOcrBackend = Normalize-UnlimitedOcrBackend $selection.UnlimitedOcrBackend
    $script:UnlimitedOcrBackendExplicit = [bool]$selection.UnlimitedOcrBackendExplicit
    Write-Ok "Selected models to deploy now: $($script:DeployModelIds -join ', ')"

    $gpus = Get-GpuList
    $gpu = Select-Gpu -Gpus $gpus -RequestedGpuId $GpuId
    $script:SelectedGpu = $gpu
    Write-Ok ("Selected GPU {0}: {1}" -f $gpu.Index, $gpu.Name)
    if ($interactiveSelection -and $script:EnableUnlimitedOcr -and -not $script:UnlimitedOcrBackendExplicit) {
        Read-UnlimitedOcrBackendSelection -Gpu $gpu
    }
    Apply-GpuSpecificBackendDefaults -Gpu $gpu
    if ($script:EnableUnlimitedOcr) {
        Write-Ok "Unlimited-OCR backend: $script:UnlimitedOcrBackend"
    }

    $gpuRequirement = Get-DeploymentGpuRequirement -ModelIds $script:DeployModelIds
    $selectedModelNames = (@($script:DeployModelIds | ForEach-Object { Get-ModelDisplayName $_ })) -join ", "
    Write-Ok "Selected model GPU requirement: total >= $($gpuRequirement.TotalMiB) MiB, currently free >= $($gpuRequirement.FreeMiB) MiB."
    if ($gpu.TotalMiB -lt $gpuRequirement.TotalMiB) {
        throw "GPU $($gpu.Index) has only $($gpu.TotalMiB) MiB total VRAM. Selected models ($selectedModelNames) require at least $($gpuRequirement.TotalMiB) MiB."
    }
    if ($script:EnableUnlimitedOcr -and $script:UnlimitedOcrBackend -eq "sglang") {
        Test-GpuSupportsSglang -Gpu $gpu
    }
    if ($gpu.FreeMiB -lt $gpuRequirement.FreeMiB) {
        throw "GPU $($gpu.Index) has only $($gpu.FreeMiB) MiB free VRAM. Selected models ($selectedModelNames) require at least $($gpuRequirement.FreeMiB) MiB currently free. Close GPU-heavy apps or choose another GPU with -GpuId."
    }

    $baseEnv = Resolve-BaseEnvFile -Gpu $gpu -RequestedEnvFile $EnvFile
    $script:RuntimeEnv = New-RuntimeEnvFile -BaseEnvFile $baseEnv -Gpu $gpu
    Write-Ok "Base env: $baseEnv"
    Write-Ok "Runtime env: $script:RuntimeEnv"

    Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs @("config", "--quiet")) -Description "Validating Docker Compose config"

    if ($DryRun) {
        Write-Section "Dry run complete"
        Write-Host "Selected GPU: $($gpu.Index) - $($gpu.Name)"
        Write-Host "Selected deployment models: $($script:DeployModelIds -join ', ')"
        Write-Host "WebUI model catalog: $($script:ModelCatalogIds -join ', ')"
        Write-Host "Active model on startup: $script:ActiveModel"
        Write-Host "Services to create: $((Get-DeploymentServiceList) -join ', ')"
        Write-Host "Base env: $baseEnv"
        Write-Host "Runtime env: $script:RuntimeEnv"
        Write-Host "No images were pulled, built, or started."
        exit 0
    }

    if ($interactiveSelection -and -not (Confirm-DeploymentPlan)) {
        Write-Section "Deployment cancelled"
        Write-Host "No images or containers were changed."
        exit 0
    }

    Set-UnlimitedOcrRuntimeSetting

    if (-not $SkipPull) {
        $pullServices = @()
        if ($script:DeployModelIds -contains "paddleocr-vl-1.6") {
            $pullServices += @("paddleocr-vlm-server", "paddleocr-vl-api")
        }
        if ($script:EnableHpdParsing) {
            $pullServices += "hpd-parsing-server"
        }
        if ($pullServices.Count -gt 0) {
            Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs (@("pull") + $pullServices)) -Description "Pulling official model images"
        }
        else {
            Write-Warn "No official PaddleOCR-VL images selected for pull."
        }
    }
    else {
        Write-Warn "Skipping image pull."
    }

    if (-not $SkipBuild) {
        $buildServices = @("pandocr-web", "pandocr-office-converter")
        if ($script:DeployModelIds -contains "pp-ocrv6") {
            $buildServices += "paddleocr-ocr-api"
        }
        $buildServices += Get-UnlimitedOcrServices
        if ($script:EnableOvisOcr2) {
            $buildServices += "ovisocr2-api"
        }
        if ($script:EnableHpdParsing) {
            $buildServices += "hpd-parsing-api"
        }
        Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs (@("build") + $buildServices)) -Description "Building local images"
    }
    else {
        Write-Warn "Skipping pandocr-web build."
    }

    if (-not $SkipClean) {
        Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs -Arguments @("down", "--remove-orphans") -IncludeOptionalProfiles) -Description "Clearing old containers"
    }
    else {
        Write-Warn "Skipping old-container cleanup."
    }

    $gpuCheckService = Get-GpuCheckService
    $gpuCheckCommand = if ($gpuCheckService -eq "hpd-parsing-server") {
        @("run", "--rm", "--no-deps", "--entrypoint", "nvidia-smi", $gpuCheckService)
    }
    else {
        @("run", "--rm", "--no-deps", $gpuCheckService, "nvidia-smi")
    }
    Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs $gpuCheckCommand) -Description "Checking Docker GPU access"
    $createArguments = @("up", "-d", "--no-start", "--force-recreate")
    if ($SkipBuild) {
        $createArguments += "--no-build"
    }
    $createArguments += Get-DeploymentServiceList
    Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs $createArguments) -Description "Creating selected PaddleOCR Local containers"
    Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs @("start", "pandocr-controller", "pandocr-office-converter", "pandocr-web")) -Description "Starting WebUI and isolated support services"

    Wait-ForServices -Timeout $TimeoutSeconds

    Write-Section "Deployment complete"
    Write-Host "WebUI: http://localhost:8000"
    if ($script:DeployModelIds -contains "paddleocr-vl-1.6") {
        Write-Host "VL API health: http://localhost:8081/health"
    }
    if ($script:DeployModelIds -contains "pp-ocrv6") {
        Write-Host "OCR API health: http://localhost:8082/health"
    }
    if ($script:EnableUnlimitedOcr) {
        Write-Host "Unlimited-OCR API health: http://localhost:8083/health"
    }
    if ($script:EnableOvisOcr2) {
        Write-Host "OvisOCR2 API health: http://localhost:8084/health"
    }
    if ($script:EnableHpdParsing) {
        Write-Host "HPD-Parsing API health: http://localhost:8085/health"
    }
    Write-Host "Active model on startup: $script:ActiveModel. When you select another model, the controller fully stops the current model and releases its GPU memory before starting the selected model."
    Write-Host "Useful logs: docker compose --env-file `"$script:RuntimeEnv`" --profile `"*`" logs -f"

    if (-not $NoOpen) {
        Start-Process "http://localhost:8000"
    }

    exit 0
}
catch {
    Write-Host ""
    Write-Host "[FAILED] $($_.Exception.Message)" -ForegroundColor Red
    Show-Diagnostics
    exit 1
}
