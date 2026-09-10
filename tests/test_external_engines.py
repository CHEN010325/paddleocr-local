import asyncio
import base64
import io
import json
import subprocess
import sys
from unittest.mock import AsyncMock, patch

import fitz
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

import ocr_engines as engines
import server


def config(**changes):
    return engines.EngineConfig(**{
        "id": "vision", "label": "Vision", "protocol": "openai",
        "url": "http://ocr.test/v1/chat/completions", "model": "vision-model", **changes,
    })


def png():
    output = io.BytesIO()
    Image.new("RGB", (16, 12), "white").save(output, format="PNG")
    return output.getvalue()


def pdf(count=2):
    with fitz.open() as document:
        for _ in range(count):
            document.new_page(width=72, height=72)
        return document.tobytes()


def transport(handler):
    original = httpx.AsyncClient
    return patch.object(engines.httpx, "AsyncClient", side_effect=lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))


def test_config_validation_and_secret_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_OCR_KEY", "secret-value")
    engine = config(apiKeyEnv="TEST_OCR_KEY")
    assert engine.headers() == {"Authorization": "Bearer secret-value"}
    assert "secret-value" not in json.dumps(engine.public())
    assert "ocr.test" not in json.dumps(engine.public())
    for changes in ({"url": "file:///tmp/foo"}, {"url": "http://user:pass@host"},
                    {"model": ""}, {"timeout": 0}, {"protocol": "unknown"},
                    {"url": "http://host:bad/ocr"}, {"url": "http://host:99999/ocr"}):
        with pytest.raises(ValidationError):
            config(**changes)
    path = tmp_path / "engines.json"
    path.write_text(json.dumps({"default": "vision", "engines": [engine.model_dump()]}))
    assert engines.load_engine_settings(str(path)).default == "vision"
    for payload in ({"default": "missing", "engines": [engine.model_dump()]},
                    {"default": "vision", "engines": [engine.model_dump()] * 2}):
        path.write_text(json.dumps(payload))
        with pytest.raises(ValidationError):
            engines.load_engine_settings(str(path))


def test_explicit_config_selects_api_mode_at_startup(tmp_path, monkeypatch):
    path = tmp_path / "engines.json"
    path.write_text(json.dumps({"default": "vision", "engines": [config().model_dump()]}))
    monkeypatch.setenv("PANDOCR_ENGINE_CONFIG", str(path))
    monkeypatch.setenv("PANDOCR_MODEL_CONTROL", "docker")
    result = subprocess.run([sys.executable, "-c", (
        "import server; assert server.MODEL_CONTROL_MODE == 'api'; "
        "assert not server.model_control_available(); "
        "assert server.model_catalog()[0]['id'] == 'vision'; "
        "assert server.app.openapi()['paths']['/api/parse']['post']['requestBody']"
        "['content']['application/json']['schema']['properties']['modelId']['enum'] == ['vision']"
    )], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_openai_image_contract(monkeypatch):
    monkeypatch.setenv("TEST_OCR_KEY", "secret-value")
    requests = []
    def handle(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == "vision-model"
        assert request.headers["Authorization"] == "Bearer secret-value"
        url = body["messages"][0]["content"][1]["image_url"]["url"]
        assert base64.b64decode(url.split(",")[1]).startswith(b"\x89PNG")
        return httpx.Response(200, json={"choices": [{"message": {"content": "# Recognized"}}]})
    with transport(handle):
        result = asyncio.run(engines.recognize(config(apiKeyEnv="TEST_OCR_KEY"), png(), 1, {}))
    assert result["markdown"] == "# Recognized"
    assert result["layoutParsingResults"][0]["width"] == 16
    assert len(requests) == 1


def test_openai_pdf_order_and_limits():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": f"page {len(calls)}"}}]})
    with transport(handle):
        result = asyncio.run(engines.recognize(config(), pdf(), 0, {}))
        assert result["markdown"] == "page 1\n\npage 2"
        assert [p["pageIndex"] for p in result["layoutParsingResults"]] == [0, 1]
        for engine, content, kind in [(config(maxPages=1), pdf(), 0),
                                      (config(maxPixels=1), pdf(), 0),
                                      (config(maxPixels=1), png(), 1)]:
            with pytest.raises(HTTPException) as error:
                asyncio.run(engines.recognize(engine, content, kind, {}))
            assert error.value.status_code == 413
    assert len(calls) == 2


@pytest.mark.parametrize("protocol", ["paddle", "unified"])
def test_document_protocol_normalizes_pages(protocol):
    def handle(request):
        body = json.loads(request.content)
        assert body["file" if protocol == "paddle" else "image"] == base64.b64encode(png()).decode()
        assert body["fileType"] == 1
        data = {"layoutParsingResults": [{"markdown": {"text": "hello", "images": {"a.png": "abc"}}}]}
        if protocol == "unified":
            data["images"] = {"top.png": "def"}
        return httpx.Response(200, json={"result": data} if protocol == "paddle" else data)
    with transport(handle):
        result = asyncio.run(engines.recognize(config(protocol=protocol), png(), 1, {}))
    assert result["markdown"] == "hello"
    assert result["images"]["a.png"] == "abc"
    if protocol == "unified":
        assert result["images"]["top.png"] == "def"


@pytest.mark.parametrize("response", [
    httpx.Response(401, text="secret-value"), httpx.Response(200, text="not JSON"),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, json={"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]}),
])
def test_upstream_errors_are_actionable_and_redacted(response):
    with transport(lambda request: response), pytest.raises(HTTPException) as error:
        asyncio.run(engines.recognize(config(), png(), 1, {}))
    assert error.value.status_code == 502
    assert "secret-value" not in error.value.detail


def test_timeout_and_invalid_input():
    def timeout(request):
        raise httpx.ReadTimeout("secret-value", request=request)
    with transport(timeout), pytest.raises(HTTPException) as error:
        asyncio.run(engines.recognize(config(), png(), 1, {}))
    assert error.value.status_code == 504
    with pytest.raises(HTTPException) as error:
        asyncio.run(engines.recognize(config(), b"invalid", 1, {}))
    assert error.value.status_code == 400


def test_external_catalog_parse_and_lifecycle_isolation(monkeypatch):
    settings = engines.EngineSettings(default="vision", engines=[config(), config(id="other")])
    monkeypatch.setattr(server, "ENGINE_SETTINGS", settings)
    monkeypatch.setattr(server, "EXTERNAL_ENGINES", {e.id: e for e in settings.engines})
    monkeypatch.setattr(server, "MODEL_CONTROL_MODE", "api")
    monkeypatch.setattr(server, "API_TOKEN", "")
    monkeypatch.setattr(server, "ocr_semaphore", asyncio.Semaphore(1))
    recognize = AsyncMock(return_value={"markdown": "ok", "images": {}, "layoutParsingResults": []})
    with patch.object(server, "recognize", recognize), \
         patch.object(server, "inspect_container", side_effect=AssertionError("Docker accessed")), \
         patch.object(server, "controller_api_request", side_effect=AssertionError("Controller accessed")):
        client = TestClient(server.app)
        catalog = client.get("/api/models").json()
        assert catalog["default"] == "vision"
        assert len(catalog["data"]) == 2
        assert all(item["managed"] is False for item in catalog["data"])
        runtime = client.get("/api/model-runtime").json()
        assert runtime["readyModelIds"] == ["vision", "other"]
        assert not runtime["exclusivityViolation"]
        assert not runtime["controlAvailable"]
        response = client.post("/api/parse", files={"file": ("test.png", png(), "image/png")}, data={"modelId": "other"})
        assert response.status_code == 200
        assert recognize.call_args.args[0].id == "other"
        response = client.post("/api/parse", json={"image": base64.b64encode(png()).decode()})
        assert response.status_code == 200
        assert recognize.call_args.args[0].id == "vision"
        assert client.post("/api/parse", json={"image": "YQ==", "modelId": "missing"}).status_code == 400
        for action in ("deploy", "switch"):
            assert client.post(f"/api/model-runtime/{action}", json={"modelId": "vision"}).status_code == 409
        recognize.side_effect = HTTPException(504, "timeout")
        assert client.post("/api/parse", json={"image": "YQ=="}).status_code == 504
        assert server.ocr_active_count == 0
        assert not server.ocr_semaphore.locked()


def test_health_is_optional_and_independent(monkeypatch):
    assert asyncio.run(engines.engine_status(config()))["state"] == "configured"
    with transport(lambda request: httpx.Response(503)):
        assert not asyncio.run(engines.engine_status(config(healthUrl="http://ocr.test/health")))["ready"]
    monkeypatch.delenv("MISSING_OCR_KEY", raising=False)
    assert not asyncio.run(engines.engine_status(config(apiKeyEnv="MISSING_OCR_KEY")))["ready"]


def test_engine_total_deadline_releases_slow_adapter(monkeypatch):
    cancelled = []
    async def slow(*args):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)
    monkeypatch.setitem(engines.ADAPTERS, "openai", slow)
    with pytest.raises(HTTPException) as error:
        asyncio.run(engines.recognize(config(timeout=0.01), png(), 1, {}))
    assert error.value.status_code == 504
    assert cancelled == [True]


@pytest.mark.parametrize("data", [
    [], {}, {"layoutParsingResults": [{"markdown": {"text": None}}]},
    {"layoutParsingResults": [{"markdown": {"text": "text", "images": []}}]},
    {"layoutParsingResults": [{"markdown": {"text": "text"}}], "images": []},
])
def test_malformed_document_responses(data):
    with transport(lambda request: httpx.Response(200, json=data)), pytest.raises(HTTPException) as error:
        asyncio.run(engines.recognize(config(protocol="unified"), png(), 1, {}))
    assert error.value.status_code == 502


def test_invalid_pdf_and_connection_failure():
    with fitz.open() as document:
        document.new_page()
        encrypted = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="password")
    for content, kind in [(b"invalid PDF", 0), (encrypted, 0), (png(), 2)]:
        with pytest.raises(HTTPException) as error:
            asyncio.run(engines.recognize(config(), content, kind, {}))
        assert error.value.status_code == 400
    def failed(request):
        raise httpx.ConnectError("private-address", request=request)
    with transport(failed), pytest.raises(HTTPException) as error:
        asyncio.run(engines.recognize(config(), png(), 1, {}))
    assert error.value.status_code == 502
    assert "private-address" not in error.value.detail


def test_parse_to_upstream_adapter_end_to_end(monkeypatch):
    settings = engines.EngineSettings(default="vision", engines=[config()])
    monkeypatch.setattr(server, "ENGINE_SETTINGS", settings)
    monkeypatch.setattr(server, "EXTERNAL_ENGINES", {"vision": settings.engines[0]})
    monkeypatch.setattr(server, "API_TOKEN", "")
    monkeypatch.setattr(server, "ocr_semaphore", asyncio.Semaphore(1))
    with transport(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "Recognized page"}}]})):
        client = TestClient(server.app)
        response = client.post("/api/parse", files={"file": ("document.pdf", pdf(), "application/pdf")})
        assert response.status_code == 200
        assert len(response.json()["layoutParsingResults"]) == 2
        assert response.json()["markdown"] == "Recognized page\n\nRecognized page"
        assert asyncio.run(server.model_runtime_status("vision"))["managed"] is False
        with pytest.raises(HTTPException):
            asyncio.run(server.model_runtime_status("missing"))
        with pytest.raises(HTTPException) as error:
            asyncio.run(server.acquire_ocr_slot("vision", "not ready"))
        assert error.value.status_code == 409
