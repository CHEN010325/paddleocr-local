import asyncio
import base64
import io
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any

import fitz
from fastapi import FastAPI, HTTPException, Request
from PIL import Image


logging.basicConfig(level=os.getenv("OVISOCR2_LOG_LEVEL", "INFO"))
logger = logging.getLogger("ovisocr2-adapter")

MODEL_NAME = os.getenv("OVISOCR2_MODEL_NAME", "ATH-MaaS/OvisOCR2")
KV_CACHE_MEMORY_MB = int(os.getenv("OVISOCR2_KV_CACHE_MEMORY_MB", "512"))
STARTUP_MEMORY_FRACTION = float(os.getenv("OVISOCR2_STARTUP_MEMORY_FRACTION", "0.50"))
MAX_MODEL_LEN = int(os.getenv("OVISOCR2_MAX_MODEL_LEN", "32768"))
MAX_NUM_SEQS = int(os.getenv("OVISOCR2_MAX_NUM_SEQS", "1"))
MAX_TOKENS = int(os.getenv("OVISOCR2_MAX_TOKENS", "8192"))
PDF_DPI = int(os.getenv("OVISOCR2_PDF_DPI", "200"))
MAX_PAGES = int(os.getenv("OVISOCR2_MAX_PAGES_PER_REQUEST", "50"))
MIN_PIXELS = int(os.getenv("OVISOCR2_MIN_PIXELS", str(448 * 448)))
MAX_PIXELS = int(os.getenv("OVISOCR2_MAX_PIXELS", str(2880 * 2880)))
GDN_PREFILL_BACKEND = os.getenv("OVISOCR2_GDN_PREFILL_BACKEND", "triton")

PROMPT = (
    "\nExtract all readable content from the image in natural human reading order and output the result "
    "as a single Markdown document. For charts or images, represent them using an HTML image tag: "
    '<img src="images/bbox_{left}_{top}_{right}_{bottom}.jpg" />, where left, top, right, bottom are '
    "bounding box coordinates scaled to [0, 1000). Format formulas as LaTeX. Format tables as HTML: "
    "<table>...</table>. Transcribe all other text as standard Markdown. Preserve the original text "
    "without translation or paraphrasing."
)
BBOX_IMAGE_RE = re.compile(
    r'<img\s+src=["\']images/bbox_(\d+)_(\d+)_(\d+)_(\d+)\.jpg["\']\s*/?>',
    re.IGNORECASE,
)

PARSER = None
PARSER_ERROR: str | None = None
PARSER_LOCK = asyncio.Lock()
INFERENCE_LOCK = asyncio.Lock()


class OvisOCR2Parser:
    def __init__(self, model_name_or_path: str):
        from vllm import LLM, SamplingParams

        self.model = LLM(
            model=model_name_or_path,
            tensor_parallel_size=1,
            # With a fixed KV cache this is vLLM's startup free-memory
            # threshold; it does not size or reserve the KV cache.
            gpu_memory_utilization=STARTUP_MEMORY_FRACTION,
            kv_cache_memory_bytes=KV_CACHE_MEMORY_MB * 1024 * 1024,
            max_model_len=MAX_MODEL_LEN,
            max_num_seqs=MAX_NUM_SEQS,
            gdn_prefill_backend=GDN_PREFILL_BACKEND,
        )
        self.prompt = self.model.get_tokenizer().apply_chat_template(
            [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": PROMPT}]}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        self.sampling_params = SamplingParams(max_tokens=MAX_TOKENS, temperature=0.0)

    @staticmethod
    def clean_truncated_repeats(
        text: str,
        min_text_len: int = 8000,
        max_period: int = 200,
        min_repeat_chars: int = 100,
        min_repeat_times: int = 5,
    ) -> str:
        n = len(text)
        if n < min_text_len:
            return text
        for unit_len in range(1, min(max_period, n - 1) + 1):
            if text[n - 1] != text[n - 1 - unit_len]:
                continue
            match_len = 1
            index = n - 2
            while index >= unit_len and text[index] == text[index - unit_len]:
                match_len += 1
                index -= 1
            total_len = match_len + unit_len
            repeat_times, tail_len = divmod(total_len, unit_len)
            if repeat_times >= min_repeat_times and total_len >= min_repeat_chars:
                return text[: n - total_len + unit_len] + text[n - tail_len :]
        return text

    def parse(self, images: list[Image.Image]) -> list[str]:
        inputs = [
            {
                "prompt": self.prompt,
                "multi_modal_data": {"image": image},
                "mm_processor_kwargs": {
                    "images_kwargs": {"min_pixels": MIN_PIXELS, "max_pixels": MAX_PIXELS}
                },
            }
            for image in images
        ]
        outputs = self.model.generate(inputs, self.sampling_params)
        return [self.clean_truncated_repeats(output.outputs[0].text.strip()) for output in outputs]


async def get_parser() -> OvisOCR2Parser:
    global PARSER, PARSER_ERROR
    if PARSER is not None:
        return PARSER
    async with PARSER_LOCK:
        if PARSER is not None:
            return PARSER
        PARSER_ERROR = None
        try:
            PARSER = await asyncio.to_thread(OvisOCR2Parser, MODEL_NAME)
            return PARSER
        except Exception as error:
            PARSER_ERROR = str(error) or error.__class__.__name__
            logger.exception("Failed to load OvisOCR2")
            raise


def decode_file_payload(value: str) -> bytes:
    encoded = value.split("base64,", 1)[1] if "base64," in value else value
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as error:
        raise HTTPException(status_code=400, detail="Invalid base64 file payload") from error


def render_pdf(file_bytes: bytes) -> list[Image.Image]:
    scale = PDF_DPI / 72
    with fitz.open(stream=file_bytes, filetype="pdf") as document:
        if len(document) > MAX_PAGES:
            raise HTTPException(status_code=400, detail=f"PDF exceeds the {MAX_PAGES}-page request limit")
        return [
            Image.open(io.BytesIO(page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")))
            .convert("RGB")
            for page in document
        ]


def prepare_images(file_bytes: bytes, file_type: int | None) -> tuple[list[Image.Image], int]:
    resolved_type = file_type if file_type is not None else (0 if file_bytes.startswith(b"%PDF-") else 1)
    if resolved_type == 0:
        return render_pdf(file_bytes), resolved_type
    try:
        return [Image.open(io.BytesIO(file_bytes)).convert("RGB")], resolved_type
    except Exception as error:
        raise HTTPException(status_code=400, detail="Unsupported or corrupt image") from error


async def read_input(request: Request) -> tuple[bytes, int | None]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if not upload or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="Missing multipart field: file")
        value = form.get("fileType")
        return await upload.read(), int(value) if value not in (None, "") else None
    try:
        payload = await request.json()
    except Exception as error:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from error
    raw_file = payload.get("file") or payload.get("image")
    if not raw_file:
        raise HTTPException(status_code=400, detail="Missing JSON field: file")
    value = payload.get("fileType")
    return decode_file_payload(str(raw_file)), int(value) if value is not None else None


def crop_visual_regions(markdown: str, page: Image.Image, page_index: int) -> tuple[str, dict[str, str], list[dict]]:
    images: dict[str, str] = {}
    blocks: list[dict] = []
    width, height = page.size

    def replace(match: re.Match) -> str:
        left, top, right, bottom = (int(value) for value in match.groups())
        x1, y1 = round(left * width / 1000), round(top * height / 1000)
        x2, y2 = round(right * width / 1000), round(bottom * height / 1000)
        x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
        y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            return ""
        path = f"ocr_images/ovisocr2_p{page_index + 1}_image_{len(images) + 1}.jpg"
        output = io.BytesIO()
        page.crop((x1, y1, x2, y2)).save(output, format="JPEG", quality=92)
        images[path] = base64.b64encode(output.getvalue()).decode("ascii")
        blocks.append({"block_label": "image", "block_bbox": [left, top, right, bottom]})
        return f"![image]({path})"

    return BBOX_IMAGE_RE.sub(replace, markdown), images, blocks


def build_response(markdowns: list[str], pages: list[Image.Image], file_type: int) -> dict[str, Any]:
    all_images: dict[str, str] = {}
    results = []
    rendered_pages = []
    for index, (markdown, page) in enumerate(zip(markdowns, pages)):
        rendered, images, blocks = crop_visual_regions(markdown, page, index)
        rendered_pages.append(rendered)
        all_images.update(images)
        results.append(
            {
                "parser": "ovisocr2",
                "pageIndex": index,
                "width": 1000,
                "height": 1000,
                "parsing_res_list": blocks,
                "markdown": {"text": rendered, "images": images},
            }
        )
    return {
        "markdown": "\n\n---\n\n".join(rendered_pages),
        "images": all_images,
        "layoutParsingResults": results,
        "model": MODEL_NAME,
        "fileType": file_type,
    }


@asynccontextmanager
async def lifespan(_: FastAPI):
    await get_parser()
    yield


app = FastAPI(title="OvisOCR2 Adapter", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    if PARSER is None:
        raise HTTPException(status_code=503, detail=PARSER_ERROR or "OvisOCR2 is loading")
    return {"status": "ok", "model": MODEL_NAME, "modelLoaded": True}


@app.post("/ocr")
async def ocr(request: Request):
    file_bytes, file_type = await read_input(request)
    pages, resolved_type = prepare_images(file_bytes, file_type)
    if not pages:
        raise HTTPException(status_code=400, detail="No images were produced for OCR")
    parser = await get_parser()
    async with INFERENCE_LOCK:
        markdowns = await asyncio.to_thread(parser.parse, pages)
    return build_response(markdowns, pages, resolved_type)
