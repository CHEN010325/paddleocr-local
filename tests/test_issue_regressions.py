"""Regression contracts for public issue reports, independent of model weights."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import server

ROOT = Path(__file__).resolve().parents[1]


def test_low_vram_report_is_rejected_before_startup():
    status = server.gpu_compatibility([{"totalMiB": 8192, "freeMiB": 7800}])
    assert not status["models"]["paddleocr-vl-1.6"]["supported"]
    assert status["models"]["pp-ocrv6"]["availableNow"]
    busy = server.gpu_compatibility([{"totalMiB": 16384, "freeMiB": 1000}])
    assert busy["models"]["paddleocr-vl-1.6"]["supported"]
    assert not busy["models"]["paddleocr-vl-1.6"]["availableNow"]


def test_footnote_retention_reaches_pipeline_payload():
    request = server.OCRRequest(image="YQ==")
    assert server.build_pipeline_payload(request, "YQ==", 1)["markdownIgnoreLabels"] == []
    request.markdownIgnoreLabels = ["footnote", "aside_text"]
    assert server.build_pipeline_payload(request, "YQ==", 1)["markdownIgnoreLabels"] == ["footnote", "aside_text"]
    for name in ("pipeline_config_vllm.yaml", "pipeline_config_macos_mlx.template.yaml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        defaults = text.split("markdown_ignore_labels:", 1)[1].split("SubModules:", 1)[0]
        assert "footnote" not in defaults and "aside_text" not in defaults


def test_explicit_unlimited_catalog_is_exposed(tmp_path):
    env = {**os.environ, "PANDOCR_ENGINE_CONFIG": "", "PANDOCR_MODEL_CONTROL": "none",
           "PANDOCR_TASK_DATA_DIR": str(tmp_path), "PANDOCR_MODEL_CATALOG": "unlimited-ocr"}
    result = subprocess.run([sys.executable, "-c", (
        "import server; assert server.DEFAULT_RUNTIME_MODEL_ID == 'unlimited-ocr'; "
        "assert [m['id'] for m in server.model_catalog()] == ['unlimited-ocr']; "
        "assert server.ENABLE_UNLIMITED_OCR"
    )], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_custom_port_defaults_allow_local_uploads_but_reject_foreign_origins(tmp_path):
    env = {**os.environ, "PANDOCR_ENGINE_CONFIG": "", "PANDOCR_MODEL_CONTROL": "none",
           "PANDOCR_TASK_DATA_DIR": str(tmp_path), "PANDOCR_PORT": "18080"}
    env.pop("PANDOCR_CORS_ORIGINS", None)
    result = subprocess.run([sys.executable, "-c", (
        "import server; from fastapi.testclient import TestClient; "
        "c=TestClient(server.app); "
        "assert 'http://127.0.0.1:18080' in server.CORS_ORIGINS; "
        "assert c.post('/api/parse',headers={'Origin':'http://evil.test:18080'},json={}).status_code==403; "
        "assert c.post('/api/parse',headers={'Origin':'http://127.0.0.1:18080'},json={}).status_code!=403"
    )], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cpu_compose_has_no_gpu_or_controller_dependency():
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker CLI unavailable")
    result = subprocess.run([docker, "compose", "-f", str(ROOT / "docker-compose.cpu.yml"),
                             "config", "--format", "json"], capture_output=True, text=True, check=True)
    services = json.loads(result.stdout)["services"]
    assert set(services) == {"ocr", "web"}
    for service in services.values():
        assert not service.get("gpus") and not service.get("deploy")
        assert "docker.sock" not in json.dumps(service)
    env = services["web"]["environment"]
    assert env["PANDOCR_MODEL_CONTROL"] == "none"
    assert env["PANDOCR_MODEL_CATALOG"] == "pp-ocrv6"
    assert env["PADDLE_OCR_SERVICE_URL"] == "http://ocr:8080/ocr"


def test_default_docker_catalog_exposes_six_managed_models(tmp_path):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker CLI unavailable")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PANDOCR_")}
    env["PANDOCR_MODEL_CONTROLLER_TOKEN"] = "test-config-only"
    result = subprocess.run(
        [docker, "compose", "--env-file", str(ROOT / "env.docker"), "-f",
         str(ROOT / "docker-compose.yml"), "config", "--format", "json"],
        env=env, capture_output=True, text=True, check=True)
    services = json.loads(result.stdout)["services"]
    expected = ["paddleocr-vl-1.6", "pp-ocrv6", "unlimited-ocr", "ovisocr2", "hpd-parsing", "navidc-ocr"]
    for name in ("pandocr-web", "pandocr-controller"):
        settings = services[name]["environment"]
        assert settings["PANDOCR_MODEL_CATALOG"].split(",") == expected
        assert not settings.get("PANDOCR_ENGINE_CONFIG")
        runtime_env = {**env, **{k: v for k, v in settings.items() if v is not None},
                       "PANDOCR_TASK_DATA_DIR": str(tmp_path / name)}
        check = subprocess.run([sys.executable, "-c", (
            "import server; from fastapi.testclient import TestClient; "
            "assert server.ENGINE_SETTINGS is None; "
            "models=TestClient(server.app).get('/api/models').json()['data']; "
            f"assert [m['id'] for m in models] == {expected!r}; "
            "assert all(m.get('managed', True) for m in models)"
        )], env=runtime_env, capture_output=True, text=True)
        assert check.returncode == 0, check.stderr


def test_cpu_runtime_uses_http_without_gpu(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(server, "ENGINE_SETTINGS", None)
    monkeypatch.setattr(server, "MODEL_CONTROL_MODE", "none")
    monkeypatch.setattr(server, "fetch_http_health", AsyncMock(return_value=(True, {})))
    monkeypatch.setattr(server, "docker_api_request", AsyncMock(side_effect=AssertionError("Docker accessed")))
    status = asyncio.run(server.model_runtime_status("pp-ocrv6"))
    assert status["ready"] is True


def test_mlx_preflight_imports_server_entrypoint(monkeypatch):
    import importlib.util
    from unittest.mock import Mock
    spec = importlib.util.spec_from_file_location("mlx_check_regression", ROOT / "scripts/check-mlx-runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "required_versions", lambda: [])
    importer = Mock(side_effect=lambda name: (_ for _ in ()).throw(ImportError("server dependency"))
                    if name == "mlx_vlm.server" else None)
    monkeypatch.setattr(module.importlib, "import_module", importer)
    with pytest.raises(SystemExit):
        module.check_runtime()
    assert importer.call_args.args == ("mlx_vlm.server",)


def test_cancelled_readiness_probe_does_not_leak_ocr_capacity(monkeypatch):
    async def exercise():
        monkeypatch.setattr(server, "ENGINE_SETTINGS", None)
        monkeypatch.setattr(server, "MODEL_CONTROL_MODE", "none")
        monkeypatch.setattr(server, "model_runtime_lock", asyncio.Lock())
        monkeypatch.setattr(server, "ocr_semaphore", asyncio.Semaphore(1))
        monkeypatch.setattr(server, "model_runtime_operation", {"state": "idle"})
        entered = asyncio.Event()
        async def probe(*args):
            entered.set()
            await asyncio.sleep(60)
        monkeypatch.setattr(server, "model_runtime_status", probe)
        task = asyncio.create_task(server.acquire_ocr_slot("pp-ocrv6", "unavailable"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not server.ocr_semaphore.locked()
        assert not server.model_runtime_lock.locked()
    asyncio.run(exercise())
