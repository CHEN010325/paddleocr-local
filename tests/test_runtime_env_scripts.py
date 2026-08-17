import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOKEN_KEY = "PANDOCR_MODEL_CONTROLLER_TOKEN"
UNSAFE_TOKENS = {
    "",
    "pandocr-internal-controller-v1",
    "change-this-to-a-random-long-value",
    "请替换为随机长值",
}


def clean_environment(**updates: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop(TOKEN_KEY, None)
    environment.update(updates)
    return environment


def read_token(runtime_env: Path) -> str:
    entries = [
        line.split("=", 1)[1]
        for line in runtime_env.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{TOKEN_KEY}=")
    ]
    assert len(entries) == 1
    return entries[0]


def run_shell_helper(
    bash: str,
    base_env: Path,
    runtime_env: Path,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            bash,
            "scripts/prepare-runtime-env.sh",
            base_env.relative_to(ROOT).as_posix(),
            runtime_env.relative_to(ROOT).as_posix(),
        ],
        cwd=ROOT,
        env=environment or clean_environment(),
        text=True,
        capture_output=True,
        check=False,
    )


def run_powershell_helper(
    powershell: str,
    base_env: Path,
    runtime_env: Path,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        powershell,
        "-NoProfile",
    ]
    if Path(powershell).name.lower().startswith("powershell"):
        command.extend(["-ExecutionPolicy", "Bypass"])
    command.extend(
        [
            "-File",
            str(ROOT / "scripts" / "prepare-runtime-env.ps1"),
            "-BaseEnvFile",
            str(base_env),
            "-RuntimeEnvFile",
            str(runtime_env),
        ]
    )
    return subprocess.run(
        command,
        cwd=ROOT,
        env=environment or clean_environment(),
        text=True,
        capture_output=True,
        check=False,
    )


def assert_persistence_and_rejection(run_helper) -> None:
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as temp_dir:
        work = Path(temp_dir)
        base_env = work / "base.env"
        runtime_env = work / "pandocr-runtime.env"
        base_env.write_text(
            "PANDOCR_MODEL_CONTROLLER_TOKEN=change-this-to-a-random-long-value\n"
            "PANDOCR_ACTIVE_MODEL_ON_START=paddleocr-vl-1.6\n",
            encoding="utf-8",
        )

        first = run_helper(base_env, runtime_env)
        assert first.returncode == 0, first.stderr
        assert first.stdout.strip().replace("\\", "/").endswith(
            runtime_env.relative_to(ROOT).as_posix()
        )
        first_token = read_token(runtime_env)
        assert first_token not in UNSAFE_TOKENS
        assert len(first_token) >= 32
        assert runtime_env.read_text(encoding="utf-8").splitlines() == [
            f"{TOKEN_KEY}={first_token}"
        ]

        base_env.write_text(
            "PANDOCR_MODEL_CONTROLLER_TOKEN=\nPANDOCR_ACTIVE_MODEL_ON_START=pp-ocrv6\n",
            encoding="utf-8",
        )
        second = run_helper(base_env, runtime_env)
        assert second.returncode == 0, second.stderr
        assert read_token(runtime_env) == first_token

        mismatch = run_helper(
            base_env,
            runtime_env,
            environment=clean_environment(**{TOKEN_KEY: "different-valid-controller-token-value"}),
        )
        assert mismatch.returncode != 0
        assert "differs from the persisted runtime token" in mismatch.stderr
        assert read_token(runtime_env) == first_token

        runtime_env.unlink()
        for invalid in (
            "",
            "short-token",
            "$COMPOSE_INTERPOLATION_IS_NOT_A_TOKEN",
            "pandocr-internal-controller-v1",
            "change-this-to-a-random-long-value",
            "请替换为随机长值",
        ):
            rejected = run_helper(
                base_env,
                runtime_env,
                environment=clean_environment(**{TOKEN_KEY: invalid}),
            )
            assert rejected.returncode != 0
            assert not runtime_env.exists()

        for invalid in (
            "",
            "   ",
            "short-token",
            "$COMPOSE_INTERPOLATION_IS_NOT_A_TOKEN",
            "pandocr-internal-controller-v1",
            "change-this-to-a-random-long-value",
            "请替换为随机长值",
        ):
            runtime_env.write_text(f"{TOKEN_KEY}={invalid}\n", encoding="utf-8")
            invalid_runtime = run_helper(base_env, runtime_env)
            assert invalid_runtime.returncode != 0
            assert any(
                marker in invalid_runtime.stderr
                for marker in ("empty", "placeholder", "whitespace", "URL-safe")
            )


def test_linux_runtime_env_is_persistent_and_fails_closed():
    bash = shutil.which("bash")
    if os.name == "nt":
        git = shutil.which("git")
        git_bash = Path(git).parents[1] / "bin" / "bash.exe" if git else None
        if git_bash and git_bash.is_file():
            bash = str(git_bash)
    if not bash:
        pytest.skip("bash is not available")
    probe = subprocess.run([bash, "--version"], text=True, capture_output=True, check=False)
    if probe.returncode != 0:
        pytest.skip("a working bash runtime is not available")
    assert_persistence_and_rejection(
        lambda base, runtime, environment=None: run_shell_helper(
            bash, base, runtime, environment=environment
        )
    )


def test_windows_runtime_env_is_persistent_and_fails_closed():
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")
    assert_persistence_and_rejection(
        lambda base, runtime, environment=None: run_powershell_helper(
            powershell, base, runtime, environment=environment
        )
    )


def test_deploy_entrypoints_use_the_untracked_persistent_runtime_env():
    deploy_sh = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    deploy_bat = (ROOT / "deploy.bat").read_text(encoding="utf-8")
    build_sh = (ROOT / "build.sh").read_text(encoding="utf-8")
    build_bat = (ROOT / "build.bat").read_text(encoding="utf-8")
    test_connection_sh = (ROOT / "test-connection.sh").read_text(encoding="utf-8")
    test_connection_bat = (ROOT / "test-connection.bat").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    windows_one_click = (ROOT / "scripts/windows-one-click.ps1").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "tmp/" in gitignore
    assert "tmp/" in dockerignore
    assert "tmp/pandocr-runtime.env" in deploy_sh
    assert "prepare-runtime-env.sh" in deploy_sh
    assert '--env-file "${BASE_ENV}" --env-file "${RUNTIME_ENV}"' in deploy_sh
    assert "secrets.token_hex" not in deploy_sh

    assert "tmp\\pandocr-runtime.env" in deploy_bat
    assert "prepare-runtime-env.ps1" in deploy_bat
    assert '--env-file "%BASE_ENV%" --env-file "%RUNTIME_ENV%"' in deploy_bat
    assert "NewGuid" not in deploy_bat
    assert "prepare-runtime-env.sh" in build_sh
    assert '--env-file env.txt --env-file "$RUNTIME_ENV"' in build_sh
    assert "prepare-runtime-env.ps1" in build_bat
    assert '--env-file "%~dp0env.txt" --env-file "%RUNTIME_ENV%"' in build_bat
    assert "prepare-runtime-env.sh" in test_connection_sh
    assert '--env-file "$ENV_FILE" --env-file "$RUNTIME_ENV"' in test_connection_sh
    assert "prepare-runtime-env.ps1" in test_connection_bat
    assert '--env-file "%ENV_FILE%" --env-file "%RUNTIME_ENV%"' in test_connection_bat
    assert "--env-file env.txt --env-file $(RUNTIME_ENV)" in makefile
    assert "prepare-runtime-env.sh" in makefile
    assert "prepare-runtime-env.ps1" in windows_one_click
    assert 'Join-Path $tmpDir "pandocr-runtime.env"' in windows_one_click
    assert "$controllerToken = ([guid]::NewGuid" not in windows_one_click

    for relative_path in (
        "QUICKSTART.md",
        "DOCKER_DEPLOY.md",
        "OVISOCR2_DEPLOY.md",
        "PROJECT_SUMMARY.md",
        "RELEASING.md",
        "SUPPORT.md",
    ):
        documentation = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "prepare-runtime-env" in documentation
        assert "tmp/pandocr-runtime.env" in documentation or relative_path == "PROJECT_SUMMARY.md"
        assert "PANDOCR_MODEL_CONTROLLER_TOKEN = ([guid]::NewGuid" not in documentation

    for relative_path in ("QUICKSTART.md", "DOCKER_DEPLOY.md", "OVISOCR2_DEPLOY.md", "PROJECT_SUMMARY.md"):
        documentation = (ROOT / relative_path).read_text(encoding="utf-8")
        for line in documentation.splitlines():
            if "force-recreate" in line and "pandocr-web" in line:
                assert "pandocr-controller" in line, (
                    f"{relative_path} recreates Web without the controller and can split their tokens"
                )
