import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import fitz
import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

import controller
import office_converter
import ovisocr2_adapter as ovis
import server
import unlimited_ocr_adapter as unlimited


def run(awaitable):
    return asyncio.run(awaitable)


def remote_payload():
    return {
        "models": {"paddleocr-vl-1.6": {"id": "paddleocr-vl-1.6", "ready": True, "running": True}},
        "operation": {"state": "ready"},
    }


def test_remote_controller_runtime_and_mutations():
    api = AsyncMock(return_value=remote_payload())
    with patch.object(server, "MODEL_CONTROL_MODE", "remote"), patch.object(
        server, "controller_api_request", api
    ), patch.object(server, "ocr_active_count", 0):
        status = run(server.model_runtime_status("paddleocr-vl-1.6"))
        assert status["ready"] is True
        payload = run(server.build_model_runtime_payload())
        assert payload["controlMode"] == "remote"
        run(server.switch_model_runtime(server.ModelSwitchRequest(modelId="paddleocr-vl-1.6")))
        run(server.deploy_model_runtime(server.ModelDeployRequest(modelId="paddleocr-vl-1.6")))
        with patch.object(server, "ENABLE_UNLIMITED_OCR", True):
            run(server.get_unlimited_ocr_backend())
            run(server.switch_unlimited_ocr_backend(server.UnlimitedOcrBackendRequest(backend="transformers")))
    assert api.await_count >= 8


def test_remote_controller_rejects_switch_while_ocr_runs():
    with patch.object(server, "MODEL_CONTROL_MODE", "remote"), patch.object(server, "ocr_active_count", 1):
        with pytest.raises(HTTPException) as error:
            run(server.switch_model_runtime(server.ModelSwitchRequest(modelId="pp-ocrv6")))
        assert error.value.status_code == 409
        with pytest.raises(HTTPException):
            run(server.deploy_model_runtime(server.ModelDeployRequest(modelId="pp-ocrv6")))
        with pytest.raises(HTTPException):
            run(server.switch_unlimited_ocr_backend(server.UnlimitedOcrBackendRequest(backend="sglang")))


def test_remote_ocr_slot_uses_controller_lease_for_full_request_lifetime():
    api = AsyncMock(
        side_effect=[
            {"leaseId": "lease-123", "modelId": "paddleocr-vl-1.6"},
            {"ok": True, "released": True},
        ]
    )

    async def scenario():
        lease_id = await server.acquire_ocr_slot(
            "paddleocr-vl-1.6",
            "not ready",
        )
        assert lease_id == "lease-123"
        assert server.ocr_active_count == 1
        await server.release_ocr_slot(lease_id)
        assert server.ocr_active_count == 0

    with (
        patch.object(server, "MODEL_CONTROL_MODE", "remote"),
        patch.object(server, "controller_api_request", api),
        patch.object(server, "model_runtime_lock", asyncio.Lock()),
        patch.object(server, "ocr_semaphore", asyncio.Semaphore(1)),
        patch.object(server, "ocr_active_count", 0),
    ):
        run(scenario())

    assert api.await_args_list[0].args == ("POST", "/ocr-leases/acquire")
    assert api.await_args_list[0].kwargs == {"json": {"modelId": "paddleocr-vl-1.6"}}
    assert api.await_args_list[1].args == ("DELETE", "/ocr-leases/lease-123")


def test_remote_ocr_slot_lease_error_and_fail_closed_release_branches():
    semaphore = asyncio.Semaphore(1)
    with (
        patch.object(server, "MODEL_CONTROL_MODE", "remote"),
        patch.object(server, "controller_api_request", AsyncMock(return_value={})),
        patch.object(server, "model_runtime_operation", {"state": "idle"}),
        patch.object(server, "model_runtime_lock", asyncio.Lock()),
        patch.object(server, "ocr_semaphore", semaphore),
        patch.object(server, "ocr_active_count", 0),
    ):
        with pytest.raises(HTTPException) as omitted:
            run(server.acquire_ocr_slot("paddleocr-vl-1.6", "not ready"))
        assert omitted.value.status_code == 502
        assert semaphore._value == 1

    class ExplodingCount:
        def __add__(self, _value):
            raise RuntimeError("counter update failed")

    api = AsyncMock(
        side_effect=[
            {"leaseId": "lease-cleanup"},
            {"ok": True, "released": True},
        ]
    )
    semaphore = asyncio.Semaphore(1)
    with (
        patch.object(server, "MODEL_CONTROL_MODE", "remote"),
        patch.object(server, "controller_api_request", api),
        patch.object(server, "model_runtime_operation", {"state": "idle"}),
        patch.object(server, "model_runtime_lock", asyncio.Lock()),
        patch.object(server, "ocr_semaphore", semaphore),
        patch.object(server, "ocr_active_count", ExplodingCount()),
    ):
        with pytest.raises(RuntimeError, match="counter update failed"):
            run(server.acquire_ocr_slot("paddleocr-vl-1.6", "not ready"))
        assert semaphore._value == 1
    assert api.await_args_list[1].args == ("DELETE", "/ocr-leases/lease-cleanup")

    semaphore = asyncio.Semaphore(0)
    with (
        patch.object(server, "MODEL_CONTROL_MODE", "remote"),
        patch.object(
            server,
            "controller_api_request",
            AsyncMock(side_effect=RuntimeError("controller unavailable")),
        ),
        patch.object(server, "model_runtime_lock", asyncio.Lock()),
        patch.object(server, "ocr_semaphore", semaphore),
        patch.object(server, "ocr_active_count", 1),
        patch.object(server.logger, "exception") as logged,
    ):
        run(server.release_ocr_slot("lease-stale"))
        assert server.ocr_active_count == 0
        assert semaphore._value == 1
    logged.assert_called_once()


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"%PDF-result", text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.text = text

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self, response, *args, **kwargs):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, *args, **kwargs):
        return self.response

    async def post(self, *args, **kwargs):
        return self.response


def test_controller_request_and_office_proxy():
    with patch.object(server, "MODEL_CONTROL_MODE", "remote"), patch.object(
        server, "MODEL_CONTROLLER_URL", "http://controller"
    ), patch.object(server.httpx, "AsyncClient", lambda *a, **k: FakeHttpClient(FakeResponse(payload={"ok": True}))):
        assert run(server.controller_api_request("GET", "/health")) == {"ok": True}

    upload = SimpleNamespace(filename="sample.docx", content_type="application/octet-stream")
    with patch.object(server, "OFFICE_CONVERTER_URL", "http://converter/convert"), patch.object(
        server, "read_upload_bytes", AsyncMock(return_value=b"doc")
    ), patch.object(server.httpx, "AsyncClient", lambda *a, **k: FakeHttpClient(FakeResponse())):
        response = run(server.convert_to_pdf(upload))
        assert response.media_type == "application/pdf"


def test_ovis_pdf_pages_are_processed_incrementally():
    document = fitz.open()
    document.new_page(width=100, height=100)
    document.new_page(width=100, height=100)
    pdf = document.tobytes()
    document.close()

    parser = SimpleNamespace(parse=lambda pages: ["page text"])
    with patch.object(ovis, "read_input", AsyncMock(return_value=(pdf, 0))), patch.object(
        ovis, "get_parser", AsyncMock(return_value=parser)
    ):
        result = run(ovis.ocr(object()))
    assert [item["pageIndex"] for item in result["layoutParsingResults"]] == [0, 1]
    assert result["markdown"] == "page text\n\n---\n\npage text"


def test_unlimited_pdf_pixel_budget_is_enforced():
    document = fitz.open()
    document.new_page(width=100, height=100)
    pdf = document.tobytes()
    document.close()
    with patch.object(unlimited, "MAX_RENDER_PIXELS", 1):
        with pytest.raises(HTTPException) as error:
            unlimited.pdf_to_png_pages(pdf, 300)
    assert error.value.status_code == 413


class FakeUpload:
    filename = "sample.docx"

    def __init__(self, chunks):
        self.chunks = iter(chunks)

    async def read(self, _size):
        return next(self.chunks, b"")


def test_office_converter_guards_and_controller_handlers(tmp_path):
    upload = FakeUpload([b"abc", b""])
    run(office_converter.write_upload(upload, tmp_path / "input.docx"))
    assert (tmp_path / "input.docx").read_bytes() == b"abc"

    with patch.object(office_converter.shutil, "which", return_value=None):
        with pytest.raises(HTTPException) as error:
            run(office_converter.convert(FakeUpload([])))
        assert error.value.status_code == 503

    with patch.object(controller.server, "model_control_available", return_value=True):
        assert run(controller.health())["controlAvailable"] is True
    with patch.object(controller.server, "build_model_runtime_payload", AsyncMock(return_value={"ok": True})):
        assert run(controller.model_runtime()) == {"ok": True}


def make_request(headers=None):
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/health", "headers": raw_headers})


def test_controller_auth_lifespan_and_remaining_handlers():
    next_handler = AsyncMock(return_value=JSONResponse({"ok": True}))
    with patch.object(controller, "CONTROLLER_TOKEN", ""):
        assert run(controller.authenticate_controller(make_request(), next_handler)).status_code == 503
    with patch.object(controller, "CONTROLLER_TOKEN", "change-this-to-a-random-long-value"):
        assert run(controller.authenticate_controller(make_request(), next_handler)).status_code == 503
    with patch.object(controller, "CONTROLLER_TOKEN", "secret"):
        assert run(controller.authenticate_controller(make_request(), next_handler)).status_code == 401
        response = run(
            controller.authenticate_controller(
                make_request({"x-pandocr-controller-token": "secret"}), next_handler
            )
        )
        assert response.status_code == 200

    async def exercise_lifespan():
        with patch.object(controller.server, "ensure_task_data_dir") as ensure, patch.object(
            controller.server, "model_control_available", return_value=True
        ), patch.object(controller.server, "schedule_model_runtime_activation", AsyncMock()) as activate:
            async with controller.lifespan(controller.app):
                pass
            ensure.assert_called_once()
            activate.assert_awaited_once()

    run(exercise_lifespan())
    with patch.object(controller.server, "schedule_model_runtime_activation", AsyncMock()) as activate, patch.object(
        controller.server, "build_model_runtime_payload", AsyncMock(return_value={"switched": True})
    ):
        assert run(controller.switch_model(server.ModelSwitchRequest(modelId="pp-ocrv6"))) == {"switched": True}
        activate.assert_awaited_once_with("pp-ocrv6")
    with patch.object(
        controller.server,
        "acquire_controller_ocr_lease",
        AsyncMock(return_value={"leaseId": "lease-123"}),
    ) as acquire:
        assert run(controller.acquire_ocr_lease(server.OCRLeaseRequest(modelId="pp-ocrv6"))) == {
            "leaseId": "lease-123"
        }
        acquire.assert_awaited_once_with("pp-ocrv6")
    with patch.object(
        controller.server,
        "release_controller_ocr_lease",
        AsyncMock(return_value=True),
    ) as release:
        assert run(controller.release_ocr_lease("lease-123")) == {"ok": True, "released": True}
        release.assert_awaited_once_with("lease-123")
    with patch.object(controller.server, "schedule_model_runtime_deploy", AsyncMock()) as deploy, patch.object(
        controller.server, "build_model_runtime_payload", AsyncMock(return_value={"deployed": True})
    ):
        assert run(controller.deploy_model(server.ModelDeployRequest(modelId="ovisocr2", backend="vllm"))) == {
            "deployed": True
        }
        deploy.assert_awaited_once_with("ovisocr2", "vllm")
    with patch.object(controller.server, "ENABLE_UNLIMITED_OCR", False):
        with pytest.raises(HTTPException) as error:
            run(controller.get_backend())
        assert error.value.status_code == 404
    with patch.object(controller.server, "schedule_unlimited_ocr_backend_activation", AsyncMock()), patch.object(
        controller.server, "build_model_runtime_payload", AsyncMock(return_value={"backend": "sglang"})
    ):
        assert run(controller.switch_backend(server.UnlimitedOcrBackendRequest(backend="sglang"))) == {
            "backend": "sglang"
        }


def test_office_converter_limit_health_and_conversion_paths(tmp_path):
    with patch.object(office_converter, "MAX_REQUEST_BYTES", 2):
        with pytest.raises(HTTPException) as error:
            run(office_converter.write_upload(FakeUpload([b"abc"]), tmp_path / "large.docx"))
        assert error.value.status_code == 413
    with patch.object(office_converter.shutil, "which", return_value="soffice"):
        assert run(office_converter.health())["soffice"] is True
        unsupported = FakeUpload([])
        unsupported.filename = "sample.txt"
        with pytest.raises(HTTPException) as error:
            run(office_converter.convert(unsupported))
        assert error.value.status_code == 400

        async def successful_run(*args, **kwargs):
            command = args[1]
            out_dir = Path(command[command.index("--outdir") + 1])
            (out_dir / "sample.pdf").write_bytes(b"%PDF-result")
            return SimpleNamespace(returncode=0)

        with patch.object(office_converter, "run_in_threadpool", successful_run):
            response = run(office_converter.convert(FakeUpload([b"doc", b""])))
            assert response.body == b"%PDF-result"

        async def failed_run(*args, **kwargs):
            return SimpleNamespace(returncode=1)

        with patch.object(office_converter, "run_in_threadpool", failed_run):
            with pytest.raises(HTTPException) as error:
                run(office_converter.convert(FakeUpload([b"doc", b""])))
            assert error.value.status_code == 500

        async def timed_out(*args, **kwargs):
            raise office_converter.subprocess.TimeoutExpired("soffice", 180)

        with patch.object(office_converter, "run_in_threadpool", timed_out):
            with pytest.raises(HTTPException) as error:
                run(office_converter.convert(FakeUpload([b"doc", b""])))
            assert error.value.status_code == 504
