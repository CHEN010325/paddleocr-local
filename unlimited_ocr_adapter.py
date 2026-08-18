import base64
import asyncio
import contextlib
import io
import json
import logging
import os
import queue
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image

logger = logging.getLogger("pandocr.unlimited_ocr")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


def parse_bool_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


SGLANG_URL = os.getenv("UNLIMITED_OCR_SGLANG_URL", "http://unlimited-ocr-sglang:10000").rstrip("/")
SERVED_MODEL_NAME = os.getenv("UNLIMITED_OCR_SERVED_MODEL_NAME", "Unlimited-OCR")
KNOWN_BACKENDS = {"transformers", "sglang"}
SUPPORTED_BACKENDS = {
    item.strip().lower()
    for item in os.getenv("UNLIMITED_OCR_SUPPORTED_BACKENDS", "transformers,sglang").split(",")
    if item.strip().lower() in KNOWN_BACKENDS
} or {"transformers"}


def normalize_backend(value: Any, fallback: str = "transformers") -> str:
    backend = str(value or fallback).strip().lower()
    if backend not in SUPPORTED_BACKENDS:
        fallback_backend = str(fallback or "").strip().lower()
        if fallback_backend in SUPPORTED_BACKENDS:
            return fallback_backend
        supported = ", ".join(sorted(SUPPORTED_BACKENDS))
        raise HTTPException(status_code=400, detail=f"Unsupported Unlimited-OCR backend. Use one of: {supported}.")
    return backend


def read_persisted_backend(default: str) -> str:
    settings_path = os.getenv("PANDOCR_RUNTIME_SETTINGS_FILE", "").strip()
    if not settings_path:
        return default
    try:
        path = Path(settings_path)
        if not path.exists():
            return default
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            return normalize_backend(data.get("unlimitedOcrBackend"), default)
    except Exception:
        logger.warning("Failed to read runtime settings: %s", settings_path, exc_info=True)
    return default


DEFAULT_BACKEND = read_persisted_backend(normalize_backend(os.getenv("UNLIMITED_OCR_BACKEND", "transformers")))
BACKEND = DEFAULT_BACKEND
MODEL_NAME = os.getenv("UNLIMITED_OCR_MODEL_NAME", "baidu/Unlimited-OCR")
DEFAULT_MODEL_REVISIONS = {
    "baidu/Unlimited-OCR": "07dea832e22aefee32ad281d4b80551282e1c168",
    "sabafallah/Unlimited-OCR-Universal": "bc00ae36def7fe8d23980adf5a901125fe0040a2",
}
MODEL_REVISION = os.getenv("UNLIMITED_OCR_MODEL_REVISION", DEFAULT_MODEL_REVISIONS.get(MODEL_NAME, "main"))
REQUEST_TIMEOUT = float(os.getenv("UNLIMITED_OCR_REQUEST_TIMEOUT", "1200"))
PDF_DPI = int(os.getenv("UNLIMITED_OCR_PDF_DPI", "300"))
SGLANG_PDF_DPI = int(os.getenv("UNLIMITED_OCR_SGLANG_PDF_DPI", "96"))
MAX_PAGES_PER_REQUEST = int(os.getenv("UNLIMITED_OCR_MAX_PAGES_PER_REQUEST", "50"))
MAX_RENDER_PIXELS = int(os.getenv("UNLIMITED_OCR_MAX_RENDER_PIXELS", "60000000"))
SINGLE_IMAGE_MODE = os.getenv("UNLIMITED_OCR_SINGLE_IMAGE_MODE", "gundam")
MULTI_IMAGE_MODE = os.getenv("UNLIMITED_OCR_MULTI_IMAGE_MODE", "base")
SGLANG_PDF_MAX_DIMENSION = int(os.getenv("UNLIMITED_OCR_SGLANG_PDF_MAX_DIMENSION", "1120"))
SINGLE_PROMPT = os.getenv("UNLIMITED_OCR_SINGLE_PROMPT", "document parsing.")
MULTI_PROMPT = os.getenv("UNLIMITED_OCR_MULTI_PROMPT", "Multi page parsing.")
NO_REPEAT_NGRAM_SIZE = int(os.getenv("UNLIMITED_OCR_NO_REPEAT_NGRAM_SIZE", "35"))
SINGLE_NGRAM_WINDOW = int(os.getenv("UNLIMITED_OCR_SINGLE_NGRAM_WINDOW", "128"))
MULTI_NGRAM_WINDOW = int(os.getenv("UNLIMITED_OCR_MULTI_NGRAM_WINDOW", "1024"))
MAX_TOKENS = int(os.getenv("UNLIMITED_OCR_MAX_TOKENS", "32768"))
STREAM_HEARTBEAT_SECONDS = float(os.getenv("UNLIMITED_OCR_STREAM_HEARTBEAT_SECONDS", "20"))
STREAM_RENDER_MIN_CHAR_DELTA = 24
STREAM_RENDER_MAX_INTERVAL_SECONDS = 0.25
STREAM_RENDER_BOUNDARY_SUFFIXES = (".", "\n", "。", "！", "？", "；", "，", "<|/det|>")
TRANSFORMERS_SINGLE_BASE_SIZE = int(os.getenv("UNLIMITED_OCR_TRANSFORMERS_SINGLE_BASE_SIZE", "1024"))
TRANSFORMERS_SINGLE_IMAGE_SIZE = int(os.getenv("UNLIMITED_OCR_TRANSFORMERS_SINGLE_IMAGE_SIZE", "640"))
TRANSFORMERS_MPS_OOM_RETRY = parse_bool_env("UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY", "1")
TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE = int(os.getenv("UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE", "640"))
TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS = int(
    os.getenv("UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS", str(min(MAX_TOKENS, 4096)))
)
SGLANG_MAX_TOKENS = int(os.getenv("UNLIMITED_OCR_SGLANG_MAX_TOKENS", str(max(1, MAX_TOKENS - 4096))))
SGLANG_CONTEXT_TOKEN_RESERVE = int(os.getenv("UNLIMITED_OCR_SGLANG_CONTEXT_TOKEN_RESERVE", "128"))
ENABLE_DEGENERATION_GUARD = os.getenv("UNLIMITED_OCR_ENABLE_DEGENERATION_GUARD", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
DEGENERATION_REPEAT_THRESHOLD = int(os.getenv("UNLIMITED_OCR_DEGENERATION_REPEAT_THRESHOLD", "12"))
DEGENERATION_CONSECUTIVE_REPEAT_THRESHOLD = int(
    os.getenv("UNLIMITED_OCR_DEGENERATION_CONSECUTIVE_REPEAT_THRESHOLD", "4")
)
DEGENERATION_REPEAT_MAX_EXTRA_GAP = int(os.getenv("UNLIMITED_OCR_DEGENERATION_REPEAT_MAX_EXTRA_GAP", "3"))
DEGENERATION_WINDOW_CHARS = int(os.getenv("UNLIMITED_OCR_DEGENERATION_WINDOW_CHARS", "4000"))
DEGENERATION_RETRY_NGRAM_SIZE = int(os.getenv("UNLIMITED_OCR_DEGENERATION_RETRY_NGRAM_SIZE", "8"))
DEGENERATION_RETRY_WINDOW = int(os.getenv("UNLIMITED_OCR_DEGENERATION_RETRY_WINDOW", "512"))
ENABLE_NO_REPEAT_PROCESSOR = os.getenv("UNLIMITED_OCR_ENABLE_NO_REPEAT_PROCESSOR", "0").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
DEFAULT_NO_REPEAT_PROCESSOR_STR = (
    os.getenv("UNLIMITED_OCR_NO_REPEAT_PROCESSOR_STR", "").strip()
    or '{"callable": "80049559000000000000008c2a73676c616e672e7372742e73616d706c696e672e637573746f6d5f6c6f6769745f70726f636573736f72948c26446565707365656b4f43524e6f5265706561744e4772616d4c6f67697450726f636573736f729493942e"}'
)
PRELOAD_TRANSFORMERS = DEFAULT_BACKEND == "transformers" and parse_bool_env("UNLIMITED_OCR_PRELOAD", "1")
TRANSFORMERS_DEVICE = os.getenv("UNLIMITED_OCR_TRANSFORMERS_DEVICE", "auto").strip().lower()
TRANSFORMERS_DTYPE = os.getenv("UNLIMITED_OCR_TRANSFORMERS_DTYPE", "auto").strip().lower()
TRANSFORMERS_ATTENTION_IMPLEMENTATION = os.getenv("UNLIMITED_OCR_ATTENTION_IMPLEMENTATION", "").strip()

NO_REPEAT_PROCESSOR_STR: str | None = None
TRANSFORMERS_TOKENIZER = None
TRANSFORMERS_MODEL = None
TRANSFORMERS_MODEL_LOCK = asyncio.Lock()
TRANSFORMERS_INFERENCE_LOCK = asyncio.Lock()
TRANSFORMERS_MODEL_LOADING = False
TRANSFORMERS_MODEL_ERROR: str | None = None
TRANSFORMERS_MODEL_LOADED_AT: float | None = None
TRANSFORMERS_RUNTIME: dict[str, Any] = {}


def cleanup_torch_accelerator_cache() -> None:
    import gc

    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            with contextlib.suppress(Exception):
                torch.cuda.ipc_collect()
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            with contextlib.suppress(Exception):
                torch.mps.empty_cache()
    except Exception:
        logger.debug("Torch accelerator cache cleanup skipped", exc_info=True)


def transformers_runtime_label() -> str:
    device = TRANSFORMERS_RUNTIME.get("device") or TRANSFORMERS_DEVICE or "auto"
    dtype = TRANSFORMERS_RUNTIME.get("dtype") or TRANSFORMERS_DTYPE or "auto"
    return f"{device}/{dtype}"


def transformers_status_markdown(message: str) -> str:
    return f"**Unlimited-OCR status**\n\n{message}"


def is_accelerator_oom(error: BaseException) -> bool:
    detail = str(error).lower()
    return "out of memory" in detail or "mps backend out of memory" in detail


def transformers_error_detail(error: BaseException) -> str:
    global TRANSFORMERS_MODEL_ERROR
    detail = str(error) or error.__class__.__name__
    TRANSFORMERS_MODEL_ERROR = detail

    if is_accelerator_oom(error):
        cleanup_torch_accelerator_cache()
        if "mps" in detail.lower():
            return (
                "Unlimited-OCR Transformers ran out of Apple Silicon MPS memory. "
                f"Current settings: PDF_DPI={PDF_DPI}, MAX_TOKENS={MAX_TOKENS}. "
                "Retry the batch after lowering UNLIMITED_OCR_PDF_DPI or UNLIMITED_OCR_MAX_TOKENS. "
                f"Original error: {detail}"
            )
        return f"Unlimited-OCR Transformers ran out of accelerator memory. Original error: {detail}"

    return detail

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    preload_task = None
    if PRELOAD_TRANSFORMERS:
        preload_task = asyncio.create_task(preload_transformers_components())
    try:
        yield
    finally:
        if preload_task and not preload_task.done():
            preload_task.cancel()


app = FastAPI(title="Unlimited-OCR Adapter", version="0.1.0", lifespan=lifespan)

DET_BLOCK_RE = re.compile(r"<\|det\|>\s*([A-Za-z_][\w-]*)\s*(\[[^\]]*\])?\s*<\|/det\|>")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]+|\d+")
ANCHOR_WORD_RE = re.compile(r"[A-Za-z0-9]+")
COMMON_BIBLIOGRAPHY_GRAMS = {
    "arxiv preprint arxiv",
}
COMMON_LAYOUT_GRAMS = {
    "td td td",
    "td td td td",
    "tr td td",
    "td tr td",
    "th th th",
    "li li li",
    "left left left",
    "right right right",
    "left right left",
}
COMMON_LAYOUT_WORDS = {"td", "tr", "th", "li", "left", "right", "text", "rowspan", "colspan"}
_UNLIMITED_LATEX_COMMAND_RUN_RE = re.compile(
    r"(?:\\(?:left|right|middle|text|frac|begin|end)\s*){6,}",
    re.IGNORECASE,
)
SGLANG_CONTEXT_ERROR_RE = re.compile(
    r"maximum context length of\s+(\d+)\s+tokens.*?"
    r"(\d+)\s+tokens from the input messages and\s+(\d+)\s+tokens for the completion",
    re.IGNORECASE | re.DOTALL,
)
SKIP_MARKDOWN_LABELS = {"header", "footer", "number", "page_number", "page_num"}
CAPTION_LABELS = {"image_caption", "figure_caption", "table_caption"}
TITLE_LABELS = {"title", "section_title"}
IMAGE_LABELS = {"image", "chart"}
UNLIMITED_OCR_COORDINATE_SIZE = 1000


class DegenerateGenerationError(RuntimeError):
    pass


def parse_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def decode_base64_payload(value: str) -> bytes:
    payload = value.split("base64,", 1)[1] if "base64," in value else value
    try:
        return base64.b64decode(payload, validate=True)
    except Exception as err:
        raise HTTPException(status_code=400, detail="Invalid base64 file payload") from err


def infer_file_type(file_bytes: bytes, file_type: int | None) -> int:
    if file_type is not None:
        return file_type
    return 0 if file_bytes.startswith(b"%PDF-") else 1


async def read_input(request: Request) -> tuple[bytes, int, str]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if not upload or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="Missing multipart field: file")
        file_bytes = await upload.read()
        file_type = parse_optional_int(form.get("fileType"))
        backend = normalize_backend(form.get("backend") or form.get("unlimitedOcrBackend"), DEFAULT_BACKEND)
        return file_bytes, infer_file_type(file_bytes, file_type), backend

    try:
        payload = await request.json()
    except Exception as err:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from err

    raw_file = payload.get("file") or payload.get("image")
    if not raw_file:
        raise HTTPException(status_code=400, detail="Missing JSON field: file")
    file_bytes = decode_base64_payload(str(raw_file))
    backend = normalize_backend(payload.get("backend") or payload.get("unlimitedOcrBackend"), DEFAULT_BACKEND)
    return file_bytes, infer_file_type(file_bytes, parse_optional_int(payload.get("fileType"))), backend


def image_bytes_to_png(file_bytes: bytes) -> bytes:
    try:
        image = Image.open(io.BytesIO(file_bytes))
        if getattr(image, "is_animated", False):
            image.seek(0)
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    except Exception:
        return file_bytes


def pdf_to_png_pages(file_bytes: bytes, dpi: int) -> list[bytes]:
    import fitz

    document = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        if document.page_count > MAX_PAGES_PER_REQUEST:
            raise HTTPException(
                status_code=413,
                detail=f"Unlimited-OCR request has {document.page_count} pages; max is {MAX_PAGES_PER_REQUEST}.",
            )
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        estimated_pixels = sum(
            int(page.rect.width * dpi / 72) * int(page.rect.height * dpi / 72)
            for page in document
        )
        if MAX_RENDER_PIXELS > 0 and estimated_pixels > MAX_RENDER_PIXELS:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Rendered PDF would contain about {estimated_pixels:,} pixels; "
                    f"the request limit is {MAX_RENDER_PIXELS:,}. Reduce the PDF batch size or DPI."
                ),
            )
        pages: list[bytes] = []
        for page in document:
            pixmap = page.get_pixmap(matrix=matrix)
            pages.append(pixmap.tobytes("png"))
        return pages
    finally:
        document.close()


def extract_pdf_page_texts(file_bytes: bytes) -> list[str]:
    if not file_bytes.startswith(b"%PDF-"):
        return []
    import fitz

    document = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        return [normalize_anchor_text(page.get_text("text")) for page in document]
    except Exception:
        logger.debug("Failed to extract PDF text anchors", exc_info=True)
        return []
    finally:
        document.close()


def prepare_image_pages_and_texts(
    file_bytes: bytes,
    file_type: int,
    pdf_dpi: int | None = None,
) -> tuple[list[bytes], list[str]]:
    if file_type == 0:
        page_texts = extract_pdf_page_texts(file_bytes)
        return pdf_to_png_pages(file_bytes, int(pdf_dpi or PDF_DPI)), page_texts
    if file_type == 1:
        return [image_bytes_to_png(file_bytes)], []
    raise HTTPException(status_code=400, detail="Unsupported fileType. Use 0 for PDF or 1 for image.")


def encode_image_content(image_bytes: bytes, mime: str = "image/png") -> dict:
    data = base64.b64encode(image_bytes).decode("utf-8")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def resize_sglang_pdf_page(image_bytes: bytes) -> bytes:
    """Bound PDF raster dimensions for SGLang's base layout mode.

    The model's PDF path is unusually sensitive to oversized raster pages:
    dense equation/algorithm pages can fall into a decoder trace even though
    the same page at the model's native layout scale parses correctly.  Keep
    the original page bytes for cropping/results; only the request image is
    resized.
    """
    limit = max(0, int(SGLANG_PDF_MAX_DIMENSION))
    if limit <= 0:
        return image_bytes
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        width, height = image.size
        longest = max(width, height)
        if longest <= limit:
            return image_bytes
        scale = limit / longest
        resized = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
        output = io.BytesIO()
        resized.save(output, format="PNG", optimize=True)
        return output.getvalue()
    except Exception:
        logger.warning("Failed to resize PDF page for SGLang; using original bytes", exc_info=True)
        return image_bytes


def select_transformers_device(torch_module) -> str:
    requested = TRANSFORMERS_DEVICE
    if requested in {"cuda", "mps", "cpu"}:
        if requested == "cuda" and not torch_module.cuda.is_available():
            raise RuntimeError("UNLIMITED_OCR_TRANSFORMERS_DEVICE=cuda was requested, but CUDA is not available.")
        if requested == "mps" and not torch_module.backends.mps.is_available():
            raise RuntimeError("UNLIMITED_OCR_TRANSFORMERS_DEVICE=mps was requested, but MPS is not available.")
        return requested
    if requested not in {"", "auto"}:
        raise RuntimeError("UNLIMITED_OCR_TRANSFORMERS_DEVICE must be auto, cuda, mps, or cpu.")
    if torch_module.cuda.is_available():
        return "cuda"
    if torch_module.backends.mps.is_available():
        return "mps"
    return "cpu"


def select_transformers_dtype(torch_module, device: str):
    requested = TRANSFORMERS_DTYPE
    dtype_by_name = {
        "float32": torch_module.float32,
        "fp32": torch_module.float32,
        "bfloat16": torch_module.bfloat16,
        "bf16": torch_module.bfloat16,
        "float16": torch_module.float16,
        "fp16": torch_module.float16,
    }
    if requested in dtype_by_name:
        return dtype_by_name[requested]
    if requested not in {"", "auto"}:
        raise RuntimeError("UNLIMITED_OCR_TRANSFORMERS_DTYPE must be auto, float32, bfloat16, or float16.")
    return torch_module.bfloat16 if device == "cuda" else torch_module.float32


async def get_transformers_components():
    global TRANSFORMERS_TOKENIZER, TRANSFORMERS_MODEL, TRANSFORMERS_MODEL_LOADING, TRANSFORMERS_MODEL_ERROR
    global TRANSFORMERS_MODEL_LOADED_AT, TRANSFORMERS_RUNTIME
    if TRANSFORMERS_TOKENIZER is not None and TRANSFORMERS_MODEL is not None:
        return TRANSFORMERS_TOKENIZER, TRANSFORMERS_MODEL

    async with TRANSFORMERS_MODEL_LOCK:
        if TRANSFORMERS_TOKENIZER is not None and TRANSFORMERS_MODEL is not None:
            return TRANSFORMERS_TOKENIZER, TRANSFORMERS_MODEL

        def load_model():
            global TRANSFORMERS_RUNTIME
            import torch
            from transformers import AutoModel, AutoTokenizer

            device = select_transformers_device(torch)
            dtype = select_transformers_dtype(torch, device)
            model_kwargs = {
                "trust_remote_code": True,
                "revision": MODEL_REVISION,
                "use_safetensors": True,
                "torch_dtype": dtype,
            }
            if TRANSFORMERS_ATTENTION_IMPLEMENTATION:
                model_kwargs["attn_implementation"] = TRANSFORMERS_ATTENTION_IMPLEMENTATION

            tokenizer = AutoTokenizer.from_pretrained(
                MODEL_NAME, trust_remote_code=True, revision=MODEL_REVISION
            )
            model = AutoModel.from_pretrained(MODEL_NAME, **model_kwargs)
            model = model.eval()
            if device == "cuda":
                model = model.cuda()
            elif device != "cpu":
                model = model.to(device)
            TRANSFORMERS_RUNTIME = {
                "device": device,
                "dtype": str(dtype).replace("torch.", ""),
                "attentionImplementation": TRANSFORMERS_ATTENTION_IMPLEMENTATION or None,
            }
            return tokenizer, model

        TRANSFORMERS_MODEL_LOADING = True
        TRANSFORMERS_MODEL_ERROR = None
        try:
            TRANSFORMERS_TOKENIZER, TRANSFORMERS_MODEL = await asyncio.to_thread(load_model)
            TRANSFORMERS_MODEL_LOADED_AT = time.time()
        except Exception as err:
            TRANSFORMERS_MODEL_ERROR = str(err)
            raise
        finally:
            TRANSFORMERS_MODEL_LOADING = False
        return TRANSFORMERS_TOKENIZER, TRANSFORMERS_MODEL


async def preload_transformers_components(force: bool = False) -> None:
    if not force and not PRELOAD_TRANSFORMERS:
        return
    try:
        logger.info("Preloading Unlimited-OCR Transformers backend: %s", MODEL_NAME)
        await get_transformers_components()
        logger.info("Unlimited-OCR Transformers backend is warm")
    except Exception:
        logger.exception("Failed to preload Unlimited-OCR Transformers backend")


async def unload_transformers_components() -> dict:
    global TRANSFORMERS_TOKENIZER, TRANSFORMERS_MODEL, TRANSFORMERS_MODEL_LOADING, TRANSFORMERS_MODEL_ERROR
    global TRANSFORMERS_MODEL_LOADED_AT
    async with TRANSFORMERS_INFERENCE_LOCK:
        async with TRANSFORMERS_MODEL_LOCK:
            was_loaded = TRANSFORMERS_MODEL is not None or TRANSFORMERS_TOKENIZER is not None
            TRANSFORMERS_TOKENIZER = None
            TRANSFORMERS_MODEL = None
            TRANSFORMERS_MODEL_LOADING = False
            TRANSFORMERS_MODEL_ERROR = None
            TRANSFORMERS_MODEL_LOADED_AT = None

            cleanup_torch_accelerator_cache()
    return {"released": was_loaded, "modelLoaded": False}


def write_temp_image_files(image_pages: list[bytes], directory: str) -> list[str]:
    paths: list[str] = []
    for index, image_bytes in enumerate(image_pages):
        path = os.path.join(directory, f"page_{index + 1:04d}.png")
        with open(path, "wb") as file:
            file.write(image_bytes)
        paths.append(path)
    return paths


class QueueTextWriter:
    def __init__(self, output_queue: queue.Queue[str] | None = None):
        self.output_queue = output_queue
        self.parts: list[str] = []

    def write(self, text: str) -> int:
        if text:
            self.parts.append(text)
            if self.output_queue is not None:
                self.output_queue.put(text)
        return len(text or "")

    def flush(self) -> None:
        return None

    def text(self) -> str:
        return "".join(self.parts)


def is_transformers_stdout_noise(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return (
        stripped.startswith("INFO:")
        or stripped.startswith("WARNING:")
        or stripped.startswith("The attention mask")
        or stripped.startswith("Setting `pad_token_id`")
        or stripped.startswith("image:")
        or stripped.startswith("other:")
        or stripped.startswith("===============")
        or stripped.startswith("===")
        or stripped.startswith("%|")
    )


def extract_layout_text_from_transformers_stdout(stdout_text: str) -> str:
    if "<|det|>" not in stdout_text:
        return ""

    text = stdout_text.replace("\r", "\n")
    lines = text.splitlines()
    kept: list[str] = []
    seen_layout = False
    for line in lines:
        stripped = line.strip()
        if "<|det|>" in stripped:
            seen_layout = True
        if not seen_layout or is_transformers_stdout_noise(stripped):
            continue
        kept.append(stripped)

    cleaned = "\n".join(kept).strip()
    if "<|det|>" not in cleaned:
        return ""
    return cleaned


def should_emit_stream_progress(markdown: str, last_markdown: str, last_emit_size: int) -> bool:
    if markdown == last_markdown:
        return False
    if len(markdown) - last_emit_size >= 24:
        return True
    return markdown.endswith((".", "\n", "\u3002", "\uff01", "\uff1f", "\uff1b", "\uff0c"))


def should_render_stream_snapshot(
    raw_text: str,
    last_render_size: int,
    last_render_at: float,
    now: float | None = None,
) -> bool:
    if not raw_text:
        return False
    if len(raw_text) - last_render_size >= STREAM_RENDER_MIN_CHAR_DELTA:
        return True
    if raw_text.endswith(STREAM_RENDER_BOUNDARY_SUFFIXES):
        return True
    current = time.monotonic() if now is None else now
    return current - last_render_at >= STREAM_RENDER_MAX_INTERVAL_SECONDS


def extract_text_from_transformers_result(result: Any, output_dir: str) -> str:
    if isinstance(result, str) and result.strip():
        return result
    if isinstance(result, dict):
        for key in ("markdown", "text", "result", "output"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                nested = value.get("text") or value.get("markdown")
                if isinstance(nested, str) and nested.strip():
                    return nested

    candidates: list[str] = []
    for root, _, files in os.walk(output_dir):
        for name in files:
            if name.lower().endswith((".md", ".markdown", ".txt", ".json")):
                candidates.append(os.path.join(root, name))
    candidates.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    for path in candidates:
        try:
            content = Path(path).read_text(encoding="utf-8")
            if not content.strip():
                continue
            if path.lower().endswith(".json"):
                data = json.loads(content)
                extracted = extract_text_from_transformers_result(data, output_dir)
                if extracted:
                    return extracted
            return content
        except Exception:
            continue
    return ""


def single_image_transformers_attempts() -> list[dict[str, Any]]:
    crop_mode = SINGLE_IMAGE_MODE == "gundam"
    image_size = TRANSFORMERS_SINGLE_IMAGE_SIZE if crop_mode else max(TRANSFORMERS_SINGLE_IMAGE_SIZE, 640)
    attempts = [
        {
            "label": SINGLE_IMAGE_MODE,
            "crop_mode": crop_mode,
            "base_size": TRANSFORMERS_SINGLE_BASE_SIZE,
            "image_size": image_size,
            "max_tokens": MAX_TOKENS,
        }
    ]

    if TRANSFORMERS_MPS_OOM_RETRY:
        attempts.append(
            {
                "label": "base-mps-oom-retry",
                "crop_mode": False,
                "base_size": TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE,
                "image_size": TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE,
                "max_tokens": max(1, TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS),
            }
        )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for attempt in attempts:
        key = (
            attempt["crop_mode"],
            attempt["base_size"],
            attempt["image_size"],
            attempt["max_tokens"],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(attempt)
    return deduped


def run_transformers_inference_sync(
    tokenizer,
    model,
    image_pages: list[bytes],
    file_type: int,
    stdout_writer: QueueTextWriter | None = None,
) -> tuple[str, dict]:
    is_multi_page = len(image_pages) > 1
    try:
        with tempfile.TemporaryDirectory(prefix="unlimited_ocr_") as tmp_dir:
            image_paths = write_temp_image_files(image_pages, tmp_dir)
            output_dir = os.path.join(tmp_dir, "outputs")
            os.makedirs(output_dir, exist_ok=True)
            capture = stdout_writer or QueueTextWriter()
            with contextlib.redirect_stdout(capture):
                if is_multi_page:
                    result = model.infer_multi(
                        tokenizer,
                        prompt="<image>Multi page parsing.",
                        image_files=image_paths,
                        output_path=output_dir,
                        image_size=1024,
                        max_length=MAX_TOKENS,
                        no_repeat_ngram_size=NO_REPEAT_NGRAM_SIZE,
                        ngram_window=MULTI_NGRAM_WINDOW,
                        save_results=True,
                    )
                    images_config = {"image_mode": "base", "backend": "transformers"}
                else:
                    last_error: BaseException | None = None
                    attempts = single_image_transformers_attempts()
                    for attempt_index, attempt in enumerate(attempts):
                        if attempt_index:
                            logger.warning(
                                "Retrying Unlimited-OCR Transformers with %s after accelerator OOM",
                                attempt["label"],
                            )
                        try:
                            result = model.infer(
                                tokenizer,
                                prompt="<image>document parsing.",
                                image_file=image_paths[0],
                                output_path=output_dir,
                                base_size=attempt["base_size"],
                                image_size=attempt["image_size"],
                                crop_mode=attempt["crop_mode"],
                                max_length=attempt["max_tokens"],
                                no_repeat_ngram_size=NO_REPEAT_NGRAM_SIZE,
                                ngram_window=SINGLE_NGRAM_WINDOW,
                                save_results=True,
                            )
                            images_config = {
                                "image_mode": attempt["label"],
                                "backend": "transformers",
                                "base_size": attempt["base_size"],
                                "image_size": attempt["image_size"],
                                "max_tokens": attempt["max_tokens"],
                            }
                            break
                        except Exception as err:
                            last_error = err
                            if attempt_index >= len(attempts) - 1 or not is_accelerator_oom(err):
                                raise
                            cleanup_torch_accelerator_cache()
                    else:
                        raise last_error or RuntimeError("Unlimited-OCR Transformers inference failed")
            raw_layout_text = extract_layout_text_from_transformers_stdout(capture.text())
            return raw_layout_text or extract_text_from_transformers_result(result, output_dir), images_config
    finally:
        cleanup_torch_accelerator_cache()


async def generate_transformers_markdown(image_pages: list[bytes], file_type: int) -> tuple[str, dict]:
    tokenizer, model = await get_transformers_components()
    async with TRANSFORMERS_INFERENCE_LOCK:
        return await asyncio.to_thread(run_transformers_inference_sync, tokenizer, model, image_pages, file_type)


async def stream_transformers_adapter_events(
    image_pages: list[bytes],
    file_type: int,
    page_texts: list[str] | None = None,
):
    page_count = len(image_pages)
    if TRANSFORMERS_MODEL is None:
        yield {
            "type": "status",
            "message": "Loading Unlimited-OCR Transformers backend...",
        }
    tokenizer, model = await get_transformers_components()
    yield {
        "type": "status",
        "message": (
            "Unlimited-OCR Transformers backend is ready on "
            f"{transformers_runtime_label()}. Parsing {page_count} page(s) at {PDF_DPI} DPI "
            f"with max {MAX_TOKENS} tokens."
        ),
    }

    async with TRANSFORMERS_INFERENCE_LOCK:
        output_queue: queue.Queue[str] = queue.Queue()
        stdout_writer = QueueTextWriter(output_queue)
        result_holder: dict[str, Any] = {}

        def run_inference():
            try:
                result_holder["result"] = run_transformers_inference_sync(
                    tokenizer,
                    model,
                    image_pages,
                    file_type,
                    stdout_writer,
                )
            except Exception as err:
                result_holder["error"] = err
            finally:
                output_queue.put("")

        thread = threading.Thread(target=run_inference, daemon=True)
        thread.start()

        last_markdown = ""
        last_emit_size = 0
        sent_images: dict[str, str] = {}
        stdout_parts: list[str] = []
        inference_started_at = time.monotonic()
        last_status_emit_at = inference_started_at
        while thread.is_alive() or not output_queue.empty():
            try:
                chunk = output_queue.get_nowait()
            except queue.Empty:
                now = time.monotonic()
                if (
                    STREAM_HEARTBEAT_SECONDS > 0
                    and not last_markdown
                    and now - last_status_emit_at >= STREAM_HEARTBEAT_SECONDS
                ):
                    elapsed = int(now - inference_started_at)
                    yield {
                        "type": "status",
                        "message": (
                            f"Still parsing with Unlimited-OCR on {transformers_runtime_label()} "
                            f"after {elapsed}s. Dense PDF pages can take several minutes on Apple Silicon."
                        ),
                    }
                    last_status_emit_at = now
                await asyncio.sleep(0.08)
                continue
            if chunk:
                stdout_parts.append(chunk)
            raw_text = extract_layout_text_from_transformers_stdout("".join(stdout_parts))
            if not raw_text:
                continue
            markdown, images = await asyncio.to_thread(
                render_streaming_markdown,
                raw_text,
                image_pages,
                page_texts,
            )
            if should_emit_stream_progress(markdown, last_markdown, last_emit_size):
                fresh_images = unsent_images(images, sent_images)
                last_markdown = markdown
                last_emit_size = len(markdown)
                event = {
                    "type": "progress",
                    "markdown": markdown,
                    "source": await asyncio.to_thread(
                        streaming_source_position,
                        raw_text,
                        len(image_pages),
                        page_texts,
                    ),
                }
                if fresh_images:
                    event["images"] = fresh_images
                yield event

        thread.join(timeout=1)
        if result_holder.get("error"):
            err = result_holder["error"]
            logger.error(
                "Unlimited-OCR Transformers inference failed",
                exc_info=(type(err), err, err.__traceback__),
            )
            yield {"type": "error", "detail": transformers_error_detail(err)}
            return

        result_text, images_config = result_holder.get("result", ("", {"backend": "transformers"}))
        raw_stdout = extract_layout_text_from_transformers_stdout(stdout_writer.text())
        raw_text = raw_stdout or result_text
        result = await asyncio.to_thread(
            build_adapter_response,
            raw_text,
            len(image_pages),
            file_type,
            images_config,
            image_pages,
            page_texts,
        )
        yield {
            "type": "final",
            "result": result,
        }


def get_no_repeat_processor_str() -> str | None:
    global NO_REPEAT_PROCESSOR_STR
    if not ENABLE_NO_REPEAT_PROCESSOR:
        return None
    if NO_REPEAT_PROCESSOR_STR is not None:
        return NO_REPEAT_PROCESSOR_STR
    try:
        from sglang.srt.sampling.custom_logit_processor import DeepseekOCRNoRepeatNGramLogitProcessor

        NO_REPEAT_PROCESSOR_STR = DeepseekOCRNoRepeatNGramLogitProcessor.to_str()
        return NO_REPEAT_PROCESSOR_STR
    except Exception as err:
        if DEFAULT_NO_REPEAT_PROCESSOR_STR:
            NO_REPEAT_PROCESSOR_STR = DEFAULT_NO_REPEAT_PROCESSOR_STR
            logger.info("Using bundled SGLang no-repeat processor string: %s", err)
            return NO_REPEAT_PROCESSOR_STR
        logger.warning("SGLang no-repeat processor is unavailable: %s", err)
        return None


def build_sglang_payload(image_pages: list[bytes], file_type: int) -> dict:
    is_multi_page = len(image_pages) > 1
    prompt = MULTI_PROMPT if is_multi_page else SINGLE_PROMPT
    # Dense PDF pages (especially algorithms/equations) are more stable in
    # Unlimited-OCR's ``base`` layout mode.  ``gundam`` remains the default
    # for standalone images, while every PDF page uses the same deterministic
    # mode even when the WebUI sends one page per request.
    image_mode = MULTI_IMAGE_MODE if is_multi_page or file_type == 0 else SINGLE_IMAGE_MODE
    ngram_window = MULTI_NGRAM_WINDOW if is_multi_page else SINGLE_NGRAM_WINDOW

    request_pages = (
        [resize_sglang_pdf_page(page) for page in image_pages]
        if file_type == 0
        else image_pages
    )
    payload = {
        "model": SERVED_MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}]
                + [encode_image_content(image_bytes) for image_bytes in request_pages],
            }
        ],
        "temperature": 0,
        "skip_special_tokens": False,
        "images_config": {"image_mode": image_mode, "backend": "sglang"},
        "stream": True,
    }
    if SGLANG_MAX_TOKENS > 0:
        payload["max_tokens"] = SGLANG_MAX_TOKENS

    processor = get_no_repeat_processor_str()
    if processor and NO_REPEAT_NGRAM_SIZE > 0 and ngram_window > 0:
        payload["custom_logit_processor"] = processor
        payload["custom_params"] = {
            "ngram_size": NO_REPEAT_NGRAM_SIZE,
            "window_size": ngram_window,
        }

    return payload


def adjust_sglang_payload_for_context_error(payload: dict, error_body: str) -> dict | None:
    match = SGLANG_CONTEXT_ERROR_RE.search(str(error_body))
    if not match:
        return None

    context_limit = int(match.group(1))
    input_tokens = int(match.group(2))
    requested_completion_tokens = int(match.group(3))
    current_max_tokens = int(payload.get("max_tokens") or requested_completion_tokens)
    adjusted_max_tokens = context_limit - input_tokens - max(0, SGLANG_CONTEXT_TOKEN_RESERVE)
    if adjusted_max_tokens <= 0 or adjusted_max_tokens >= current_max_tokens:
        return None

    adjusted_payload = dict(payload)
    adjusted_payload["max_tokens"] = adjusted_max_tokens
    adjusted_payload["images_config"] = dict(payload.get("images_config") or {})
    adjusted_payload["images_config"]["backend"] = "sglang"
    adjusted_payload["images_config"]["max_tokens_adjusted_from"] = current_max_tokens
    adjusted_payload["images_config"]["max_tokens_adjusted_to"] = adjusted_max_tokens
    logger.warning(
        "Adjusted SGLang max_tokens from %s to %s for context limit %s with %s input tokens",
        current_max_tokens,
        adjusted_max_tokens,
        context_limit,
        input_tokens,
    )
    return adjusted_payload


def adjust_sglang_payload_for_degeneration(payload: dict, reason: str) -> dict | None:
    """Retry one degenerate generation with a stricter no-repeat window."""
    images_config = dict(payload.get("images_config") or {})
    if images_config.get("degeneration_retry"):
        return None
    if not payload.get("custom_logit_processor"):
        return None

    custom_params = dict(payload.get("custom_params") or {})
    current_ngram_size = int(custom_params.get("ngram_size") or NO_REPEAT_NGRAM_SIZE)
    retry_ngram_size = max(2, min(current_ngram_size - 1, DEGENERATION_RETRY_NGRAM_SIZE))
    if retry_ngram_size >= current_ngram_size:
        return None

    adjusted_payload = dict(payload)
    custom_params["ngram_size"] = retry_ngram_size
    custom_params["window_size"] = max(
        int(custom_params.get("window_size") or 0),
        DEGENERATION_RETRY_WINDOW,
    )
    adjusted_payload["custom_params"] = custom_params
    images_config.update(
        {
            "degeneration_retry": True,
            "degeneration_reason": str(reason),
            "retry_no_repeat_ngram_size": retry_ngram_size,
            "retry_no_repeat_window": custom_params["window_size"],
        }
    )
    adjusted_payload["images_config"] = images_config
    logger.warning(
        "Retrying degenerate SGLang generation near %r with no-repeat ngram=%s window=%s",
        reason,
        retry_ngram_size,
        custom_params["window_size"],
    )
    return adjusted_payload


async def collect_streaming_response(response: httpx.Response) -> str:
    chunks: list[str] = []
    async for line in response.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
            delta = event["choices"][0].get("delta", {}).get("content", "")
        except (json.JSONDecodeError, KeyError, IndexError, AttributeError):
            continue
        if delta:
            chunks.append(delta)
            _raw_text, reason = await asyncio.to_thread(inspect_sglang_chunks, chunks)
            if reason:
                raise DegenerateGenerationError(reason)
    return "".join(chunks)


def inspect_sglang_chunks(chunks: list[str]) -> tuple[str, str | None]:
    raw_text = "".join(chunks)
    return raw_text, detect_degenerate_repetition(raw_text)


def parse_stream_delta(line: str) -> str:
    if not line or not line.startswith("data:"):
        return ""
    data = line[len("data:") :].strip()
    if data == "[DONE]":
        return ""
    try:
        event = json.loads(data)
        return event["choices"][0].get("delta", {}).get("content", "")
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError):
        return ""


async def generate_markdown(image_pages: list[bytes], file_type: int, backend: str | None = None) -> tuple[str, dict]:
    resolved_backend = normalize_backend(backend, DEFAULT_BACKEND)
    if resolved_backend == "transformers":
        return await generate_transformers_markdown(image_pages, file_type)

    payload = await asyncio.to_thread(build_sglang_payload, image_pages, file_type)
    timeout = REQUEST_TIMEOUT if REQUEST_TIMEOUT > 0 else None
    context_adjusted = False
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        # At most one context-size adjustment and one degeneration retry.
        # Keeping three total requests prevents an unbounded retry loop.
        for attempt in range(3):
            async with client.stream(
                "POST",
                f"{SGLANG_URL}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    adjusted_payload = None if context_adjusted else adjust_sglang_payload_for_context_error(payload, body)
                    if adjusted_payload and attempt < 2:
                        payload = adjusted_payload
                        context_adjusted = True
                        continue
                    raise HTTPException(status_code=response.status_code, detail=f"Unlimited-OCR upstream error: {body}")
                try:
                    text = await collect_streaming_response(response)
                except DegenerateGenerationError as err:
                    adjusted_payload = adjust_sglang_payload_for_degeneration(payload, str(err))
                    if adjusted_payload and attempt < 2:
                        payload = adjusted_payload
                        continue
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            "Unlimited-OCR generation remained repetitive after the guarded retry "
                            f"near '{err}'. Try the Transformers backend for this document."
                        ),
                    ) from err
                return text, payload.get("images_config", {})
    raise HTTPException(status_code=500, detail="Unlimited-OCR SGLang request failed before streaming.")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def compact_block_text(text: str) -> str:
    text = normalize_newlines(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_UNLIMITED_BBOX_ARTIFACT_RE = re.compile(
    r"\btext\s*\[(?=[^\]\n]*(?:-?\d+(?:\.\d+)?\s*,\s*){3}-?\d+(?:\.\d+)?\s*\])[^\]\n]*\]",
    re.IGNORECASE,
)
_UNLIMITED_NUMBERED_LINE_RE = re.compile(r"^\s*\d{1,3}\s*:\s*")


def detect_unlimited_structural_artifacts(text: str) -> str | None:
    """Detect leaked layout/decoder scaffolding in an unstructured stream.

    Unlimited-OCR normally emits ``<|det|>...`` blocks.  During a bad
    generation it can instead print its internal algorithm trace, including
    line numbers and ``text [x, y, w, h]`` coordinates.  That trace must never
    be presented as document text or allowed to grow the browser DOM forever.
    """
    normalized = normalize_newlines(str(text))
    # Once a real det block exists, numbered lines may be legitimate paper
    # content (algorithm pseudocode).  Only apply the fallback-trace detector
    # to the unstructured stream prefix where layout metadata has leaked.
    if "<|det|>" in normalized:
        return None
    bbox_count = len(_UNLIMITED_BBOX_ARTIFACT_RE.findall(normalized))
    numbered_lines = sum(
        1 for line in normalized.splitlines() if _UNLIMITED_NUMBERED_LINE_RE.match(line)
    )
    trace_words = len(re.findall(r"\b(?:end\s+while|end\s+if|output:)\b", normalized, re.IGNORECASE))
    if bbox_count >= 2 and (numbered_lines >= 3 or trace_words >= 2):
        return "layout coordinate trace"
    if numbered_lines >= 6 and trace_words >= 2:
        return "decoder trace"
    return None


def sanitize_unlimited_ocr_fallback(text: str) -> str:
    """Return safe fallback text when det blocks are temporarily unavailable.

    A suspicious trace is hidden rather than rendered.  The stream-level
    degeneration guard will request a bounded retry; this function is the
    final presentation barrier for partial events and persisted responses.
    """
    normalized = normalize_newlines(str(text))
    if detect_unlimited_structural_artifacts(normalized):
        return ""
    return compact_block_text(normalized)


def parse_bbox(raw_bbox: str | None) -> list[float] | None:
    if not raw_bbox:
        return None
    values = re.findall(r"-?\d+(?:\.\d+)?", raw_bbox)
    if len(values) < 4:
        return None
    return [float(value) for value in values[:4]]


def parse_layout_blocks(markdown: str) -> list[dict]:
    text = normalize_newlines(markdown)
    matches = list(DET_BLOCK_RE.finditer(text))
    blocks: list[dict] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append(
            {
                "label": match.group(1),
                "bbox": parse_bbox(match.group(2)),
                "text": text[match.end() : next_start],
            }
        )
    return blocks


def normalize_anchor_text(text: str) -> str:
    words = ANCHOR_WORD_RE.findall(str(text).lower())
    return " ".join(words)


def anchor_page_for_block(block: dict, page_texts: list[str] | None, current_page: int = 0) -> int | None:
    if not page_texts:
        return None
    current_page = max(0, min(int(current_page or 0), len(page_texts) - 1))
    text = normalize_anchor_text(block.get("text") or "")
    words = text.split()
    if len(words) < 4:
        return None

    candidates = []
    for window_size in (18, 12, 8, 5):
        if len(words) < window_size:
            continue
        candidates.append(" ".join(words[:window_size]))
    if not candidates:
        candidates.append(" ".join(words[: min(len(words), 6)]))

    search_order = list(range(current_page, len(page_texts))) + list(range(0, current_page))
    for anchor in candidates:
        if len(anchor) < 16:
            continue
        matches = [page_index for page_index in search_order if anchor in page_texts[page_index]]
        if len(matches) == 1:
            return matches[0]
        if matches:
            forward_matches = [page_index for page_index in matches if page_index >= current_page]
            return forward_matches[0] if forward_matches else matches[0]
    return None


def backfill_visual_blocks(blocks: list[dict]) -> None:
    for index, block in enumerate(blocks):
        label = str(block.get("label") or "").lower().strip()
        if label not in IMAGE_LABELS and label not in {"table", "algorithm"}:
            continue
        if block.get("page_confidence") == "text":
            continue
        for next_block in blocks[index + 1 : min(len(blocks), index + 4)]:
            next_label = str(next_block.get("label") or "").lower().strip()
            if next_label in CAPTION_LABELS and next_block.get("page_confidence") == "text":
                block["page_index"] = next_block.get("page_index", block.get("page_index", 0))
                block["page_confidence"] = "caption"
                break


def assign_block_pages(blocks: list[dict], page_count: int, page_texts: list[str] | None = None) -> None:
    page_index = 0
    last_y = None
    page_count = max(1, int(page_count or 1))
    for block in blocks:
        bbox = block.get("bbox")
        anchored_page = anchor_page_for_block(block, page_texts, page_index)
        if anchored_page is not None:
            page_index = max(0, min(anchored_page, page_count - 1))
            block["page_index"] = page_index
            block["page_confidence"] = "text"
            if bbox:
                last_y = bbox[1]
            continue

        if bbox:
            y1 = bbox[1]
            if last_y is not None and y1 + 80 < last_y and page_index < page_count - 1:
                page_index += 1
            last_y = y1
        block["page_index"] = page_index
        block["page_confidence"] = "heuristic" if page_count > 1 else "single-page"
    if page_texts:
        backfill_visual_blocks(blocks)


def clamp_float(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def has_repeat_position_run(positions: list[int], max_gap: int, min_count: int) -> bool:
    if len(positions) < min_count:
        return False
    run_length = 1
    for previous, current in zip(positions, positions[1:]):
        if current - previous <= max_gap:
            run_length += 1
            if run_length >= min_count:
                return True
        else:
            run_length = 1
    return False


def detect_degenerate_repetition(text: str) -> str | None:
    if not ENABLE_DEGENERATION_GUARD:
        return None

    tail = normalize_newlines(str(text))[-max(512, DEGENERATION_WINDOW_CHARS) :].lower()
    # A valid formula may contain an occasional ``\\left``/``\\right``;
    # dozens of adjacent commands are a decoder loop.  Check this before the
    # det/layout exceptions so malformed structured output cannot bypass the
    # guard (the UI otherwise shows a wall of repeated LaTeX commands).
    latex_run = _UNLIMITED_LATEX_COMMAND_RUN_RE.search(tail)
    if latex_run:
        return "repeated LaTeX command"
    # SGLang can occasionally enter a line-level loop that changes only a
    # replacement glyph or trailing coordinate. Word n-grams are too sparse
    # for that shape, so fail closed when the same long line is emitted again
    # and again in the recent tail.
    recent_lines = [re.sub(r"\s+", " ", line).strip() for line in tail.splitlines()]
    recent_lines = [line for line in recent_lines if len(line) >= 24]
    if recent_lines:
        line_counts: dict[str, int] = {}
        for line in recent_lines[-32:]:
            line_counts[line] = line_counts.get(line, 0) + 1
            if line_counts[line] >= 4:
                return line[:160]
    structural_artifact = detect_unlimited_structural_artifacts(tail)
    if structural_artifact:
        return structural_artifact
    words = WORD_RE.findall(tail)
    if len(words) < 48:
        return None

    for gram_size in (3, 4, 5, 6):
        counts: dict[str, int] = {}
        positions_by_gram: dict[str, list[int]] = {}
        dense_repeat_gap = gram_size + max(0, DEGENERATION_REPEAT_MAX_EXTRA_GAP)
        for index in range(0, len(words) - gram_size + 1):
            gram_words = words[index : index + gram_size]
            if sum(len(word) for word in gram_words) < 12:
                continue
            # Tables frequently repeat years, page numbers, or metric values
            # in adjacent cells.  A run made entirely of numeric tokens is
            # valid document structure, not a decoder loop.
            if all(re.fullmatch(r"\d+(?:[.,]\d+)?", word) for word in gram_words):
                continue
            gram = " ".join(gram_words)
            # Tables/algorithms legitimately repeat short layout tokens around
            # numeric cell values (for example ``td 100 td td 100``).  Treat a
            # gram containing multiple layout markers as structural markup,
            # not decoder degeneration; otherwise dense PDF tables are aborted
            # even though the generated document is valid.
            layout_word_count = sum(word in COMMON_LAYOUT_WORDS for word in gram_words)
            if (
                gram in COMMON_LAYOUT_GRAMS
                or all(word in COMMON_LAYOUT_WORDS for word in gram_words)
                or layout_word_count >= 2
            ):
                continue
            counts[gram] = counts.get(gram, 0) + 1
            positions = positions_by_gram.setdefault(gram, [])
            positions.append(index)
            exact_repeat = has_repeat_position_run(
                positions,
                gram_size,
                DEGENERATION_CONSECUTIVE_REPEAT_THRESHOLD,
            )
            dense_repeat = (
                counts[gram] >= DEGENERATION_REPEAT_THRESHOLD
                and has_repeat_position_run(positions, dense_repeat_gap, DEGENERATION_REPEAT_THRESHOLD)
            )
            if gram in COMMON_BIBLIOGRAPHY_GRAMS and not exact_repeat:
                continue
            if exact_repeat or dense_repeat:
                return gram
    return None


def streaming_source_position(markdown: str, page_count: int, page_texts: list[str] | None = None) -> dict:
    blocks = parse_layout_blocks(markdown)
    if not blocks:
        return {"pageIndex": 0, "pageProgress": 0}

    page_count = max(1, page_count)
    assign_block_pages(blocks, page_count, page_texts)
    positioned_blocks = [block for block in blocks if block.get("bbox")]
    if page_count > 1 and page_texts:
        confident_blocks = [
            block for block in positioned_blocks
            if block.get("page_confidence") in {"text", "caption", "single-page"}
        ]
        if confident_blocks:
            positioned_blocks = confident_blocks
    current_block = positioned_blocks[-1] if positioned_blocks else blocks[-1]
    page_index = int(current_block.get("page_index") or 0)
    page_index = max(0, min(page_index, page_count - 1))

    page_progress = 0.0
    bbox = current_block.get("bbox")
    if bbox:
        y_center = (float(bbox[1]) + float(bbox[3])) / 2
        page_progress = clamp_float(y_center / UNLIMITED_OCR_COORDINATE_SIZE)

    position = {
        "pageIndex": page_index,
        "pageNumber": page_index + 1,
        "pageProgress": page_progress,
    }
    if bbox:
        position.update(
            {
                "bbox": [float(value) for value in bbox[:4]],
                "pageWidth": UNLIMITED_OCR_COORDINATE_SIZE,
                "pageHeight": UNLIMITED_OCR_COORDINATE_SIZE,
                "label": str(current_block.get("label") or ""),
                "pageConfidence": str(current_block.get("page_confidence") or ""),
            }
        )
    return position


def scaled_crop_box(bbox: list[float], image: Image.Image) -> tuple[int, int, int, int] | None:
    width, height = image.size
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        return None

    if width > 1400 and max(x1, y1, x2, y2) <= 1150:
        scale_x = width / UNLIMITED_OCR_COORDINATE_SIZE
        scale_y = height / UNLIMITED_OCR_COORDINATE_SIZE
        x1, x2 = x1 * scale_x, x2 * scale_x
        y1, y2 = y1 * scale_y, y2 * scale_y
    elif x2 > width or y2 > height:
        scale = min(width / max(x2, 1), height / max(y2, 1))
        x1, x2 = x1 * scale, x2 * scale
        y1, y2 = y1 * scale, y2 * scale

    pad_x = max(6, int((x2 - x1) * 0.02))
    pad_y = max(6, int((y2 - y1) * 0.02))
    left = max(0, int(x1) - pad_x)
    top = max(0, int(y1) - pad_y)
    right = min(width, int(x2) + pad_x)
    bottom = min(height, int(y2) + pad_y)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def crop_block_image(block: dict, image_pages: list[bytes], image_number: int) -> tuple[str, str] | None:
    bbox = block.get("bbox")
    page_index = int(block.get("page_index") or 0)
    if not bbox or not image_pages or page_index < 0 or page_index >= len(image_pages):
        return None
    try:
        image = Image.open(io.BytesIO(image_pages[page_index])).convert("RGB")
        box = scaled_crop_box(bbox, image)
        if not box:
            return None
        cropped = image.crop(box)
        output = io.BytesIO()
        cropped.save(output, format="PNG")
        path = f"ocr_images/unlimited_p{page_index + 1}_{block.get('label', 'image')}_{image_number}.png"
        data = base64.b64encode(output.getvalue()).decode("utf-8")
        return path, data
    except Exception as err:
        logger.warning("Failed to crop Unlimited-OCR image block: %s", err)
        return None


def format_unlimited_ocr_block(label: str, content: str, *, seen_title: bool) -> tuple[str, bool]:
    normalized_label = label.lower().strip()
    text = compact_block_text(content)
    if not text or normalized_label in SKIP_MARKDOWN_LABELS:
        return "", seen_title

    if normalized_label in TITLE_LABELS:
        level = "##" if seen_title else "#"
        return f"{level} {text}", True

    if normalized_label in CAPTION_LABELS:
        return f"*{text}*", seen_title

    if normalized_label in {"formula", "display_formula"}:
        return f"$$\n{text}\n$$", seen_title

    if normalized_label in IMAGE_LABELS:
        return (f"**{normalized_label.replace('_', ' ').title()}:** {text}" if text else ""), seen_title

    return text, seen_title


def render_unlimited_ocr_document(
    markdown: str,
    image_pages: list[bytes] | None = None,
    page_texts: list[str] | None = None,
) -> tuple[str, dict]:
    text = normalize_newlines(markdown)
    matches = list(DET_BLOCK_RE.finditer(text))
    if not matches:
        return sanitize_unlimited_ocr_fallback(text), {}

    blocks: list[str] = []
    images: dict[str, str] = {}
    prefix = compact_block_text(text[: matches[0].start()])
    if prefix:
        blocks.append(prefix)

    layout_blocks = parse_layout_blocks(text)
    assign_block_pages(layout_blocks, len(image_pages or []) or 1, page_texts)
    seen_title = False
    image_number = 1
    for block_info in layout_blocks:
        label = str(block_info.get("label") or "")
        normalized_label = label.lower().strip()
        if image_pages and normalized_label in IMAGE_LABELS:
            cropped = crop_block_image(block_info, image_pages, image_number)
            if cropped:
                path, data = cropped
                images[path] = data
                blocks.append(f"![{normalized_label.replace('_', ' ')}]({path})")
                image_number += 1
        block, seen_title = format_unlimited_ocr_block(label, str(block_info.get("text") or ""), seen_title=seen_title)
        if block:
            blocks.append(block)

    rendered = "\n\n".join(blocks)
    rendered = re.sub(r"<\|/?det\|>", "", rendered)
    return compact_block_text(rendered), images


def build_layout_parsing_results(
    raw_text: str,
    rendered_text: str,
    images: dict,
    image_count: int,
    file_type: int,
    images_config: dict,
    page_texts: list[str] | None = None,
) -> list[dict]:
    page_count = max(1, int(image_count or 1))
    metadata = {
        "fileType": file_type,
        "imagesConfig": images_config,
    }
    if raw_text != rendered_text:
        metadata["rawMarkdown"] = raw_text

    pages = [
        {
            "model": SERVED_MODEL_NAME,
            "parser": "unlimited-ocr",
            "page_count": page_count,
            "page_index": page_index + 1,
            "width": UNLIMITED_OCR_COORDINATE_SIZE,
            "height": UNLIMITED_OCR_COORDINATE_SIZE,
            "markdown": {
                "text": rendered_text if page_count == 1 else "",
                "images": images if page_count == 1 else {},
            },
            "metadata": metadata,
            "parsing_res_list": [],
        }
        for page_index in range(page_count)
    ]

    layout_blocks = parse_layout_blocks(raw_text)
    if not layout_blocks:
        pages[0]["markdown"] = {"text": rendered_text, "images": images}
        return pages

    assign_block_pages(layout_blocks, page_count, page_texts)
    for block_order, block in enumerate(layout_blocks):
        page_index = max(0, min(int(block.get("page_index") or 0), page_count - 1))
        bbox = block.get("bbox")
        label = str(block.get("label") or "")
        content = compact_block_text(str(block.get("text") or ""))
        parsing_block = {
            "block_label": label,
            "block_order": block_order,
            "block_content": content,
        }
        if bbox:
            parsing_block["block_bbox"] = [float(value) for value in bbox[:4]]
        pages[page_index]["parsing_res_list"].append(parsing_block)

    return pages


def normalize_markdown(markdown: str) -> str:
    text = str(markdown)
    if "<|det|>" in text:
        rendered, _ = render_unlimited_ocr_document(text)
        return rendered
    return sanitize_unlimited_ocr_fallback(text)


def render_streaming_markdown(
    markdown: str,
    image_pages: list[bytes] | None = None,
    page_texts: list[str] | None = None,
) -> tuple[str, dict]:
    text = str(markdown)
    if "<|det|>" in text:
        return render_unlimited_ocr_document(text, image_pages, page_texts)
    return sanitize_unlimited_ocr_fallback(text), {}


def unsent_images(images: dict[str, str], sent_images: dict[str, str]) -> dict[str, str]:
    fresh = {path: data for path, data in images.items() if sent_images.get(path) != data}
    sent_images.update(fresh)
    return fresh


def build_adapter_response(
    markdown: str,
    image_count: int,
    file_type: int,
    images_config: dict,
    image_pages: list[bytes] | None = None,
    page_texts: list[str] | None = None,
) -> dict:
    raw_text = normalize_newlines(str(markdown))
    if "<|det|>" in raw_text:
        text, images = render_unlimited_ocr_document(raw_text, image_pages, page_texts)
    else:
        text = normalize_markdown(raw_text)
        images = {}
    layout_results = build_layout_parsing_results(raw_text, text, images, image_count, file_type, images_config, page_texts)
    return {
        "markdown": text,
        "images": images,
        "layoutParsingResults": layout_results,
    }


async def stream_sglang_payload_events(
    payload: dict,
    image_pages: list[bytes],
    file_type: int,
    page_texts: list[str] | None = None,
):
    timeout = REQUEST_TIMEOUT if REQUEST_TIMEOUT > 0 else None
    images_config = payload.get("images_config") or {}
    context_adjusted = "max_tokens_adjusted_from" in images_config

    # Retrying recursively from inside client.stream() keeps the previous
    # response (and its generation) alive while the next request starts.  Keep
    # all retries in one bounded state machine so every response and client has
    # exited before the next attempt can acquire GPU work.
    for attempt in range(3):
        raw_chunks: list[str] = []
        last_markdown = ""
        last_emit_size = 0
        last_render_size = 0
        last_render_at = time.monotonic()
        sent_images: dict[str, str] = {}
        retry_payload: dict | None = None
        terminal_error: dict | None = None
        response_completed = False

        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            async with client.stream(
                "POST",
                f"{SGLANG_URL}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    adjusted_payload = (
                        None
                        if context_adjusted
                        else adjust_sglang_payload_for_context_error(payload, body)
                    )
                    if adjusted_payload and attempt < 2:
                        retry_payload = adjusted_payload
                        context_adjusted = True
                    else:
                        terminal_error = {
                            "type": "error",
                            "detail": f"Unlimited-OCR upstream error: {body}",
                        }
                else:
                    async for line in response.aiter_lines():
                        delta = parse_stream_delta(line)
                        if not delta:
                            continue
                        raw_chunks.append(delta)
                        raw_text, repetition = await asyncio.to_thread(
                            inspect_sglang_chunks,
                            raw_chunks,
                        )
                        if repetition:
                            adjusted_payload = adjust_sglang_payload_for_degeneration(payload, repetition)
                            if adjusted_payload and attempt < 2:
                                retry_payload = adjusted_payload
                            else:
                                terminal_error = {
                                    "type": "error",
                                    "detail": (
                                        "Unlimited-OCR generation remained repetitive after the guarded retry "
                                        f"near '{repetition}'. Try a smaller PDF batch size, such as 5 pages, "
                                        "or use the Transformers backend for this document."
                                    ),
                                }
                            break
                        now = time.monotonic()
                        if not should_render_stream_snapshot(
                            raw_text,
                            last_render_size,
                            last_render_at,
                            now,
                        ):
                            continue
                        markdown, images = await asyncio.to_thread(
                            render_streaming_markdown,
                            raw_text,
                            image_pages,
                            page_texts,
                        )
                        last_render_size = len(raw_text)
                        last_render_at = time.monotonic()
                        if should_emit_stream_progress(markdown, last_markdown, last_emit_size):
                            fresh_images = unsent_images(images, sent_images)
                            last_markdown = markdown
                            last_emit_size = len(markdown)
                            event = {
                                "type": "progress",
                                "markdown": markdown,
                                "source": await asyncio.to_thread(
                                    streaming_source_position,
                                    raw_text,
                                    len(image_pages),
                                    page_texts,
                                ),
                            }
                            if fresh_images:
                                event["images"] = fresh_images
                            yield event
                    else:
                        response_completed = True

        # Both nested async contexts are closed before any retry is issued.
        if retry_payload is not None:
            payload = retry_payload
            continue
        if terminal_error is not None:
            yield terminal_error
            return
        if response_completed:
            raw_text = await asyncio.to_thread("".join, raw_chunks)
            result = await asyncio.to_thread(
                build_adapter_response,
                raw_text,
                len(image_pages),
                file_type,
                payload.get("images_config", {}),
                image_pages,
                page_texts,
            )
            yield {
                "type": "final",
                "result": result,
            }
            return

    yield {"type": "error", "detail": "Unlimited-OCR SGLang retry limit was exhausted."}


async def stream_adapter_events(
    image_pages: list[bytes],
    file_type: int,
    backend: str | None = None,
    page_texts: list[str] | None = None,
):
    resolved_backend = normalize_backend(backend, DEFAULT_BACKEND)
    if resolved_backend == "transformers":
        async for event in stream_transformers_adapter_events(image_pages, file_type, page_texts):
            yield event
        return

    payload = await asyncio.to_thread(build_sglang_payload, image_pages, file_type)
    try:
        async for event in stream_sglang_payload_events(
            payload,
            image_pages,
            file_type,
            page_texts,
        ):
            yield event
    except DegenerateGenerationError as err:
        yield {
            "type": "error",
            "detail": (
                "Unlimited-OCR generation became repetitive "
                f"near '{err}'. Try a smaller PDF batch size, such as 5 pages, "
                "or use the Transformers backend for this document."
            ),
        }
    except Exception as err:
        logger.exception("Unlimited-OCR streaming failed")
        yield {"type": "error", "detail": str(err)}


async def ndjson_event_stream(events):
    async for event in events:
        serialized = await asyncio.to_thread(json.dumps, event, ensure_ascii=False)
        yield serialized + "\n"


async def sglang_health_status() -> dict:
    if "sglang" not in SUPPORTED_BACKENDS:
        return {
            "ready": False,
            "disabled": True,
            "url": SGLANG_URL,
        }
    try:
        async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
            response = await client.get(f"{SGLANG_URL}/health")
        return {
            "ready": 200 <= response.status_code < 300,
            "statusCode": response.status_code,
            "url": SGLANG_URL,
        }
    except Exception as err:
        return {
            "ready": False,
            "error": str(err),
            "url": SGLANG_URL,
        }


@app.get("/health")
async def health():
    sglang = await sglang_health_status()
    return {
        "status": "ok",
        "backend": DEFAULT_BACKEND,
        "supportedBackends": sorted(SUPPORTED_BACKENDS),
        "model": MODEL_NAME,
        "runtime": TRANSFORMERS_RUNTIME,
        "modelLoaded": TRANSFORMERS_MODEL is not None,
        "modelLoading": TRANSFORMERS_MODEL_LOADING,
        "modelLoadedAt": TRANSFORMERS_MODEL_LOADED_AT,
        "modelError": TRANSFORMERS_MODEL_ERROR,
        "preloadEnabled": PRELOAD_TRANSFORMERS,
        "transformers": {
            "model": MODEL_NAME,
            "runtime": TRANSFORMERS_RUNTIME,
            "modelLoaded": TRANSFORMERS_MODEL is not None,
            "modelLoading": TRANSFORMERS_MODEL_LOADING,
            "modelLoadedAt": TRANSFORMERS_MODEL_LOADED_AT,
            "modelError": TRANSFORMERS_MODEL_ERROR,
            "preloadEnabled": PRELOAD_TRANSFORMERS,
        },
        "sglang": sglang,
    }


@app.post("/backend/transformers/preload")
async def preload_transformers_backend():
    await preload_transformers_components(force=True)
    return {
        "status": "ok",
        "backend": "transformers",
        "runtime": TRANSFORMERS_RUNTIME,
        "modelLoaded": TRANSFORMERS_MODEL is not None,
        "modelLoading": TRANSFORMERS_MODEL_LOADING,
        "modelLoadedAt": TRANSFORMERS_MODEL_LOADED_AT,
        "modelError": TRANSFORMERS_MODEL_ERROR,
    }


@app.post("/backend/transformers/unload")
async def unload_transformers_backend():
    return await unload_transformers_components()


@app.post("/ocr")
async def ocr(request: Request):
    file_bytes, file_type, backend = await read_input(request)
    image_pages, page_texts = await asyncio.to_thread(
        prepare_image_pages_and_texts,
        file_bytes,
        file_type,
        SGLANG_PDF_DPI if backend == "sglang" else None,
    )

    if not image_pages:
        raise HTTPException(status_code=400, detail="No images were produced for OCR.")

    markdown, images_config = await generate_markdown(image_pages, file_type, backend)
    return await asyncio.to_thread(
        build_adapter_response,
        markdown,
        len(image_pages),
        file_type,
        images_config,
        image_pages,
        page_texts,
    )


@app.post("/ocr/stream")
async def ocr_stream(request: Request):
    file_bytes, file_type, backend = await read_input(request)
    image_pages, page_texts = await asyncio.to_thread(
        prepare_image_pages_and_texts,
        file_bytes,
        file_type,
        SGLANG_PDF_DPI if backend == "sglang" else None,
    )

    if not image_pages:
        raise HTTPException(status_code=400, detail="No images were produced for OCR.")

    return StreamingResponse(
        ndjson_event_stream(stream_adapter_events(image_pages, file_type, backend, page_texts)),
        media_type="application/x-ndjson",
    )


@app.post("/ocr/multipart")
async def ocr_multipart(
    file: UploadFile = File(...),
    fileType: int | None = Form(None),
    backend: str | None = Form(None),
):
    file_bytes = await file.read()
    resolved_type = infer_file_type(file_bytes, fileType)
    resolved_backend = normalize_backend(backend, DEFAULT_BACKEND)
    image_pages, page_texts = await asyncio.to_thread(
        prepare_image_pages_and_texts,
        file_bytes,
        resolved_type,
        SGLANG_PDF_DPI if resolved_backend == "sglang" else None,
    )

    markdown, images_config = await generate_markdown(image_pages, resolved_type, resolved_backend)
    return await asyncio.to_thread(
        build_adapter_response,
        markdown,
        len(image_pages),
        resolved_type,
        images_config,
        image_pages,
        page_texts,
    )
