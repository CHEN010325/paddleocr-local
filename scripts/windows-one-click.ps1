param(
    [string]$EnvFile = "",
    [int]$GpuId = -1,
    [int]$TimeoutSeconds = 1800,
    [string]$Models = "",
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
$script:RequestedModels = $Models
$script:RequestedUnlimitedOcrBackend = $UnlimitedOcrBackend
$script:RuntimeEnv = ""
$script:DiagnosticsShown = $false
$script:ActiveModel = "paddleocr-vl-1.6"
$script:EnableUnlimitedOcr = $false
$script:UnlimitedOcrBackend = "transformers"
$script:UnlimitedOcrBackendExplicit = $false
$script:DeployModelIds = @("paddleocr-vl-1.6")
$script:ModelCatalogIds = @("paddleocr-vl-1.6", "pp-ocrv6", "unlimited-ocr")
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
        "--query-gpu=index,name,memory.total,memory.free",
        "--format=csv,noheader,nounits"
    )
    $output = & nvidia-smi @args
    if ($LASTEXITCODE -ne 0) {
        throw "nvidia-smi failed. Please install/update the NVIDIA driver first."
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

        $gpus += [pscustomobject]@{
            Index = [int]($parts[0].Trim())
            Name = $parts[1].Trim()
            TotalMiB = [int]([double]($parts[2].Trim()))
            FreeMiB = [int]([double]($parts[3].Trim()))
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
        Write-Host ("GPU {0}: {1} | total={2} MiB free={3} MiB" -f $gpu.Index, $gpu.Name, $gpu.TotalMiB, $gpu.FreeMiB)
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

function Read-DeploymentSelection {
    Write-Section "Choose models to deploy"
    Write-Host "Only selected model containers/images will be pulled or built now."
    Write-Host "The WebUI will still show the other models as undeployed and explain how to enable them."
    Write-Host ""
    Write-Host "  1) PaddleOCR-VL 1.6        document parsing, recommended default"
    Write-Host "  2) PP-OCRv6                text OCR"
    Write-Host "  3) Unlimited-OCR           Transformers backend"
    Write-Host "  4) Unlimited-OCR           SGLang backend"
    Write-Host "  5) PaddleOCR core          PaddleOCR-VL 1.6 + PP-OCRv6"
    Write-Host "  6) All three               PaddleOCR-VL 1.6 + PP-OCRv6 + Unlimited-OCR Transformers"
    Write-Host ""
    $answer = Read-Host "Enter one or more options separated by comma [1]"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        return "1"
    }
    return $answer
}

function Resolve-DeploymentSelection {
    param(
        [string]$RequestedModels,
        [string]$RequestedBackend
    )

    $rawSelection = $RequestedModels
    if ([string]::IsNullOrWhiteSpace($rawSelection)) {
        $rawSelection = Read-DeploymentSelection
    }

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
            { $_ -in @("6", "all", "three", "full") } {
                Add-DeploymentModel -Models $selected -ModelId "paddleocr-vl-1.6"
                Add-DeploymentModel -Models $selected -ModelId "pp-ocrv6"
                Add-DeploymentModel -Models $selected -ModelId "unlimited-ocr"
                if (-not $backendExplicit) {
                    $backend = "transformers"
                }
                continue
            }
            default {
                throw "Unknown model selection '$token'. Use 1,2,3,4,5,6 or model ids such as paddleocr-vl-1.6, pp-ocrv6, unlimited-ocr."
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
    return [string[]]$services.ToArray()
}

function Get-DeploymentServiceList {
    $services = New-Object System.Collections.Generic.List[string]
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
    $lines = [string[]](Get-Content -Path $BaseEnvFile -Encoding UTF8)
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_GPU_DEVICE_ID" -Value ([string]$Gpu.Index)
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_MODEL_CONTROL" -Value "docker"
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_ACTIVE_MODEL_ON_START" -Value $script:ActiveModel
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_MODEL_CATALOG" -Value ($script:ModelCatalogIds -join ",")
    $lines = Ensure-EnvLine -Lines $lines -Key "UNLIMITED_OCR_MODEL_NAME" -Value "baidu/Unlimited-OCR"
    $lines = Set-EnvLine -Lines $lines -Key "UNLIMITED_OCR_BACKEND" -Value $script:UnlimitedOcrBackend
    $lines = Ensure-EnvLine -Lines $lines -Key "UNLIMITED_OCR_PRELOAD" -Value "1"
    $lines = Set-EnvLine -Lines $lines -Key "PANDOCR_ENABLE_UNLIMITED_OCR" -Value "1"
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_MODEL_SWITCH_TIMEOUT" -Value "1200"
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_MAX_UPLOAD_MB" -Value "512"
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_MAX_CONCURRENT_OCR" -Value "1"
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_ENFORCE_ORIGIN_CHECK" -Value "1"
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_API_TOKEN" -Value ""
    $lines = Ensure-EnvLine -Lines $lines -Key "PANDOCR_ENABLE_API_DOCS" -Value "0"
    $script:ActiveModel = Get-EnvLineValue -Lines $lines -Key "PANDOCR_ACTIVE_MODEL_ON_START" -DefaultValue $script:ActiveModel
    $script:EnableUnlimitedOcr = $script:DeployModelIds -contains "unlimited-ocr"
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
        [switch]$IncludeUnlimitedOcrProfile
    )
    $args = @("compose", "--env-file", $script:RuntimeEnv)
    if ($script:EnableUnlimitedOcr -or $IncludeUnlimitedOcrProfile) {
        $args += @("--profile", "unlimited-ocr")
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
    }
    catch {
        return "missing|none"
    }
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
        return "missing|none"
    }

    return $output.Trim()
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
        $uow = if ($script:EnableUnlimitedOcr -and $script:UnlimitedOcrBackend -eq "sglang") { Get-ContainerStatus "unlimited-ocr-sglang" } else { "disabled|none" }
        $uowApi = if ($script:EnableUnlimitedOcr) { Get-ContainerStatus "unlimited-ocr-api" } else { "disabled|none" }
        $web = Get-ContainerStatus "pandocr-web"
        $apiOk = Test-HttpOk "http://localhost:8081/health"
        $ocrOk = Test-HttpOk "http://localhost:8082/health"
        $uowOk = if ($script:EnableUnlimitedOcr) { Test-HttpOk "http://localhost:8083/health" } else { $false }
        $webOk = Test-HttpOk "http://localhost:8000/"
        $runtime = if ($webOk) { Get-ModelRuntimePayload } else { $null }
        $activeRuntimeStatus = Get-RuntimeModelStatus -Runtime $runtime -ModelId $script:ActiveModel
        $runtimeReady = [bool]($activeRuntimeStatus -and $activeRuntimeStatus.ready)
        $runtimeState = if ($activeRuntimeStatus) { [string]$activeRuntimeStatus.state } else { "unavailable" }
        $operationState = if ($runtime -and $runtime.operation) { [string]$runtime.operation.state } else { "unavailable" }
        $operationTarget = if ($runtime -and $runtime.operation) { [string]$runtime.operation.targetModelId } else { "" }

        $activeStatuses = @()
        if ($script:ActiveModel -eq "pp-ocrv6") {
            $activeStatuses = @($ocr, $web)
        }
        elseif ($script:ActiveModel -eq "unlimited-ocr") {
            $activeStatuses = if ($script:UnlimitedOcrBackend -eq "sglang") { @($uow, $uowApi, $web) } else { @($uowApi, $web) }
        }
        else {
            $activeStatuses = @($vlm, $api, $web)
        }

        if ($runtime -and -not $runtime.controlAvailable) {
            Show-Diagnostics
            throw "WebUI is running, but Docker model runtime control is not available."
        }

        if ($runtimeReady -and $webOk) {
            Write-Ok "WebUI runtime reports $script:ActiveModel is ready. The other model remains on standby."
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

        $line = "vlm=$vlm api=$api ocr=$ocr uow=$uow uowApi=$uowApi web=$web apiHttp=$apiOk ocrHttp=$ocrOk uowHttp=$uowOk webHttp=$webOk runtime=$runtimeState operation=$operationState"
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

    $selection = Resolve-DeploymentSelection -RequestedModels $script:RequestedModels -RequestedBackend $script:RequestedUnlimitedOcrBackend
    $script:DeployModelIds = @($selection.ModelIds)
    $script:ActiveModel = $script:DeployModelIds[0]
    $script:EnableUnlimitedOcr = $script:DeployModelIds -contains "unlimited-ocr"
    $script:UnlimitedOcrBackend = Normalize-UnlimitedOcrBackend $selection.UnlimitedOcrBackend
    $script:UnlimitedOcrBackendExplicit = [bool]$selection.UnlimitedOcrBackendExplicit
    Write-Ok "Selected models to deploy now: $($script:DeployModelIds -join ', ')"

    $gpus = Get-GpuList
    $gpu = Select-Gpu -Gpus $gpus -RequestedGpuId $GpuId
    Write-Ok ("Selected GPU {0}: {1}" -f $gpu.Index, $gpu.Name)
    Apply-GpuSpecificBackendDefaults -Gpu $gpu
    if ($script:EnableUnlimitedOcr) {
        Write-Ok "Unlimited-OCR backend: $script:UnlimitedOcrBackend"
    }

    if ($gpu.TotalMiB -lt 8192) {
        throw "GPU $($gpu.Index) has only $($gpu.TotalMiB) MiB VRAM. PaddleOCR-VL requires at least 8192 MiB."
    }
    if ($gpu.FreeMiB -lt 6656) {
        throw "GPU $($gpu.Index) has only $($gpu.FreeMiB) MiB free VRAM. Close GPU-heavy apps or choose another GPU with -GpuId."
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

    Set-UnlimitedOcrRuntimeSetting

    if (-not $SkipPull) {
        $pullServices = @()
        if ($script:DeployModelIds -contains "paddleocr-vl-1.6") {
            $pullServices += @("paddleocr-vlm-server", "paddleocr-vl-api")
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
        $buildServices = @("pandocr-web")
        if ($script:DeployModelIds -contains "pp-ocrv6") {
            $buildServices += "paddleocr-ocr-api"
        }
        $buildServices += Get-UnlimitedOcrServices
        Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs (@("build") + $buildServices)) -Description "Building local images"
    }
    else {
        Write-Warn "Skipping pandocr-web build."
    }

    if (-not $SkipClean) {
        Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs -Arguments @("down", "--remove-orphans") -IncludeUnlimitedOcrProfile) -Description "Clearing old containers"
    }
    else {
        Write-Warn "Skipping old-container cleanup."
    }

    $gpuCheckService = Get-GpuCheckService
    Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs @("run", "--rm", "--no-deps", $gpuCheckService, "nvidia-smi")) -Description "Checking Docker GPU access"
    Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs (@("up", "-d", "--no-start", "--force-recreate") + (Get-DeploymentServiceList))) -Description "Creating selected PaddleOCR Local containers"
    Invoke-Checked -File "docker" -Arguments (Get-ComposeArgs @("start", "pandocr-web")) -Description "Starting WebUI and model runtime controller"

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
    Write-Host "Active model on startup: $script:ActiveModel. Select another model in the UI to stop this one and start the others."
    Write-Host "Useful logs: docker compose --env-file `"$script:RuntimeEnv`" logs -f"

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
