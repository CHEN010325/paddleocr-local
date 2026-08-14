import asyncio
import base64
import importlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image


def png_bytes(size=(8, 6), color="white"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class _StreamingResponse:
    def __init__(self, lines):
        self.lines = lines

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class UnlimitedOcrAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = importlib.import_module("unlimited_ocr_adapter")
        cls.client = TestClient(cls.adapter.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_backend_normalization_and_persisted_selection(self):
        with patch.object(self.adapter, "SUPPORTED_BACKENDS", {"transformers", "sglang"}):
            self.assertEqual(self.adapter.normalize_backend(" SGLANG "), "sglang")
            self.assertEqual(self.adapter.normalize_backend("bad", "transformers"), "transformers")
            with self.assertRaises(HTTPException):
                self.adapter.normalize_backend("bad", "also-bad")

        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "runtime.json"
            settings.write_text(
                json.dumps({"unlimitedOcrBackend": "sglang"}),
                encoding="utf-8",
            )
            with (
                patch.dict(
                    "os.environ",
                    {"PANDOCR_RUNTIME_SETTINGS_FILE": str(settings)},
                    clear=False,
                ),
                patch.object(self.adapter, "SUPPORTED_BACKENDS", {"transformers", "sglang"}),
            ):
                self.assertEqual(self.adapter.read_persisted_backend("transformers"), "sglang")

            settings.write_text("not-json", encoding="utf-8")
            with patch.dict(
                "os.environ",
                {"PANDOCR_RUNTIME_SETTINGS_FILE": str(settings)},
                clear=False,
            ):
                self.assertEqual(self.adapter.read_persisted_backend("transformers"), "transformers")

    def test_input_decoding_type_inference_and_image_conversion(self):
        encoded = base64.b64encode(b"%PDF-1.4\n").decode("ascii")
        self.assertEqual(
            self.adapter.decode_base64_payload(f"data:application/pdf;base64,{encoded}"),
            b"%PDF-1.4\n",
        )
        self.assertEqual(self.adapter.infer_file_type(b"%PDF-1.4\n", None), 0)
        self.assertEqual(self.adapter.infer_file_type(b"image", None), 1)
        self.assertEqual(self.adapter.infer_file_type(b"image", 0), 0)
        with self.assertRaises(HTTPException):
            self.adapter.decode_base64_payload("invalid!")

        converted = self.adapter.image_bytes_to_png(png_bytes())
        self.assertEqual(Image.open(io.BytesIO(converted)).format, "PNG")
        self.assertEqual(self.adapter.image_bytes_to_png(b"not-an-image"), b"not-an-image")
        with self.assertRaises(HTTPException):
            self.adapter.prepare_image_pages_and_texts(b"data", 9)

    def test_transformers_device_selection_validates_requested_accelerator(self):
        torch_stub = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        )
        with patch.object(self.adapter, "TRANSFORMERS_DEVICE", "auto"):
            self.assertEqual(self.adapter.select_transformers_device(torch_stub), "cpu")
        with patch.object(self.adapter, "TRANSFORMERS_DEVICE", "cuda"):
            with self.assertRaisesRegex(RuntimeError, "CUDA is not available"):
                self.adapter.select_transformers_device(torch_stub)
        with patch.object(self.adapter, "TRANSFORMERS_DEVICE", "invalid"):
            with self.assertRaisesRegex(RuntimeError, "must be auto"):
                self.adapter.select_transformers_device(torch_stub)

    def test_queue_writer_progress_and_result_extraction(self):
        writer = self.adapter.QueueTextWriter()
        self.assertEqual(writer.write("hello"), 5)
        self.assertEqual(writer.write(" world"), 6)
        self.assertIsNone(writer.flush())
        self.assertEqual(writer.text(), "hello world")

        self.assertFalse(self.adapter.should_emit_stream_progress("same", "same", 0))
        self.assertTrue(self.adapter.should_emit_stream_progress("x" * 24, "", 0))
        self.assertTrue(self.adapter.should_emit_stream_progress("short.", "", 0))

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                self.adapter.extract_text_from_transformers_result(
                    {"result": {"text": "nested result"}},
                    directory,
                ),
                "nested result",
            )
            Path(directory, "result.md").write_text("file result", encoding="utf-8")
            self.assertEqual(
                self.adapter.extract_text_from_transformers_result(None, directory),
                "file result",
            )

    def test_stream_delta_and_collection_ignore_malformed_events(self):
        self.assertEqual(
            self.adapter.parse_stream_delta(
                'data: {"choices":[{"delta":{"content":"Hello"}}]}'
            ),
            "Hello",
        )
        self.assertEqual(self.adapter.parse_stream_delta("event: ping"), "")
        self.assertEqual(self.adapter.parse_stream_delta("data: not-json"), "")

        response = _StreamingResponse(
            [
                "event: ping",
                'data: {"choices":[{"delta":{"content":"Hello "}}]}',
                "data: malformed",
                'data: {"choices":[{"delta":{"content":"world"}}]}',
                "data: [DONE]",
            ]
        )
        with patch.object(self.adapter, "detect_degenerate_repetition", return_value=None):
            text = asyncio.run(self.adapter.collect_streaming_response(response))
        self.assertEqual(text, "Hello world")

    def test_layout_assignment_and_visual_backfill(self):
        raw = (
            "<|det|>image [10, 100, 300, 400]<|/det|>"
            "<|det|>image_caption [10, 420, 300, 450]<|/det|>"
            "A unique caption phrase with enough words for page matching."
        )
        blocks = self.adapter.parse_layout_blocks(raw)
        page_texts = [
            "unrelated text from the first page",
            self.adapter.normalize_anchor_text(
                "A unique caption phrase with enough words for page matching."
            ),
        ]
        self.adapter.assign_block_pages(blocks, 2, page_texts)

        self.assertEqual(blocks[1]["page_index"], 1)
        self.assertEqual(blocks[1]["page_confidence"], "text")
        self.assertEqual(blocks[0]["page_index"], 1)
        self.assertEqual(blocks[0]["page_confidence"], "caption")
        self.assertIsNone(self.adapter.parse_bbox("[1, 2]"))
        self.assertEqual(self.adapter.parse_bbox("[-1, 2.5, 3, 4, 5]"), [-1.0, 2.5, 3.0, 4.0])

    def test_crop_helpers_handle_invalid_and_valid_regions(self):
        image = Image.new("RGB", (100, 100), "white")
        self.assertIsNone(self.adapter.scaled_crop_box([20, 20, 10, 30], image))
        self.assertIsNone(self.adapter.crop_block_image({}, [png_bytes()], 1))

        cropped = self.adapter.crop_block_image(
            {"bbox": [100, 100, 500, 500], "page_index": 0, "label": "image"},
            [png_bytes((1000, 1000))],
            1,
        )
        self.assertIsNotNone(cropped)
        path, encoded = cropped
        self.assertEqual(path, "ocr_images/unlimited_p1_image_1.png")
        self.assertTrue(base64.b64decode(encoded).startswith(b"\x89PNG"))

    def test_ocr_endpoint_uses_selected_backend_without_real_model(self):
        markdown = "<|det|>title [1, 2, 3, 4]<|/det|>Hello"
        with patch.object(
            self.adapter,
            "generate_markdown",
            new=AsyncMock(return_value=(markdown, {"backend": "transformers"})),
        ) as generate:
            response = self.client.post(
                "/ocr",
                json={
                    "file": base64.b64encode(png_bytes()).decode("ascii"),
                    "fileType": 1,
                    "backend": "transformers",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["markdown"], "# Hello")
        self.assertEqual(generate.await_args.args[2], "transformers")

    def test_health_reports_sglang_and_transformers_state(self):
        with patch.object(
            self.adapter,
            "sglang_health_status",
            new=AsyncMock(return_value={"ready": True, "statusCode": 200}),
        ):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertIn("transformers", response.json())
        self.assertTrue(response.json()["sglang"]["ready"])


class OvisOcr2AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter = importlib.import_module("ovisocr2_adapter")
        cls.client = TestClient(cls.adapter.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def setUp(self):
        self.adapter.PARSER = None
        self.adapter.PARSER_ERROR = None

    def test_generation_cleanup_removes_repeated_tail_and_restart(self):
        unit = "repeat-block-"
        prefix = "prefix-" * 120
        repeated = prefix + (unit * 10)
        cleaned = self.adapter.VllmOvisOCR2Parser.clean_truncated_repeats(
            repeated,
            min_text_len=100,
            max_period=20,
            min_repeat_chars=40,
            min_repeat_times=4,
        )
        self.assertLess(len(cleaned), len(repeated))

        restarted = "Document title\nFirst paragraph\nSecond paragraph\nDocument title\nduplicate"
        self.assertEqual(
            self.adapter.VllmOvisOCR2Parser.clean_restarted_document(restarted),
            "Document title\nFirst paragraph\nSecond paragraph",
        )
        self.assertEqual(
            self.adapter.VllmOvisOCR2Parser.clean_restarted_document("short\nbody\nshort"),
            "short\nbody\nshort",
        )

    def test_decode_and_prepare_image_validate_inputs(self):
        encoded = base64.b64encode(png_bytes()).decode("ascii")
        decoded = self.adapter.decode_file_payload(f"data:image/png;base64,{encoded}")
        pages, file_type = self.adapter.prepare_images(decoded, None)

        self.assertEqual(file_type, 1)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].mode, "RGB")
        with self.assertRaises(HTTPException):
            self.adapter.decode_file_payload("invalid!")
        with self.assertRaises(HTTPException):
            self.adapter.prepare_images(b"not-an-image", 1)

    def test_build_response_combines_pages_and_cropped_images(self):
        response = self.adapter.build_response(
            [
                'Page one\n<img src="images/bbox_100_100_500_500.jpg" />',
                "Page two",
            ],
            [
                Image.new("RGB", (100, 100), "white"),
                Image.new("RGB", (100, 100), "black"),
            ],
            0,
        )

        self.assertIn("\n\n---\n\n", response["markdown"])
        self.assertEqual(len(response["layoutParsingResults"]), 2)
        self.assertEqual(len(response["images"]), 1)
        self.assertEqual(response["layoutParsingResults"][0]["parsing_res_list"][0]["block_label"], "image")

    def test_get_parser_caches_success_and_records_failure(self):
        parser = object()
        with patch.object(self.adapter, "create_parser", return_value=parser) as create:
            first = asyncio.run(self.adapter.get_parser())
            second = asyncio.run(self.adapter.get_parser())
        self.assertIs(first, parser)
        self.assertIs(second, parser)
        create.assert_called_once()

        self.adapter.PARSER = None
        with patch.object(self.adapter, "create_parser", side_effect=RuntimeError("load failed")):
            with self.assertRaisesRegex(RuntimeError, "load failed"):
                asyncio.run(self.adapter.get_parser())
        self.assertEqual(self.adapter.PARSER_ERROR, "load failed")

    def test_health_transitions_from_loading_to_ready(self):
        loading = self.client.get("/health")
        self.assertEqual(loading.status_code, 503)
        self.assertIn("loading", loading.json()["detail"])

        self.adapter.PARSER = object()
        ready = self.client.get("/health")
        self.assertEqual(ready.status_code, 200)
        self.assertTrue(ready.json()["modelLoaded"])

    def test_ocr_endpoint_runs_cached_parser_without_loading_model(self):
        parser = SimpleNamespace(parse=lambda pages: ["Parsed page" for _ in pages])
        with patch.object(
            self.adapter,
            "get_parser",
            new=AsyncMock(return_value=parser),
        ):
            response = self.client.post(
                "/ocr",
                json={
                    "file": base64.b64encode(png_bytes()).decode("ascii"),
                    "fileType": 1,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["markdown"], "Parsed page")
        self.assertEqual(response.json()["fileType"], 1)


if __name__ == "__main__":
    unittest.main()
