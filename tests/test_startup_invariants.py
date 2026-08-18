import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_tracked_gitlinks_have_gitmodules_paths_and_urls():
    tracked = subprocess.run(
        ["git", "ls-files", "--stage"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.splitlines()
    gitlinks = {
        line.split(maxsplit=3)[3]
        for line in tracked
        if line.startswith("160000 ")
    }
    if not gitlinks:
        return

    gitmodules = ROOT / ".gitmodules"
    assert gitmodules.is_file(), f"tracked gitlinks lack .gitmodules: {sorted(gitlinks)}"
    configured_paths = set()
    for block in re.findall(
        r'^\[submodule "[^"]+"\]\s*\n(?P<body>.*?)(?=^\[|\Z)',
        gitmodules.read_text(encoding="utf-8"),
        re.MULTILINE | re.DOTALL,
    ):
        path = re.search(r"^\s*path\s*=\s*(.+?)\s*$", block, re.MULTILINE)
        url = re.search(r"^\s*url\s*=\s*(.+?)\s*$", block, re.MULTILINE)
        if path:
            assert url and url.group(1).strip(), f"submodule path lacks a URL: {path.group(1)}"
            configured_paths.add(path.group(1))
    assert gitlinks <= configured_paths, (
        f"tracked gitlinks lack .gitmodules path entries: {sorted(gitlinks - configured_paths)}"
    )


def compose_service_block(compose: str, service: str) -> str:
    match = re.search(
        rf"^  {re.escape(service)}:\s*\n(?P<body>.*?)(?=^  [A-Za-z0-9][A-Za-z0-9_-]*:\s*\n|\Z)",
        compose,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"missing Compose service: {service}"
    return match.group("body")


def test_every_model_service_requires_its_logical_model_profile():
    compose = read("docker-compose.yml")
    expected_profiles = {
        "paddleocr-vlm-server": "paddleocr-vl",
        "paddleocr-vl-api": "paddleocr-vl",
        "paddleocr-ocr-api": "pp-ocrv6",
        "unlimited-ocr-sglang": "unlimited-ocr",
        "unlimited-ocr-api": "unlimited-ocr",
        "ovisocr2-api": "ovisocr2",
        "hpd-parsing-server": "hpd-parsing",
        "hpd-parsing-api": "hpd-parsing",
    }
    for service, profile in expected_profiles.items():
        block = compose_service_block(compose, service)
        assert f'profiles: ["{profile}"]' in block


def test_windows_one_click_profiles_and_wait_guard_cover_all_five_models():
    script = read("scripts/windows-one-click.ps1")
    for profile in ("paddleocr-vl", "pp-ocrv6", "unlimited-ocr", "ovisocr2", "hpd-parsing"):
        assert f'@("--profile", "{profile}")' in script
    for model_id in ("paddleocr-vl-1.6", "pp-ocrv6", "unlimited-ocr", "ovisocr2", "hpd-parsing"):
        assert f'$running.Add("{model_id}")' in script
    assert "$runningLogicalModels.Count -gt 1" in script
    assert "$runningLogicalModels.Count -ne 1" in script
    assert "$runningLogicalModels[0] -ne $script:ActiveModel" in script
    assert 'Single-model invariant violated.' in script
    assert '$createArguments += "--no-build"' in script


def test_windows_one_click_uses_per_model_gpu_requirements():
    script = read("scripts/windows-one-click.ps1")
    expected = {
        "paddleocr-vl-1.6": (11264, 6656),
        "pp-ocrv6": (4096, 4096),
        "unlimited-ocr": (7680, 6656),
        "ovisocr2": (7680, 6656),
        "hpd-parsing": (7680, 6656),
    }
    for model_id, (total_mib, free_mib) in expected.items():
        requirement = (
            rf'"{re.escape(model_id)}"\s*\{{\s*return \[pscustomobject\]@\{{\s*'
            rf'TotalMiB = {total_mib}; FreeMiB = {free_mib}\s*\}}\s*\}}'
        )
        assert re.search(requirement, script)
    assert "Get-DeploymentGpuRequirement -ModelIds $script:DeployModelIds" in script
    assert "$gpu.TotalMiB -lt $gpuRequirement.TotalMiB" in script
    assert "$gpu.FreeMiB -lt $gpuRequirement.FreeMiB" in script
    assert "$gpu.TotalMiB -lt 8192" not in script
    assert "$gpu.FreeMiB -lt 6656" not in script


def test_windows_local_build_embeds_non_secret_version_metadata():
    compose = read("docker-compose.yml")
    web = compose_service_block(compose, "pandocr-web")
    controller = compose_service_block(compose, "pandocr-controller")
    dockerfile = read("Dockerfile")
    release_workflow = read(".github/workflows/release.yml")
    script = read("scripts/windows-one-click.ps1")
    for service_block in (web, controller):
        assert "PANDOCR_APP_VERSION: ${PANDOCR_APP_VERSION:-0.2.0}" in service_block
        assert "PANDOCR_GIT_COMMIT: ${PANDOCR_GIT_COMMIT:-}" in service_block
    assert 'rev-parse --short HEAD' in script
    assert 'Set-EnvLine -Lines $lines -Key "PANDOCR_GIT_COMMIT"' in script
    assert "ARG PANDOCR_APP_VERSION=0.2.0" in dockerfile
    assert "ARG PANDOCR_GIT_COMMIT=" in dockerfile
    assert "PANDOCR_APP_VERSION=${{ github.ref_name }}" in release_workflow
    assert "PANDOCR_GIT_COMMIT=${{ github.sha }}" in release_workflow
    build_section = web.split("    image:", 1)[0]
    assert "PANDOCR_MODEL_CONTROLLER_TOKEN" not in build_section


def test_browser_entrypoint_revalidates_code_and_escapes_legacy_cache_keys():
    index = read("static/index.html")
    bootstrap = read("static/bootstrap.mjs")
    server = read("server.py")

    bootstrap_version = re.search(r"/static/bootstrap\.mjs\?v=(\d+)", index)
    app_version = re.search(r"\./app\.js\?v=(\d+)", bootstrap)
    assert bootstrap_version and int(bootstrap_version.group(1)) >= 3
    assert app_version and int(app_version.group(1)) >= 101
    for asset_path in (
        "/static/app.js",
        "/static/bootstrap.mjs",
        "/static/i18n.js",
        "/static/index.html",
        "/static/style.css",
    ):
        assert f'"{asset_path}"' in server
    assert 'response.headers["Cache-Control"] = "no-cache, must-revalidate"' in server


def test_controller_healthcheck_expands_its_internal_token():
    compose = read("docker-compose.yml")
    controller = compose_service_block(compose, "pandocr-controller")
    assert "-H X-Pandocr-Controller-Token:$${PANDOCR_MODEL_CONTROLLER_TOKEN}" in controller
    assert "'X-Pandocr-Controller-Token: $${PANDOCR_MODEL_CONTROLLER_TOKEN}'" not in controller


def test_only_controller_persists_ocr_leases_in_shared_data():
    compose = read("docker-compose.yml")
    controller = compose_service_block(compose, "pandocr-controller")
    web = compose_service_block(compose, "pandocr-web")
    assert "PANDOCR_CONTROLLER_OCR_LEASE_STORE_ENABLED=1" in controller
    assert "PANDOCR_CONTROLLER_OCR_LEASE_STORE_FILE=/app/data/controller-ocr-leases.json" in controller
    assert "PANDOCR_CONTROLLER_OCR_LEASE_TTL_SECONDS=${PANDOCR_CONTROLLER_OCR_LEASE_TTL_SECONDS:-0}" in controller
    assert "PANDOCR_CONTROLLER_OCR_LEASE_STORE_ENABLED=1" not in web
    assert "PANDOCR_CONTROLLER_OCR_LEASE_STORE_FILE=" not in web
    assert "PANDOCR_CONTROLLER_OCR_LEASE_TTL_SECONDS=" not in web
    assert "./data:/app/data" in controller


def test_controller_and_web_pin_the_same_hpd_runtime_image():
    compose = read("docker-compose.yml")
    server = read("server.py")
    controller = compose_service_block(compose, "pandocr-controller")
    web = compose_service_block(compose, "pandocr-web")
    pinned_image = (
        "HPD_PARSING_IMAGE=${HPD_PARSING_IMAGE:-"
        "ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/"
        "hpd-parsing-vllm:latest-nvidia-gpu@"
        "sha256:87496aa5dd702a7df6c70bb00c29d5bf8d1a0f0505d66b613fafa6f71cd72de2}"
    )
    assert pinned_image in controller
    assert pinned_image in web
    assert "sha256:87496aa5dd702a7df6c70bb00c29d5bf8d1a0f0505d66b613fafa6f71cd72de2" in server


def test_unlimited_ocr_sglang_wheel_uses_immutable_verified_upstream_artifact():
    dockerfile = read("Dockerfile.unlimited-ocr-sglang")
    compose = read("docker-compose.yml")
    server = read("server.py")
    for content in (dockerfile, compose, server):
        assert "huggingface.co/baidu/Unlimited-OCR/resolve/07dea832e22aefee32ad281d4b80551282e1c168/" in content
        assert "#sha256=2644a1f349c55f0ca822e70a70679c98475754ec4722c3be1b18a72bac477cd5" in content
        assert "/resolve/main/" not in content


def test_unlimited_ocr_sglang_uses_digest_pinned_cuda_12_9_for_sm120():
    dockerfile = read("Dockerfile.unlimited-ocr-sglang")
    compose = read("docker-compose.yml")
    expected_image = (
        "nvidia/cuda:12.9.1-cudnn-devel-ubuntu24.04@"
        "sha256:a2e1e2360c85298ac47ec2543b406ab1e8cec42e31ee47e4d32140ebc82e1067"
    )
    assert expected_image in dockerfile
    assert expected_image in compose
    assert "nvidia/cuda:12.6.3-cudnn-devel-ubuntu24.04" not in dockerfile
    assert "nvidia/cuda:12.6.3-cudnn-devel-ubuntu24.04" not in compose


def test_macos_launcher_requires_exactly_one_model():
    script = read("scripts/start-macos.sh")
    stop_script = read("scripts/stop-macos.sh")
    ovis_script = read("scripts/start-macos-ovisocr2.sh")
    ovis_one_click = read("macos-ovisocr2-one-click.command")
    test_script = read("tests/test_macos_model_selection.sh")
    assert 'PANDOCR_ENABLE_PADDLEOCR_VL="${PANDOCR_ENABLE_PADDLEOCR_VL:-0}"' in script
    assert 'PANDOCR_ENABLE_PPOCRV6="${PANDOCR_ENABLE_PPOCRV6:-0}"' in script
    assert "ENABLED_MODEL_COUNT != 1" in script
    assert 'PANDOCR_MODEL_CATALOG="${PANDOCR_MODEL_CATALOG:-$ENABLED_MODEL_ID}"' in script
    assert 'PANDOCR_ENABLE_HPD_PARSING="${PANDOCR_ENABLE_HPD_PARSING:-0}"' in script
    assert "PANDOCR_MODEL_SELECTION_CHECK_ONLY" in script
    assert "has_running_non_target_model" in script
    assert "A non-selected macOS model is still running" in script
    assert "did not stop; refusing to start another model" in stop_script
    assert "export PANDOCR_ENABLE_PADDLEOCR_VL=0" in ovis_script
    assert "export PANDOCR_ENABLE_PPOCRV6=0" in ovis_script
    assert "export PANDOCR_ENABLE_UNLIMITED_OCR=0" in ovis_script
    assert "export PANDOCR_ENABLE_OVISOCR2=1" in ovis_script
    assert "export PANDOCR_ACTIVE_MODEL_ON_START=ovisocr2" in ovis_script
    assert "export PANDOCR_MODEL_CATALOG=ovisocr2" in ovis_script
    assert "exec bash scripts/start-macos.sh" in ovis_script
    assert "uvicorn" not in ovis_script
    assert "server.py" not in ovis_script
    assert "bash scripts/start-macos-ovisocr2.sh" in ovis_one_click
    assert "unexpectedly accepted two enabled logical models" in test_script
    assert "unexpectedly accepted zero enabled logical models" in test_script
    assert "legacy_ovis_output" in test_script


def test_documented_model_deployments_never_use_bare_compose_up():
    files = (
        "README.md",
        "README.en.md",
        "QUICKSTART.md",
        "DOCKER_DEPLOY.md",
        "OVISOCR2_DEPLOY.md",
        "RELEASING.md",
        "build.sh",
        "build.bat",
        "deploy.sh",
        "deploy.bat",
        "test-connection.sh",
        "test-connection.bat",
        "Makefile",
    )
    unsafe_patterns = (
        re.compile(r"docker compose(?: --env-file \S+)? up -d --no-start\s*$", re.MULTILINE),
        re.compile(r"docker compose(?: --env-file \S+)? up -d --build(?:\s|$)"),
    )
    for relative_path in files:
        content = read(relative_path)
        for pattern in unsafe_patterns:
            assert not pattern.search(content), f"unsafe bare Compose startup in {relative_path}"

    makefile = read("Makefile")
    assert "$(COMPOSE_CORE) up -d --no-start --force-recreate $(CORE_SERVICES)" in makefile

    assert 'COMPOSE=(docker compose --env-file "$ENV_FILE" --env-file "$RUNTIME_ENV" --profile "*")' in read("test-connection.sh")
    assert 'set "COMPOSE=docker compose --env-file "%ENV_FILE%" --env-file "%RUNTIME_ENV%" --profile "*""' in read("test-connection.bat")


def test_makefile_controller_token_is_persistent_and_fails_closed():
    makefile = read("Makefile")
    assert "RUNTIME_ENV ?= tmp/pandocr-runtime.env" in makefile
    assert "--env-file env.txt --env-file $(RUNTIME_ENV)" in makefile
    assert "bash scripts/prepare-runtime-env.sh env.txt $(RUNTIME_ENV)" in makefile
    assert "secrets.token_hex" not in makefile
    assert re.search(r"^up:\s+check-controller-token\s*$", makefile, re.MULTILINE)
    assert re.search(r"^build:\s+check-controller-token\s*$", makefile, re.MULTILINE)
