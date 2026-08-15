import asyncio
import base64
import io
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import fitz
from fastapi import FastAPI, HTTPException, Request
from PIL import Image


logging.basicConfig(level=os.getenv("OVISOCR2_LOG_LEVEL", "INFO"))
logger = logging.getLogger("ovisocr2-adapter")

MODEL_NAME = os.getenv("OVISOCR2_MODEL_NAME", "ATH-MaaS/OvisOCR2")
MODEL_REVISION = os.getenv("OVISOCR2_MODEL_REVISION", "65c619d374b55d4152e85150fc1b003700bc1f0c")
BACKEND = os.getenv("OVISOCR2_BACKEND", "vllm").strip().lower()
TRANSFORMERS_DEVICE = os.getenv("OVISOCR2_TRANSFORMERS_DEVICE", "auto").strip().lower()
TRANSFORMERS_DTYPE = os.getenv("OVISOCR2_TRANSFORMERS_DTYPE", "auto").strip().lower()
ATTENTION_IMPLEMENTATION = os.getenv("OVISOCR2_ATTENTION_IMPLEMENTATION", "eager").strip()
RESTART_CHECK_INTERVAL = max(32, int(os.getenv("OVISOCR2_RESTART_CHECK_INTERVAL", "128")))
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


class VllmOvisOCR2Parser:
    def __init__(self, model_name_or_path: str):
        from vllm import LLM, SamplingParams

        self.model = LLM(
            model=model_name_or_path,
            revision=MODEL_REVISION,
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
        min_text_len: int = 800,
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

    @staticmethod
    def clean_restarted_document(text: str) -> str:
        """Remove a second pass that restarts from the document's first line."""
        lines = text.splitlines()
        first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
        if first_index is None:
            return text
        first_line = lines[first_index].strip()
        if len(first_line) < 8:
            return text
        substantial_lines = 0
        for index in range(first_index + 1, len(lines)):
            current = lines[index].strip()
            if current:
                substantial_lines += 1
            if substantial_lines >= 2 and current == first_line:
                return "\n".join(lines[:index]).rstrip()
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
        return [
            self.clean_truncated_repeats(
                self.clean_restarted_document(output.outputs[0].text.strip())
            )
            for output in outputs
        ]


class TransformersOvisOCR2Parser:
    """PyTorch backend for Apple Silicon and other machines without vLLM/CUDA."""

    clean_truncated_repeats = staticmethod(VllmOvisOCR2Parser.clean_truncated_repeats)
    clean_restarted_document = staticmethod(VllmOvisOCR2Parser.clean_restarted_document)

    def __init__(self, model_name_or_path: str):
        import torch
        from transformers import AutoModelForMultimodalLM, AutoProcessor

        if TRANSFORMERS_DEVICE == "auto":
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        else:
            device = TRANSFORMERS_DEVICE

        if TRANSFORMERS_DTYPE == "auto":
            dtype = torch.float16 if device == "mps" else (
                torch.bfloat16 if device == "cuda" else torch.float32
            )
        else:
            dtype = getattr(torch, TRANSFORMERS_DTYPE)

        logger.info(
            "Loading OvisOCR2 with Transformers on %s using %s",
            device,
            str(dtype).removeprefix("torch."),
        )
        self.device = torch.device(device)
        self.processor = AutoProcessor.from_pretrained(model_name_or_path, revision=MODEL_REVISION)
        self.model = AutoModelForMultimodalLM.from_pretrained(
            model_name_or_path,
            revision=MODEL_REVISION,
            dtype=dtype,
            attn_implementation=ATTENTION_IMPLEMENTATION,
            low_cpu_mem_usage=True,
        ).to(self.device)
        self.model.eval()

    def parse(self, images: list[Image.Image]) -> list[str]:
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList

        markdowns = []
        for image_index, image in enumerate(images):
            started_at = time.monotonic()
            logger.info(
                "Starting Transformers OCR for image %s/%s (%sx%s), restart check every %s tokens",
                image_index + 1,
                len(images),
                image.width,
                image.height,
                RESTART_CHECK_INTERVAL,
            )
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ]
            inputs = self.processor.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                enable_thinking=False,
                return_dict=True,
                return_tensors="pt",
                processor_kwargs={
                    "images_kwargs": {
                        "min_pixels": MIN_PIXELS,
                        "max_pixels": MAX_PIXELS,
                    }
                },
            )
            inputs = {
                key: value.to(self.device) if hasattr(value, "to") else value
                for key, value in inputs.items()
            }
            input_length = inputs["input_ids"].shape[-1]
            image_grid = inputs.get("image_grid_thw")
            visual_tokens = (
                int(image_grid.prod(dim=-1).sum().item())
                // int(self.processor.image_processor.merge_size) ** 2
                if image_grid is not None
                else 0
            )
            logger.info(
                "Prepared image %s/%s with %s visual tokens (%s total prompt tokens)",
                image_index + 1,
                len(images),
                visual_tokens,
                input_length,
            )

            processor = self.processor
            clean_restarted_document = self.clean_restarted_document

            class DocumentRestartStoppingCriteria(StoppingCriteria):
                def __call__(self, input_ids, scores, **kwargs):
                    generated_length = input_ids.shape[-1] - input_length
                    if (
                        generated_length < RESTART_CHECK_INTERVAL
                        or generated_length % RESTART_CHECK_INTERVAL
                    ):
                        return False
                    partial = processor.decode(
                        input_ids[0][input_length:],
                        skip_special_tokens=True,
                    )
                    partial = re.sub(r"\n*<think>\s*</think>\n*", "\n\n", partial).strip()
                    return clean_restarted_document(partial) != partial

            with torch.inference_mode():
                output_ids = self.model.generate(
                    **inputs,
                    max_new_tokens=MAX_TOKENS,
                    do_sample=False,
                    no_repeat_ngram_size=64,
                    stopping_criteria=StoppingCriteriaList([DocumentRestartStoppingCriteria()]),
                    use_cache=True,
                )
            text = self.processor.decode(
                output_ids[0][input_length:],
                skip_special_tokens=True,
            ).strip()
            text = re.sub(r"\n*<think>\s*</think>\n*", "\n\n", text).strip()
            text = self.clean_restarted_document(text)
            markdowns.append(self.clean_truncated_repeats(text))
            logger.info(
                "Finished Transformers OCR for image %s/%s in %.1fs (%s generated tokens)",
                image_index + 1,
                len(images),
                time.monotonic() - started_at,
                output_ids.shape[-1] - input_length,
            )
        return markdowns


class MlxOvisOCR2Parser:
    """Native Apple Silicon backend powered by MLX-VLM."""

    clean_truncated_repeats = staticmethod(VllmOvisOCR2Parser.clean_truncated_repeats)
    clean_restarted_document = staticmethod(VllmOvisOCR2Parser.clean_restarted_document)

    def __init__(self, model_name_or_path: str):
        from huggingface_hub import snapshot_download
        from mlx_vlm import load
        from mlx_vlm.prompt_utils import apply_chat_template

        logger.info("Loading OvisOCR2 with MLX-VLM")
        resolved_model = model_name_or_path
        if not Path(model_name_or_path).exists():
            resolved_model = snapshot_download(repo_id=model_name_or_path, revision=MODEL_REVISION)
        self.model, self.processor = load(resolved_model)
        self.prompt = apply_chat_template(
            self.processor,
            self.model.config,
            PROMPT,
            num_images=1,
            enable_thinking=False,
            thinking_mode="disabled",
        )

    def parse(self, images: list[Image.Image]) -> list[str]:
        from mlx_vlm import generate

        markdowns = []
        for image_index, image in enumerate(images):
            started_at = time.monotonic()
            logger.info(
                "Starting MLX OCR for image %s/%s (%sx%s, max %s pixels)",
                image_index + 1,
                len(images),
                image.width,
                image.height,
                MAX_PIXELS,
            )
            result = generate(
                self.model,
                self.processor,
                self.prompt,
                image=[image],
                max_tokens=MAX_TOKENS,
                temperature=0.0,
                min_pixels=MIN_PIXELS,
                max_pixels=MAX_PIXELS,
                enable_thinking=False,
                skip_special_tokens=True,
                verbose=False,
            )
            text = re.sub(r"\n*<think>\s*</think>\n*", "\n\n", result.text).strip()
            text = self.clean_restarted_document(text)
            markdowns.append(self.clean_truncated_repeats(text))
            logger.info(
                "Finished MLX OCR for image %s/%s in %.1fs "
                "(%s generated tokens, %.1f tokens/s, %.2f GB peak memory, reason=%s)",
                image_index + 1,
                len(images),
                time.monotonic() - started_at,
                result.generation_tokens,
                result.generation_tps,
                result.peak_memory,
                result.finish_reason,
            )
        return markdowns


def create_parser():
    if BACKEND == "vllm":
        return VllmOvisOCR2Parser(MODEL_NAME)
    if BACKEND == "transformers":
        return TransformersOvisOCR2Parser(MODEL_NAME)
    if BACKEND == "mlx":
        return MlxOvisOCR2Parser(MODEL_NAME)
    raise ValueError(f"Unsupported OvisOCR2 backend: {BACKEND}")


async def get_parser():
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


def build_response(
    markdowns: list[str], pages: list[Image.Image], file_type: int, *, page_offset: int = 0
) -> dict[str, Any]:
    all_images: dict[str, str] = {}
    results = []
    rendered_pages = []
    for index, (markdown, page) in enumerate(zip(markdowns, pages)):
        page_index = page_offset + index
        rendered, images, blocks = crop_visual_regions(markdown, page, page_index)
        rendered_pages.append(rendered)
        all_images.update(images)
        results.append(
            {
                "parser": "ovisocr2",
                "pageIndex": page_index,
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
    return {"status": "ok", "model": MODEL_NAME, "backend": BACKEND, "modelLoaded": True}


@app.post("/ocr")
async def ocr(request: Request):
    file_bytes, file_type = await read_input(request)
    resolved_type = file_type if file_type is not None else (0 if file_bytes.startswith(b"%PDF-") else 1)
    if resolved_type != 0:
        pages, resolved_type = prepare_images(file_bytes, resolved_type)
        if not pages:
            raise HTTPException(status_code=400, detail="No images were produced for OCR")
        parser = await get_parser()
        async with INFERENCE_LOCK:
            markdowns = await asyncio.to_thread(parser.parse, pages)
        return build_response(markdowns, pages, resolved_type)

    parser = await get_parser()
    scale = PDF_DPI / 72
    markdown_pages: list[str] = []
    all_images: dict[str, str] = {}
    all_results: list[dict] = []
    with fitz.open(stream=file_bytes, filetype="pdf") as document:
        if len(document) < 1:
            raise HTTPException(status_code=400, detail="PDF contains no pages")
        if len(document) > MAX_PAGES:
            raise HTTPException(status_code=400, detail=f"PDF exceeds the {MAX_PAGES}-page request limit")
        for page_index in range(len(document)):
            pixmap = document[page_index].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            page = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
            try:
                async with INFERENCE_LOCK:
                    markdown = (await asyncio.to_thread(parser.parse, [page]))[0]
                chunk = build_response([markdown], [page], resolved_type, page_offset=page_index)
                markdown_pages.append(chunk["markdown"])
                all_images.update(chunk["images"])
                all_results.extend(chunk["layoutParsingResults"])
            finally:
                page.close()
    return {
        "markdown": "\n\n---\n\n".join(markdown_pages),
        "images": all_images,
        "layoutParsingResults": all_results,
        "model": MODEL_NAME,
        "fileType": resolved_type,
    }
