import asyncio
import base64
import io
import json
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import fitz
from fastapi import HTTPException
from PIL import Image

import unlimited_ocr_adapter as adapter


def png_bytes(size=(20, 20), mode="RGB"):
    output = io.BytesIO()
    Image.new(mode, size, 128 if mode == "L" else "white").save(output, "PNG")
    return output.getvalue()


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_):
        return False


class FakeClient:
    def __init__(self, responses=None, get=None, **_):
        self.responses = iter(responses or [])
        self.get_result = get

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def stream(self, *_args, **_kwargs):
        return AsyncContext(next(self.responses))

    async def get(self, _url):
        if isinstance(self.get_result, BaseException):
            raise self.get_result
        return self.get_result


class StreamResponse:
    def __init__(self, status=200, lines=(), body=b"error"):
        self.status_code = status
        self.lines = lines
        self.body = body

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    async def aread(self):
        return self.body


class UnlimitedRuntimeTests(unittest.TestCase):
    def tearDown(self):
        adapter.TRANSFORMERS_TOKENIZER = None
        adapter.TRANSFORMERS_MODEL = None
        adapter.TRANSFORMERS_MODEL_ERROR = None
        adapter.TRANSFORMERS_MODEL_LOADING = False
        adapter.TRANSFORMERS_RUNTIME = {}
        adapter.NO_REPEAT_PROCESSOR_STR = None

    def test_environment_runtime_and_error_helpers(self):
        with patch.dict("os.environ", {"BOOL": " YES "}):
            self.assertTrue(adapter.parse_bool_env("BOOL"))
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(adapter.parse_bool_env("BOOL"))
            self.assertEqual(adapter.read_persisted_backend("transformers"), "transformers")
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing.json")
            with patch.dict("os.environ", {"PANDOCR_RUNTIME_SETTINGS_FILE": missing}):
                self.assertEqual(adapter.read_persisted_backend("transformers"), "transformers")
            settings = Path(directory) / "settings.json"
            settings.write_text("[]", encoding="utf-8")
            with patch.dict("os.environ", {"PANDOCR_RUNTIME_SETTINGS_FILE": str(settings)}):
                self.assertEqual(adapter.read_persisted_backend("transformers"), "transformers")

        with patch.object(adapter, "TRANSFORMERS_RUNTIME", {"device": "mps", "dtype": "float16"}):
            self.assertEqual(adapter.transformers_runtime_label(), "mps/float16")
        self.assertIn("hello", adapter.transformers_status_markdown("hello"))
        self.assertTrue(adapter.is_accelerator_oom(RuntimeError("out of memory")))
        self.assertFalse(adapter.is_accelerator_oom(RuntimeError("ordinary")))
        self.assertEqual(adapter.transformers_error_detail(RuntimeError()), "RuntimeError")
        with patch.object(adapter, "cleanup_torch_accelerator_cache") as cleanup:
            self.assertIn("Apple Silicon", adapter.transformers_error_detail(RuntimeError("MPS backend out of memory")))
            cleanup.assert_called_once()
        self.assertIn("accelerator memory", adapter.transformers_error_detail(RuntimeError("CUDA out of memory")))

    def test_cleanup_cache_all_paths(self):
        torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: True, empty_cache=MagicMock(), ipc_collect=MagicMock()),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
            mps=SimpleNamespace(empty_cache=MagicMock()),
        )
        with patch.dict(sys.modules, {"torch": torch}):
            adapter.cleanup_torch_accelerator_cache()
        torch.cuda.empty_cache.assert_called_once()
        torch.mps.empty_cache.assert_called_once()
        with patch.dict(sys.modules, {"torch": None}):
            adapter.cleanup_torch_accelerator_cache()

    def test_lifespan_preload_and_cancel(self):
        async def scenario():
            blocker = asyncio.Event()

            async def preload():
                await blocker.wait()

            with patch.object(adapter, "PRELOAD_TRANSFORMERS", True), patch.object(
                adapter, "preload_transformers_components", preload
            ):
                context = adapter.lifespan(adapter.app)
                await context.__aenter__()
                await context.__aexit__(None, None, None)
            with patch.object(adapter, "PRELOAD_TRANSFORMERS", False):
                async with adapter.lifespan(adapter.app):
                    pass

        asyncio.run(scenario())

    def test_read_input_json_and_multipart_errors(self):
        class Request:
            def __init__(self, content_type="", payload=None, form=None, json_error=None):
                self.headers = {"content-type": content_type}
                self.payload = payload
                self.form_value = form
                self.json_error = json_error

            async def form(self):
                return self.form_value

            async def json(self):
                if self.json_error:
                    raise self.json_error
                return self.payload

        with self.assertRaises(HTTPException):
            asyncio.run(adapter.read_input(Request("multipart/form-data", form={})))
        upload = SimpleNamespace(read=AsyncMock(return_value=png_bytes()))
        result = asyncio.run(
            adapter.read_input(
                Request(
                    "multipart/form-data",
                    form={"file": upload, "fileType": "1", "unlimitedOcrBackend": "transformers"},
                )
            )
        )
        self.assertEqual(result[1:], (1, "transformers"))
        with self.assertRaises(HTTPException):
            asyncio.run(adapter.read_input(Request(payload=None, json_error=ValueError())))
        with self.assertRaises(HTTPException):
            asyncio.run(adapter.read_input(Request(payload={})))
        encoded = base64.b64encode(png_bytes()).decode()
        result = asyncio.run(adapter.read_input(Request(payload={"image": encoded})))
        self.assertEqual(result[1], 1)
        self.assertIsNone(adapter.parse_optional_int(""))
        self.assertEqual(adapter.parse_optional_int("2"), 2)

    def test_images_pdf_and_encoding(self):
        rgba = io.BytesIO()
        Image.new("RGBA", (2, 2), "red").save(rgba, "PNG")
        self.assertEqual(Image.open(io.BytesIO(adapter.image_bytes_to_png(rgba.getvalue()))).mode, "RGB")
        content = adapter.encode_image_content(b"abc", "x/test")
        self.assertIn(base64.b64encode(b"abc").decode(), content["image_url"]["url"])

        document = fitz.open()
        document.new_page().insert_text((72, 72), "Page one anchor text")
        document.new_page().insert_text((72, 72), "Page two anchor text")
        pdf = document.tobytes()
        document.close()
        pages = adapter.pdf_to_png_pages(pdf, 72)
        self.assertEqual(len(pages), 2)
        self.assertEqual(len(adapter.extract_pdf_page_texts(pdf)), 2)
        self.assertEqual(adapter.extract_pdf_page_texts(b"image"), [])
        self.assertEqual(len(adapter.prepare_image_pages_and_texts(pdf, 0)[0]), 2)

        fake_document = MagicMock()
        fake_document.page_count = adapter.MAX_PAGES_PER_REQUEST + 1
        with patch.dict(sys.modules, {"fitz": SimpleNamespace(open=lambda **_: fake_document, Matrix=lambda *_: None)}):
            with self.assertRaises(HTTPException):
                adapter.pdf_to_png_pages(pdf, 72)
        fake_document.close.assert_called_once()

        broken = MagicMock()
        broken.__iter__.side_effect = RuntimeError("text")
        broken.page_count = 1
        with patch("fitz.open", return_value=broken):
            self.assertEqual(adapter.extract_pdf_page_texts(pdf), [])
        broken.close.assert_called_once()

    def test_device_and_dtype_all_choices(self):
        torch = SimpleNamespace(
            float32="f32",
            bfloat16="bf16",
            float16="f16",
            cuda=SimpleNamespace(is_available=lambda: True),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
        )
        for requested, expected in (("cuda", "cuda"), ("mps", "mps"), ("cpu", "cpu")):
            with patch.object(adapter, "TRANSFORMERS_DEVICE", requested):
                self.assertEqual(adapter.select_transformers_device(torch), expected)
        with patch.object(adapter, "TRANSFORMERS_DEVICE", "auto"):
            self.assertEqual(adapter.select_transformers_device(torch), "cuda")
            torch.cuda.is_available = lambda: False
            self.assertEqual(adapter.select_transformers_device(torch), "mps")
        unavailable = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        )
        with patch.object(adapter, "TRANSFORMERS_DEVICE", "mps"), self.assertRaises(RuntimeError):
            adapter.select_transformers_device(unavailable)
        for requested, expected in (("float32", "f32"), ("fp32", "f32"), ("bfloat16", "bf16"), ("bf16", "bf16"), ("float16", "f16"), ("fp16", "f16")):
            with patch.object(adapter, "TRANSFORMERS_DTYPE", requested):
                self.assertEqual(adapter.select_transformers_dtype(torch, "cpu"), expected)
        with patch.object(adapter, "TRANSFORMERS_DTYPE", "auto"):
            self.assertEqual(adapter.select_transformers_dtype(torch, "cuda"), "bf16")
            self.assertEqual(adapter.select_transformers_dtype(torch, "cpu"), "f32")
        with patch.object(adapter, "TRANSFORMERS_DTYPE", "bad"), self.assertRaises(RuntimeError):
            adapter.select_transformers_dtype(torch, "cpu")

    def test_model_loading_cache_failure_preload_and_unload(self):
        async def scenario():
            adapter.TRANSFORMERS_TOKENIZER = "tokenizer"
            adapter.TRANSFORMERS_MODEL = "model"
            self.assertEqual(await adapter.get_transformers_components(), ("tokenizer", "model"))
            adapter.TRANSFORMERS_TOKENIZER = None
            adapter.TRANSFORMERS_MODEL = None
            with patch("asyncio.to_thread", new=AsyncMock(return_value=("t", "m"))):
                self.assertEqual(await adapter.get_transformers_components(), ("t", "m"))
                self.assertIsNotNone(adapter.TRANSFORMERS_MODEL_LOADED_AT)
            adapter.TRANSFORMERS_TOKENIZER = None
            adapter.TRANSFORMERS_MODEL = None
            with patch("asyncio.to_thread", new=AsyncMock(side_effect=RuntimeError("load"))):
                with self.assertRaises(RuntimeError):
                    await adapter.get_transformers_components()
            with patch.object(adapter, "PRELOAD_TRANSFORMERS", False), patch.object(
                adapter, "get_transformers_components", new=AsyncMock()
            ) as get:
                await adapter.preload_transformers_components()
                get.assert_not_awaited()
                await adapter.preload_transformers_components(force=True)
                get.assert_awaited_once()
            with patch.object(adapter, "get_transformers_components", new=AsyncMock(side_effect=RuntimeError())):
                await adapter.preload_transformers_components(force=True)
            adapter.TRANSFORMERS_TOKENIZER = "t"
            adapter.TRANSFORMERS_MODEL = "m"
            with patch.object(adapter, "cleanup_torch_accelerator_cache"):
                self.assertTrue((await adapter.unload_transformers_components())["released"])
                self.assertFalse((await adapter.unload_transformers_components())["released"])

        asyncio.run(scenario())

    def test_loader_inner_function_for_cuda_and_mps(self):
        class Model:
            def eval(self):
                return self

            def cuda(self):
                self.moved = "cuda"
                return self

            def to(self, device):
                self.moved = device
                return self

        for device in ("cuda", "mps"):
            adapter.TRANSFORMERS_TOKENIZER = None
            adapter.TRANSFORMERS_MODEL = None
            model = Model()
            torch = SimpleNamespace(float32="torch.float32", bfloat16="torch.bfloat16", float16="torch.float16")
            transformers = SimpleNamespace(
                AutoTokenizer=SimpleNamespace(from_pretrained=MagicMock(return_value="tok")),
                AutoModel=SimpleNamespace(from_pretrained=MagicMock(return_value=model)),
            )

            async def direct(function, *_args):
                return function()

            with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}), patch.object(
                adapter, "select_transformers_device", return_value=device
            ), patch.object(adapter, "select_transformers_dtype", return_value="torch.float16"), patch.object(
                adapter, "TRANSFORMERS_ATTENTION_IMPLEMENTATION", "flash"
            ), patch("asyncio.to_thread", side_effect=direct):
                asyncio.run(adapter.get_transformers_components())
            self.assertEqual(model.moved, device)
            kwargs = transformers.AutoModel.from_pretrained.call_args.kwargs
            self.assertEqual(kwargs["attn_implementation"], "flash")

    def test_file_writer_stdout_and_result_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(len(adapter.write_temp_image_files([b"a", b"b"], directory)), 2)
            writer = adapter.QueueTextWriter(queue.Queue())
            self.assertEqual(writer.write(""), 0)
            writer.write("x")
            self.assertEqual(writer.output_queue.get(), "x")
            for noise in ("", "INFO:x", "WARNING:x", "The attention mask", "Setting `pad_token_id`", "image:x", "other:x", "===", "%|x"):
                self.assertTrue(adapter.is_transformers_stdout_noise(noise))
            self.assertFalse(adapter.is_transformers_stdout_noise("content"))
            self.assertEqual(adapter.extract_layout_text_from_transformers_stdout("plain"), "")
            layout = "log\n<|det|>text [1,2,3,4]<|/det|>\nbody\nINFO:x"
            self.assertIn("<|det|>", adapter.extract_layout_text_from_transformers_stdout(layout))
            self.assertEqual(adapter.extract_text_from_transformers_result("direct", directory), "direct")
            self.assertEqual(adapter.extract_text_from_transformers_result({"markdown": "md"}, directory), "md")
            self.assertEqual(adapter.extract_text_from_transformers_result({"output": {"markdown": "nested"}}, directory), "nested")
            Path(directory, "empty.txt").write_text("", encoding="utf-8")
            Path(directory, "data.json").write_text('{"text":"json"}', encoding="utf-8")
            self.assertEqual(adapter.extract_text_from_transformers_result(None, directory), "json")
            Path(directory, "bad.json").write_text("{", encoding="utf-8")
            with patch("os.path.getmtime", return_value=1):
                self.assertIn(adapter.extract_text_from_transformers_result(None, directory), {"json", ""})

    def test_attempts_and_sync_inference(self):
        with patch.object(adapter, "TRANSFORMERS_MPS_OOM_RETRY", False), patch.object(adapter, "SINGLE_IMAGE_MODE", "base"):
            self.assertEqual(len(adapter.single_image_transformers_attempts()), 1)
        with patch.object(adapter, "TRANSFORMERS_MPS_OOM_RETRY", True), patch.object(
            adapter, "TRANSFORMERS_SINGLE_BASE_SIZE", adapter.TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE
        ), patch.object(adapter, "TRANSFORMERS_SINGLE_IMAGE_SIZE", adapter.TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE), patch.object(
            adapter, "TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS", adapter.MAX_TOKENS
        ), patch.object(adapter, "SINGLE_IMAGE_MODE", "base"):
            self.assertEqual(len(adapter.single_image_transformers_attempts()), 1)

        model = SimpleNamespace(
            infer=MagicMock(return_value="single"),
            infer_multi=MagicMock(return_value={"text": "multi"}),
        )
        with patch.object(adapter, "cleanup_torch_accelerator_cache"):
            result, config = adapter.run_transformers_inference_sync("tok", model, [png_bytes()], 1)
            self.assertEqual(result, "single")
            self.assertEqual(config["backend"], "transformers")
            result, config = adapter.run_transformers_inference_sync("tok", model, [png_bytes(), png_bytes()], 0)
            self.assertEqual(result, "multi")
            self.assertEqual(config["image_mode"], "base")

        attempts = [
            {"label": "a", "crop_mode": True, "base_size": 1, "image_size": 1, "max_tokens": 1},
            {"label": "b", "crop_mode": False, "base_size": 2, "image_size": 2, "max_tokens": 2},
        ]
        retry_model = SimpleNamespace(infer=MagicMock(side_effect=[RuntimeError("out of memory"), "ok"]))
        with patch.object(adapter, "single_image_transformers_attempts", return_value=attempts), patch.object(
            adapter, "cleanup_torch_accelerator_cache"
        ):
            self.assertEqual(adapter.run_transformers_inference_sync("t", retry_model, [png_bytes()], 1)[0], "ok")
        fail_model = SimpleNamespace(infer=MagicMock(side_effect=RuntimeError("bad")))
        with patch.object(adapter, "single_image_transformers_attempts", return_value=attempts), patch.object(
            adapter, "cleanup_torch_accelerator_cache"
        ), self.assertRaises(RuntimeError):
            adapter.run_transformers_inference_sync("t", fail_model, [png_bytes()], 1)

    def test_payload_context_stream_parsers_and_generation(self):
        original = png_bytes((2000, 3000))
        resized = adapter.resize_sglang_pdf_page(original)
        self.assertLessEqual(max(Image.open(io.BytesIO(resized)).size), adapter.SGLANG_PDF_MAX_DIMENSION)
        self.assertEqual(adapter.resize_sglang_pdf_page(png_bytes((20, 20))), png_bytes((20, 20)))
        with patch.object(adapter, "get_no_repeat_processor_str", return_value="processor"):
            payload = adapter.build_sglang_payload([b"a", b"b"], 0)
        self.assertIn("custom_params", payload)
        self.assertEqual(adapter.build_sglang_payload([b"a"], 0)["images_config"]["image_mode"], "base")
        self.assertEqual(adapter.build_sglang_payload([b"a"], 1)["images_config"]["image_mode"], adapter.SINGLE_IMAGE_MODE)
        with patch.object(adapter, "SGLANG_MAX_TOKENS", 0), patch.object(
            adapter, "get_no_repeat_processor_str", return_value=None
        ):
            self.assertNotIn("max_tokens", adapter.build_sglang_payload([b"a"], 1))
        error = "maximum context length of 100 tokens; 70 tokens from the input messages and 50 tokens for the completion"
        with patch.object(adapter, "SGLANG_CONTEXT_TOKEN_RESERVE", 5):
            adjusted = adapter.adjust_sglang_payload_for_context_error({"max_tokens": 50}, error)
        self.assertEqual(adjusted["max_tokens"], 25)
        self.assertIsNone(adapter.adjust_sglang_payload_for_context_error({}, "other"))
        with patch.object(adapter, "SGLANG_CONTEXT_TOKEN_RESERVE", 100):
            self.assertIsNone(adapter.adjust_sglang_payload_for_context_error({"max_tokens": 50}, error))

        retry_payload = adapter.adjust_sglang_payload_for_degeneration(payload, "det det text")
        self.assertEqual(retry_payload["custom_params"]["ngram_size"], adapter.DEGENERATION_RETRY_NGRAM_SIZE)
        self.assertGreaterEqual(
            retry_payload["custom_params"]["window_size"],
            adapter.DEGENERATION_RETRY_WINDOW,
        )
        self.assertTrue(retry_payload["images_config"]["degeneration_retry"])
        self.assertEqual(payload["custom_params"]["ngram_size"], adapter.NO_REPEAT_NGRAM_SIZE)
        self.assertIsNone(adapter.adjust_sglang_payload_for_degeneration(retry_payload, "repeat"))
        self.assertIsNone(adapter.adjust_sglang_payload_for_degeneration({}, "repeat"))

        degenerate = StreamResponse(lines=['data: {"choices":[{"delta":{"content":"x"}}]}'])
        with patch.object(adapter, "detect_degenerate_repetition", return_value="repeat"):
            with self.assertRaises(adapter.DegenerateGenerationError):
                asyncio.run(adapter.collect_streaming_response(degenerate))
        self.assertEqual(adapter.parse_stream_delta("data: [DONE]"), "")
        self.assertEqual(adapter.parse_stream_delta('data: {"choices":[]}'), "")

        async def generation():
            with patch.object(adapter, "generate_transformers_markdown", new=AsyncMock(return_value=("t", {}))):
                self.assertEqual((await adapter.generate_markdown([b"a"], 1, "transformers"))[0], "t")
            okay = StreamResponse(lines=['data: {"choices":[{"delta":{"content":"ok"}}]}', "data: [DONE]"])
            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([okay])):
                self.assertEqual((await adapter.generate_markdown([b"a"], 1, "sglang"))[0], "ok")
            first_repeat = StreamResponse(lines=['data: {"choices":[{"delta":{"content":"bad"}}]}'])
            recovered = StreamResponse(lines=['data: {"choices":[{"delta":{"content":"recovered"}}]}', "data: [DONE]"])
            with patch.object(adapter, "get_no_repeat_processor_str", return_value="processor"), patch.object(
                adapter, "detect_degenerate_repetition", side_effect=["det det text", None]
            ), patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([first_repeat, recovered])):
                recovered_text, recovered_config = await adapter.generate_markdown([b"a"], 1, "sglang")
            self.assertEqual(recovered_text, "recovered")
            self.assertTrue(recovered_config["degeneration_retry"])

            repeated_again = StreamResponse(lines=['data: {"choices":[{"delta":{"content":"bad again"}}]}'])
            with patch.object(adapter, "get_no_repeat_processor_str", return_value="processor"), patch.object(
                adapter, "detect_degenerate_repetition", return_value="det det text"
            ), patch.object(
                adapter.httpx,
                "AsyncClient",
                return_value=FakeClient([first_repeat, repeated_again]),
            ):
                with self.assertRaises(HTTPException) as retry_error:
                    await adapter.generate_markdown([b"a"], 1, "sglang")
            self.assertEqual(retry_error.exception.status_code, 502)
            bad = StreamResponse(status=400, body=b"bad")
            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([bad])):
                with self.assertRaises(HTTPException):
                    await adapter.generate_markdown([b"a"], 1, "sglang")
            context = StreamResponse(status=400, body=error.encode())
            okay2 = StreamResponse(lines=["data: [DONE]"])
            with patch.object(adapter, "SGLANG_CONTEXT_TOKEN_RESERVE", 5), patch.object(
                adapter.httpx, "AsyncClient", return_value=FakeClient([context, okay2])
            ):
                await adapter.generate_markdown([b"a"], 1, "sglang")

        asyncio.run(generation())

    def test_layout_degeneration_and_rendering_helpers(self):
        self.assertEqual(adapter.normalize_newlines("a\r\nb\rc"), "a\nb\nc")
        self.assertEqual(adapter.compact_block_text(" a   b\n\n\nc "), "a b\n\nc")
        self.assertIsNone(adapter.parse_bbox(None))
        blocks = adapter.parse_layout_blocks("<|det|>text<|/det|>a<|det|>text [1,2,3,4]<|/det|>b")
        self.assertEqual(len(blocks), 2)
        self.assertIsNone(adapter.anchor_page_for_block({"text": "few words"}, ["few words"]))
        duplicate = "one two three four five"
        self.assertEqual(adapter.anchor_page_for_block({"text": duplicate}, [duplicate, duplicate], 1), 1)
        self.assertIsNone(adapter.anchor_page_for_block({"text": "alpha beta gamma delta epsilon"}, ["other"]))
        heuristic = [{"bbox": [0, 900, 1, 950], "text": "", "label": "text"}, {"bbox": [0, 10, 1, 20], "text": "", "label": "text"}]
        adapter.assign_block_pages(heuristic, 2)
        self.assertEqual(heuristic[1]["page_index"], 1)
        self.assertEqual(adapter.clamp_float(2), 1)
        self.assertFalse(adapter.has_repeat_position_run([1], 1, 2))
        self.assertFalse(adapter.has_repeat_position_run([1, 10, 11], 1, 3))
        self.assertTrue(adapter.has_repeat_position_run([1, 2, 3], 1, 3))
        with patch.object(adapter, "ENABLE_DEGENERATION_GUARD", False):
            self.assertIsNone(adapter.detect_degenerate_repetition("x " * 100))
        self.assertIsNone(adapter.detect_degenerate_repetition("short"))
        repeat = ("longword anotherword thirdword " * 30)
        self.assertIsNotNone(adapter.detect_degenerate_repetition(repeat))
        repeated_line = "\n".join(["29: end while Output: \u00e2 . text [509, 565, 704, 635]"] * 8)
        self.assertIn("end while output", adapter.detect_degenerate_repetition(repeated_line))
        leaked_trace = "\n".join(
            [
                "22: d(l_k) 23: d(l_k, ..., d_lk)",
                "22: d(l_k, text [516, text [516, 116, 882, 130]",
                "22: end while Output: \u00e2 . text [509, 565, 704, 635]",
            ]
        )
        self.assertEqual(adapter.detect_unlimited_structural_artifacts(leaked_trace), "layout coordinate trace")
        self.assertEqual(adapter.sanitize_unlimited_ocr_fallback(leaked_trace), "")
        self.assertEqual(adapter.render_streaming_markdown(leaked_trace), ("", {}))
        self.assertEqual(adapter.streaming_source_position("plain", 1), {"pageIndex": 0, "pageProgress": 0})
        position = adapter.streaming_source_position("<|det|>text [0,0,100,100]<|/det|>body", 1)
        self.assertEqual(position["pageNumber"], 1)

        large = Image.new("RGB", (2000, 2000))
        self.assertIsNotNone(adapter.scaled_crop_box([100, 100, 500, 500], large))
        self.assertIsNotNone(adapter.scaled_crop_box([0, 0, 200, 200], Image.new("RGB", (100, 100))))
        self.assertIsNone(adapter.crop_block_image({"bbox": [1, 1, 2, 2], "page_index": 2}, [png_bytes()], 1))
        self.assertIsNone(adapter.crop_block_image({"bbox": [1, 1, 2, 2]}, [b"bad"], 1))
        for label, expected in (
            ("header", ""),
            ("title", "# Title"),
            ("section_title", "## Title"),
            ("image_caption", "*Title*"),
            ("formula", "$$\nTitle\n$$"),
            ("image", "**Image:** Title"),
            ("text", "Title"),
        ):
            rendered, _ = adapter.format_unlimited_ocr_block(label, "Title", seen_title=label == "section_title")
            self.assertEqual(rendered, expected)
        self.assertEqual(adapter.render_unlimited_ocr_document(" plain ")[0], "plain")
        raw = "prefix<|det|>title [1,2,3,4]<|/det|>Title<|det|>image [1,2,10,10]<|/det|>pic"
        rendered, images = adapter.render_unlimited_ocr_document(raw, [png_bytes()])
        self.assertIn("# Title", rendered)
        self.assertTrue(images)
        self.assertEqual(adapter.normalize_markdown(raw), adapter.render_unlimited_ocr_document(raw)[0])
        self.assertEqual(adapter.render_streaming_markdown(" plain ")[0], "plain")
        sent = {"a": "old"}
        self.assertEqual(adapter.unsent_images({"a": "new", "b": "x"}, sent), {"a": "new", "b": "x"})
        response = adapter.build_adapter_response(raw, 2, 1, {}, [png_bytes(), png_bytes()])
        self.assertEqual(len(response["layoutParsingResults"]), 2)
        no_blocks = adapter.build_layout_parsing_results("plain", "plain", {}, 0, 1, {})
        self.assertEqual(no_blocks[0]["markdown"]["text"], "plain")

    def test_health_endpoints_and_simple_streams(self):
        async def events():
            yield {"x": "é"}

        async def collect(generator):
            return [item async for item in generator]

        self.assertIn("é", asyncio.run(collect(adapter.ndjson_event_stream(events())))[0])

        async def scenario():
            with patch.object(adapter, "SUPPORTED_BACKENDS", {"transformers"}):
                self.assertTrue((await adapter.sglang_health_status())["disabled"])
            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient(get=SimpleNamespace(status_code=204))):
                self.assertTrue((await adapter.sglang_health_status())["ready"])
            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient(get=RuntimeError("down"))):
                self.assertFalse((await adapter.sglang_health_status())["ready"])
            with patch.object(adapter, "preload_transformers_components", new=AsyncMock()), patch.object(
                adapter, "unload_transformers_components", new=AsyncMock(return_value={"released": True})
            ):
                self.assertEqual((await adapter.preload_transformers_backend())["status"], "ok")
                self.assertTrue((await adapter.unload_transformers_backend())["released"])

        asyncio.run(scenario())

    def test_remaining_small_branches(self):
        gif = io.BytesIO()
        Image.new("P", (2, 2)).save(gif, "GIF", save_all=True, append_images=[Image.new("P", (2, 2))])
        self.assertTrue(adapter.image_bytes_to_png(gif.getvalue()).startswith(b"\x89PNG"))

        class LoadingLock:
            async def __aenter__(self):
                adapter.TRANSFORMERS_TOKENIZER = "late-tokenizer"
                adapter.TRANSFORMERS_MODEL = "late-model"

            async def __aexit__(self, *_):
                return False

        adapter.TRANSFORMERS_TOKENIZER = None
        adapter.TRANSFORMERS_MODEL = None
        with patch.object(adapter, "TRANSFORMERS_MODEL_LOCK", LoadingLock()):
            self.assertEqual(asyncio.run(adapter.get_transformers_components()), ("late-tokenizer", "late-model"))

        with patch.object(adapter, "TRANSFORMERS_ATTENTION_IMPLEMENTATION", ""):
            self.test_loader_inner_function_for_cuda_and_mps()

        self.assertEqual(adapter.extract_layout_text_from_transformers_stdout("INFO:<|det|>"), "")
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "empty.txt").write_text("", encoding="utf-8")
            self.assertEqual(adapter.extract_text_from_transformers_result({"result": {}}, directory), "")
            Path(directory, "nested.json").write_text('{"result":{"markdown":"from json"}}', encoding="utf-8")
            self.assertEqual(adapter.extract_text_from_transformers_result(None, directory), "from json")

        with patch.object(adapter, "ENABLE_NO_REPEAT_PROCESSOR", False):
            self.assertIsNone(adapter.get_no_repeat_processor_str())
        adapter.NO_REPEAT_PROCESSOR_STR = "cached"
        self.assertEqual(adapter.get_no_repeat_processor_str(), "cached")
        adapter.NO_REPEAT_PROCESSOR_STR = None
        processor_class = SimpleNamespace(to_str=MagicMock(return_value="generated"))
        module = SimpleNamespace(DeepseekOCRNoRepeatNGramLogitProcessor=processor_class)
        with patch.dict(sys.modules, {"sglang": MagicMock(), "sglang.srt": MagicMock(), "sglang.srt.sampling": MagicMock(), "sglang.srt.sampling.custom_logit_processor": module}):
            self.assertEqual(adapter.get_no_repeat_processor_str(), "generated")
        adapter.NO_REPEAT_PROCESSOR_STR = None
        with patch.dict(sys.modules, {"sglang": None}), patch.object(adapter, "DEFAULT_NO_REPEAT_PROCESSOR_STR", ""):
            self.assertIsNone(adapter.get_no_repeat_processor_str())

        # Force the defensive post-loop fallback, which normally cannot occur with range(2).
        with patch.object(adapter, "range", lambda *_: [], create=True):
            with self.assertRaises(HTTPException):
                asyncio.run(adapter.generate_markdown([b"a"], 1, "sglang"))

        words = "one two three four"
        self.assertIsNone(adapter.anchor_page_for_block({"text": words}, ["tiny"]))
        blocks = [
            {"label": "image", "page_confidence": "text"},
            {"label": "text"},
            {"label": "image"},
            {"label": "image_caption", "page_confidence": "heuristic"},
        ]
        adapter.backfill_visual_blocks(blocks)
        anchored = [{"bbox": [1, 2, 3, 4], "text": "unique anchor words enough now", "label": "text"}]
        adapter.assign_block_pages(anchored, 1, ["unique anchor words enough now"])
        no_bbox = [{"text": "", "label": "text"}]
        adapter.assign_block_pages(no_bbox, 2)

        with patch.object(adapter, "DEGENERATION_REPEAT_THRESHOLD", 999), patch.object(
            adapter, "DEGENERATION_CONSECUTIVE_REPEAT_THRESHOLD", 999
        ):
            self.assertIsNone(adapter.detect_degenerate_repetition("longword anotherword thirdword " * 30))
        bibliography = ("arxiv preprint arxiv other words here " * 20)
        adapter.detect_degenerate_repetition(bibliography)

        positioned = (
            "<|det|>text [0,0,100,100]<|/det|>unique anchor words enough now"
            "<|det|>text<|/det|>tail"
        )
        pos = adapter.streaming_source_position(positioned, 2, ["unique anchor words enough now", "other"])
        self.assertIn("bbox", pos)
        no_position = adapter.streaming_source_position("<|det|>text<|/det|>body", 1)
        self.assertNotIn("bbox", no_position)

        self.assertIsNone(adapter.scaled_crop_box([0, 0, 1, 1], SimpleNamespace(size=(-1, -1))))
        with patch.object(adapter, "scaled_crop_box", return_value=None):
            self.assertIsNone(adapter.crop_block_image({"bbox": [1, 1, 2, 2]}, [png_bytes()], 1))
        raw = "<|det|>image [1,2,10,10]<|/det|>pic<|det|>header<|/det|>skip"
        with patch.object(adapter, "crop_block_image", return_value=None):
            adapter.render_unlimited_ocr_document(raw, [png_bytes()])
        result = adapter.build_layout_parsing_results(
            "<|det|>text [1,2,3,4]<|/det|>body", "different", {}, 1, 1, {}
        )
        self.assertIn("block_bbox", result[0]["parsing_res_list"][0])
        self.assertEqual(adapter.normalize_markdown(" plain "), "plain")
        self.assertTrue(adapter.render_streaming_markdown(raw, [png_bytes()])[1])
        self.assertEqual(adapter.build_adapter_response("plain", 1, 1, {})["markdown"], "plain")

    def test_transformers_generation_and_stream_success_error(self):
        async def collect(events):
            return [event async for event in events]

        async def scenario():
            async def direct(function, *args, **kwargs):
                return function(*args, **kwargs)

            with patch.object(adapter, "get_transformers_components", new=AsyncMock(return_value=("t", "m"))), patch(
                "asyncio.to_thread", new=AsyncMock(return_value=("markdown", {"backend": "transformers"}))
            ):
                self.assertEqual((await adapter.generate_transformers_markdown([b"a"], 1))[0], "markdown")

            class ImmediateThread:
                def __init__(self, target, daemon=True):
                    self.target = target

                def start(self):
                    self.target()

                def is_alive(self):
                    return False

                def join(self, timeout=None):
                    pass

            adapter.TRANSFORMERS_MODEL = None
            layout = "<|det|>text [1,2,3,4]<|/det|>" + ("progress text " * 4)

            def inference(*_args):
                writer = _args[-1]
                writer.write(layout)
                return layout, {"backend": "transformers"}

            with patch.object(adapter, "get_transformers_components", new=AsyncMock(return_value=("t", "m"))), patch.object(
                adapter, "run_transformers_inference_sync", side_effect=inference
            ), patch.object(adapter.threading, "Thread", ImmediateThread), patch(
                "asyncio.to_thread", side_effect=direct
            ):
                events = await collect(adapter.stream_transformers_adapter_events([png_bytes()], 1))
            self.assertEqual(events[0]["type"], "status")
            self.assertTrue(any(event["type"] == "progress" for event in events))
            self.assertEqual(events[-1]["type"], "final")

            adapter.TRANSFORMERS_MODEL = object()
            with patch.object(adapter, "get_transformers_components", new=AsyncMock(return_value=("t", "m"))), patch.object(
                adapter, "run_transformers_inference_sync", side_effect=RuntimeError("out of memory")
            ), patch.object(adapter.threading, "Thread", ImmediateThread), patch.object(
                adapter, "cleanup_torch_accelerator_cache"
            ), patch(
                "asyncio.to_thread", side_effect=direct
            ):
                events = await collect(adapter.stream_transformers_adapter_events([png_bytes()], 1))
            self.assertEqual(events[-1]["type"], "error")

            with patch.object(adapter, "stream_transformers_adapter_events", return_value=_event_generator([{"type": "final"}])):
                self.assertEqual((await collect(adapter.stream_adapter_events([b"a"], 1, "transformers")))[0]["type"], "final")

        asyncio.run(scenario())

    def test_sglang_stream_state_machines(self):
        async def collect(events):
            return [event async for event in events]

        async def scenario():
            progress = "<|det|>text [1,2,3,4]<|/det|>" + ("long progress " * 4)
            lines = [
                "ping",
                'data: {"choices":[{"delta":{"content":""}}]}',
                "data: " + json.dumps({"choices": [{"delta": {"content": progress}}]}),
            ]
            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([StreamResponse(lines=lines)])), patch.object(
                adapter, "detect_degenerate_repetition", return_value=None
            ):
                events = await collect(adapter.stream_sglang_payload_events({"images_config": {}}, [png_bytes()], 1))
            self.assertTrue(any(event["type"] == "progress" for event in events))
            self.assertEqual(events[-1]["type"], "final")

            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([StreamResponse(status=500)])):
                self.assertEqual((await collect(adapter.stream_sglang_payload_events({}, [b"a"], 1)))[0]["type"], "error")
            repeat_line = 'data: {"choices":[{"delta":{"content":"repeat"}}]}'
            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([StreamResponse(lines=[repeat_line])])), patch.object(
                adapter, "detect_degenerate_repetition", return_value="repeat"
            ):
                self.assertEqual((await collect(adapter.stream_sglang_payload_events({}, [b"a"], 1)))[0]["type"], "error")

            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([StreamResponse(lines=lines)])), patch.object(
                adapter, "detect_degenerate_repetition", return_value=None
            ):
                events = await collect(adapter.stream_adapter_events([png_bytes()], 1, "sglang"))
            self.assertEqual(events[-1]["type"], "final")

            first_repeat = StreamResponse(lines=['data: {"choices":[{"delta":{"content":"bad"}}]}'])
            recovered = StreamResponse(lines=['data: {"choices":[{"delta":{"content":"recovered"}}]}'])
            with patch.object(adapter, "get_no_repeat_processor_str", return_value="processor"), patch.object(
                adapter, "detect_degenerate_repetition", side_effect=["det det text", None]
            ), patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([first_repeat, recovered])):
                retry_events = await collect(adapter.stream_adapter_events([png_bytes()], 1, "sglang"))
            self.assertEqual(retry_events[-1]["type"], "final")
            self.assertTrue(
                retry_events[-1]["result"]["layoutParsingResults"][0]["metadata"]["imagesConfig"][
                    "degeneration_retry"
                ]
            )

            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([StreamResponse(status=500, body=b"plain")])):
                self.assertEqual((await collect(adapter.stream_adapter_events([b"a"], 1, "sglang")))[0]["type"], "error")

            context_error = b"maximum context length of 100 tokens; 70 tokens from the input messages and 50 tokens for the completion"
            with patch.object(adapter, "SGLANG_CONTEXT_TOKEN_RESERVE", 5), patch.object(
                adapter.httpx, "AsyncClient", side_effect=[
                    FakeClient([StreamResponse(status=500, body=context_error)]),
                    FakeClient([StreamResponse(lines=[])]),
                ]
            ):
                events = await collect(adapter.stream_adapter_events([b"a"], 1, "sglang"))
            self.assertEqual(events[-1]["type"], "final")

            with patch.object(adapter.httpx, "AsyncClient", side_effect=adapter.DegenerateGenerationError("repeat")):
                self.assertEqual((await collect(adapter.stream_adapter_events([b"a"], 1, "sglang")))[0]["type"], "error")
            with patch.object(adapter.httpx, "AsyncClient", side_effect=RuntimeError("broken")):
                self.assertEqual((await collect(adapter.stream_adapter_events([b"a"], 1, "sglang")))[0]["detail"], "broken")

        asyncio.run(scenario())

    def test_buffered_sglang_streams_yield_to_health_checks(self):
        self.assertFalse(adapter.should_render_stream_snapshot("", 0, 0, now=0))
        self.assertTrue(adapter.should_render_stream_snapshot("complete.", 8, 0, now=0))
        self.assertTrue(adapter.should_render_stream_snapshot("pending", 0, 0, now=0.25))
        self.assertFalse(adapter.should_render_stream_snapshot("pending", 0, 0, now=0.24))

        async def scenario():
            main_thread = adapter.threading.current_thread()

            async def health_while_worker_is_blocked(started, release):
                deadline = asyncio.get_running_loop().time() + 1
                while not started.is_set():
                    if asyncio.get_running_loop().time() >= deadline:
                        release.set()
                        self.fail("CPU worker did not start")
                    await asyncio.sleep(0.001)
                result = await adapter.health()
                release.set()
                return result

            collect_started = adapter.threading.Event()
            collect_release = adapter.threading.Event()

            def blocked_detect(_text):
                self.assertIsNot(adapter.threading.current_thread(), main_thread)
                collect_started.set()
                if not collect_release.wait(timeout=1):
                    raise AssertionError("health coroutine was blocked by repetition detection")
                return None

            collect_line = "data: " + json.dumps(
                {"choices": [{"delta": {"content": "token"}}]}
            )
            with patch.object(
                adapter, "sglang_health_status", new=AsyncMock(return_value={"ready": True})
            ), patch.object(
                adapter, "detect_degenerate_repetition", side_effect=blocked_detect
            ):
                health_task = asyncio.create_task(
                    health_while_worker_is_blocked(collect_started, collect_release)
                )
                text = await adapter.collect_streaming_response(
                    StreamResponse(lines=[collect_line])
                )
                health_result = await health_task
            self.assertEqual(text, "token")
            self.assertEqual(health_result["status"], "ok")

            render_started = adapter.threading.Event()
            render_release = adapter.threading.Event()

            def blocked_render(*_args):
                self.assertIsNot(adapter.threading.current_thread(), main_thread)
                render_started.set()
                if not render_release.wait(timeout=1):
                    raise AssertionError("health coroutine was blocked by stream rendering")
                return "rendered progress long enough", {}

            stream_line = "data: " + json.dumps(
                {"choices": [{"delta": {"content": "stream token " * 3}}]}
            )
            with patch.object(
                adapter.httpx,
                "AsyncClient",
                return_value=FakeClient([StreamResponse(lines=[stream_line])]),
            ), patch.object(
                adapter, "sglang_health_status", new=AsyncMock(return_value={"ready": True})
            ), patch.object(
                adapter, "detect_degenerate_repetition", return_value=None
            ), patch.object(
                adapter, "render_streaming_markdown", side_effect=blocked_render
            ):
                health_task = asyncio.create_task(
                    health_while_worker_is_blocked(render_started, render_release)
                )
                events = [
                    event
                    async for event in adapter.stream_sglang_payload_events(
                        {"images_config": {}}, [b"page"], 1
                    )
                ]
                health_result = await health_task
            self.assertEqual(health_result["status"], "ok")
            self.assertTrue(any(event["type"] == "progress" for event in events))
            self.assertEqual(events[-1]["type"], "final")

            tiny_lines = [
                "data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]})
                for _ in range(128)
            ]
            render_threads = []

            def render_snapshot(*_args):
                render_threads.append(adapter.threading.current_thread())
                return "progress text that is long enough", {}

            with patch.object(
                adapter.httpx,
                "AsyncClient",
                return_value=FakeClient([StreamResponse(lines=tiny_lines)]),
            ), patch.object(
                adapter, "detect_degenerate_repetition", return_value=None
            ) as detector, patch.object(
                adapter, "render_streaming_markdown", side_effect=render_snapshot
            ) as render, patch.object(
                adapter, "should_emit_stream_progress", return_value=False
            ):
                throttled_events = [
                    event
                    async for event in adapter.stream_sglang_payload_events(
                        {"images_config": {}}, [b"page"], 1
                    )
                ]
            self.assertEqual(throttled_events[-1]["type"], "final")
            self.assertEqual(detector.call_count, len(tiny_lines))
            self.assertGreater(render.call_count, 0)
            self.assertLess(render.call_count, len(tiny_lines) // 4)
            self.assertTrue(all(thread is not main_thread for thread in render_threads))

        asyncio.run(scenario())

    def test_sglang_stream_retries_are_bounded_and_sequential(self):
        async def collect(events):
            return [event async for event in events]

        async def scenario():
            state = {
                "active_clients": 0,
                "active_responses": 0,
                "max_active_clients": 0,
                "max_active_responses": 0,
                "payloads": [],
            }

            class TrackedResponse(StreamResponse):
                async def __aenter__(self):
                    state["active_responses"] += 1
                    state["max_active_responses"] = max(
                        state["max_active_responses"], state["active_responses"]
                    )
                    return self

                async def __aexit__(self, *_):
                    state["active_responses"] -= 1
                    return False

            class TrackedClient:
                def __init__(self, stream_response):
                    self.stream_response = stream_response

                async def __aenter__(self):
                    state["active_clients"] += 1
                    state["max_active_clients"] = max(
                        state["max_active_clients"], state["active_clients"]
                    )
                    return self

                async def __aexit__(self, *_):
                    self.assert_no_open_response()
                    state["active_clients"] -= 1
                    return False

                def assert_no_open_response(self):
                    if state["active_responses"] != 0:
                        raise AssertionError("response remained open when client exited")

                def stream(self, *_args, **kwargs):
                    state["payloads"].append(
                        json.loads(json.dumps(kwargs["json"]))
                    )
                    return self.stream_response

            context_error = (
                b"maximum context length of 100 tokens; 70 tokens from the input "
                b"messages and 50 tokens for the completion"
            )
            responses = iter(
                [
                    TrackedResponse(status=500, body=context_error),
                    TrackedResponse(
                        lines=['data: {"choices":[{"delta":{"content":"repeat"}}]}']
                    ),
                    TrackedResponse(
                        lines=['data: {"choices":[{"delta":{"content":"recovered"}}]}']
                    ),
                ]
            )

            def client_factory(**_kwargs):
                self.assertEqual(state["active_clients"], 0)
                self.assertEqual(state["active_responses"], 0)
                return TrackedClient(next(responses))

            with patch.object(adapter, "SGLANG_CONTEXT_TOKEN_RESERVE", 5), patch.object(
                adapter, "get_no_repeat_processor_str", return_value="processor"
            ), patch.object(
                adapter, "detect_degenerate_repetition", side_effect=["repeat", None]
            ), patch.object(adapter.httpx, "AsyncClient", side_effect=client_factory):
                events = await collect(
                    adapter.stream_adapter_events([png_bytes()], 1, "sglang")
                )

            self.assertEqual(events[-1]["type"], "final")
            self.assertEqual(len(state["payloads"]), 3)
            self.assertEqual(state["max_active_clients"], 1)
            self.assertEqual(state["max_active_responses"], 1)
            self.assertEqual(state["active_clients"], 0)
            self.assertEqual(state["active_responses"], 0)
            final_config = events[-1]["result"]["layoutParsingResults"][0][
                "metadata"
            ]["imagesConfig"]
            self.assertEqual(final_config["max_tokens_adjusted_from"], adapter.SGLANG_MAX_TOKENS)
            self.assertEqual(final_config["max_tokens_adjusted_to"], 25)
            self.assertTrue(final_config["degeneration_retry"])
            self.assertNotIn("degeneration_retry", state["payloads"][1]["images_config"])
            self.assertTrue(state["payloads"][2]["images_config"]["degeneration_retry"])

            persistent_clients = [
                FakeClient([StreamResponse(status=500, body=context_error)]),
                FakeClient(
                    [StreamResponse(lines=['data: {"choices":[{"delta":{"content":"one"}}]}'])]
                ),
                FakeClient(
                    [StreamResponse(lines=['data: {"choices":[{"delta":{"content":"two"}}]}'])]
                ),
            ]
            with patch.object(adapter, "SGLANG_CONTEXT_TOKEN_RESERVE", 5), patch.object(
                adapter, "get_no_repeat_processor_str", return_value="processor"
            ), patch.object(
                adapter,
                "detect_degenerate_repetition",
                side_effect=["repeat one", "repeat two"],
            ), patch.object(
                adapter.httpx, "AsyncClient", side_effect=persistent_clients
            ) as async_client:
                persistent_events = await collect(
                    adapter.stream_adapter_events([png_bytes()], 1, "sglang")
                )
            self.assertEqual(async_client.call_count, 3)
            self.assertEqual(persistent_events[-1]["type"], "error")
            self.assertIn("remained repetitive", persistent_events[-1]["detail"])

        asyncio.run(scenario())

    def test_endpoint_guard_stream_and_multipart(self):
        async def scenario():
            request = object()
            with patch.object(adapter, "read_input", new=AsyncMock(return_value=(b"x", 1, "transformers"))), patch.object(
                adapter, "prepare_image_pages_and_texts", return_value=([], [])
            ):
                with self.assertRaises(HTTPException):
                    await adapter.ocr(request)
                with self.assertRaises(HTTPException):
                    await adapter.ocr_stream(request)

            with patch.object(adapter, "read_input", new=AsyncMock(return_value=(b"x", 1, "transformers"))), patch.object(
                adapter, "prepare_image_pages_and_texts", return_value=([b"page"], [])
            ):
                response = await adapter.ocr_stream(request)
                self.assertEqual(response.media_type, "application/x-ndjson")

            upload = SimpleNamespace(read=AsyncMock(return_value=png_bytes()))
            with patch.object(adapter, "generate_markdown", new=AsyncMock(return_value=("plain", {}))):
                response = await adapter.ocr_multipart(upload, 1, "transformers")
            self.assertEqual(response["markdown"], "plain")

        asyncio.run(scenario())

    def test_last_unlimited_branches(self):
        torch_no_cuda = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        )
        with patch.dict(sys.modules, {"torch": torch_no_cuda}):
            adapter.cleanup_torch_accelerator_cache()
        torch_mps = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: True)),
            mps=SimpleNamespace(empty_cache=MagicMock(side_effect=RuntimeError())),
        )
        with patch.dict(sys.modules, {"torch": torch_mps}):
            adapter.cleanup_torch_accelerator_cache()

        image = MagicMock(is_animated=True, mode="RGB")
        image.save.side_effect = lambda output, **_: output.write(b"png")
        with patch.object(adapter.Image, "open", return_value=image):
            self.assertEqual(adapter.image_bytes_to_png(b"x"), b"png")
        image.seek.assert_called_once_with(0)

        class CpuModel:
            def eval(self):
                return self

        torch = SimpleNamespace(float32="torch.float32", bfloat16="torch.bfloat16", float16="torch.float16")
        transformers = SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=MagicMock(return_value="tok")),
            AutoModel=SimpleNamespace(from_pretrained=MagicMock(return_value=CpuModel())),
        )

        async def direct(function, *_args):
            return function()

        adapter.TRANSFORMERS_TOKENIZER = None
        adapter.TRANSFORMERS_MODEL = None
        with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}), patch.object(
            adapter, "select_transformers_device", return_value="cpu"
        ), patch.object(adapter, "select_transformers_dtype", return_value="torch.float32"), patch.object(
            adapter, "TRANSFORMERS_ATTENTION_IMPLEMENTATION", ""
        ), patch("asyncio.to_thread", side_effect=direct):
            asyncio.run(adapter.get_transformers_components())

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "data.json").write_text('{"unknown": 1}', encoding="utf-8")
            self.assertEqual(adapter.extract_text_from_transformers_result(None, directory), '{"unknown": 1}')

        with patch.object(adapter, "single_image_transformers_attempts", return_value=[]), patch.object(
            adapter, "cleanup_torch_accelerator_cache"
        ), self.assertRaises(RuntimeError):
            adapter.run_transformers_inference_sync("t", SimpleNamespace(), [png_bytes()], 1)

        response = StreamResponse(lines=["data: bad", 'data: {"choices":[{"delta":{"content":""}}]}'])
        self.assertEqual(asyncio.run(adapter.collect_streaming_response(response)), "")
        self.assertIsNone(adapter.anchor_page_for_block({"text": "a b c d"}, ["a b c d"]))
        anchored = [{"text": "unique anchor words enough now", "label": "text"}]
        adapter.assign_block_pages(anchored, 1, ["unique anchor words enough now"])
        adapter.detect_degenerate_repetition(("aa bb cc " * 30))
        pos = adapter.streaming_source_position(
            "<|det|>text [0,1,2,3]<|/det|>no match words enough here", 2, ["other", "also other"]
        )
        self.assertEqual(pos["pageConfidence"], "heuristic")
        no_bbox = adapter.build_layout_parsing_results("<|det|>text<|/det|>body", "body", {}, 1, 1, {})
        self.assertNotIn("block_bbox", no_bbox[0]["parsing_res_list"][0])

    def test_transformers_heartbeat_and_image_progress(self):
        async def collect(events):
            return [event async for event in events]

        class WaitingThread:
            def __init__(self, target, daemon=True):
                self.calls = 0

            def start(self):
                pass

            def is_alive(self):
                self.calls += 1
                return self.calls == 1

            def join(self, timeout=None):
                pass

        async def scenario():
            async def direct(function, *args, **kwargs):
                return function(*args, **kwargs)

            adapter.TRANSFORMERS_MODEL = object()
            with patch.object(adapter, "get_transformers_components", new=AsyncMock(return_value=("t", "m"))), patch.object(
                adapter.threading, "Thread", WaitingThread
            ), patch.object(adapter.time, "monotonic", side_effect=[0, 2]), patch.object(
                adapter, "STREAM_HEARTBEAT_SECONDS", 1
            ), patch("asyncio.sleep", new=AsyncMock()), patch(
                "asyncio.to_thread", side_effect=direct
            ):
                events = await collect(adapter.stream_transformers_adapter_events([png_bytes()], 1))
            self.assertTrue(any(event["type"] == "status" for event in events))
            with patch.object(adapter, "get_transformers_components", new=AsyncMock(return_value=("t", "m"))), patch.object(
                adapter.threading, "Thread", WaitingThread
            ), patch.object(adapter.time, "monotonic", side_effect=[0, 1]), patch.object(
                adapter, "STREAM_HEARTBEAT_SECONDS", 0
            ), patch("asyncio.sleep", new=AsyncMock()), patch(
                "asyncio.to_thread", side_effect=direct
            ):
                await collect(adapter.stream_transformers_adapter_events([png_bytes()], 1))

            class ImmediateThread:
                def __init__(self, target, daemon=True):
                    self.target = target

                def start(self):
                    self.target()

                def is_alive(self):
                    return False

                def join(self, timeout=None):
                    pass

            raw = "<|det|>image [1,2,10,10]<|/det|>" + ("picture " * 5)

            def inference(*args):
                args[-1].write(raw)
                return raw, {}

            with patch.object(adapter, "get_transformers_components", new=AsyncMock(return_value=("t", "m"))), patch.object(
                adapter.threading, "Thread", ImmediateThread
            ), patch.object(adapter, "run_transformers_inference_sync", side_effect=inference), patch(
                "asyncio.to_thread", side_effect=direct
            ):
                events = await collect(adapter.stream_transformers_adapter_events([png_bytes()], 1))
            progress = next(event for event in events if event["type"] == "progress")
            self.assertTrue(progress["images"])

        asyncio.run(scenario())

    def test_stream_loop_and_repetition_branches(self):
        async def collect(events):
            return [event async for event in events]

        async def scenario():
            raw = "<|det|>image [1,2,10,10]<|/det|>" + ("picture " * 5)
            line = "data: " + json.dumps({"choices": [{"delta": {"content": raw}}]})
            response = StreamResponse(lines=[line, line])
            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([response])), patch.object(
                adapter, "detect_degenerate_repetition", return_value=None
            ), patch.object(
                adapter, "should_emit_stream_progress", side_effect=[True, False]
            ):
                events = await collect(adapter.stream_sglang_payload_events({}, [png_bytes()], 1))
            progress = next(event for event in events if event["type"] == "progress")
            self.assertTrue(progress["images"])

            repeat = StreamResponse(lines=['data: {"choices":[{"delta":{"content":"repeat"}}]}'])
            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([repeat])), patch.object(
                adapter, "detect_degenerate_repetition", return_value="bad"
            ):
                events = await collect(adapter.stream_adapter_events([b"a"], 1, "sglang"))
            self.assertEqual(events[0]["type"], "error")

            response2 = StreamResponse(lines=[line, line])
            with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient([response2])), patch.object(
                adapter, "detect_degenerate_repetition", return_value=None
            ), patch.object(
                adapter, "should_emit_stream_progress", side_effect=[True, False]
            ):
                events = await collect(adapter.stream_adapter_events([png_bytes()], 1, "sglang"))
            progress = next(event for event in events if event["type"] == "progress")
            self.assertTrue(progress["images"])

        asyncio.run(scenario())


async def _event_generator(items):
    for item in items:
        yield item
