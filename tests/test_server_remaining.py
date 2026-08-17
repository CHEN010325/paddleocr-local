import asyncio
import base64
import importlib
import io
import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect


def response(status=200, *, payload=None, text=None):
    request = httpx.Request("POST", "http://upstream/test")
    if payload is not None:
        return httpx.Response(status, json=payload, request=request)
    return httpx.Response(status, text=text or "", request=request)


class FakeClient:
    def __init__(self, post_response=None, stream_response=None):
        self.post_response = post_response or response()
        self.stream_response = stream_response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, *args, **kwargs):
        return self.post_response

    def stream(self, *args, **kwargs):
        return self.stream_response


class FakeStream:
    def __init__(self, status=200, lines=(), body=b""):
        self.status_code = status
        self.lines = lines
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def aread(self):
        return self.body

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class NumpyLike:
    def __init__(self, value):
        self.value = value

    def tolist(self):
        return self.value


class ServerRemainingTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.original_task_dir = cls.server.TASK_DATA_DIR
        cls.server.TASK_DATA_DIR = Path(cls.temp_dir.name).resolve()
        cls.client = TestClient(cls.server.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.server.TASK_DATA_DIR = cls.original_task_dir
        cls.temp_dir.cleanup()

    async def asyncSetUp(self):
        self.server.model_runtime_lock = asyncio.Lock()
        self.server.ocr_semaphore = asyncio.Semaphore(1)
        self.server.ocr_active_count = 0
        self.server.set_model_runtime_operation("idle", "", self.server.DEFAULT_RUNTIME_MODEL_ID)

    async def test_config_parsing_and_runtime_settings_error_paths(self):
        with patch.dict(os.environ, {"COUNT": "bad"}):
            self.assertEqual(self.server.parse_positive_int_env("COUNT", "2"), 2)
        with patch.dict(os.environ, {"COUNT": "0"}):
            self.assertEqual(self.server.parse_positive_int_env("COUNT", "2"), 1)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("[]", encoding="utf-8")
            with patch.object(self.server, "RUNTIME_SETTINGS_FILE", path):
                self.assertEqual(self.server.load_runtime_settings(), {})
            path.write_text("invalid", encoding="utf-8")
            with (
                patch.object(self.server, "RUNTIME_SETTINGS_FILE", path),
                patch.object(self.server.logger, "warning") as warning,
            ):
                self.assertEqual(self.server.load_runtime_settings(), {})
            warning.assert_called_once()
            path.write_text("{}", encoding="utf-8")
            with (
                patch.object(self.server, "RUNTIME_SETTINGS_FILE", path),
                patch.object(Path, "write_text", side_effect=OSError("read only")),
                patch.object(self.server.logger, "warning") as warning,
            ):
                self.server.save_runtime_settings({"x": 1})
            warning.assert_called_once()

        with (
            patch.object(self.server, "MODEL_CATALOG_ENV", ""),
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(self.server, "ENABLE_OVISOCR2", True),
            patch.object(self.server, "ENABLE_HPD_PARSING", True),
        ):
            self.assertEqual(
                self.server.parse_model_catalog(),
                ["paddleocr-vl-1.6", "pp-ocrv6", "unlimited-ocr", "ovisocr2", "hpd-parsing"],
            )
        with (
            patch.object(self.server, "MODEL_CATALOG_ENV", "invalid"),
            patch.dict(os.environ, {"PANDOCR_MODEL_CATALOG": "invalid"}),
        ):
            self.assertEqual(self.server.parse_model_catalog(), ["paddleocr-vl-1.6"])

    async def test_remaining_runtime_branches(self):
        with patch.object(self.server, "docker_image_name_for", return_value="image"):
            with self.assertRaisesRegex(ValueError, "Unknown deploy service"):
                self.server.container_payload_for(
                    "unknown",
                    host_root="/host",
                    network_name="network",
                )

        for waiter, ready_value, pending_value, args in (
            (
                "wait_model_ready",
                {"ready": True},
                {"ready": False},
                ("model", 10),
            ),
            (
                "wait_unlimited_ocr_backend_ready",
                {"ready": True, "unlimitedOcrBackend": "sglang"},
                {"ready": False, "unlimitedOcrBackend": "sglang"},
                ("sglang", 10),
            ),
        ):
            target = (
                "model_runtime_status"
                if waiter in {"wait_model_ready", "wait_unlimited_ocr_backend_ready"}
                else "fetch_http_health"
            )
            with (
                patch.object(
                    self.server,
                    target,
                    new=AsyncMock(side_effect=[pending_value, ready_value]),
                ),
                patch.object(self.server.asyncio, "sleep", new=AsyncMock()) as sleep,
            ):
                await getattr(self.server, waiter)(*args)
            sleep.assert_awaited_once()

        with (
            patch.object(
                self.server,
                "inspect_container",
                new=AsyncMock(
                    side_effect=[
                        {"exists": True, "running": False, "health": "starting"},
                        {"exists": True, "running": True, "health": "none"},
                    ]
                ),
            ),
            patch.object(self.server.asyncio, "sleep", new=AsyncMock()) as sleep,
        ):
            await self.server.wait_container_runtime_ready("worker", 10)
        sleep.assert_awaited_once()

        with (
            patch.object(
                self.server,
                "fetch_http_health",
                new=AsyncMock(side_effect=[(False, {}), (True, {})]),
            ),
            patch.object(self.server.asyncio, "sleep", new=AsyncMock()) as sleep,
        ):
            await self.server.wait_unlimited_ocr_adapter_http(10)
        sleep.assert_awaited_once()

        with (
            patch.object(
                self.server,
                "model_runtime_status",
                new=AsyncMock(return_value={"running": False}),
            ),
            patch.object(self.server, "save_runtime_settings") as save,
            patch.object(self.server, "unlimited_ocr_runtime_backend", "transformers"),
        ):
            await self.server.activate_unlimited_ocr_backend("sglang")
        save.assert_called_once_with({"unlimitedOcrBackend": "sglang"})

    async def test_origin_auth_and_root_endpoint_branches(self):
        self.assertEqual(self.server.normalize_origin("not-an-origin"), "")
        self.assertEqual(self.server.normalize_origin("http://[bad"), "")
        self.assertEqual(
            self.server.normalize_origin("HTTPS://Example.COM/path"),
            "https://example.com",
        )

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("client", 123),
            "query_string": b"",
        }
        from starlette.requests import Request

        request = Request(scope)
        with patch.object(self.server, "ENFORCE_ORIGIN_CHECK", False):
            self.assertTrue(self.server.request_origin_is_allowed(request))
        scope["path"] = "/static/app.js"
        self.assertTrue(self.server.request_origin_is_allowed(Request(scope)))
        scope["path"] = "/api/test"
        self.assertTrue(self.server.request_origin_is_allowed(Request(scope)))
        scope["headers"] = [(b"origin", b"https://evil.example")]
        with patch.object(self.server, "CORS_ORIGINS", ["*"]):
            self.assertTrue(self.server.request_origin_is_allowed(Request(scope)))

        with patch.object(self.server, "API_TOKEN", "secret"):
            self.assertFalse(self.server.request_is_authenticated(Request(scope)))

        root_response = self.client.get("/")
        self.assertEqual(root_response.status_code, 200)
        self.assertIn("no-cache", root_response.headers["cache-control"])
        for asset_path in self.server.BROWSER_CODE_ASSET_PATHS:
            asset_response = self.client.get(f"{asset_path}?cache-contract=1")
            self.assertEqual(asset_response.status_code, 200, asset_path)
            self.assertEqual(
                asset_response.headers["cache-control"],
                "no-cache, must-revalidate",
                asset_path,
            )
        bootstrap_response = self.client.get("/static/bootstrap.mjs?v=3")
        conditional_response = self.client.get(
            "/static/bootstrap.mjs?v=3",
            headers={"If-None-Match": bootstrap_response.headers["etag"]},
        )
        self.assertEqual(conditional_response.status_code, 304)
        self.assertEqual(
            conditional_response.headers["cache-control"],
            "no-cache, must-revalidate",
        )
        invalid_length = self.client.post(
            "/api/paddleocr-vl-1.6",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "not-a-number",
            },
        )
        self.assertEqual(invalid_length.status_code, 400)

    async def test_runtime_api_endpoints_delegate_and_validate(self):
        with (
            patch.object(
                self.server,
                "schedule_model_runtime_activation",
                new=AsyncMock(),
            ) as schedule,
            patch.object(
                self.server,
                "build_model_runtime_payload",
                new=AsyncMock(return_value={"ok": True}),
            ),
        ):
            self.assertEqual(
                await self.server.switch_model_runtime(
                    self.server.ModelSwitchRequest(modelId="pp-ocrv6")
                ),
                {"ok": True},
            )
        schedule.assert_awaited_once_with("pp-ocrv6")

        with (
            patch.object(
                self.server,
                "schedule_model_runtime_deploy",
                new=AsyncMock(),
            ) as schedule,
            patch.object(
                self.server,
                "build_model_runtime_payload",
                new=AsyncMock(return_value={"ok": True}),
            ),
        ):
            await self.server.deploy_model_runtime(
                self.server.ModelDeployRequest(modelId="unlimited-ocr", backend="sglang")
            )
        schedule.assert_awaited_once_with("unlimited-ocr", "sglang")

        with patch.object(self.server, "ENABLE_UNLIMITED_OCR", False):
            with self.assertRaises(HTTPException):
                await self.server.get_unlimited_ocr_backend()
        with (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(
                self.server,
                "model_runtime_status",
                new=AsyncMock(return_value={"ready": True}),
            ),
        ):
            payload = await self.server.get_unlimited_ocr_backend()
        self.assertTrue(payload["runtime"]["ready"])

        with (
            patch.object(
                self.server,
                "schedule_unlimited_ocr_backend_activation",
                new=AsyncMock(),
            ) as schedule,
            patch.object(
                self.server,
                "build_model_runtime_payload",
                new=AsyncMock(return_value={"ok": True}),
            ),
        ):
            await self.server.switch_unlimited_ocr_backend(
                self.server.UnlimitedOcrBackendRequest(backend="sglang")
            )
        schedule.assert_awaited_once_with("sglang")

    async def test_task_storage_error_and_compaction_branches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(self.server, "TASK_DATA_DIR", root):
                task_id = "task_storage"
                task_dir = root / task_id
                task_dir.mkdir()
                (task_dir / "source.bin").write_bytes(b"source")
                stored, result = self.server.split_task_for_storage(
                    {
                        "id": task_id,
                        "batches": [
                            "invalid",
                            {"id": "", "markdown": "discard"},
                            {"id": "b1", "payloadBlob": "x", "markdown": "keep"},
                        ],
                    }
                )
                self.assertEqual(stored["sourceUrl"], f"/api/tasks/{task_id}/source")
                self.assertEqual(result["batchMarkdown"], {"b1": "keep"})

                (task_dir / "task.json").write_text("invalid", encoding="utf-8")
                stored, result = self.server.split_task_for_storage(
                    {"id": task_id, "_preserveResult": True, "batches": []}
                )
                self.assertIsNone(result)

                bad_json = root / "bad.json"
                bad_json.write_text("[]", encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.server.read_json_file(bad_json)

                result_path = task_dir / self.server.TASK_RESULT_FILE
                result_path.write_text('{"markdown":"old"}', encoding="utf-8")
                self.server.write_task_bundle(task_id, {"id": task_id, "batches": []})
                self.assertFalse(result_path.exists())

                result_path.write_text("invalid", encoding="utf-8")
                with patch.object(self.server.logger, "warning") as warning:
                    hydrated = self.server.hydrate_task_detail(task_id, {})
                warning.assert_called_once()
                self.assertEqual(hydrated["markdown"], "")

                self.assertFalse(self.server.task_needs_compaction({"batches": []}))
                self.assertTrue(
                    self.server.task_needs_compaction(
                        {"batches": [{"payloadBlob": "blob"}]}
                    )
                )
                self.assertEqual(self.server.task_sort_timestamp({"updatedAt": " "}), 0)
                self.assertEqual(self.server.task_sort_timestamp({"updatedAt": "bad"}), 0)
                self.assertEqual(self.server.task_sort_timestamp({"updatedAt": {}}), 0)

                invalid_dir = root / "task_invalid"
                invalid_dir.mkdir()
                (invalid_dir / "task.json").write_text("invalid", encoding="utf-8")
                with patch.object(self.server.logger, "warning") as warning:
                    self.server.list_task_summaries()
                warning.assert_called()

                removable = root / "task_remove"
                removable.mkdir()
                (removable / "file").write_text("x", encoding="utf-8")
                self.server.remove_task_dir("task_remove")
                self.assertFalse(removable.exists())

    async def test_upload_reading_and_pdf_empty_document(self):
        upload = UploadFile(file=io.BytesIO(b"hello"), filename="x")
        self.assertEqual(await self.server.read_upload_bytes(upload), b"hello")
        upload = UploadFile(file=io.BytesIO(b"hello"), filename="x")
        with self.assertRaises(HTTPException):
            await self.server.read_upload_bytes(upload, max_bytes=2)

        with tempfile.TemporaryDirectory() as directory:
            empty_pdf = Path(directory) / "empty.pdf"
            writer = PdfWriter()
            with empty_pdf.open("wb") as stream:
                writer.write(stream)
            with self.assertRaisesRegex(ValueError, "no pages"):
                self.server.extract_pdf_pages(empty_pdf, 1, 1)

    async def test_task_source_metadata_invalid_metadata_and_server_error(self):
        task_id = "task_source_meta"
        task_dir = self.server.TASK_DATA_DIR / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "source.bin").write_bytes(b"source-data")
        (task_dir / "task.json").write_text(
            json.dumps({"id": task_id, "mimeType": "text/plain", "name": "doc.txt"}),
            encoding="utf-8",
        )
        response_value = await self.server.get_task_source(task_id)
        self.assertEqual(response_value.media_type, "text/plain")

        (task_dir / "task.json").write_text("invalid", encoding="utf-8")
        fallback = await self.server.get_task_source(task_id)
        self.assertEqual(fallback.media_type, "application/octet-stream")

        with patch.object(
            self.server,
            "extract_pdf_pages",
            side_effect=RuntimeError("reader crashed"),
        ), patch.object(self.server.logger, "exception"):
            with self.assertRaises(HTTPException) as raised:
                await self.server.get_task_source_pages(task_id, 1, 1)
        self.assertEqual(raised.exception.status_code, 500)

        with patch.object(
            self.server,
            "read_task_file",
            side_effect=OSError("read failed"),
        ):
            with self.assertRaises(HTTPException) as raised:
                await self.server.get_task(task_id)
        self.assertEqual(raised.exception.status_code, 500)

        await self.server.delete_task(task_id)
        self.assertFalse(task_dir.exists())

    async def test_conversion_success_and_all_runtime_failures(self):
        async def perform(run_side_effect, create_pdf=False):
            upload = UploadFile(file=io.BytesIO(b"doc"), filename="sample.docx")

            def runner(command, **kwargs):
                if isinstance(run_side_effect, Exception):
                    raise run_side_effect
                if create_pdf:
                    Path(command[command.index("--outdir") + 1], "sample.pdf").write_bytes(
                        b"%PDF-result"
                    )
                return run_side_effect

            with (
                patch.object(self.server.shutil, "which", return_value="/usr/bin/soffice"),
                patch.object(self.server.subprocess, "run", side_effect=runner),
            ):
                return await self.server.convert_to_pdf(upload)

        success = await perform(SimpleNamespace(returncode=0, stderr=""), create_pdf=True)
        self.assertEqual(success.body, b"%PDF-result")

        with self.assertRaises(HTTPException) as failed:
            await perform(SimpleNamespace(returncode=1, stderr="conversion failed"))
        self.assertIn("conversion failed", failed.exception.detail)

        with self.assertRaises(HTTPException) as no_pdf:
            await perform(SimpleNamespace(returncode=0, stderr=""))
        self.assertEqual(no_pdf.exception.detail, "PDF file not generated")

        with self.assertRaises(HTTPException) as timeout:
            await perform(self.server.subprocess.TimeoutExpired("soffice", 180))
        self.assertEqual(timeout.exception.detail, "File conversion timed out")

        with patch.object(self.server.shutil, "which", side_effect=RuntimeError("probe failed")):
            upload = UploadFile(file=io.BytesIO(b"doc"), filename="sample.docx")
            with self.assertRaises(RuntimeError):
                await self.server.convert_to_pdf(upload)

    async def test_parsing_helpers_cover_all_value_shapes(self):
        self.assertFalse(self.server.parse_bool(None))
        self.assertTrue(self.server.parse_bool(None, True))
        self.assertTrue(self.server.parse_bool(True))
        self.assertTrue(self.server.parse_bool(" yes "))
        self.assertFalse(self.server.parse_bool("no"))
        self.assertIsNone(self.server.parse_optional_float(""))
        self.assertEqual(self.server.parse_optional_float("1.5"), 1.5)
        self.assertIsNone(self.server.parse_optional_int(None))
        self.assertEqual(self.server.parse_optional_int("2"), 2)
        self.assertIsNone(self.server.parse_optional_string(""))
        self.assertEqual(self.server.parse_optional_string(3), "3")

        self.assertEqual(self.server.parse_markdown_ignore_labels(None), [])
        self.assertEqual(self.server.parse_markdown_ignore_labels(["a", 2]), ["a", "2"])
        self.assertEqual(self.server.parse_markdown_ignore_labels("  "), [])
        self.assertEqual(self.server.parse_markdown_ignore_labels('["a",2]'), ["a", "2"])
        self.assertEqual(self.server.parse_markdown_ignore_labels('{"a":1}'), ['{"a":1}'])
        self.assertEqual(self.server.parse_markdown_ignore_labels("plain"), ["plain"])

        self.assertEqual(
            self.server.build_ovisocr2_payload(self.server.OCRRequest(), "abc", 1),
            {"file": "abc", "fileType": 1},
        )
        self.assertEqual(
            self.server.build_hpd_parsing_payload("abc", 1),
            {"file": "abc", "fileType": 1},
        )
        self.assertEqual(
            self.server.as_jsonable(
                {"array": NumpyLike([1, 2]), "items": [NumpyLike(3)]}
            ),
            {"array": [1, 2], "items": [3]},
        )

    async def test_ppocr_and_unlimited_response_edge_shapes(self):
        lines = self.server.extract_ppocr_lines(
            {
                "rec_texts": ["a", "b"],
                "rec_scores": [0.9],
                "rec_boxes": NumpyLike([[1, 2, 3, 4]]),
                "rec_polys": NumpyLike([[[1, 2]]]),
            }
        )
        self.assertEqual(lines[1]["score"], None)
        self.assertNotIn("box", lines[1])
        parsed = self.server.parse_ppocr_response(
            {"result": {"ocrResults": ["bad", {"prunedResult": "bad"}]}}
        )
        self.assertEqual(len(parsed["layoutParsingResults"]), 2)

        self.assertEqual(
            self.server.format_unlimited_ocr_block(
                "formula",
                "x",
                seen_title=False,
            )[0],
            "$$\nx\n$$",
        )
        self.assertIn(
            "**Image:**",
            self.server.format_unlimited_ocr_block(
                "image",
                "content",
                seen_title=False,
            )[0],
        )
        self.assertEqual(self.server.clean_unlimited_ocr_markdown(" a   b "), "a b")
        self.assertEqual(
            self.server.clean_unlimited_ocr_markdown("<|det|> malformed"),
            "malformed",
        )
        normalized = self.server.parse_unlimited_ocr_response(
            {
                "text": "fallback",
                "images": [],
                "layoutParsingResults": ["raw", {"markdown": "text"}],
            }
        )
        self.assertEqual(normalized["images"], {})
        self.assertEqual(normalized["layoutParsingResults"][0], "raw")
        with self.assertRaises(HTTPException):
            self.server.parse_unlimited_ocr_response([])

    async def test_slot_acquisition_and_release_paths(self):
        self.server.set_model_runtime_operation("switching", "", "target")
        with self.assertRaises(HTTPException) as switching:
            await self.server.acquire_ocr_slot("model", "not ready")
        self.assertEqual(switching.exception.status_code, 409)

        self.server.set_model_runtime_operation("idle", "", "target")
        with patch.object(
            self.server,
            "model_runtime_status",
            new=AsyncMock(return_value={"ready": False}),
        ):
            with self.assertRaises(HTTPException) as unavailable:
                await self.server.acquire_ocr_slot("model", "not ready")
        self.assertEqual(unavailable.exception.status_code, 503)

        with patch.object(
            self.server,
            "model_runtime_status",
            new=AsyncMock(return_value={"ready": True}),
        ):
            await self.server.acquire_ocr_slot("model", "not ready")
        self.assertEqual(self.server.ocr_active_count, 1)
        await self.server.release_ocr_slot()
        self.assertEqual(self.server.ocr_active_count, 0)
        await self.server.release_ocr_slot()
        self.assertEqual(self.server.ocr_active_count, 0)

    async def test_all_upstream_request_functions(self):
        pipeline_payload = {
            "result": {
                "layoutParsingResults": [
                    {"markdown": {"text": "ok", "images": {}}}
                ]
            }
        }
        with (
            patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
            patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
            patch.object(
                self.server.httpx,
                "AsyncClient",
                return_value=FakeClient(response(200, payload=pipeline_payload)),
            ),
        ):
            result = await self.server.run_ocr_request(
                self.server.OCRRequest(fileType=1),
                b"image",
            )
        self.assertIn("ok", result["markdown"])

        with (
            patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
            patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
            patch.object(
                self.server.httpx,
                "AsyncClient",
                return_value=FakeClient(
                    response(422, payload={"detail": "invalid"})
                ),
            ),
        ):
            with self.assertRaises(HTTPException):
                await self.server.run_ocr_request(
                    self.server.OCRRequest(fileType=1),
                    b"image",
                )

        with patch.object(self.server, "ENABLE_UNLIMITED_OCR", False):
            with self.assertRaises(HTTPException):
                await self.server.run_unlimited_ocr_request(
                    self.server.OCRRequest(),
                    b"image",
                )
        with (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
            patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
            patch.object(
                self.server.httpx,
                "AsyncClient",
                return_value=FakeClient(
                    response(200, payload={"markdown": "unlimited"})
                ),
            ),
        ):
            result = await self.server.run_unlimited_ocr_request(
                self.server.OCRRequest(fileType=1),
                b"image",
            )
        self.assertEqual(result["markdown"], "unlimited")

        with (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
            patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
            patch.object(
                self.server.httpx,
                "AsyncClient",
                return_value=FakeClient(response(500, text="failed")),
            ),
        ):
            with self.assertRaises(HTTPException):
                await self.server.run_unlimited_ocr_request(
                    self.server.OCRRequest(fileType=1),
                    b"image",
                )

        with patch.object(self.server, "ENABLE_OVISOCR2", False):
            with self.assertRaises(HTTPException):
                await self.server.run_ovisocr2_request(
                    self.server.OCRRequest(),
                    b"image",
                )
        for upstream, should_raise in (
            (response(500, text="failed"), True),
            (response(200, payload=[]), True),
            (response(200, payload={"layoutParsingResults": []}), False),
        ):
            with (
                patch.object(self.server, "ENABLE_OVISOCR2", True),
                patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
                patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
                patch.object(
                    self.server.httpx,
                    "AsyncClient",
                    return_value=FakeClient(upstream),
                ),
            ):
                if should_raise:
                    with self.assertRaises(HTTPException):
                        await self.server.run_ovisocr2_request(
                            self.server.OCRRequest(fileType=1),
                            b"image",
                        )
                else:
                    result = await self.server.run_ovisocr2_request(
                        self.server.OCRRequest(fileType=1),
                        b"image",
                    )
                    self.assertEqual(result["layoutParsingResults"], [])

        with patch.object(self.server, "ENABLE_HPD_PARSING", False):
            with self.assertRaises(HTTPException):
                await self.server.run_hpd_parsing_request(
                    self.server.OCRRequest(),
                    b"image",
                )
        for upstream, should_raise in (
            (response(500, text="failed"), True),
            (response(200, payload=[]), True),
            (response(200, payload={"layoutParsingResults": []}), False),
        ):
            with (
                patch.object(self.server, "ENABLE_HPD_PARSING", True),
                patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
                patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
                patch.object(
                    self.server.httpx,
                    "AsyncClient",
                    return_value=FakeClient(upstream),
                ),
            ):
                if should_raise:
                    with self.assertRaises(HTTPException):
                        await self.server.run_hpd_parsing_request(
                            self.server.OCRRequest(fileType=1),
                            b"image",
                        )
                else:
                    result = await self.server.run_hpd_parsing_request(
                        self.server.OCRRequest(fileType=1),
                        b"image",
                    )
                    self.assertEqual(result["layoutParsingResults"], [])

    async def test_stream_proxy_generator_success_upstream_error_and_exception(self):
        release = AsyncMock()
        stream = FakeStream(200, lines=["", '{"type":"progress"}'])
        with (
            patch.object(self.server, "release_ocr_slot", new=release),
            patch.object(
                self.server.httpx,
                "AsyncClient",
                return_value=FakeClient(stream_response=stream),
            ),
        ):
            output = [
                item
                async for item in self.server.stream_unlimited_ocr_events(
                    self.server.OCRRequest(fileType=1),
                    b"image",
                )
            ]
        self.assertEqual(output, ['{"type":"progress"}\n'])
        release.assert_awaited_once()

        stream = FakeStream(500, body=b"failed")
        with (
            patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
            patch.object(
                self.server.httpx,
                "AsyncClient",
                return_value=FakeClient(stream_response=stream),
            ),
        ):
            output = [
                item
                async for item in self.server.stream_unlimited_ocr_events(
                    self.server.OCRRequest(fileType=1),
                    b"image",
                )
            ]
        self.assertIn("error", output[0])

        with (
            patch.object(
                self.server,
                "prepare_service_input",
                side_effect=RuntimeError("stream crashed"),
            ),
            patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
            patch.object(self.server.logger, "exception"),
        ):
            output = [
                item
                async for item in self.server.stream_unlimited_ocr_events(
                    self.server.OCRRequest(),
                    b"image",
                )
            ]
        self.assertIn("stream crashed", output[0])

    async def test_streaming_response_releases_slot_once_for_all_response_exits(self):
        scope = {
            "type": "http",
            "asgi": {"spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/unlimited-ocr/stream",
            "raw_path": b"/api/unlimited-ocr/stream",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
        }

        async def exercise(fail_on: str | None):
            lease_id = f"lease-{fail_on or 'normal'}"
            release_guard = self.server.OCRSlotReleaseGuard(lease_id)
            body = self.server.stream_unlimited_ocr_events(
                self.server.OCRRequest(fileType=1),
                b"image",
                lease_id,
                release_guard,
            )
            response = self.server.OCRSlotStreamingResponse(
                body,
                release_guard=release_guard,
                media_type="application/x-ndjson",
            )
            sent_messages = []
            first_chunk_sent = asyncio.Event()

            async def receive():
                if fail_on == "receive-after-first-chunk":
                    await first_chunk_sent.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                sent_messages.append(message)
                if fail_on == "before-first-chunk" and message["type"] == "http.response.start":
                    raise OSError("client disconnected before response body")
                if (
                    fail_on == "after-first-chunk"
                    and message["type"] == "http.response.body"
                    and message.get("body")
                ):
                    raise OSError("client disconnected after first response chunk")
                if (
                    fail_on == "receive-after-first-chunk"
                    and message["type"] == "http.response.body"
                    and message.get("body")
                ):
                    first_chunk_sent.set()

            class BlockingStream(FakeStream):
                async def aiter_lines(self):
                    yield '{"type":"progress","markdown":"ok"}'
                    await asyncio.Event().wait()

            controller_api = AsyncMock(return_value={})
            semaphore = asyncio.Semaphore(0)
            upstream_stream = (
                BlockingStream(200)
                if fail_on == "receive-after-first-chunk"
                else FakeStream(
                    200,
                    lines=['{"type":"progress","markdown":"ok"}'],
                )
            )
            upstream_client = MagicMock(
                return_value=FakeClient(
                    stream_response=upstream_stream
                )
            )
            with (
                patch.object(self.server, "MODEL_CONTROL_MODE", "remote"),
                patch.object(self.server, "ocr_active_count", 1),
                patch.object(self.server, "ocr_semaphore", semaphore),
                patch.object(
                    self.server,
                    "controller_api_request",
                    new=controller_api,
                ),
                patch.object(
                    self.server.httpx,
                    "AsyncClient",
                    new=upstream_client,
                ),
            ):
                case_scope = dict(scope)
                if fail_on == "receive-after-first-chunk":
                    case_scope["asgi"] = {"spec_version": "2.3"}
                    await response(case_scope, receive, send)
                elif fail_on:
                    with self.assertRaises(ClientDisconnect):
                        await response(case_scope, receive, send)
                else:
                    await response(case_scope, receive, send)

                controller_api.assert_awaited_once_with(
                    "DELETE", f"/ocr-leases/{lease_id}"
                )
                self.assertEqual(self.server.ocr_active_count, 0)
                self.assertEqual(semaphore._value, 1)

            if fail_on == "before-first-chunk":
                upstream_client.assert_not_called()
            else:
                upstream_client.assert_called_once()
            return sent_messages

        await exercise("before-first-chunk")
        await exercise("after-first-chunk")
        await exercise("receive-after-first-chunk")
        normal_messages = await exercise(None)
        self.assertEqual(normal_messages[-1]["type"], "http.response.body")
        self.assertFalse(normal_messages[-1]["more_body"])

    async def test_streaming_response_close_fallback_branches(self):
        scope = {
            "type": "http",
            "asgi": {"spec_version": "2.4"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/stream",
            "raw_path": b"/stream",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
        }

        async def receive():
            return {"type": "http.disconnect"}

        async def send(_message):
            return None

        class IteratorWithoutClose:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        release_without_close = SimpleNamespace(release_once=AsyncMock())
        response_without_close = self.server.OCRSlotStreamingResponse(
            IteratorWithoutClose(),
            release_guard=release_without_close,
        )
        await response_without_close(scope, receive, send)
        release_without_close.release_once.assert_awaited_once()

        class IteratorWithFailingClose(IteratorWithoutClose):
            async def aclose(self):
                raise RuntimeError("iterator close failed")

        release_after_close_error = SimpleNamespace(release_once=AsyncMock())
        response_with_close_error = self.server.OCRSlotStreamingResponse(
            IteratorWithFailingClose(),
            release_guard=release_after_close_error,
        )
        with patch.object(self.server.logger, "exception") as log_exception:
            await response_with_close_error(scope, receive, send)
        log_exception.assert_called_once_with(
            "Failed to close Unlimited-OCR response iterator"
        )
        release_after_close_error.release_once.assert_awaited_once()

    async def test_proxy_size_and_generic_exception_wrappers(self):
        with patch.object(self.server, "MAX_REQUEST_BYTES", 10):
            self.assertEqual(self.server.validate_proxy_input_size("abc"), 3)
            with self.assertRaises(HTTPException):
                self.server.validate_proxy_input_size("x" * 2000)

        endpoints = (
            ("/api/paddleocr-vl-1.6", "run_ocr_request"),
            ("/api/pp-ocrv6", "run_ppocrv6_request"),
            ("/api/unlimited-ocr", "run_unlimited_ocr_request"),
            ("/api/ovisocr2", "run_ovisocr2_request"),
            ("/api/hpd-parsing", "run_hpd_parsing_request"),
        )
        for endpoint, runner in endpoints:
            with (
                patch.object(
                    self.server,
                    runner,
                    new=AsyncMock(side_effect=RuntimeError("unexpected")),
                ),
                patch.object(self.server.logger, "exception"),
            ):
                result = self.client.post(
                    endpoint,
                    json={"image": "AA==", "fileType": 1},
            )
            self.assertEqual(result.status_code, 500)

        with (
            patch.object(
                self.server,
                "parse_ocr_input",
                new=AsyncMock(side_effect=RuntimeError("unified crashed")),
            ),
            patch.object(self.server.logger, "exception") as log_exception,
        ):
            with self.assertRaises(HTTPException) as unified_error:
                await self.server.parse_with_selected_model(object())
        self.assertEqual(unified_error.exception.status_code, 500)
        self.assertEqual(unified_error.exception.detail, "unified crashed")
        self.assertIsInstance(unified_error.exception.__cause__, RuntimeError)
        log_exception.assert_called_once_with("Unified OCR endpoint error")

        original_hpd_error = HTTPException(status_code=409, detail="hpd busy")
        with patch.object(
            self.server,
            "parse_ocr_input",
            new=AsyncMock(side_effect=original_hpd_error),
        ):
            with self.assertRaises(HTTPException) as hpd_error:
                await self.server.proxy_hpd_parsing(object())
        self.assertIs(hpd_error.exception, original_hpd_error)

        with patch.object(self.server, "ENABLE_UNLIMITED_OCR", False):
            response_value = self.client.post(
                "/api/unlimited-ocr/stream",
                json={"image": "AA==", "fileType": 1},
            )
        self.assertEqual(response_value.status_code, 404)

    async def test_final_server_lines_and_http_exception_branches(self):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/models",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("client", 1),
            "query_string": b"",
        }
        with patch.object(self.server, "API_TOKEN", ""):
            self.assertTrue(self.server.request_is_authenticated(Request(scope)))

        self.assertTrue(self.server.task_needs_compaction({"markdown": "x"}))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            task_id = "task_compact"
            task_dir = root / task_id
            task_dir.mkdir()
            (task_dir / "task.json").write_text(
                json.dumps({"id": task_id, "markdown": "legacy"}),
                encoding="utf-8",
            )
            with patch.object(self.server, "TASK_DATA_DIR", root):
                summaries = self.server.list_task_summaries()
                self.assertEqual(summaries[0]["id"], task_id)
                self.assertTrue((task_dir / self.server.TASK_RESULT_FILE).exists())

                with patch.object(
                    self.server,
                    "task_dir_path",
                    return_value=root.parent / "outside",
                ):
                    with self.assertRaises(HTTPException):
                        self.server.remove_task_dir("task_bad")

                source_id = "task_get_source"
                source_dir = root / source_id
                source_dir.mkdir()
                (source_dir / "task.json").write_text(
                    json.dumps({"id": source_id}),
                    encoding="utf-8",
                )
                (source_dir / "source.bin").write_bytes(b"source")
                task = await self.server.get_task(source_id)
                self.assertEqual(task["sourceUrl"], f"/api/tasks/{source_id}/source")

        upload = UploadFile(file=io.BytesIO(b"doc"), filename="sample.docx")
        with (
            patch.object(self.server.shutil, "which", return_value="/usr/bin/soffice"),
            patch.object(
                self.server,
                "write_upload_to_path",
                new=AsyncMock(side_effect=RuntimeError("write crashed")),
            ),
            patch.object(self.server.logger, "exception"),
        ):
            with self.assertRaises(HTTPException) as conversion:
                await self.server.convert_to_pdf(upload)
        self.assertEqual(conversion.exception.detail, "write crashed")

        multipart_result = {"layoutParsingResults": [], "markdown": "ok", "images": {}}
        with patch.object(
            self.server,
            "run_ocr_request",
            new=AsyncMock(return_value=multipart_result),
        ):
            result = self.client.post(
                "/api/paddleocr-vl-1.6",
                files={"file": ("image.png", b"image", "image/png")},
                data={
                    "fileType": "1",
                    "useLayoutDetection": "false",
                    "layoutNms": "false",
                    "visualize": "true",
                    "markdownIgnoreLabels": '["header"]',
                },
            )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["markdown"], "ok")

        body = b"x" * 20
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        oversized_request = Request(
            {
                **scope,
                "method": "POST",
                "headers": [(b"content-type", b"application/json")],
            },
            receive,
        )
        with patch.object(self.server, "MAX_REQUEST_BYTES", 10):
            with self.assertRaises(HTTPException) as oversized:
                await self.server.parse_ocr_input(oversized_request)
        self.assertEqual(oversized.exception.status_code, 413)

        pdf_string = base64.b64encode(b"%PDF-1.4").decode("ascii")
        self.assertEqual(
            self.server.prepare_service_input(
                self.server.OCRRequest(),
                pdf_string,
            )[1],
            0,
        )
        self.assertEqual(
            self.server.prepare_service_input(
                self.server.OCRRequest(),
                base64.b64encode(b"plain image").decode("ascii"),
            )[1],
            1,
        )
        self.assertIn(
            "prefix",
            self.server.clean_unlimited_ocr_markdown(
                "prefix <|det|>text [1,2,3,4]<|/det|>body"
            ),
        )

        with (
            patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
            patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
            patch.object(
                self.server.httpx,
                "AsyncClient",
                return_value=FakeClient(
                    response(422, payload={"detail": "bad ppocr"})
                ),
            ),
        ):
            with self.assertRaises(HTTPException):
                await self.server.run_ppocrv6_request(
                    self.server.OCRRequest(fileType=1),
                    b"image",
                )

        for endpoint in ("/api/pp-ocrv6", "/api/ovisocr2"):
            result = self.client.post(endpoint, json={})
            self.assertEqual(result.status_code, 400)

        with (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
        ):
            result = self.client.post(
                "/api/unlimited-ocr/stream",
                json={"image": "AA==", "fileType": 1},
            )
        self.assertEqual(result.status_code, 200)

        with (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(
                self.server,
                "acquire_ocr_slot",
                new=AsyncMock(side_effect=RuntimeError("slot crashed")),
            ),
            patch.object(self.server.logger, "exception"),
        ):
            result = self.client.post(
                "/api/unlimited-ocr/stream",
                json={"image": "AA==", "fileType": 1},
            )
        self.assertEqual(result.status_code, 500)

    async def test_module_enabled_models_and_main_entrypoint(self):
        fake_uvicorn = SimpleNamespace(run=MagicMock())
        environment = {
            "PANDOCR_TASK_DATA_DIR": self.temp_dir.name,
            "PANDOCR_MODEL_CONTROL": "none",
            "PANDOCR_API_TOKEN": "",
            "PANDOCR_MODEL_CATALOG": (
                "paddleocr-vl-1.6,pp-ocrv6,unlimited-ocr,ovisocr2,"
                "unlimited-ocr"
            ),
        }
        with (
            patch.dict(os.environ, environment, clear=False),
            patch.dict(sys.modules, {"uvicorn": fake_uvicorn}),
        ):
            namespace = runpy.run_path(
                str(Path(self.server.__file__).resolve()),
                run_name="__main__",
            )
        self.assertIn("unlimited-ocr", namespace["MODEL_RUNTIME_CONFIG"])
        self.assertIn("ovisocr2", namespace["MODEL_RUNTIME_CONFIG"])
        fake_uvicorn.run.assert_called_once()

    async def test_final_server_branch_coverage(self):
        with (
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "docker_api_request",
                new=AsyncMock(return_value=response(200)),
            ),
        ):
            await self.server.docker_container_action("worker", "stop")

        context = self.server.make_docker_build_context("paddleocr-ocr-api")
        self.assertTrue(context)
        with (
            patch.object(self.server, "docker_image_exists", new=AsyncMock(return_value=False)),
            patch.object(self.server, "make_docker_build_context", return_value=b"context"),
            patch.object(
                self.server,
                "docker_api_request",
                new=AsyncMock(return_value=response(200, text="")),
            ) as request_mock,
        ):
            await self.server.docker_build_image("unlimited-ocr-api")
        self.assertNotIn("buildargs=", request_mock.await_args.args[1])

        with patch.object(
            self.server,
            "docker_inspect_self",
            new=AsyncMock(
                return_value={
                    "Mounts": [
                        {"Destination": "/irrelevant", "Source": "/x"},
                        {"Destination": "/app/server.py", "Source": "/host/server.py"},
                    ]
                }
            ),
        ):
            self.assertEqual(
                await self.server.docker_host_repo_root(),
                str(Path("/host/server.py").parent),
            )

        with patch.object(self.server, "model_control_available", return_value=False):
            enriched = await self.server.enrich_unlimited_ocr_runtime_status(
                "unlimited-ocr",
                {},
            )
        self.assertNotIn("sglangContainer", enriched)

        self.server.set_model_runtime_operation("idle", "message")
        self.assertEqual(self.server.model_runtime_operation["message"], "message")

        with (
            patch.object(
                self.server,
                "wait_unlimited_ocr_adapter_http",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "call_unlimited_ocr_adapter_control",
                new=AsyncMock(),
            ),
            patch.object(self.server, "model_control_available", return_value=False),
            patch.object(
                self.server,
                "wait_unlimited_ocr_backend_ready",
                new=AsyncMock(),
            ),
        ):
            await self.server.ensure_unlimited_ocr_backend_runtime("sglang", 10)
            await self.server.ensure_unlimited_ocr_backend_runtime("transformers", 10)

        class DoneTask:
            def done(self):
                return True

        self.server.model_runtime_task = DoneTask()

        def create_task(coroutine):
            coroutine.close()
            return DoneTask()

        with (
            patch.dict(
                self.server.MODEL_RUNTIME_CONFIG,
                {"target": {"stop_order": [], "start_order": []}},
                clear=True,
            ),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server.asyncio, "create_task", side_effect=create_task),
        ):
            await self.server.schedule_model_runtime_activation("target")

        from starlette.requests import Request

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/test",
                "headers": [],
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("client", 1),
                "query_string": b"",
            }
        )

        async def call_next(_request):
            return self.server.Response(content=b"ok")

        with (
            patch.object(self.server, "API_TOKEN", ""),
            patch.object(self.server, "MAX_REQUEST_BYTES", 100),
        ):
            result = await self.server.enforce_request_security(request, call_next)
        self.assertEqual(result.body, b"ok")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with patch.object(self.server, "TASK_DATA_DIR", root):
                stored, result_payload = self.server.split_task_for_storage(
                    {
                        "id": "task_absent",
                        "_preserveResult": True,
                        "batches": [],
                    }
                )
                self.assertIsNone(result_payload)
                self.assertEqual(stored["_resultState"], {})

                task_id = "task_hydrate"
                task_dir = root / task_id
                task_dir.mkdir()
                (task_dir / self.server.TASK_RESULT_FILE).write_text(
                    json.dumps(
                        {
                            "batchMarkdown": {"known": "text"},
                        }
                    ),
                    encoding="utf-8",
                )
                hydrated = self.server.hydrate_task_detail(
                    task_id,
                    {"batches": ["bad", {"id": "unknown"}]},
                )
                self.assertNotIn("markdown", hydrated["batches"][1])

                self.server.remove_task_dir("task_missing")

                destination = root / "never-created.bin"
                upload = UploadFile(file=io.BytesIO(b"x"), filename="x")
                with patch.object(Path, "open", side_effect=OSError("open failed")):
                    with self.assertRaises(OSError):
                        await self.server.write_upload_to_path(upload, destination)
                self.assertFalse(destination.exists())

                source_id = "source_without_task"
                source_dir = root / source_id
                source_dir.mkdir()
                (source_dir / "source.bin").write_bytes(b"source")
                source = await self.server.get_task_source(source_id)
                self.assertEqual(source.media_type, "application/octet-stream")

        image_buffer = io.BytesIO()
        Image.new("RGB", (2, 2)).save(image_buffer, format="PNG")
        _, image_type = self.server.prepare_service_input(
            self.server.OCRRequest(),
            image_buffer.getvalue(),
        )
        self.assertEqual(image_type, 1)

        pipeline = self.server.parse_pipeline_response(
            {
                "result": {
                    "layoutParsingResults": [
                        {"noMarkdown": True},
                        {"markdown": {"text": "ok"}},
                    ]
                }
            }
        )
        self.assertEqual(pipeline["markdown"], "ok\n\n")

        with (
            patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
            patch.object(self.server, "release_ocr_slot", new=AsyncMock()),
            patch.object(
                self.server.httpx,
                "AsyncClient",
                return_value=FakeClient(response(500, text="failed")),
            ),
        ):
            with self.assertRaises(HTTPException):
                await self.server.run_ocr_request(
                    self.server.OCRRequest(fileType=1),
                    b"image",
                )


if __name__ == "__main__":
    unittest.main()
