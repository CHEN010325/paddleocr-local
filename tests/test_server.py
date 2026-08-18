import asyncio
import importlib
import io
import json
import os
import tarfile
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter


class ServerTaskApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        os.environ["PANDOCR_TASK_DATA_DIR"] = cls.temp_dir.name
        os.environ["PANDOCR_MAX_UPLOAD_MB"] = "1"
        os.environ["PANDOCR_MODEL_CONTROL"] = "none"
        os.environ["PANDOCR_API_TOKEN"] = ""
        # Collection may import the module before this suite installs its
        # environment. Reload so paths and size limits are order-independent.
        cls.server = importlib.reload(importlib.import_module("server"))
        cls.client = TestClient(cls.server.app)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def test_task_list_returns_summaries_and_detail_endpoint_returns_full_task(self):
        task = {
            "id": "task_123",
            "name": "sample.pdf",
            "sourceKind": "pdf",
            "modelId": "pp-ocrv6",
            "modelName": "PP-OCRv6",
            "size": 1200,
            "createdAt": 100,
            "updatedAt": 200,
            "status": "processing",
            "pageCount": 3,
            "sourceDataUrl": "data:application/pdf;base64,JVBERi0=",
            "batches": [
                {"id": "b1", "status": "completed", "pageCount": 1},
                {"id": "b2", "status": "pending", "pageCount": 2},
            ],
            "markdown": "# Result",
            "images": {"ocr_images/a.jpg": "abc"},
            "ocrResults": [{"markdown": {"text": "# Result"}}],
        }

        put_response = self.client.put("/api/tasks/task_123", json=task)
        self.assertEqual(put_response.status_code, 200)

        list_response = self.client.get("/api/tasks")
        self.assertEqual(list_response.status_code, 200)
        summary = list_response.json()["tasks"][0]
        self.assertEqual(summary["id"], "task_123")
        self.assertEqual(summary["modelId"], "pp-ocrv6")
        self.assertEqual(summary["modelName"], "PP-OCRv6")
        self.assertEqual(summary["completedPages"], 1)
        self.assertTrue(summary["hasMarkdown"])
        self.assertNotIn("sourceDataUrl", summary)
        self.assertNotIn("batches", summary)
        self.assertNotIn("ocrResults", summary)

        detail_response = self.client.get("/api/tasks/task_123")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.json()
        self.assertEqual(detail["sourceDataUrl"], task["sourceDataUrl"])
        self.assertEqual(detail["batches"], task["batches"])
        self.assertTrue(detail["detailLoaded"])

    def test_task_list_sorts_mixed_timestamp_formats(self):
        numeric_dir = Path(self.temp_dir.name) / "task_sort_numeric"
        iso_dir = Path(self.temp_dir.name) / "task_sort_iso"
        numeric_dir.mkdir(parents=True, exist_ok=True)
        iso_dir.mkdir(parents=True, exist_ok=True)
        (numeric_dir / "task.json").write_text(
            json.dumps({"id": "task_sort_numeric", "updatedAt": 4102444800}),
            encoding="utf-8",
        )
        (iso_dir / "task.json").write_text(
            json.dumps({"id": "task_sort_iso", "updatedAt": "1970-01-01T00:01:00Z"}),
            encoding="utf-8",
        )

        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200)
        ids = [task["id"] for task in response.json()["tasks"]]
        self.assertLess(ids.index("task_sort_numeric"), ids.index("task_sort_iso"))

    def test_task_source_can_be_cloned_for_comparison(self):
        upload = self.client.post(
            "/api/tasks/source_123/source",
            files={"file": ("sample.pdf", b"%PDF-test", "application/pdf")},
        )
        self.assertEqual(upload.status_code, 200)

        clone = self.client.post("/api/tasks/source_123/clone-source/target_123")
        self.assertEqual(clone.status_code, 200)
        self.assertEqual(clone.json()["url"], "/api/tasks/target_123/source")
        cloned_source = self.client.get(clone.json()["url"])
        self.assertEqual(cloned_source.content, b"%PDF-test")

        self.assertEqual(
            self.client.post("/api/tasks/source_123/clone-source/target_123").status_code,
            409,
        )
        self.assertEqual(
            self.client.post("/api/tasks/missing_123/clone-source/other_123").status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/api/tasks/source_123/clone-source/source_123").status_code,
            400,
        )

    def test_unified_parse_endpoint_dispatches_by_model_id(self):
        runner_names = {
            "paddleocr-vl-1.6": "run_ocr_request",
            "pp-ocrv6": "run_ppocrv6_request",
            "unlimited-ocr": "run_unlimited_ocr_request",
            "ovisocr2": "run_ovisocr2_request",
            "hpd-parsing": "run_hpd_parsing_request",
        }
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.server, "MODEL_CATALOG_IDS", list(runner_names)))
            runners = {
                model_id: stack.enter_context(
                    patch.object(
                        self.server,
                        runner_name,
                        new=AsyncMock(return_value={"modelId": model_id}),
                    )
                )
                for model_id, runner_name in runner_names.items()
            }

            for model_id, expected_runner in runners.items():
                with self.subTest(model_id=model_id):
                    response = self.client.post(
                        "/api/parse",
                        data={"modelId": model_id, "fileType": "1"},
                        files={"file": ("sample.png", b"image", "image/png")},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"modelId": model_id})
                    expected_runner.assert_awaited_once()
                    ocr_request, raw_input = expected_runner.await_args.args
                    self.assertEqual(ocr_request.modelId, model_id)
                    self.assertEqual(raw_input, b"image")
                    for other_id, other_runner in runners.items():
                        if other_id != model_id:
                            other_runner.assert_not_awaited()
                    for runner in runners.values():
                        runner.reset_mock()

            json_response = self.client.post(
                "/api/parse",
                json={"modelId": "hpd-parsing", "fileType": 1, "image": "aW1hZ2U="},
            )
            self.assertEqual(json_response.status_code, 200)
            self.assertEqual(json_response.json(), {"modelId": "hpd-parsing"})
            runners["hpd-parsing"].assert_awaited_once()

            unknown = self.client.post(
                "/api/parse",
                data={"modelId": "unknown", "fileType": "1"},
                files={"file": ("sample.png", b"image", "image/png")},
            )
            self.assertEqual(unknown.status_code, 400)

    def test_unified_parse_multipart_accepts_repeated_and_json_ignore_labels(self):
        runner = AsyncMock(return_value={"markdown": "ok"})
        with patch.object(self.server, "run_ppocrv6_request", new=runner):
            repeated = self.client.post(
                "/api/parse",
                files=[
                    ("file", ("sample.png", b"image", "image/png")),
                    ("modelId", (None, "pp-ocrv6")),
                    ("fileType", (None, "1")),
                    ("markdownIgnoreLabels", (None, "header")),
                    ("markdownIgnoreLabels", (None, "footer")),
                ],
            )
            self.assertEqual(repeated.status_code, 200)
            ocr_request, raw_input = runner.await_args.args
            self.assertEqual(ocr_request.markdownIgnoreLabels, ["header", "footer"])
            self.assertEqual(raw_input, b"image")

            runner.reset_mock()
            encoded_single = self.client.post(
                "/api/parse",
                data={
                    "modelId": "pp-ocrv6",
                    "fileType": "1",
                    "markdownIgnoreLabels": '["number", "header"]',
                },
                files={"file": ("sample.png", b"image", "image/png")},
            )
            self.assertEqual(encoded_single.status_code, 200)
            ocr_request, _ = runner.await_args.args
            self.assertEqual(ocr_request.markdownIgnoreLabels, ["number", "header"])

    def test_unified_parse_openapi_documents_both_request_formats_and_all_models(self):
        response = self.client.get("/api/openapi.json")
        self.assertEqual(response.status_code, 200)
        operation = response.json()["paths"]["/api/parse"]["post"]
        request_body = operation["requestBody"]
        self.assertTrue(request_body["required"])
        self.assertEqual(
            set(request_body["content"]),
            {"multipart/form-data", "application/json"},
        )
        multipart_schema = request_body["content"]["multipart/form-data"]["schema"]
        self.assertEqual(multipart_schema["required"], ["file"])
        self.assertEqual(multipart_schema["properties"]["file"]["format"], "binary")
        self.assertEqual(
            multipart_schema["properties"]["modelId"]["enum"],
            self.server.UNIFIED_PARSE_MODEL_IDS,
        )
        json_schema = request_body["content"]["application/json"]["schema"]
        self.assertIn("image", json_schema["required"])
        self.assertEqual(
            json_schema["properties"]["modelId"]["enum"],
            self.server.UNIFIED_PARSE_MODEL_IDS,
        )
        self.assertTrue({"400", "409", "413", "503"}.issubset(operation["responses"]))

    def test_model_list_includes_vl_and_ppocrv6(self):
        response = self.client.get("/api/models")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        model_ids = [model["id"] for model in payload["data"]]
        self.assertIn("paddleocr-vl-1.6", model_ids)
        self.assertIn("pp-ocrv6", model_ids)
        self.assertNotIn("unlimited-ocr", model_ids)
        self.assertEqual(payload["version"], self.server.APP_VERSION)
        self.assertEqual(payload["commit"], self.server.APP_COMMIT or None)
        self.assertEqual(payload["apiDocsEnabled"], self.server.ENABLE_API_DOCS)
        self.assertEqual(payload["openapiUrl"], "/api/openapi.json")
        self.assertEqual(
            self.server.app.docs_url,
            "/docs" if self.server.ENABLE_API_DOCS else None,
        )
        self.assertEqual(
            self.server.app.redoc_url,
            "/redoc" if self.server.ENABLE_API_DOCS else None,
        )

    def test_model_runtime_reports_both_models(self):
        with patch.object(self.server, "fetch_http_health", new=AsyncMock(return_value=(False, {}))):
            response = self.client.get("/api/model-runtime")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("models", payload)
        self.assertIn("paddleocr-vl-1.6", payload["models"])
        self.assertIn("pp-ocrv6", payload["models"])
        self.assertIn("controlAvailable", payload)
        self.assertIn("ocrActiveCount", payload)
        self.assertIn("maxConcurrentOcr", payload)

    def test_model_catalog_can_include_all_independent_models(self):
        with (
            patch.object(
                self.server,
                "MODEL_CATALOG_ENV",
                "paddleocr-vl-1.6,pp-ocrv6,unlimited-ocr,ovisocr2",
            ),
            patch.dict(
                os.environ,
                {"PANDOCR_MODEL_CATALOG": "paddleocr-vl-1.6,pp-ocrv6,unlimited-ocr,ovisocr2"},
            ),
        ):
            self.assertEqual(
                self.server.parse_model_catalog(),
                ["paddleocr-vl-1.6", "pp-ocrv6", "unlimited-ocr", "ovisocr2"],
            )
        self.assertEqual(
            {
                "paddleocr-vl-1.6": self.server.services_for_model_deploy("paddleocr-vl-1.6"),
                "pp-ocrv6": self.server.services_for_model_deploy("pp-ocrv6"),
                "unlimited-ocr": self.server.services_for_model_deploy("unlimited-ocr", "transformers"),
                "ovisocr2": self.server.services_for_model_deploy("ovisocr2"),
            },
            {
                "paddleocr-vl-1.6": ["paddleocr-vlm-server", "paddleocr-vl-api"],
                "pp-ocrv6": ["paddleocr-ocr-api"],
                "unlimited-ocr": ["unlimited-ocr-api"],
                "ovisocr2": ["ovisocr2-api"],
            },
        )

    def test_model_runtime_switch_requires_docker_control(self):
        with patch.object(self.server, "model_control_available", return_value=False):
            response = self.client.post("/api/model-runtime/switch", json={"modelId": "pp-ocrv6"})
        self.assertEqual(response.status_code, 503)

    def test_dynamic_docker_build_context_uses_project_dockerfiles(self):
        context = self.server.make_docker_build_context("unlimited-ocr-sglang")
        with tarfile.open(fileobj=io.BytesIO(context), mode="r") as tar:
            self.assertIn("Dockerfile", tar.getnames())
            self.assertIn("unlimited_ocr_adapter.py", tar.getnames())
            dockerfile = tar.extractfile("Dockerfile").read()

        expected = (self.server.PROJECT_ROOT / "Dockerfile.unlimited-ocr-sglang").read_bytes()
        self.assertEqual(dockerfile, expected)
        self.assertEqual(
            self.server.docker_build_args_for("unlimited-ocr-sglang"),
            {"UNLIMITED_OCR_SGLANG_WHEEL_URL": self.server.UNLIMITED_OCR_SGLANG_WHEEL_URL},
        )
        self.assertEqual(
            self.server.docker_build_args_for("paddleocr-ocr-api"),
            {
                "API_IMAGE_TAG_SUFFIX": self.server.API_IMAGE_TAG_SUFFIX,
                "API_IMAGE_DIGEST": self.server.API_IMAGE_DIGEST,
            },
        )

    def test_ovisocr2_build_context_and_runtime_service(self):
        context = self.server.make_docker_build_context("ovisocr2-api")
        with tarfile.open(fileobj=io.BytesIO(context), mode="r") as tar:
            self.assertIn("Dockerfile", tar.getnames())
            self.assertIn("ovisocr2_adapter.py", tar.getnames())

        self.assertEqual(self.server.docker_image_name_for("ovisocr2-api"), "pandocr-ovisocr2:latest")
        self.assertEqual(self.server.services_for_model_deploy("ovisocr2"), ["ovisocr2-api"])
        web_dockerfile = (self.server.PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY ovisocr2_adapter.py .", web_dockerfile)
        self.assertIn("COPY Dockerfile.ovisocr2 ./", web_dockerfile)
        ovis_dockerfile = (self.server.PROJECT_ROOT / "Dockerfile.ovisocr2").read_text(encoding="utf-8")
        self.assertIn("python3 -m venv /opt/venv", ovis_dockerfile)
        self.assertNotIn("python3 -m pip install", ovis_dockerfile)
        container_config = self.server.container_payload_for(
            "ovisocr2-api",
            host_root=str(self.server.PROJECT_ROOT),
            network_name="test-network",
        )
        self.assertIn(f"OVISOCR2_KV_CACHE_MEMORY_MB={self.server.OVISOCR2_KV_CACHE_MEMORY_MB}", container_config["Env"])
        self.assertIn(
            f"OVISOCR2_STARTUP_MEMORY_FRACTION={self.server.OVISOCR2_STARTUP_MEMORY_FRACTION}",
            container_config["Env"],
        )
        self.assertIn(f"OVISOCR2_MAX_MODEL_LEN={self.server.OVISOCR2_MAX_MODEL_LEN}", container_config["Env"])
        self.assertIn(f"OVISOCR2_MAX_NUM_SEQS={self.server.OVISOCR2_MAX_NUM_SEQS}", container_config["Env"])
        self.assertIn(
            f"OVISOCR2_GDN_PREFILL_BACKEND={self.server.OVISOCR2_GDN_PREFILL_BACKEND}",
            container_config["Env"],
        )
        self.assertFalse(any(value.startswith("OVISOCR2_GPU_MEMORY_UTILIZATION=") for value in container_config["Env"]))

    def test_runtime_settings_can_persist_unlimited_ocr_backend(self):
        settings_path = self.server.RUNTIME_SETTINGS_FILE
        previous = settings_path.read_text(encoding="utf-8") if settings_path.exists() else None
        try:
            self.server.save_runtime_settings({"unlimitedOcrBackend": "sglang"})

            self.assertEqual(self.server.load_runtime_settings()["unlimitedOcrBackend"], "sglang")
            self.assertEqual(self.server.initial_unlimited_ocr_backend(), "sglang")
        finally:
            if previous is None:
                settings_path.unlink(missing_ok=True)
            else:
                settings_path.write_text(previous, encoding="utf-8")

    def test_unlimited_ocr_backend_switch_restores_previous_backend_on_failure(self):
        previous_backend = self.server.unlimited_ocr_runtime_backend
        previous_lock = self.server.model_runtime_lock
        self.server.unlimited_ocr_runtime_backend = "sglang"
        self.server.model_runtime_lock = asyncio.Lock()
        ensure_mock = AsyncMock(side_effect=[RuntimeError("preload failed"), None])
        try:
            with (
                patch.object(self.server, "model_runtime_status", new=AsyncMock(return_value={"running": True})),
                patch.object(self.server, "ensure_unlimited_ocr_backend_runtime", new=ensure_mock),
                patch.object(self.server.logger, "exception"),
            ):
                asyncio.run(self.server.activate_unlimited_ocr_backend("transformers"))

            self.assertEqual(self.server.unlimited_ocr_runtime_backend, "sglang")
            self.assertEqual([call.args[0] for call in ensure_mock.await_args_list], ["transformers", "sglang"])
            self.assertEqual(self.server.model_runtime_operation["state"], "error")
        finally:
            self.server.unlimited_ocr_runtime_backend = previous_backend
            self.server.model_runtime_lock = previous_lock
            self.server.set_model_runtime_operation("idle", "", "paddleocr-vl-1.6")

    def test_cross_origin_mutation_is_rejected_without_allowlisted_origin(self):
        response = self.client.post(
            "/api/model-runtime/switch",
            json={"modelId": "pp-ocrv6"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(response.status_code, 403)

    def test_allowlisted_origin_can_reach_api(self):
        with patch.object(self.server, "model_control_available", return_value=False):
            response = self.client.post(
                "/api/model-runtime/switch",
                json={"modelId": "pp-ocrv6"},
                headers={"Origin": "http://localhost:8000"},
            )
        self.assertEqual(response.status_code, 503)

    def test_invalid_task_id_is_rejected(self):
        response = self.client.get("/api/tasks/bad!")
        self.assertEqual(response.status_code, 400)

    def test_oversized_request_is_rejected_before_proxying(self):
        large_payload = {"image": "x" * (2 * 1024 * 1024), "fileType": 1}
        response = self.client.post("/api/paddleocr-vl-1.6", json=large_payload)
        self.assertEqual(response.status_code, 413)

    def test_ppocr_response_is_normalized_for_existing_frontend(self):
        response = self.server.parse_ppocr_response(
            {
                "result": {
                    "ocrResults": [
                        {
                            "inputImage": "base64-page-image",
                            "prunedResult": {
                                "page_index": 0,
                                "rec_texts": ["Hello", "World"],
                                "rec_scores": [0.98, 0.95],
                                "rec_boxes": [[1, 2, 30, 10], [1, 14, 40, 22]],
                            }
                        }
                    ]
                }
            }
        )

        self.assertEqual(response["markdown"], "Hello\nWorld")
        self.assertEqual(len(response["layoutParsingResults"]), 1)
        page = response["layoutParsingResults"][0]
        self.assertEqual(page["parser"], "pp-ocrv6")
        self.assertEqual(page["pageImage"], "base64-page-image")
        self.assertEqual(page["ocrLines"][0]["text"], "Hello")
        self.assertEqual(page["ocrLines"][0]["box"], [1, 2, 30, 10])

    def test_unlimited_ocr_response_is_normalized_for_existing_frontend(self):
        response = self.server.parse_unlimited_ocr_response(
            {
                "markdown": "# Parsed\n\nBody",
                "layoutParsingResults": [
                    {
                        "parser": "unlimited-ocr",
                        "markdown": {"text": "# Parsed\n\nBody", "images": {}},
                    }
                ],
            }
        )

        self.assertEqual(response["markdown"], "# Parsed\n\nBody")
        self.assertEqual(response["images"], {})
        self.assertEqual(response["layoutParsingResults"][0]["parser"], "unlimited-ocr")

    def test_unlimited_ocr_layout_tags_are_converted_to_markdown(self):
        raw = (
            "<|det|>header [1, 2, 3, 4]<|/det|>Baidu "
            "<|det|>title [10, 20, 30, 40]<|/det|>Unlimited OCR Works "
            "<|det|>title [10, 50, 30, 70]<|/det|>Abstract "
            "<|det|>text [10, 80, 90, 120]<|/det|>Body text. "
            "<|det|>image_caption [10, 130, 90, 150]<|/det|>Figure 1. Caption."
        )
        response = self.server.parse_unlimited_ocr_response({"markdown": raw})

        self.assertNotIn("<|det|>", response["markdown"])
        self.assertNotIn("Baidu", response["markdown"])
        self.assertIn("# Unlimited OCR Works", response["markdown"])
        self.assertIn("## Abstract", response["markdown"])
        self.assertIn("Body text.", response["markdown"])
        self.assertIn("*Figure 1. Caption.*", response["markdown"])

    def test_unlimited_ocr_stream_position_tracks_page_reset(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        raw = (
            "<|det|>text [10, 850, 900, 930]<|/det|>End of page one. "
            "<|det|>text [10, 30, 900, 90]<|/det|>Start of page two."
        )
        position = adapter.streaming_source_position(raw, 2)

        self.assertEqual(position["pageIndex"], 1)
        self.assertEqual(position["pageNumber"], 2)
        self.assertLess(position["pageProgress"], 0.1)
        self.assertEqual(position["bbox"], [10.0, 30.0, 900.0, 90.0])
        self.assertEqual(position["pageWidth"], 1000)
        self.assertEqual(position["pageHeight"], 1000)

    def test_unlimited_ocr_stream_position_uses_pdf_text_anchor_for_batch_pages(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        raw = (
            "<|det|>text [100, 700, 900, 760]<|/det|>"
            "Traditional OCR models adopt a pipeline architecture. "
            "<|det|>image [100, 100, 500, 400]<|/det|>"
        )
        page_texts = [
            adapter.normalize_anchor_text("Introduction and summary text."),
            adapter.normalize_anchor_text("Traditional OCR models adopt a pipeline architecture for document parsing."),
        ]

        position = adapter.streaming_source_position(raw, 2, page_texts)

        self.assertEqual(position["pageIndex"], 1)
        self.assertEqual(position["pageNumber"], 2)
        self.assertEqual(position["pageConfidence"], "text")

    def test_unlimited_ocr_adapter_exposes_layout_blocks_for_frontend_mapping(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        raw = (
            "<|det|>title [10, 20, 300, 60]<|/det|>Unlimited OCR Works "
            "<|det|>text [20, 100, 900, 180]<|/det|>Body text."
        )
        response = adapter.build_adapter_response(raw, 1, 0, {"backend": "test"})
        page = response["layoutParsingResults"][0]

        self.assertEqual(page["parser"], "unlimited-ocr")
        self.assertEqual(page["width"], 1000)
        self.assertEqual(page["height"], 1000)
        self.assertEqual(page["parsing_res_list"][0]["block_label"], "title")
        self.assertEqual(page["parsing_res_list"][0]["block_bbox"], [10.0, 20.0, 300.0, 60.0])

    def test_unlimited_ocr_image_crop_uses_independent_normalized_axes(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        from PIL import Image

        box = adapter.scaled_crop_box([100, 200, 500, 600], Image.new("RGB", (2000, 3000)))

        self.assertEqual(box, (184, 576, 1016, 1824))

    def test_ovisocr2_visual_regions_are_cropped_and_rewritten(self):
        adapter = importlib.import_module("ovisocr2_adapter")
        from PIL import Image

        markdown, images, blocks = adapter.crop_visual_regions(
            'Before\n\n<img src="images/bbox_100_200_500_600.jpg" />\n\nAfter',
            Image.new("RGB", (2000, 3000), "white"),
            0,
        )

        self.assertIn("![image](ocr_images/ovisocr2_p1_image_1.jpg)", markdown)
        self.assertEqual(list(images), ["ocr_images/ovisocr2_p1_image_1.jpg"])
        self.assertEqual(blocks[0]["block_bbox"], [100, 200, 500, 600])

    def test_ovisocr2_mlx_backend_dispatches_to_native_parser(self):
        adapter = importlib.import_module("ovisocr2_adapter")
        sentinel = object()
        with (
            patch.object(adapter, "BACKEND", "mlx"),
            patch.object(adapter, "MlxOvisOCR2Parser", return_value=sentinel) as parser,
        ):
            self.assertIs(adapter.create_parser(), sentinel)
        parser.assert_called_once_with(adapter.MODEL_NAME)

    def test_unlimited_ocr_streaming_markdown_can_include_images_once(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        from PIL import Image

        image_buffer = io.BytesIO()
        Image.new("RGB", (1000, 1000), "white").save(image_buffer, format="PNG")
        raw = (
            "<|det|>image [100, 100, 500, 500]<|/det|>"
            "<|det|>image_caption [100, 520, 500, 560]<|/det|>Figure 1. Caption."
        )

        markdown, images = adapter.render_streaming_markdown(raw, [image_buffer.getvalue()])
        sent_images = {}

        self.assertIn("![image](ocr_images/unlimited_p1_image_1.png)", markdown)
        self.assertIn("ocr_images/unlimited_p1_image_1.png", images)
        self.assertEqual(adapter.unsent_images(images, sent_images), images)
        self.assertEqual(adapter.unsent_images(images, sent_images), {})

    def test_unlimited_ocr_sglang_payload_reserves_context_for_input(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")

        with patch.object(adapter, "ENABLE_NO_REPEAT_PROCESSOR", True):
            payload = adapter.build_sglang_payload([b"not-real-image"], 1)

        self.assertEqual(payload["images_config"]["backend"], "sglang")
        self.assertLess(payload["max_tokens"], adapter.MAX_TOKENS)
        self.assertIn("custom_logit_processor", payload)
        self.assertEqual(payload["custom_params"]["ngram_size"], adapter.NO_REPEAT_NGRAM_SIZE)

    def test_unlimited_ocr_sglang_context_error_can_reduce_max_tokens(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        payload = {"max_tokens": 32768, "images_config": {"backend": "sglang"}}
        error_body = (
            "Requested token count exceeds the model's maximum context length of 32768 tokens. "
            "You requested a total of 35505 tokens: 2737 tokens from the input messages "
            "and 32768 tokens for the completion."
        )

        adjusted = adapter.adjust_sglang_payload_for_context_error(payload, error_body)

        self.assertIsNotNone(adjusted)
        self.assertEqual(adjusted["max_tokens"], 32768 - 2737 - adapter.SGLANG_CONTEXT_TOKEN_RESERVE)
        self.assertEqual(adjusted["images_config"]["max_tokens_adjusted_from"], 32768)

    def test_unlimited_ocr_repetition_guard_flags_degenerate_output(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        repeated = " ".join(["attention weight normalization"] * 20)

        self.assertEqual(adapter.detect_degenerate_repetition(repeated), "attention weight normalization")

    def test_unlimited_ocr_repetition_guard_flags_dense_numbered_loop(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        repeated = " ".join(f"attention weight normalization {index}" for index in range(20))

        self.assertEqual(adapter.detect_degenerate_repetition(repeated), "attention weight normalization")

    def test_unlimited_ocr_repetition_guard_allows_reference_arxiv_phrase(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        references = " ".join(
            (
                f"[{index}] A. Author, B. Researcher, and C. Writer. "
                f"A useful method for document parsing and visual models. "
                f"arXiv preprint arXiv:{2400 + index}.01234, 2025."
            )
            for index in range(20)
        )

        self.assertIsNone(adapter.detect_degenerate_repetition(references))

    def test_unlimited_ocr_extracts_layout_from_transformers_stdout(self):
        adapter = importlib.import_module("unlimited_ocr_adapter")
        stdout = (
            "INFO:     127.0.0.1:123 - \"GET /health HTTP/1.1\" 200 OK\n"
            "image: 100%|##########| 1/1 [00:00<00:00, 10it/s]\n"
            "<|det|>title [10, 20, 30, 40]<|/det|>Title\n"
            "<|det|>image [40, 50, 80, 100]<|/det|>\n"
            "===============save results:===============\n"
        )
        extracted = adapter.extract_layout_text_from_transformers_stdout(stdout)

        self.assertIn("<|det|>title", extracted)
        self.assertIn("<|det|>image", extracted)
        self.assertNotIn("GET /health", extracted)
        self.assertNotIn("save results", extracted)

    def test_unlimited_ocr_endpoint_is_disabled_by_default(self):
        response = self.client.post(
            "/api/unlimited-ocr",
            json={"image": "AA==", "fileType": 1},
        )
        self.assertEqual(response.status_code, 404)

    def test_task_source_is_stored_outside_task_json_and_page_ranges_can_be_read(self):
        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=72, height=72)
        pdf_buffer = io.BytesIO()
        writer.write(pdf_buffer)
        pdf_bytes = pdf_buffer.getvalue()

        upload_response = self.client.post(
            "/api/tasks/task_src/source",
            files={"file": ("source.pdf", pdf_bytes, "application/pdf")},
        )
        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(upload_response.json()["url"], "/api/tasks/task_src/source")

        page_response = self.client.get("/api/tasks/task_src/source/pages?start_page=2&end_page=3")
        self.assertEqual(page_response.status_code, 200)
        subset = PdfReader(io.BytesIO(page_response.content))
        self.assertEqual(len(subset.pages), 2)

    def test_task_save_strips_heavy_fields_when_external_source_exists(self):
        self.client.post(
            "/api/tasks/task_big/source",
            files={"file": ("source.pdf", b"%PDF-1.4\n", "application/pdf")},
        )
        task = {
            "id": "task_big",
            "name": "big.pdf",
            "sourceKind": "pdf",
            "sourceUrl": "/api/tasks/task_big/source",
            "sourceDataUrl": "data:application/pdf;base64," + ("x" * 1000),
            "batches": [
                {
                    "id": "b1",
                    "status": "pending",
                    "pageCount": 20,
                    "payloadDataUrl": "data:application/pdf;base64," + ("y" * 1000),
                }
            ],
        }

        response = self.client.put("/api/tasks/task_big", json=task)
        self.assertEqual(response.status_code, 200)

        detail = self.client.get("/api/tasks/task_big").json()
        self.assertEqual(detail["sourceUrl"], "/api/tasks/task_big/source")
        self.assertNotIn("sourceDataUrl", detail)
        self.assertNotIn("payloadDataUrl", detail["batches"][0])

    def test_task_save_splits_results_into_sidecar_and_preserves_them_on_metadata_save(self):
        task = {
            "id": "task_side",
            "name": "sidecar.pdf",
            "sourceKind": "pdf",
            "status": "processing",
            "pageCount": 1,
            "batches": [
                {"id": "b1", "status": "completed", "pageCount": 1, "markdown": "Batch text"}
            ],
            "markdown": "# Heavy Markdown",
            "images": {"ocr_images/a.jpg": "base64-image"},
            "ocrResults": [{"markdown": {"text": "# Heavy Markdown"}}],
        }

        response = self.client.put("/api/tasks/task_side", json=task)
        self.assertEqual(response.status_code, 200)

        task_path = Path(self.temp_dir.name) / "task_side" / "task.json"
        result_path = Path(self.temp_dir.name) / "task_side" / "result.json"
        stored = json.loads(task_path.read_text(encoding="utf-8"))
        self.assertNotIn("markdown", stored)
        self.assertNotIn("images", stored)
        self.assertNotIn("ocrResults", stored)
        self.assertTrue(result_path.exists())

        metadata_only = {
            "id": "task_side",
            "name": "sidecar.pdf",
            "sourceKind": "pdf",
            "status": "completed",
            "pageCount": 1,
            "batches": [{"id": "b1", "status": "completed", "pageCount": 1}],
            "_preserveResult": True,
        }
        response = self.client.put("/api/tasks/task_side", json=metadata_only)
        self.assertEqual(response.status_code, 200)

        detail = self.client.get("/api/tasks/task_side").json()
        self.assertEqual(detail["markdown"], "# Heavy Markdown")
        self.assertEqual(detail["images"], {"ocr_images/a.jpg": "base64-image"})
        self.assertEqual(detail["ocrResults"], [{"markdown": {"text": "# Heavy Markdown"}}])
        self.assertEqual(detail["batches"][0]["markdown"], "Batch text")

    def test_batch_markdown_only_task_is_marked_as_having_markdown(self):
        task = {
            "id": "task_batch_markdown",
            "name": "batch-only.pdf",
            "batches": [{"id": "b1", "status": "completed", "pageCount": 1, "markdown": "Batch text"}],
        }

        response = self.client.put("/api/tasks/task_batch_markdown", json=task)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["task"]["hasMarkdown"])

    def test_clear_tasks_only_removes_task_directories(self):
        task_dir = Path(self.temp_dir.name) / "task_keep"
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "task.json").write_text('{"id":"task_keep"}', encoding="utf-8")
        keep_file = Path(self.temp_dir.name) / "keep.txt"
        keep_file.write_text("keep", encoding="utf-8")
        keep_dir = Path(self.temp_dir.name) / "docs"
        keep_dir.mkdir(exist_ok=True)

        response = self.client.delete("/api/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(task_dir.exists())
        self.assertTrue(keep_file.exists())
        self.assertTrue(keep_dir.exists())

    def test_model_runtime_switch_is_rejected_while_ocr_is_active(self):
        self.server.ocr_active_count = 1
        try:
            with patch.object(self.server, "model_control_available", return_value=True):
                response = self.client.post("/api/model-runtime/switch", json={"modelId": "pp-ocrv6"})
            self.assertEqual(response.status_code, 409)
        finally:
            self.server.ocr_active_count = 0

    def test_ocr_request_is_rejected_during_model_switch(self):
        self.server.set_model_runtime_operation("switching", "Switching to pp-ocrv6", "pp-ocrv6")
        try:
            response = self.client.post(
                "/api/paddleocr-vl-1.6",
                json={"image": "AA==", "fileType": 1},
            )
            self.assertEqual(response.status_code, 409)
        finally:
            self.server.set_model_runtime_operation("idle", "", "paddleocr-vl-1.6")


if __name__ == "__main__":
    unittest.main()
