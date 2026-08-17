import asyncio
import base64
import io
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import fitz
from fastapi import HTTPException
from PIL import Image

import ovisocr2_adapter as adapter


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (20, 10), "white").save(output, "PNG")
    return output.getvalue()


class Tensor:
    def __init__(self, length=4):
        self.shape = (1, length)

    def to(self, device):
        self.device = device
        return self

    def __getitem__(self, _):
        return self


class Grid:
    def prod(self, dim=-1):
        return self

    def sum(self):
        return self

    def item(self):
        return 16

    def to(self, device):
        return self


class OvisRuntimeTests(unittest.TestCase):
    def tearDown(self):
        adapter.PARSER = None
        adapter.PARSER_ERROR = None

    def test_vllm_initialization_parse_and_cleaning_edges(self):
        tokenizer = SimpleNamespace(apply_chat_template=MagicMock(return_value="prompt"))
        model = SimpleNamespace(
            get_tokenizer=lambda: tokenizer,
            generate=MagicMock(
                return_value=[
                    SimpleNamespace(outputs=[SimpleNamespace(text="  parsed one  ")]),
                    SimpleNamespace(outputs=[SimpleNamespace(text="parsed two")]),
                ]
            ),
        )
        llm = MagicMock(return_value=model)
        sampling = MagicMock(return_value="params")
        module = SimpleNamespace(LLM=llm, SamplingParams=sampling)
        with patch.dict(sys.modules, {"vllm": module}):
            parser = adapter.VllmOvisOCR2Parser("model")
        self.assertEqual(parser.parse([Image.new("RGB", (1, 1)), Image.new("RGB", (1, 1))]), ["parsed one", "parsed two"])
        self.assertEqual(adapter.VllmOvisOCR2Parser.clean_truncated_repeats("short"), "short")
        self.assertEqual(adapter.VllmOvisOCR2Parser.clean_truncated_repeats("abcdefghij", min_text_len=1, max_period=2, min_repeat_chars=99), "abcdefghij")
        self.assertEqual(adapter.VllmOvisOCR2Parser.clean_restarted_document(" \n "), " \n ")
        self.assertEqual(adapter.VllmOvisOCR2Parser.clean_restarted_document("tiny\none\ntwo\ntiny"), "tiny\none\ntwo\ntiny")
        self.assertEqual(adapter.VllmOvisOCR2Parser.clean_restarted_document("Long first line\n\none\ntwo"), "Long first line\n\none\ntwo")

    def test_transformers_initialization_all_device_dtype_paths(self):
        class Model:
            def to(self, device):
                self.device = device
                return self

            def eval(self):
                self.evaluated = True

        for requested_device, mps, cuda, expected in (
            ("auto", True, False, "mps"),
            ("auto", False, True, "cuda"),
            ("auto", False, False, "cpu"),
            ("custom", False, False, "custom"),
        ):
            torch = SimpleNamespace(
                backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
                cuda=SimpleNamespace(is_available=lambda: cuda),
                float16="torch.float16",
                bfloat16="torch.bfloat16",
                float32="torch.float32",
                custom_dtype="torch.custom",
                device=lambda value: value,
            )
            model = Model()
            transformers = SimpleNamespace(
                AutoProcessor=SimpleNamespace(from_pretrained=MagicMock(return_value="processor")),
                AutoModelForMultimodalLM=SimpleNamespace(from_pretrained=MagicMock(return_value=model)),
            )
            dtype = "auto" if requested_device != "custom" else "custom_dtype"
            with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}), patch.object(
                adapter, "TRANSFORMERS_DEVICE", requested_device
            ), patch.object(adapter, "TRANSFORMERS_DTYPE", dtype):
                parser = adapter.TransformersOvisOCR2Parser("model")
            self.assertEqual(parser.device, expected)
            self.assertTrue(model.evaluated)

    def test_transformers_parse_with_grid_and_restart_criteria(self):
        class StoppingCriteria:
            pass

        class StoppingCriteriaList(list):
            pass

        decode_values = iter(
            [
                "Document title\none\ntwo\nDocument title",
                "<think></think>\nFinal document",
            ]
        )
        processor = SimpleNamespace(
            image_processor=SimpleNamespace(merge_size=2),
            apply_chat_template=MagicMock(return_value={"input_ids": Tensor(4), "image_grid_thw": Grid(), "plain": "x"}),
            decode=MagicMock(side_effect=lambda *_args, **_kwargs: next(decode_values)),
        )

        class Model:
            def generate(self, **kwargs):
                criteria = kwargs["stopping_criteria"][0]
                self.false_result = criteria(Tensor(5), None)
                self.true_result = criteria(Tensor(4 + adapter.RESTART_CHECK_INTERVAL), None)
                return Tensor(9)

        parser = object.__new__(adapter.TransformersOvisOCR2Parser)
        parser.device = "cpu"
        parser.processor = processor
        parser.model = Model()
        torch = SimpleNamespace(inference_mode=lambda: _SyncContext())
        transformers = SimpleNamespace(StoppingCriteria=StoppingCriteria, StoppingCriteriaList=StoppingCriteriaList)
        with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}):
            result = parser.parse([Image.new("RGB", (20, 10))])
        self.assertEqual(result, ["Final document"])
        self.assertFalse(parser.model.false_result)
        self.assertTrue(parser.model.true_result)

        processor.apply_chat_template.return_value = {"input_ids": Tensor(4)}
        processor.decode.side_effect = ["plain final"]
        parser.model = SimpleNamespace(generate=lambda **_: Tensor(9))
        with patch.dict(sys.modules, {"torch": torch, "transformers": transformers}):
            self.assertEqual(parser.parse([Image.new("RGB", (2, 2))]), ["plain final"])

    def test_mlx_initialization_and_parse(self):
        processor = object()
        model = SimpleNamespace(config="config")
        load = MagicMock(return_value=(model, processor))
        apply = MagicMock(return_value="prompt")
        result = SimpleNamespace(
            text="<think></think>\nMLX result",
            generation_tokens=3,
            generation_tps=2.5,
            peak_memory=1.2,
            finish_reason="stop",
        )
        mlx = SimpleNamespace(load=load, generate=MagicMock(return_value=result))
        prompt = SimpleNamespace(apply_chat_template=apply)
        hub = SimpleNamespace(snapshot_download=MagicMock(return_value="model"))
        with patch.dict(
            sys.modules,
            {"mlx_vlm": mlx, "mlx_vlm.prompt_utils": prompt, "huggingface_hub": hub},
        ):
            parser = adapter.MlxOvisOCR2Parser("model")
            self.assertEqual(parser.parse([Image.new("RGB", (4, 3))]), ["MLX result"])

    def test_create_parser_backend_dispatch(self):
        for backend, name in (("vllm", "VllmOvisOCR2Parser"), ("transformers", "TransformersOvisOCR2Parser"), ("mlx", "MlxOvisOCR2Parser")):
            constructor = MagicMock(return_value=backend)
            with patch.object(adapter, "BACKEND", backend), patch.object(adapter, name, constructor):
                self.assertEqual(adapter.create_parser(), backend)
        with patch.object(adapter, "BACKEND", "bad"), self.assertRaises(ValueError):
            adapter.create_parser()

    def test_get_parser_second_lock_check_and_empty_error(self):
        class Lock:
            async def __aenter__(self):
                adapter.PARSER = "late"

            async def __aexit__(self, *_):
                return False

        with patch.object(adapter, "PARSER_LOCK", Lock()):
            self.assertEqual(asyncio.run(adapter.get_parser()), "late")
        adapter.PARSER = None
        with patch.object(adapter, "create_parser", side_effect=RuntimeError()):
            with self.assertRaises(RuntimeError):
                asyncio.run(adapter.get_parser())
        self.assertEqual(adapter.PARSER_ERROR, "RuntimeError")

    def test_pdf_rendering_and_limit(self):
        document = fitz.open()
        document.new_page()
        pdf = document.tobytes()
        document.close()
        pages = adapter.render_pdf(pdf)
        self.assertEqual(len(pages), 1)
        self.assertEqual(adapter.prepare_images(pdf, None)[1], 0)

        fake_document = MagicMock()
        fake_document.__enter__.return_value = fake_document
        fake_document.__exit__.return_value = False
        fake_document.__len__.return_value = adapter.MAX_PAGES + 1
        with patch.object(adapter.fitz, "open", return_value=fake_document), self.assertRaises(HTTPException):
            adapter.render_pdf(pdf)

    def test_read_input_all_paths(self):
        class Request:
            def __init__(self, content_type="", form=None, payload=None, error=None):
                self.headers = {"content-type": content_type}
                self._form = form
                self.payload = payload
                self.error = error

            async def form(self):
                return self._form

            async def json(self):
                if self.error:
                    raise self.error
                return self.payload

        with self.assertRaises(HTTPException):
            asyncio.run(adapter.read_input(Request("multipart/form-data", form={})))
        upload = SimpleNamespace(read=AsyncMock(return_value=b"x"))
        self.assertEqual(asyncio.run(adapter.read_input(Request("multipart/form-data", form={"file": upload, "fileType": ""}))), (b"x", None))
        self.assertEqual(asyncio.run(adapter.read_input(Request("multipart/form-data", form={"file": upload, "fileType": "1"})))[1], 1)
        with self.assertRaises(HTTPException):
            asyncio.run(adapter.read_input(Request(error=ValueError())))
        with self.assertRaises(HTTPException):
            asyncio.run(adapter.read_input(Request(payload={})))
        encoded = base64.b64encode(b"x").decode()
        self.assertEqual(asyncio.run(adapter.read_input(Request(payload={"image": encoded}))), (b"x", None))
        self.assertEqual(asyncio.run(adapter.read_input(Request(payload={"file": encoded, "fileType": "1"})))[1], 1)

    def test_crop_invalid_region_and_empty_response(self):
        markdown, images, blocks = adapter.crop_visual_regions(
            '<img src="images/bbox_500_500_100_100.jpg" />',
            Image.new("RGB", (10, 10)),
            0,
        )
        self.assertEqual((markdown, images, blocks), ("", {}, []))
        response = adapter.build_response([], [], 1)
        self.assertEqual(response["markdown"], "")

    def test_lifespan_health_error_and_ocr_empty(self):
        async def scenario():
            with patch.object(adapter, "get_parser", new=AsyncMock(return_value=object())) as get:
                async with adapter.lifespan(adapter.app):
                    pass
                get.assert_awaited_once()
            adapter.PARSER = None
            adapter.PARSER_ERROR = "failed"
            with self.assertRaises(HTTPException) as caught:
                await adapter.health()
            self.assertEqual(caught.exception.detail, "failed")
            with patch.object(adapter, "read_input", new=AsyncMock(return_value=(b"x", 1))), patch.object(
                adapter, "prepare_images", return_value=([], 1)
            ):
                with self.assertRaises(HTTPException):
                    await adapter.ocr(object())

        asyncio.run(scenario())


class _SyncContext:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False
