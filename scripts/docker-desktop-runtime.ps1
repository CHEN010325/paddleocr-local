# Windows Docker Desktop startup recovery. No actions occur when dot-sourced.
function New-DockerInfoProbe {
    $probe = New-Object System.Diagnostics.Process
    $probe.StartInfo.FileName = @(Get-Command docker -CommandType Application -ErrorAction Stop)[0].Source
    $probe.StartInfo.Arguments = 'info --format "{{.ServerVersion}}"'
    $probe.StartInfo.UseShellExecute = $false
    $probe.StartInfo.CreateNoWindow = $true
    $probe.StartInfo.RedirectStandardOutput = $true
    $probe.StartInfo.RedirectStandardError = $true
    return $probe
}

function Test-DockerEngineReady {
    $probe = New-DockerInfoProbe
    try {
        $null = $probe.Start()
        if (-not $probe.WaitForExit(5000)) {
            # This is our own docker-info CLI child, never the Desktop backend.
            try { $probe.Kill() } catch { Write-Verbose 'Readiness probe already exited.' }
            return $false
        }
        # Docker CLI 28 can exit 0 with only a newline when Desktop's pipe is absent.
        # A real server version is required; an exit code alone is not readiness.
        $version = $probe.StandardOutput.ReadToEnd().Trim()
        return $probe.ExitCode -eq 0 -and $version -match '^\d+\.\d+(?:\.\d+)?(?:[-+]\S+)?$'
    }
    finally { $probe.Dispose() }
}

function Assert-LocalDockerDesktopContext {
    $ErrorActionPreference = 'Continue'
    $endpoint = $env:DOCKER_HOST
    $context = $env:DOCKER_CONTEXT
    if (-not [string]::IsNullOrWhiteSpace($context) -or [string]::IsNullOrWhiteSpace($endpoint)) {
        if ([string]::IsNullOrWhiteSpace($context)) {
            $context = & docker context show 2>$null
            if ($LASTEXITCODE -ne 0) { throw 'Cannot determine Docker context; no local recovery attempted.' }
        }
        # Avoid embedded quotes: Windows PowerShell 5.1 strips them in native argv.
        $endpoint = & docker context inspect $context --format '{{.Endpoints.docker.Host}}' 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Docker context; no local recovery attempted.' }
    }
    if ("$endpoint" -notmatch '^npipe:/{2,}\.(/|\\)pipe(/|\\)(docker_engine|dockerDesktopLinuxEngine)$') {
        throw 'Automatic recovery only supports the local Docker Desktop Linux engine. Check DOCKER_HOST / Docker context.'
    }
}

function Get-DockerDesktopExecutable {
    $executable = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw 'Docker Desktop executable was not found. Start your Docker engine manually; no recovery attempted.'
    }
    return $executable
}

function Start-LocalDockerDesktop {
    Start-Process -FilePath (Get-DockerDesktopExecutable) -WindowStyle Hidden
}

function Get-DockerDesktopStartupSince {
    # An already-open error dialog belongs to this Desktop process attempt too.
    # Ignore evidence from older Desktop runs or other Windows sessions.
    $since = [DateTimeOffset]::UtcNow
    $dockerRoot = [IO.Path]::GetDirectoryName((Get-DockerDesktopExecutable)) + '\'
    $session = (Get-Process -Id $PID).SessionId
    foreach ($process in @(Get-Process -Name 'com.docker.backend' -ErrorAction SilentlyContinue)) {
        if ($process.SessionId -eq $session -and $process.Path -and
            $process.Path.StartsWith($dockerRoot, [StringComparison]::OrdinalIgnoreCase)) {
            $started = [DateTimeOffset]$process.StartTime.ToUniversalTime()
            if ($started -lt $since) { $since = $started }
        }
    }
    return $since
}

function Get-DockerDesktopBackendLogTail {
    $logPath = Join-Path $env:LOCALAPPDATA 'Docker\log\host\com.docker.backend.exe.log'
    return @(Get-Content -LiteralPath $logPath -Tail 250 -ErrorAction SilentlyContinue)
}

function Test-FreshDockerTelemetrySocketFailure {
    param([DateTimeOffset]$Since)
    foreach ($line in (Get-DockerDesktopBackendLogTail)) {
        if ($line -notmatch '^\[([^\]]+)\]') { continue }
        $stamp = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse($Matches[1], [ref]$stamp) -or $stamp -lt $Since) { continue }
        if ($line -match 'running OTel manager' -and $line -match 'removing stale socket' -and
            $line -match '(?<![A-Za-z0-9_.])userAnalyticsOtlpHttp\.sock(?:[\\"\s:]|$)') { return $true }
    }
    return $false
}

function Stop-LocalDockerDesktopProcesses {
    $dockerRoot = [IO.Path]::GetDirectoryName((Get-DockerDesktopExecutable)) + '\'
    $session = (Get-Process -Id $PID).SessionId
    $processes = @(Get-Process -Name 'Docker Desktop','com.docker.backend','com.docker.build' -ErrorAction SilentlyContinue |
        Where-Object { $_.SessionId -eq $session })
    foreach ($process in $processes) {
        if ($process.HasExited) { continue }
        if (-not $process.Path -or -not $process.Path.StartsWith($dockerRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Cannot verify Docker process ownership; no socket cleanup attempted.'
        }
        # Pin the verified process handle, rather than later acting on a reused PID.
        $null = $process.Handle
    }
    foreach ($process in $processes) {
        try { if (-not $process.HasExited) { $process.Kill() } }
        catch { if (-not $process.HasExited) { throw } }
    }
    foreach ($process in $processes) {
        if (-not $process.WaitForExit(10000)) { throw 'Docker process did not exit; no socket cleanup attempted.' }
    }
}

function Invoke-DockerSocketWsl {
    param([string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    $output = & wsl.exe --distribution docker-desktop @Arguments 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) { Write-Verbose ("$output") }
    return $code
}

function Remove-DockerTelemetrySocket {
    # No caller-supplied path, shell interpolation, directory deletion or recursion.
    $localRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
    if ($localRoot -notmatch '^([a-zA-Z]):[\\/]') { throw 'Unsupported LOCALAPPDATA path; no socket cleanup attempted.' }
    $drive = $Matches[1].ToLowerInvariant()
    $target = Join-Path $localRoot 'Docker\run\userAnalyticsOtlpHttp.sock'
    $item = Get-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return }
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ($item.Attributes -band [IO.FileAttributes]::Directory)) {
        throw 'The telemetry socket is not the expected Windows socket reparse point; no cleanup attempted.'
    }
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'WSL is unavailable; no socket cleanup attempted.' }
    $linuxTarget = '/mnt/host/' + $drive + '/' + $target.Substring(3).Replace('\', '/')
    if ((Invoke-DockerSocketWsl -Arguments @('--exec','rm','--',$linuxTarget)) -ne 0) {
        throw 'Unable to remove the stale Docker telemetry socket through WSL; no other files were changed.'
    }
    foreach ($test in @('-e','-L')) {
        if ((Invoke-DockerSocketWsl -Arguments @('--exec','test','!',$test,$linuxTarget)) -ne 0) {
            throw 'Docker telemetry socket removal could not be verified. No further recovery attempted.'
        }
    }
}

function Repair-DockerTelemetrySocket {
    if (Test-DockerEngineReady) { return }
    Assert-LocalDockerDesktopContext
    Write-Host '[WARN] Current Docker startup failed on userAnalyticsOtlpHttp.sock; repairing this socket once.'
    Stop-LocalDockerDesktopProcesses
    Remove-DockerTelemetrySocket
}

function Ensure-DockerDesktopReady {
    param([switch]$DryRun, [int]$TimeoutSeconds = 180)
    if (Test-DockerEngineReady) { return }
    if ($DryRun) { throw 'Docker is not ready. DryRun does not start or repair Docker Desktop.' }
    Assert-LocalDockerDesktopContext
    $since = Get-DockerDesktopStartupSince
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    $repaired = $false
    Write-Host '[INFO] Starting local Docker Desktop and waiting for the engine.'
    Start-LocalDockerDesktop
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if (Test-DockerEngineReady) { return }
        if (Test-FreshDockerTelemetrySocketFailure -Since $since) {
            if ($repaired) { throw 'Docker telemetry socket error recurred after one recovery; stopping without further cleanup.' }
            Repair-DockerTelemetrySocket
            $repaired = $true
            $since = [DateTimeOffset]::UtcNow
            $deadline = $since.AddSeconds($TimeoutSeconds)
            Start-LocalDockerDesktop
        }
        Start-Sleep -Seconds 2
    }
    throw 'Docker Desktop did not become ready. Unrelated startup errors are not automatically cleaned; inspect Docker Desktop diagnostics.'
}
