import asyncio
import base64
import io
import json
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
from ovisocr3_config import resolve_model_settings, ovis_env
from ovisocr3_tasks import (CATEGORIES, TASKS, DOCUMENT_TASKS, JSON_TASKS, PairRequest,
                           task_prompt, validate_options, decode_result, caption_markdown,
                           document_content, page_hash)
from ovisocr3_inference import infer as dynamic_infer


logging.basicConfig(level=ovis_env("LOG_LEVEL", "INFO"))
logger = logging.getLogger("ovisocr2-adapter")

MODEL_NAME, MODEL_REVISION = resolve_model_settings()
OUTPUT_FORMAT = ovis_env('OUTPUT_FORMAT', 'structured').strip().lower()
if OUTPUT_FORMAT not in {'structured', 'markdown'}:
    raise ValueError('OvisOCR3 output format must be structured or markdown')
BACKEND = ovis_env("BACKEND", "vllm").strip().lower()
TRANSFORMERS_DEVICE = ovis_env("TRANSFORMERS_DEVICE", "auto").strip().lower()
TRANSFORMERS_DTYPE = ovis_env("TRANSFORMERS_DTYPE", "auto").strip().lower()
ATTENTION_IMPLEMENTATION = ovis_env("ATTENTION_IMPLEMENTATION", "auto").strip()
RESTART_CHECK_INTERVAL = max(32, int(ovis_env("RESTART_CHECK_INTERVAL", "128")))
KV_CACHE_MEMORY_MB = int(ovis_env("KV_CACHE_MEMORY_MB", "512"))
STARTUP_MEMORY_FRACTION = float(ovis_env("STARTUP_MEMORY_FRACTION", "0.50"))
MAX_MODEL_LEN = int(ovis_env("MAX_MODEL_LEN", "32768"))
MAX_NUM_SEQS = int(ovis_env("MAX_NUM_SEQS", "1"))
MAX_TOKENS = int(ovis_env("MAX_TOKENS", "16384"))
PDF_DPI = int(ovis_env("PDF_DPI", "200"))
MAX_PAGES = int(ovis_env("MAX_PAGES_PER_REQUEST", "50"))
MAX_DOCUMENT_PAGES = int(ovis_env('MAX_DOCUMENT_PAGES', '400'))
MIN_PIXELS = int(ovis_env("MIN_PIXELS", str(448 * 448)))
MAX_PIXELS = int(ovis_env("MAX_PIXELS", str(2880 * 2880)))
GDN_PREFILL_BACKEND = ovis_env("GDN_PREFILL_BACKEND", "triton")

PROMPT = (Path(__file__).with_name('ovisocr3_prompts') / ('document_' + OUTPUT_FORMAT + '.txt')).read_text(encoding='utf8').strip()
BBOX_IMAGE_RE = re.compile(
    r'<img\s+src=["\']images/bbox_(\d+)_(\d+)_(\d+)_(\d+)\.jpg["\']\s*/?>',
    re.IGNORECASE,
)

PARSER = None
PARSER_ERROR: str | None = None
PARSER_LOCK = asyncio.Lock()
INFERENCE_LOCK = asyncio.Lock()


class VllmOvisOCR3Parser:
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
            limit_mm_per_prompt={"image": 2},
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
        if len(outputs) != len(images) or any(getattr(o.outputs[0], 'finish_reason', None) == 'length' for o in outputs):
            raise HTTPException(status_code=502, detail='OvisOCR3 returned missing or truncated pages')
        if OUTPUT_FORMAT == 'structured':
            return [o.outputs[0].text.strip() for o in outputs]
        return [
            self.clean_truncated_repeats(
                self.clean_restarted_document(output.outputs[0].text.strip())
            )
            for output in outputs
        ]


class TransformersOvisOCR3Parser:
    """PyTorch backend for Apple Silicon and other machines without vLLM/CUDA."""

    clean_truncated_repeats = staticmethod(VllmOvisOCR3Parser.clean_truncated_repeats)
    clean_restarted_document = staticmethod(VllmOvisOCR3Parser.clean_restarted_document)

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
            cuda_bf16 = (device == 'cuda'
                         and getattr(torch.cuda, 'get_device_capability', lambda: (8, 0))()[0] >= 8
                         and getattr(torch.cuda, 'is_bf16_supported', lambda: True)())
            dtype = torch.bfloat16 if cuda_bf16 else (torch.float16 if device in {'cuda', 'mps'} else torch.float32)
        else:
            dtype = getattr(torch, TRANSFORMERS_DTYPE)

        logger.info(
            "Loading OvisOCR3 with Transformers on %s using %s",
            device,
            str(dtype).removeprefix("torch."),
        )
        self.device = torch.device(device)
        self.processor = AutoProcessor.from_pretrained(model_name_or_path, revision=MODEL_REVISION)
        self.model = AutoModelForMultimodalLM.from_pretrained(
            model_name_or_path,
            revision=MODEL_REVISION,
            dtype=dtype,
            attn_implementation=('sdpa' if device == 'cuda' else 'eager') if ATTENTION_IMPLEMENTATION == 'auto' else ATTENTION_IMPLEMENTATION,
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
                    if OUTPUT_FORMAT != 'markdown':
                        return False
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
                    no_repeat_ngram_size=64 if OUTPUT_FORMAT == 'markdown' else 0,
                    stopping_criteria=StoppingCriteriaList([DocumentRestartStoppingCriteria()]),
                    use_cache=True,
                )
            text = self.processor.decode(
                output_ids[0][input_length:],
                skip_special_tokens=True,
            ).strip()
            text = re.sub(r"\n*<think>\s*</think>\n*", "\n\n", text).strip()
            if output_ids.shape[-1] - input_length >= MAX_TOKENS:
                raise HTTPException(status_code=502, detail='OvisOCR3 generation reached the output limit')
            if OUTPUT_FORMAT == 'markdown':
                text = self.clean_truncated_repeats(self.clean_restarted_document(text))
            markdowns.append(text)
            logger.info(
                "Finished Transformers OCR for image %s/%s in %.1fs (%s generated tokens)",
                image_index + 1,
                len(images),
                time.monotonic() - started_at,
                output_ids.shape[-1] - input_length,
            )
        return markdowns


class MlxOvisOCR3Parser:
    """Native Apple Silicon backend powered by MLX-VLM."""

    clean_truncated_repeats = staticmethod(VllmOvisOCR3Parser.clean_truncated_repeats)
    clean_restarted_document = staticmethod(VllmOvisOCR3Parser.clean_restarted_document)

    def __init__(self, model_name_or_path: str):
        from huggingface_hub import snapshot_download
        from mlx_vlm import load
        from mlx_vlm.prompt_utils import apply_chat_template

        logger.info("Loading OvisOCR3 with MLX-VLM")
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
            if result.finish_reason == 'length':
                raise HTTPException(status_code=502, detail='OvisOCR3 generation reached the output limit')
            if OUTPUT_FORMAT == 'markdown':
                text = self.clean_truncated_repeats(self.clean_restarted_document(text))
            markdowns.append(text)
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
        return VllmOvisOCR3Parser(MODEL_NAME)
    if BACKEND == "transformers":
        return TransformersOvisOCR3Parser(MODEL_NAME)
    if BACKEND == "mlx":
        return MlxOvisOCR3Parser(MODEL_NAME)
    raise ValueError(f"Unsupported OvisOCR3 backend: {BACKEND}")


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
            logger.exception("Failed to load OvisOCR3")
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
        path = f"ocr_images/ovisocr3_p{page_index + 1}_image_{len(images) + 1}.jpg"
        output = io.BytesIO()
        page.crop((x1, y1, x2, y2)).save(output, format="JPEG", quality=92)
        images[path] = base64.b64encode(output.getvalue()).decode("ascii")
        blocks.append({"block_label": "image", "block_bbox": [left, top, right, bottom], "block_content": f"![image]({path})"})
        return f"![image]({path})"

    return BBOX_IMAGE_RE.sub(replace, markdown), images, blocks



def structured_page(text: str) -> tuple[str, list[dict]]:
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=502, detail='OvisOCR3 returned invalid structured JSON') from error
    if not isinstance(value, list):
        raise HTTPException(status_code=502, detail='OvisOCR3 structured output must be an array')
    blocks, pieces = [], []
    for node in value:
        if not isinstance(node, dict):
            raise HTTPException(status_code=502, detail='Invalid OvisOCR3 document element')
        box, label, content = node.get('bbox'), node.get('category'), node.get('content')
        if (not isinstance(box, list) or len(box) != 4
            or any(type(x) is not int or not 0 <= x < 1000 for x in box)
            or box[0] >= box[2] or box[1] >= box[3]
            or not isinstance(label, str) or label not in CATEGORIES or not isinstance(content, str)):
            raise HTTPException(status_code=502, detail='Invalid OvisOCR3 category, content or bounding box')
        content = document_content(label, content)
        blocks.append({'block_label':label,'block_bbox':box,'block_content':content})
        pieces.append(content)
    return '\n\n'.join(pieces), blocks


def build_response(
    markdowns: list[str], pages: list[Image.Image], file_type: int, *, page_offset: int = 0,
    output_format: str | None = None
) -> dict[str, Any]:
    output_format = output_format or OUTPUT_FORMAT
    all_images: dict[str, str] = {}
    if len(markdowns) != len(pages):
        raise HTTPException(status_code=502, detail='OvisOCR3 page result count does not match input')
    results = []
    rendered_pages = []
    for index, (markdown, page) in enumerate(zip(markdowns, pages)):
        page_index = page_offset + index
        structured_blocks = None
        if output_format == 'structured':
            markdown, structured_blocks = structured_page(markdown)
        rendered, images, blocks = crop_visual_regions(markdown, page, page_index)
        if structured_blocks is not None:
            crop_markdown = {tuple(b['block_bbox']): b['block_content'] for b in blocks}
            for block in structured_blocks:
                block['block_content'] = BBOX_IMAGE_RE.sub(
                    lambda m: crop_markdown.get(tuple(int(v) for v in m.groups()), ''),
                    block['block_content'],
                )
            blocks = structured_blocks
        rendered_pages.append(rendered)
        all_images.update(images)
        results.append(
            {
                "parser": "ovisocr3",
                "metadata": {"model": MODEL_NAME, "modelRevision": MODEL_REVISION, "backend": BACKEND, "outputFormat": output_format},
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


app = FastAPI(title="OvisOCR3 Adapter", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    if PARSER is None:
        raise HTTPException(status_code=503, detail=PARSER_ERROR or "OvisOCR3 is loading")
    return {"status": "ok", "model": MODEL_NAME, "backend": BACKEND, "modelLoaded": True}


@app.get('/capabilities')
async def capabilities():
    return {'model': MODEL_NAME, 'tasks': list(TASKS.values()), 'maxPages': MAX_PAGES,
            'maxDocumentPages': MAX_DOCUMENT_PAGES, 'crossPageTables': True, 'backends': ['vllm', 'transformers', 'mlx']}


def infer_task(parser, images, prompt, *, max_tokens=None):
    try:
        return dynamic_infer(parser, BACKEND, images, prompt, max_tokens=MAX_TOKENS if max_tokens is None else min(MAX_TOKENS, max_tokens),
                             min_pixels=MIN_PIXELS, max_pixels=MAX_PIXELS)
    finally:
        if BACKEND == 'transformers' and ovis_env('RELEASE_CUDA_CACHE', '0').lower() in {'1', 'true'}:
            import torch
            if torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                except RuntimeError:
                    logger.warning('Could not release inactive CUDA cache after inference', exc_info=True)


def build_task_response(task, options, raws, pages, file_type):
    outputs = [decode_result(task, raw, options) for raw in raws]
    if task in {'document_structured', 'document_markdown', 'figure_captioning'}:
        projected = raws
        if task == 'figure_captioning':
            projected = []
            for value in outputs:
                blocks = []
                for block in value:
                    content = caption_markdown(block['content'])
                    if block['category'] in {'image', 'chart', 'header_image', 'footer_image'}:
                        box = '_'.join(str(n) for n in block['bbox'])
                        content = f'<img src="images/bbox_{box}.jpg" />\n\n' + content
                    blocks.append(dict(block, content=content))
                projected.append(json.dumps(blocks, ensure_ascii=False))
        response = build_response(projected, pages, file_type,
                                  output_format='markdown' if task == 'document_markdown' else 'structured')
    else:
        results, texts = [], []
        for index, value in enumerate(outputs):
            blocks = []
            if task in {'document_layout', 'word_spotting', 'reference_grounding'}:
                blocks = [{'block_bbox': item['bbox'], 'block_label': item.get('category', 'text'),
                           'block_content': item.get('text', item.get('reference', ''))} for item in value]
            elif task == 'position_aware_vqa':
                box = [int(n) for n in re.findall(r'\d+', value['bbox'])]
                blocks = [{'block_bbox': box, 'block_label': 'text', 'block_content': value['answer']}]
            text = '```json\n' + json.dumps(value, ensure_ascii=False, indent=2) + '\n```' if task in JSON_TASKS else str(value)
            texts.append(text)
            results.append({'parser': 'ovisocr3', 'pageIndex': index, 'width': 1000, 'height': 1000,
                            'parsing_res_list': blocks, 'markdown': {'text': text, 'images': {}}})
        response = {'markdown': '\n\n---\n\n'.join(texts), 'images': {}, 'layoutParsingResults': results,
                    'model': MODEL_NAME, 'fileType': file_type}
    for index, result in enumerate(response['layoutParsingResults']):
        result.update(task=task, taskResult=outputs[index], rawOutput=raws[index], sourceHash=page_hash(pages[index]),
                      searchableText=task in {'document_structured', 'document_markdown', 'word_spotting'})
    response.update(task=task, taskResults=outputs, rawOutputs=raws)
    return response


async def run_task_request(request, file_bytes, file_type):
    content_type = request.headers.get('content-type', '')
    payload = dict(await request.form()) if 'multipart/form-data' in content_type else await request.json()
    task = payload.get('task') or ('document_' + OUTPUT_FORMAT)
    options = payload.get('taskOptions', {})
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except ValueError as exc:
            raise HTTPException(422, 'taskOptions must be JSON') from exc
    options = validate_options(task, options)
    enhance_only = str(payload.get('enhanceOnly', 'false')).lower() in {'1', 'true'}
    cross_page = task == 'crosspage_table' or str(payload.get('crossPageTables', 'true' if task == 'document_auto' else 'false')).lower() in {'1', 'true'}
    stage1_task = 'figure_captioning' if task == 'document_auto' else ('document_structured' if task == 'crosspage_table' else task)
    if (cross_page or enhance_only) and stage1_task not in DOCUMENT_TASKS:
        raise HTTPException(422, 'Cross-page tables require a document parsing task')
    if enhance_only:
        from ovisocr3_crosspage import PDFPages, enhance_document
        cached = payload.get('pageResults', [])
        if isinstance(cached, str):
            try:
                cached = json.loads(cached)
            except ValueError as exc:
                raise HTTPException(422, 'pageResults must be JSON') from exc
        if not file_bytes.startswith(b'%PDF-'):
            raise HTTPException(422, 'Document enhancement requires the complete original PDF')
        pages = PDFPages(file_bytes, cached, stage1_task, dpi=PDF_DPI, max_pages=MAX_DOCUMENT_PAGES)
        parser = await get_parser()
        async with INFERENCE_LOCK:
            enhanced = await asyncio.to_thread(enhance_document, pages, [record['rawOutput'] for record in cached],
                                              stage1_task, lambda images, prompt: infer_task(parser, images, prompt))
        markdown = enhanced.pop('markdown')
        if task == 'document_auto':
            from ovisocr3_document import append_document_supplements
            markdown = append_document_supplements(markdown, cached)
        return {'task': task, 'model': MODEL_NAME, 'fileType': 0, 'layoutParsingResults': [],
                'markdown': markdown, 'images': enhanced.pop('images'), 'enhancement': enhanced}
    pages, resolved_type = prepare_images(file_bytes, file_type)
    if not pages:
        raise HTTPException(400, 'No source pages')
    try:
        prompt = task_prompt(stage1_task, options)
        parser = await get_parser()
        raws = []
        document_extras = []
        for page in pages:
            async with INFERENCE_LOCK:
                if task == 'document_auto':
                    from ovisocr3_document import parse_document_page
                    raw, extras, source_blocks = await asyncio.to_thread(
                        parse_document_page, page, lambda images, prompt: infer_task(parser, images, prompt),
                        refine=lambda images, prompt: infer_task(parser, images, prompt,
                            max_tokens=max(256, int(ovis_env('DOCUMENT_TOOL_MAX_TOKENS', '2048')))))
                    raws.append(raw)
                    document_extras.append((extras, source_blocks))
                else:
                    raws.append(await asyncio.to_thread(infer_task, parser, [page], prompt))
        response = build_task_response(stage1_task, options, raws, pages, resolved_type)
        if task == 'document_auto':
            for result, (extras, source_blocks) in zip(response['layoutParsingResults'], document_extras):
                from ovisocr3_document import document_supplement
                result.update(documentExtras=extras, sourceTextBlocks=source_blocks, searchableText=True)
                supplement = document_supplement(extras)
                if supplement:
                    result['markdown']['text'] += '\n\n' + supplement
        if cross_page and len(pages) > 1:
            from ovisocr3_crosspage import enhance_document
            parser = await get_parser()
            async with INFERENCE_LOCK:
                enhanced = await asyncio.to_thread(enhance_document, pages, raws, stage1_task,
                                                  lambda images, prompt: infer_task(parser, images, prompt))
            response['originalMarkdown'] = response['markdown']
            response['markdown'] = enhanced.pop('markdown')
            response['images'].update(enhanced.pop('images'))
            response['enhancement'] = enhanced
        if task == 'document_auto':
            from ovisocr3_document import append_document_supplements
            response['markdown'] = append_document_supplements(response['markdown'], response['layoutParsingResults'])
            if 'originalMarkdown' in response:
                response['originalMarkdown'] = append_document_supplements(response['originalMarkdown'], response['layoutParsingResults'])
        response['task'] = task
        return response
    finally:
        for page in pages:
            page.close()


@app.post('/crosspage')
async def crosspage_pair(payload: PairRequest):
    from ovisocr3_reference import page_markdown
    if sum(map(len, payload.images)) > 70 * 1024 * 1024 or any(not md.strip() or len(md) > 400000 for md in payload.markdowns):
        raise HTTPException(422, 'Pair input exceeds limits or has empty Markdown')
    images = []
    try:
        for encoded in payload.images:
            page, _ = prepare_images(decode_file_payload(encoded), 1)
            images.extend(page)
        context = '\n\n'.join(f'<INPUT_PAGE index="{n}">\n{page_markdown(md, n)}\n</INPUT_PAGE>'
                              for n, md in enumerate(payload.markdowns, 1))
        prompt = task_prompt('crosspage_table') + '\n\nInput Markdown:\n\n' + context
        parser = await get_parser()
        async with INFERENCE_LOCK:
            raw = await asyncio.to_thread(infer_task, parser, images, prompt)
        return {'task':'crosspage_table', 'taskResult':decode_result('crosspage_table', raw), 'rawOutput':raw}
    finally:
        for image in images:
            image.close()


@app.post("/ocr")
async def ocr(request: Request):
    file_bytes, file_type = await read_input(request)
    payload = dict(await request.form()) if 'multipart/form-data' in request.headers.get('content-type', '') else await request.json()
    if any(key in payload for key in ('task', 'enhanceOnly', 'crossPageTables')):
        return await run_task_request(request, file_bytes, file_type)
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
