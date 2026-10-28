"""Exercise Windows startup recovery with fake Docker/WSL; never stop live models."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_ps(tmp_path, body):
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell unavailable")
    source = str(ROOT / "scripts/docker-desktop-runtime.ps1").replace("'", "''")
    probe = tmp_path / "probe.ps1"
    probe.write_text("$ErrorActionPreference='Stop'\nSet-StrictMode -Version 2.0\n. '" + source + "'\n" + body, encoding="utf-8")
    result = subprocess.run([shell, "-NoProfile", "-File", str(probe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("mode,expected", [
    ("healthy", [0, 0, False]),
    ("stopped", [1, 0, False]),
    ("socket", [2, 1, False]),
    ("repeated", [2, 1, True]),
    ("remote", [0, 0, True]),
    ("dryrun", [0, 0, True]),
    ("unrelated", [1, 0, True]),
])
def test_recovery_is_bounded_and_only_handles_this_fault(tmp_path, mode, expected):
    result = run_ps(tmp_path, r"""
$script:starts=0; $script:repairs=0
function Test-DockerEngineReady { return MODE -eq 'healthy' -or (MODE -eq 'stopped' -and $script:starts -gt 0) -or (MODE -eq 'socket' -and $script:starts -eq 2) }
function Assert-LocalDockerDesktopContext { if (MODE -eq 'remote') { throw 'remote context' } }
function Start-LocalDockerDesktop { $script:starts++ }
function Get-DockerDesktopStartupSince { return [DateTimeOffset]::UtcNow }
function Test-FreshDockerTelemetrySocketFailure { param($Since) return MODE -in @('socket','repeated') }
function Repair-DockerTelemetrySocket { $script:repairs++ }
function Start-Sleep { param($Seconds) }
$failed=$false
try { Ensure-DockerDesktopReady -DryRun:(MODE -eq 'dryrun') -TimeoutSeconds 1 | Out-Null }
catch { $failed=$true }
ConvertTo-Json -InputObject @($script:starts,$script:repairs,$failed) -Compress
""".replace("MODE", "'" + mode + "'"))
    assert result == expected


@pytest.mark.parametrize("age,message,expected", [
    (-1, "running OTel manager: removing stale socket: remove userAnalyticsOtlpHttp.sock: inaccessible", True),
    (-600, "running OTel manager: removing stale socket: remove userAnalyticsOtlpHttp.sock: inaccessible", False),
    (-1, "running OTel manager: removing stale socket: remove other.sock: inaccessible", False),
    (-1, "userAnalyticsOtlpHttp.sock listening", False),
])
def test_socket_signature_must_be_fresh_and_exact(tmp_path, age, message, expected):
    result = run_ps(tmp_path, r"""
$since=[DateTimeOffset]::UtcNow.AddSeconds(-10)
$stamp=[DateTimeOffset]::UtcNow.AddSeconds(AGE).ToString('o')
function Get-DockerDesktopBackendLogTail { return "[$stamp]MESSAGE" }
Test-FreshDockerTelemetrySocketFailure -Since $since | ConvertTo-Json -Compress
""".replace("MESSAGE", message).replace("AGE", str(age)))
    assert result is expected


@pytest.mark.skipif(os.name != "nt", reason="Windows socket path")
def test_cleanup_uses_only_the_fixed_socket_and_wsl_argv(tmp_path):
    result = run_ps(tmp_path, r"""
$env:LOCALAPPDATA='C:\Users\Example User\AppData\Local'
$script:commands=@()
function Get-Item { param($LiteralPath,[switch]$Force,$ErrorAction) return [pscustomobject]@{Attributes=[IO.FileAttributes]::ReparsePoint} }
function Invoke-DockerSocketWsl { param([string[]]$Arguments) $script:commands+=,@($Arguments); return 0 }
function Get-Command { param($Name,$ErrorAction) return [pscustomobject]@{Name='wsl.exe'} }
Remove-DockerTelemetrySocket
ConvertTo-Json -InputObject $script:commands -Depth 4 -Compress
""")
    target = "/mnt/host/c/Users/Example User/AppData/Local/Docker/run/userAnalyticsOtlpHttp.sock"
    assert result == [["--exec", "rm", "--", target], ["--exec", "test", "!", "-e", target], ["--exec", "test", "!", "-L", target]]


@pytest.mark.skipif(os.name != "nt", reason="Windows socket path")
def test_regular_file_is_not_removed(tmp_path):
    result = run_ps(tmp_path, r"""
function Get-Item { param($LiteralPath,[switch]$Force,$ErrorAction) return [pscustomobject]@{Attributes=[IO.FileAttributes]::Archive} }
function Invoke-DockerSocketWsl { throw 'must not reach WSL' }
$failed=$false; try { Remove-DockerTelemetrySocket } catch { $failed=$true }
$failed | ConvertTo-Json -Compress
""")
    assert result is True


def test_shared_launcher_calls_recovery_before_compose():
    script = (ROOT / "scripts/windows-one-click.ps1").read_text(encoding="utf-8")
    assert '"docker-desktop-runtime.ps1"' in script
    assert "Ensure-DockerDesktopReady -DryRun:$DryRun" in script
    assert script.index("Ensure-DockerDesktopReady -DryRun:$DryRun") < script.index('Description "Checking Docker Compose"')


@pytest.mark.parametrize("endpoint,allowed", [
    ("npipe:////./pipe/docker_engine", True),
    ("npipe:////./pipe/dockerDesktopLinuxEngine", True),
    ("ssh://example", False),
    ("tcp://127.0.0.1:2375", False),
])
def test_only_local_desktop_endpoints_can_be_repaired(tmp_path, endpoint, allowed):
    result = run_ps(tmp_path, r"""
$env:DOCKER_CONTEXT=''; $env:DOCKER_HOST='ENDPOINT'
$allowed=$true; try { Assert-LocalDockerDesktopContext } catch { $allowed=$false }
$allowed | ConvertTo-Json -Compress
""".replace("ENDPOINT", endpoint))
    assert result is allowed


def test_explicit_remote_context_takes_precedence_over_local_host(tmp_path):
    result = run_ps(tmp_path, r"""
$env:DOCKER_CONTEXT='remote'; $env:DOCKER_HOST='npipe:////./pipe/docker_engine'
function docker { $global:LASTEXITCODE=0; return 'ssh://example' }
$allowed=$true; try { Assert-LocalDockerDesktopContext } catch { $allowed=$false }
$allowed | ConvertTo-Json -Compress
""")
    assert result is False


def test_context_inspection_avoids_embedded_quotes_in_windows_powershell(tmp_path):
    result = run_ps(tmp_path, r"""
$env:DOCKER_CONTEXT='desktop-linux'; $env:DOCKER_HOST=''
$script:template=''
function docker { $script:template=$args[-1]; $global:LASTEXITCODE=0; return 'npipe:////./pipe/dockerDesktopLinuxEngine' }
Assert-LocalDockerDesktopContext
$script:template | ConvertTo-Json -Compress
""")
    assert result == '{{.Endpoints.docker.Host}}'


def test_engine_recovered_during_detection_is_not_stopped(tmp_path):
    result = run_ps(tmp_path, r"""
function Test-DockerEngineReady { return $true }
function Assert-LocalDockerDesktopContext { throw 'must not stop a healthy engine' }
function Stop-LocalDockerDesktopProcesses { throw 'must not stop a healthy engine' }
Repair-DockerTelemetrySocket
$true | ConvertTo-Json -Compress
""")
    assert result is True


@pytest.mark.skipif(os.name != "nt", reason="Windows process paths")
@pytest.mark.parametrize("kind,expected", [("owned", [1, False]), ("foreign", [0, True]), ("other_session", [0, False]), ("exit_race", [1, False]), ("kill_failure", [1, True])])
def test_only_verified_docker_processes_in_this_session_are_stopped(tmp_path, kind, expected):
    result = run_ps(tmp_path, r"""
$script:stops=0
function Get-DockerDesktopExecutable { return 'C:\Program Files\Docker\Docker\Docker Desktop.exe' }
function Get-Process {
    param($Id,$Name,$ErrorAction)
    if ($Id) { return [pscustomobject]@{SessionId=1} }
    $entry=[pscustomobject]@{SessionId=$(if ('KIND' -eq 'other_session') { 2 } else { 1 }); Path=$(if ('KIND' -eq 'foreign') { 'C:\elsewhere\Docker Desktop.exe' } else { 'C:\Program Files\Docker\Docker\Docker Desktop.exe' }); HasExited=$false; Id=123; Handle=123}
    $entry | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($Milliseconds) return $true }
    $entry | Add-Member -MemberType ScriptMethod -Name Kill -Value {
        $script:stops++
        if ('KIND' -eq 'exit_race') { $this.HasExited=$true; throw 'already exited' }
        if ('KIND' -eq 'kill_failure') { throw 'access denied' }
    }
    return $entry
}
function Stop-Process { throw 'never kill by a potentially reused PID' }
$failed=$false; try { Stop-LocalDockerDesktopProcesses } catch { $failed=$true }
ConvertTo-Json -InputObject @($script:stops,$failed) -Compress
""".replace("KIND", kind))
    assert result == expected


@pytest.mark.skipif(os.name != "nt", reason="Windows process paths")
def test_existing_error_dialog_is_scoped_to_the_running_desktop_session(tmp_path):
    result = run_ps(tmp_path, r"""
function Get-DockerDesktopExecutable { return 'C:\Program Files\Docker\Docker\Docker Desktop.exe' }
$stamp=[DateTime]::UtcNow.AddMinutes(-2)
function Get-Process {
    param($Id,$Name,$ErrorAction)
    if ($Id) { return [pscustomobject]@{SessionId=1} }
    return @(
        [pscustomobject]@{SessionId=1;Path='C:\Program Files\Docker\Docker\com.docker.backend.exe';StartTime=$stamp},
        [pscustomobject]@{SessionId=2;Path='C:\Program Files\Docker\Docker\com.docker.backend.exe';StartTime=$stamp.AddDays(-1)}
    )
}
$since=Get-DockerDesktopStartupSince
($since.UtcDateTime -eq $stamp) | ConvertTo-Json -Compress
""")
    assert result is True


@pytest.mark.parametrize('exits,version,expected', [(True, '28.0.4', [True, 0, 1]), (False, '', [False, 1, 1]), (True, '', [False, 0, 1])])
def test_docker_probe_has_a_timeout_and_cleans_up_only_its_child(tmp_path, exits, version, expected):
    result = run_ps(tmp_path, r"""
$script:kills=0; $script:disposes=0
function New-DockerInfoProbe {
    $probe=[pscustomobject]@{ExitCode=0}
    $reader=[pscustomobject]@{}
    $reader | Add-Member -MemberType ScriptMethod -Name ReadToEnd -Value { return 'VERSION' }
    $probe | Add-Member -NotePropertyName StandardOutput -NotePropertyValue $reader
    $probe | Add-Member -MemberType ScriptMethod -Name Start -Value { return $true }
    $probe | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($Milliseconds) if($Milliseconds -ne 5000) { throw 'unbounded probe' }; return EXITS }
    $probe | Add-Member -MemberType ScriptMethod -Name Kill -Value { $script:kills++ }
    $probe | Add-Member -MemberType ScriptMethod -Name Dispose -Value { $script:disposes++ }
    return $probe
}
$ready=Test-DockerEngineReady
ConvertTo-Json -InputObject @($ready,$script:kills,$script:disposes) -Compress
""".replace('EXITS', '$true' if exits else '$false').replace('VERSION',version))
    assert result == expected


def test_probe_selects_one_executable_when_both_docker_wrappers_exist(tmp_path):
    result = run_ps(tmp_path, r"""
function Get-Command { param($Name,$CommandType,$ErrorAction) return @([pscustomobject]@{Source='C:\Docker\docker.exe'},[pscustomobject]@{Source='C:\Docker\docker'}) }
$probe=New-DockerInfoProbe
try { $probe.StartInfo.FileName | ConvertTo-Json -Compress } finally { $probe.Dispose() }
""")
    assert result == r'C:\Docker\docker.exe'
