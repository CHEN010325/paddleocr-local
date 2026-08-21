"""NaviDC-OCR adapter.

The adapter keeps the model-specific two-step layout/extraction pipeline in an
isolated service.  The WebUI only sees the same markdown/images/layout result
shape as the other document parsers.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import fitz
from fastapi import FastAPI, HTTPException, Request
from PIL import Image


logging.basicConfig(level=os.getenv("NAVIDC_OCR_LOG_LEVEL", "INFO"))
logger = logging.getLogger("navidc-ocr-adapter")

MODEL_NAME = os.getenv("NAVIDC_OCR_MODEL_NAME", "StarDoc-AI/NaviDC-OCR")
MODEL_REVISION = os.getenv(
    "NAVIDC_OCR_MODEL_REVISION",
    "c7179051a52a0a54a549388de89c6aa715cfd0af",
)
MODEL_SOURCE_REVISION = os.getenv(
    "NAVIDC_OCR_SOURCE_REVISION",
    "737e185c7b74288091cd4395ea80c14b1f71422b",
)
DTYPE_NAME = os.getenv("NAVIDC_OCR_DTYPE", "bfloat16").strip().lower()
BACKEND = os.getenv("NAVIDC_OCR_BACKEND", "vllm-async-engine").strip().lower()
MAX_TOKENS = max(128, int(os.getenv("NAVIDC_OCR_MAX_TOKENS", "4096")))
PDF_DPI = max(36, int(os.getenv("NAVIDC_OCR_PDF_DPI", "200")))
MAX_PAGES = max(1, int(os.getenv("NAVIDC_OCR_MAX_PAGES_PER_REQUEST", "50")))
MAX_RENDER_PIXELS = max(1_000_000, int(os.getenv("NAVIDC_OCR_MAX_RENDER_PIXELS", "60000000")))
MAX_IMAGE_EDGE = max(512, int(os.getenv("NAVIDC_OCR_MAX_IMAGE_EDGE", "8000")))
MAX_MODEL_LEN = max(4096, int(os.getenv("NAVIDC_OCR_MAX_MODEL_LEN", "32768")))
GPU_MEMORY_UTILIZATION = min(0.98, max(0.5, float(os.getenv("NAVIDC_OCR_GPU_MEMORY_UTILIZATION", "0.90"))))

PARSER = None
PARSER_ERROR: str | None = None
PARSER_LOCK = asyncio.Lock()
INFERENCE_LOCK = asyncio.Lock()

FORMULA_TYPES = {"equation", "equation_block", "formula"}
VISUAL_TYPES = {"image", "chart", "seal", "char"}
DEFAULT_IGNORE_LABELS = {"header", "footer", "page_number"}


def decode_file_payload(value: str) -> bytes:
    encoded = value.split("base64,", 1)[1] if "base64," in value else value
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as error:
        raise HTTPException(status_code=400, detail="Invalid base64 file payload") from error


def parse_ignore_labels(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    labels: list[str] = []
    for item in values:
        if isinstance(item, str):
            try:
                parsed = json.loads(item)
                if isinstance(parsed, list):
                    values.extend(parsed)
                    continue
            except Exception:
                pass
        label = str(item).strip().lower()
        if label and label not in labels:
            labels.append(label)
    return labels


async def read_input(request: Request) -> tuple[bytes, int | None, list[str]]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if not upload or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="Missing multipart field: file")
        file_type = form.get("fileType")
        ignore_labels = form.getlist("markdownIgnoreLabels") if hasattr(form, "getlist") else form.get("markdownIgnoreLabels")
        return await upload.read(), int(file_type) if file_type not in (None, "") else None, parse_ignore_labels(ignore_labels)
    try:
        payload = await request.json()
    except Exception as error:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from error
    raw_file = payload.get("file") or payload.get("image")
    if not raw_file:
        raise HTTPException(status_code=400, detail="Missing JSON field: file")
    file_type = payload.get("fileType")
    return decode_file_payload(str(raw_file)), int(file_type) if file_type is not None else None, parse_ignore_labels(payload.get("markdownIgnoreLabels"))


def infer_file_type(file_bytes: bytes, file_type: int | None) -> int:
    if file_type in (0, 1):
        return file_type
    return 0 if file_bytes.startswith(b"%PDF-") else 1


def _limit_image(image: Image.Image) -> Image.Image:
    width, height = image.size
    scale = min(1.0, (MAX_RENDER_PIXELS / max(1, width * height)) ** 0.5, MAX_IMAGE_EDGE / max(width, height))
    if scale < 1.0:
        image = image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS)
    return image.convert("RGB")


def render_pdf(file_bytes: bytes) -> list[Image.Image]:
    scale = PDF_DPI / 72
    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as document:
            if len(document) > MAX_PAGES:
                raise HTTPException(status_code=400, detail=f"PDF exceeds the {MAX_PAGES}-page request limit")
            return [
                _limit_image(
                    Image.open(
                        io.BytesIO(
                            document[index]
                            .get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                            .tobytes("png")
                        )
                    )
                )
                for index in range(len(document))
            ]
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=400, detail="Unsupported or corrupt PDF") from error


def prepare_images(file_bytes: bytes, file_type: int | None) -> tuple[list[Image.Image], int]:
    resolved_type = infer_file_type(file_bytes, file_type)
    if resolved_type == 0:
        pages = render_pdf(file_bytes)
    else:
        try:
            pages = [_limit_image(Image.open(io.BytesIO(file_bytes)))]
        except Exception as error:
            raise HTTPException(status_code=400, detail="Unsupported or corrupt image") from error
    if not pages:
        raise HTTPException(status_code=400, detail="Input contains no pages")
    return pages, resolved_type


def _dtype(torch):
    if DTYPE_NAME in {"float16", "half", "fp16"}:
        return torch.float16
    if DTYPE_NAME in {"float32", "float", "fp32"}:
        return torch.float32
    return torch.bfloat16


class NaviDCOCRParser:
    def __init__(self) -> None:
        logger.info("Loading NaviDC-OCR %s at revision %s", MODEL_NAME, MODEL_REVISION)
        if BACKEND not in {"vllm-async-engine", "transformers"}:
            raise RuntimeError(f"Unsupported NAVIDC_OCR_BACKEND: {BACKEND}")
        from NaviOCR.vlm_utils.NaviOCR_client import (
            DEFAULT_PROMPTS,
            DEFAULT_SAMPLING_PARAMS,
            NaviOCRClient,
        )
        import NaviOCR.config as config

        config.LAYOUT_MODE = os.getenv("NAVIDC_OCR_LAYOUT_MODE", "Detection")
        config.MAX_PIXELS = MAX_RENDER_PIXELS
        sampling_params = copy.deepcopy(DEFAULT_SAMPLING_PARAMS)
        for params in sampling_params.values():
            params.max_new_tokens = MAX_TOKENS
        if BACKEND == "vllm-async-engine":
            # This is the backend used by the official NaviDC-OCR README. It
            # overlaps region extraction requests instead of stepping through
            # them serially with generate() calls in Transformers.
            from vllm.engine.arg_utils import AsyncEngineArgs
            from vllm.v1.engine.async_llm import AsyncLLM
            engine = AsyncLLM.from_engine_args(AsyncEngineArgs(
                model=MODEL_NAME,
                trust_remote_code=True,
                max_model_len=MAX_MODEL_LEN,
                gpu_memory_utilization=GPU_MEMORY_UTILIZATION,
            ))
            self.client = NaviOCRClient(
                backend=BACKEND,
                vllm_async_llm=engine,
                prompts=DEFAULT_PROMPTS,
                sampling_params=sampling_params,
                max_concurrency=max(1, int(os.getenv("NAVIDC_OCR_MAX_CONCURRENCY", "8"))),
                use_tqdm=False,
            )
        else:
            import torch
            from transformers import AutoProcessor
            if not torch.cuda.is_available():
                raise RuntimeError("NaviDC-OCR requires a CUDA-enabled GPU")
            try:
                from transformers import AutoModelForImageTextToText as model_class
            except ImportError:
                from transformers import AutoModelForVision2Seq as model_class
            processor = AutoProcessor.from_pretrained(
                MODEL_NAME, revision=MODEL_REVISION, trust_remote_code=True, use_fast=True
            )
            model = model_class.from_pretrained(
                MODEL_NAME, revision=MODEL_REVISION, trust_remote_code=True,
                torch_dtype=_dtype(torch), low_cpu_mem_usage=True,
            ).to("cuda")
            model.eval()
            self.client = NaviOCRClient(
                backend=BACKEND, model=model, processor=processor,
                prompts=DEFAULT_PROMPTS, sampling_params=sampling_params,
                batch_size=1, max_concurrency=1, use_tqdm=False,
            )

    def parse(self, image: Image.Image) -> list[dict[str, Any]]:
        return [block_to_dict(block) for block in self.client.two_step_extract(image)]

    async def parse_async(self, image: Image.Image) -> list[dict[str, Any]]:
        if BACKEND == "vllm-async-engine":
            blocks = await self.client.aio_two_step_extract(image)
        else:
            blocks = await asyncio.to_thread(self.client.two_step_extract, image)
        return [block_to_dict(block) for block in blocks]

    async def parse_batch_async(self, images: list[Image.Image]) -> list[list[dict[str, Any]]]:
        """Run the official NaviDC-OCR multi-page batch path.

        The WebUI's "pages per batch" setting controls how many PDF pages are
        sent in one request.  Preserve the one-page mode, but when a request
        contains multiple pages use NaviOCRClient's batch API so layout and
        extraction requests can be scheduled across the whole page group.
        """
        if not images:
            return []
        if len(images) == 1:
            return [await self.parse_async(images[0])]
        if BACKEND == "vllm-async-engine":
            blocks_by_page = await self.client.aio_batch_two_step_extract(images=images)
        else:
            blocks_by_page = await asyncio.to_thread(self.client.batch_two_step_extract, images=images)
        return [
            [block_to_dict(block) for block in blocks]
            for blocks in blocks_by_page
        ]


def create_parser() -> NaviDCOCRParser:
    return NaviDCOCRParser()


async def get_parser() -> NaviDCOCRParser:
    global PARSER, PARSER_ERROR
    if PARSER is not None:
        return PARSER
    async with PARSER_LOCK:
        if PARSER is not None:
            return PARSER
        PARSER_ERROR = None
        try:
            PARSER = await asyncio.to_thread(create_parser)
            return PARSER
        except Exception as error:
            PARSER_ERROR = str(error) or error.__class__.__name__
            logger.exception("Failed to load NaviDC-OCR")
            raise


def block_bbox(block: dict[str, Any], width: int, height: int) -> list[int] | None:
    raw = block.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        values = [float(item) for point in raw for item in (point if isinstance(point, (list, tuple)) else [point])]
    except (TypeError, ValueError):
        return None
    if len(values) >= 8:
        xs, ys = values[0::2], values[1::2]
        values = [min(xs), min(ys), max(xs), max(ys)]
    x1, y1, x2, y2 = values[:4]
    # NaviDC-OCR emits normalized coordinates in [0, 1].
    if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5:
        x1, x2 = x1 * width, x2 * width
        y1, y2 = y1 * height, y2 * height
    x1, x2 = sorted((round(x1), round(x2)))
    y1, y2 = sorted((round(y1), round(y2)))
    return [max(0, min(width, x1)), max(0, min(height, y1)), max(0, min(width, x2)), max(0, min(height, y2))]


def block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    return {
        "type": getattr(block, "type", "unknown"),
        "bbox": getattr(block, "bbox", None),
        "content": getattr(block, "content", "") or "",
        "angle": getattr(block, "angle", None),
    }


def crop_region(image: Image.Image, bbox: list[int]) -> str | None:
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        return None
    output = io.BytesIO()
    image.crop((x1, y1, x2, y2)).save(output, format="JPEG", quality=92)
    return base64.b64encode(output.getvalue()).decode("ascii")


def format_content(block_type: str, content: str) -> str:
    content = content.strip()
    if not content:
        return ""
    if block_type == "title" and not content.startswith("#"):
        return f"# {content}"
    if block_type in FORMULA_TYPES:
        if content.startswith(("$", r"\(", r"\[")):
            return content
        return f"$$\n{content}\n$$"
    if block_type == "code" and not content.startswith("```"):
        return f"```\n{content}\n```"
    return content


def build_page_response(
    image: Image.Image,
    blocks: list[dict[str, Any]],
    page_index: int,
    ignore_labels: set[str] | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    width, height = image.size
    page_images: dict[str, str] = {}
    markdown_parts: list[str] = []
    render_blocks: list[dict[str, Any]] = []
    visual_index = 0
    ignore_labels = ignore_labels or set()
    for block in blocks:
        block_type = str(block.get("type") or "unknown").lower()
        if block_type in ignore_labels:
            continue
        bbox = block_bbox(block, width, height)
        content = str(block.get("content") or "").strip()
        block_content = format_content(block_type, content)
        if block_type in VISUAL_TYPES and bbox:
            encoded = crop_region(image, bbox)
            if encoded:
                visual_index += 1
                path = f"ocr_images/navidc_p{page_index + 1}_{block_type}_{visual_index}.jpg"
                page_images[path] = encoded
                if block_type in {"image", "chart", "seal"}:
                    block_content = f"![{block_type}]({path})"
                elif block_content:
                    block_content = f"![{block_type}]({path})\n\n{block_content}"
                else:
                    block_content = f"![{block_type}]({path})"
        if block_content:
            markdown_parts.append(block_content)
        render_blocks.append({
            "block_label": block_type,
            "block_content": block_content,
            "block_bbox": bbox,
        })
    markdown = "\n\n".join(markdown_parts).strip()
    result = {
        "parser": "navidc-ocr",
        "pageIndex": page_index,
        "width": width,
        "height": height,
        "markdown": {"text": markdown, "images": page_images},
        "parsing_res_list": render_blocks,
    }
    return markdown, page_images, result


def build_response(
    pages: list[Image.Image],
    parsed: list[list[dict[str, Any]]],
    ignore_labels: set[str] | None = None,
) -> dict[str, Any]:
    markdown_pages: list[str] = []
    all_images: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    for page_index, (image, blocks) in enumerate(zip(pages, parsed)):
        markdown, images, result = build_page_response(image, blocks, page_index, ignore_labels)
        if markdown:
            markdown_pages.append(markdown)
        all_images.update(images)
        results.append(result)
    return {
        "markdown": "\n\n---\n\n".join(markdown_pages),
        "images": all_images,
        "layoutParsingResults": results,
        "model": MODEL_NAME,
        "modelRevision": MODEL_REVISION,
        "fileType": 0,
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    await get_parser()
    yield


app = FastAPI(title="NaviDC-OCR Adapter", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    if PARSER is None:
        raise HTTPException(status_code=503, detail=PARSER_ERROR or "NaviDC-OCR is loading")
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "modelRevision": MODEL_REVISION,
        "sourceRevision": MODEL_SOURCE_REVISION,
        "backend": BACKEND,
        "modelLoaded": True,
    }


@app.post("/ocr")
async def ocr(request: Request) -> dict[str, Any]:
    file_bytes, file_type, requested_ignore_labels = await read_input(request)
    aliases = {
        "number": "page_number",
        "header_image": "header",
        "footer_image": "footer",
        "footnote": "page_footnote",
    }
    ignore_labels = {aliases.get(label, label) for label in requested_ignore_labels}
    # Match the WebUI's default parsing settings for direct/API callers too.
    # An explicit non-empty list (including one produced by the UI) overrides
    # these defaults when the user disables a switch.
    if not requested_ignore_labels:
        ignore_labels.update(DEFAULT_IGNORE_LABELS)
    pages, resolved_type = prepare_images(file_bytes, file_type)
    parser = await get_parser()
    parsed: list[list[dict[str, Any]]] = []
    async with INFERENCE_LOCK:
        # A one-page request remains page-local. Multi-page PDF batches use
        # the same batch entry point as the official NaviDC-OCR implementation.
        parsed = await parser.parse_batch_async(pages)
    response = build_response(pages, parsed, ignore_labels)
    response["fileType"] = resolved_type
    logger.info(
        "Parsed %d page(s) with NaviDC-OCR (%s mode)",
        len(pages),
        "official multi-page batch" if len(pages) > 1 else "single-page",
    )
    return response
