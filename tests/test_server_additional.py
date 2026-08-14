import asyncio
import base64
import importlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfWriter
from starlette.datastructures import UploadFile


class _FakeAsyncClient:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self.response


class ServerAdditionalCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.original_globals = {
            "TASK_DATA_DIR": cls.server.TASK_DATA_DIR,
            "RUNTIME_SETTINGS_FILE": cls.server.RUNTIME_SETTINGS_FILE,
            "MAX_REQUEST_BYTES": cls.server.MAX_REQUEST_BYTES,
            "API_TOKEN": cls.server.API_TOKEN,
        }
        cls.server.TASK_DATA_DIR = Path(cls.temp_dir.name).resolve()
        cls.server.RUNTIME_SETTINGS_FILE = cls.server.TASK_DATA_DIR / "runtime-settings.json"
        cls.server.MAX_REQUEST_BYTES = 1024
        cls.server.API_TOKEN = ""
        cls.client = TestClient(cls.server.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        for name, value in cls.original_globals.items():
            setattr(cls.server, name, value)
        cls.temp_dir.cleanup()

    def setUp(self):
        self.server.API_TOKEN = ""
        self.server.set_model_runtime_operation("idle", "", self.server.DEFAULT_RUNTIME_MODEL_ID)

    def test_api_token_supports_bearer_and_fallback_header(self):
        with patch.object(self.server, "API_TOKEN", "top-secret"):
            self.assertEqual(self.client.get("/api/models").status_code, 401)
            self.assertEqual(
                self.client.get(
                    "/api/models",
                    headers={"Authorization": "Bearer wrong"},
                ).status_code,
                401,
            )
            self.assertEqual(
                self.client.get(
                    "/api/models",
                    headers={"Authorization": "Bearer top-secret"},
                ).status_code,
                200,
            )
            self.assertEqual(
                self.client.get(
                    "/api/models",
                    headers={"X-Pandocr-Token": "top-secret"},
                ).status_code,
                200,
            )

    def test_security_headers_and_auth_warning_are_added(self):
        response = self.client.get("/api/models")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["x-pandocr-auth-warning"], "PANDOCR_API_TOKEN is not set")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_task_save_rejects_non_object_and_id_mismatch(self):
        non_object = self.client.put("/api/tasks/task_array", json=["not", "an", "object"])
        mismatch = self.client.put("/api/tasks/task_right", json={"id": "task_wrong"})

        self.assertEqual(non_object.status_code, 400)
        self.assertEqual(non_object.json()["detail"], "Task payload must be a JSON object")
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(mismatch.json()["detail"], "Task id mismatch")

    def test_missing_task_and_source_endpoints_return_not_found(self):
        self.assertEqual(self.client.get("/api/tasks/task_missing").status_code, 404)
        self.assertEqual(self.client.get("/api/tasks/task_missing/source").status_code, 404)
        self.assertEqual(
            self.client.get(
                "/api/tasks/task_missing/source/pages",
                params={"start_page": 1, "end_page": 1},
            ).status_code,
            404,
        )

    def test_pdf_page_endpoint_rejects_invalid_ranges_and_clamps_end(self):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.add_blank_page(width=72, height=72)
        pdf = io.BytesIO()
        writer.write(pdf)
        upload = self.client.post(
            "/api/tasks/task_pages/source",
            files={"file": ("source.pdf", pdf.getvalue(), "application/pdf")},
        )
        self.assertEqual(upload.status_code, 200)

        reversed_range = self.client.get(
            "/api/tasks/task_pages/source/pages",
            params={"start_page": 2, "end_page": 1},
        )
        out_of_range = self.client.get(
            "/api/tasks/task_pages/source/pages",
            params={"start_page": 3, "end_page": 4},
        )
        clamped = self.client.get(
            "/api/tasks/task_pages/source/pages",
            params={"start_page": 2, "end_page": 99},
        )

        self.assertEqual(reversed_range.status_code, 400)
        self.assertEqual(out_of_range.status_code, 400)
        self.assertEqual(clamped.status_code, 200)

    def test_oversized_streamed_upload_removes_partial_file(self):
        destination = self.server.TASK_DATA_DIR / "oversized.bin"
        upload = UploadFile(file=io.BytesIO(b"1234"), filename="oversized.bin")

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(self.server.write_upload_to_path(upload, destination, max_bytes=3))

        self.assertEqual(raised.exception.status_code, 413)
        self.assertFalse(destination.exists())

    def test_prepare_service_input_autodetects_pdf_and_converts_gif(self):
        pdf_b64, pdf_type = self.server.prepare_service_input(
            self.server.OCRRequest(),
            b"%PDF-1.4\n",
        )
        self.assertEqual(pdf_type, 0)
        self.assertTrue(base64.b64decode(pdf_b64).startswith(b"%PDF-"))

        gif = io.BytesIO()
        Image.new("P", (2, 2)).save(gif, format="GIF")
        gif_b64, image_type = self.server.prepare_service_input(
            self.server.OCRRequest(),
            gif.getvalue(),
        )
        converted = Image.open(io.BytesIO(base64.b64decode(gif_b64)))
        self.assertEqual(image_type, 1)
        self.assertEqual(converted.format, "JPEG")

    def test_raw_input_helpers_accept_data_urls_and_reject_invalid_base64(self):
        data_url = "data:text/plain;base64,SGVsbG8="

        self.assertEqual(self.server.normalize_raw_input_to_base64(data_url), "SGVsbG8=")
        self.assertEqual(self.server.raw_input_to_bytes(data_url), b"Hello")
        with self.assertRaises(HTTPException) as raised:
            self.server.raw_input_to_bytes("not-base64!")
        self.assertEqual(raised.exception.status_code, 400)

    def test_payload_builders_preserve_false_and_zero_optional_values(self):
        request = self.server.OCRRequest(
            useLayoutDetection=False,
            layoutThreshold=0.0,
            layoutNms=False,
            temperature=0.0,
            visualize=False,
        )

        pipeline = self.server.build_pipeline_payload(request, "abc", 1)
        ppocr = self.server.build_ppocr_payload(request, "abc", 1)
        unlimited = self.server.build_unlimited_ocr_payload(request, "abc", 1)

        self.assertFalse(pipeline["useLayoutDetection"])
        self.assertEqual(pipeline["layoutThreshold"], 0.0)
        self.assertFalse(pipeline["layoutNms"])
        self.assertFalse(pipeline["visualize"])
        self.assertFalse(ppocr["visualize"])
        self.assertEqual(unlimited["temperature"], 0.0)
        self.assertFalse(unlimited["visualize"])

    def test_response_parsers_merge_images_and_reject_malformed_payloads(self):
        parsed = self.server.parse_pipeline_response(
            {
                "result": {
                    "layoutParsingResults": [
                        {"markdown": {"text": "Page one", "images": {"image.jpg": "one"}}},
                        {"markdown": {"text": "Page two", "images": {"chart.jpg": "two"}}},
                    ]
                }
            },
            image_prefix="batch1",
        )

        self.assertEqual(parsed["markdown"], "Page one\n\nPage two\n\n")
        self.assertEqual(
            parsed["images"],
            {"batch1_image.jpg": "one", "batch1_chart.jpg": "two"},
        )
        with self.assertRaises(HTTPException):
            self.server.parse_pipeline_response({})
        with self.assertRaises(HTTPException):
            self.server.parse_ppocr_response({"result": {}})

    def test_ocr_json_validation_fails_before_upstream_call(self):
        invalid_json = self.client.post(
            "/api/paddleocr-vl-1.6",
            content=b"{",
            headers={"Content-Type": "application/json"},
        )
        missing_image = self.client.post("/api/paddleocr-vl-1.6", json={})
        boundary = "pandocr-test-boundary"
        missing_multipart_file = self.client.post(
            "/api/paddleocr-vl-1.6",
            content=(
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="fileType"\r\n\r\n'
                f"1\r\n--{boundary}--\r\n"
            ).encode("ascii"),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )

        self.assertEqual(invalid_json.status_code, 400)
        self.assertEqual(invalid_json.json()["detail"], "Invalid JSON payload")
        self.assertEqual(missing_image.status_code, 400)
        self.assertEqual(missing_image.json()["detail"], "Missing JSON field: image")
        self.assertEqual(missing_multipart_file.status_code, 400)
        self.assertEqual(missing_multipart_file.json()["detail"], "Missing multipart field: file")

    def test_office_conversion_checks_runtime_and_extension(self):
        with patch.object(self.server.shutil, "which", return_value=None):
            missing_runtime = self.client.post(
                "/api/convert/to-pdf",
                files={"file": ("sample.docx", b"document", "application/octet-stream")},
            )
        with patch.object(self.server.shutil, "which", return_value="/usr/bin/soffice"):
            unsupported = self.client.post(
                "/api/convert/to-pdf",
                files={"file": ("sample.txt", b"text", "text/plain")},
            )

        self.assertEqual(missing_runtime.status_code, 500)
        self.assertIn("LibreOffice", missing_runtime.json()["detail"])
        self.assertEqual(unsupported.status_code, 400)

    def test_runtime_normalizers_and_task_directory_safety(self):
        with patch.object(self.server, "UNLIMITED_OCR_SUPPORTED_BACKENDS", {"transformers"}):
            self.assertEqual(
                self.server.normalize_unlimited_ocr_backend("unknown", "transformers"),
                "transformers",
            )
            with self.assertRaises(HTTPException):
                self.server.normalize_unlimited_ocr_backend("unknown")

        with patch.object(self.server, "TASK_DATA_DIR", self.server.PROJECT_ROOT):
            with self.assertRaises(RuntimeError):
                self.server.validate_task_data_dir()

    def test_ppocr_upstream_success_and_failure_always_release_slot(self):
        success_response = httpx.Response(
            200,
            json={"result": {"ocrResults": []}},
        )
        acquire = AsyncMock()
        release = AsyncMock()
        with (
            patch.object(self.server, "acquire_ocr_slot", new=acquire),
            patch.object(self.server, "release_ocr_slot", new=release),
            patch.object(
                self.server.httpx,
                "AsyncClient",
                return_value=_FakeAsyncClient(success_response),
            ),
        ):
            result = asyncio.run(
                self.server.run_ppocrv6_request(
                    self.server.OCRRequest(fileType=1),
                    b"image-bytes",
                )
            )

        self.assertEqual(result["layoutParsingResults"], [])
        acquire.assert_awaited_once()
        release.assert_awaited_once()

        failure_response = httpx.Response(502, text="upstream unavailable")
        release = AsyncMock()
        with (
            patch.object(self.server, "acquire_ocr_slot", new=AsyncMock()),
            patch.object(self.server, "release_ocr_slot", new=release),
            patch.object(
                self.server.httpx,
                "AsyncClient",
                return_value=_FakeAsyncClient(failure_response),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    self.server.run_ppocrv6_request(
                        self.server.OCRRequest(fileType=1),
                        b"image-bytes",
                    )
                )

        self.assertEqual(raised.exception.status_code, 502)
        release.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
