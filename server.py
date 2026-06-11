import os
import base64
import httpx
import subprocess
import tempfile
import shutil
import io
import json
import re
import logging
from pathlib import Path
from PIL import Image
from typing import List, Optional, Union
from fastapi import FastAPI, HTTPException, File, UploadFile, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI()

logger = logging.getLogger("pandocr")
logging.basicConfig(level=os.getenv("PANDOCR_LOG_LEVEL", "INFO"))


def parse_csv_env(name: str, default: str) -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


PADDLE_SERVICE_URL = os.getenv("PADDLE_SERVICE_URL", "http://localhost:8081/layout-parsing")
PADDLEOCR_VL_MODEL_NAME = os.getenv("PADDLEOCR_VL_MODEL_NAME", "PaddleOCR-VL-1.6-0.9B")
PADDLE_REQUEST_TIMEOUT = float(os.getenv("PADDLE_REQUEST_TIMEOUT", "3600"))
TASK_DATA_DIR = Path(os.getenv("PANDOCR_TASK_DATA_DIR", "data/tasks")).resolve()
MAX_REQUEST_BYTES = int(float(os.getenv("PANDOCR_MAX_UPLOAD_MB", "512")) * 1024 * 1024)
PANDOCR_HOST = os.getenv("PANDOCR_HOST", "0.0.0.0")
PANDOCR_PORT = int(os.getenv("PANDOCR_PORT", "8000"))
CORS_ORIGINS = parse_csv_env(
    "PANDOCR_CORS_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials="*" not in CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.middleware("http")
async def reject_oversized_requests(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH"} and MAX_REQUEST_BYTES > 0:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    max_mb = MAX_REQUEST_BYTES / 1024 / 1024
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request body is too large. Max upload size is {max_mb:.0f} MB."},
                    )
            except ValueError:
                pass
    return await call_next(request)


@app.get("/")
async def read_root():
    return FileResponse("static/index.html")


@app.get("/api/models")
async def get_models():
    """Return the active OCR model for frontend display."""
    return {"data": [{"id": PADDLEOCR_VL_MODEL_NAME}]}


def safe_task_id(task_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", task_id or ""):
        raise HTTPException(status_code=400, detail="Invalid task id")
    return task_id


def task_file_path(task_id: str) -> Path:
    return TASK_DATA_DIR / safe_task_id(task_id) / "task.json"


def task_summary(task: dict) -> dict:
    batches = task.get("batches") if isinstance(task.get("batches"), list) else []
    completed_pages = sum(
        int(batch.get("pageCount") or 0)
        for batch in batches
        if isinstance(batch, dict) and batch.get("status") == "completed"
    )
    return {
        "id": task.get("id"),
        "name": task.get("name"),
        "originalName": task.get("originalName"),
        "sourceKind": task.get("sourceKind"),
        "mimeType": task.get("mimeType"),
        "size": task.get("size"),
        "createdAt": task.get("createdAt"),
        "updatedAt": task.get("updatedAt"),
        "status": task.get("status"),
        "pageCount": task.get("pageCount"),
        "pdfBatchSize": task.get("pdfBatchSize"),
        "error": task.get("error"),
        "completedPages": completed_pages,
        "batchCount": len(batches),
        "hasMarkdown": bool(task.get("markdown")),
        "hasOcrResults": bool(task.get("ocrResults")),
        "detailLoaded": False,
    }


def read_task_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        task = json.load(f)
    if not isinstance(task, dict):
        raise ValueError("Task file must contain a JSON object")
    return task


def write_task_file(path: Path, task: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(task, f, ensure_ascii=False)
    temp_path.replace(path)


def list_task_summaries() -> list[dict]:
    TASK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    tasks = []
    for path in TASK_DATA_DIR.glob("*/task.json"):
        try:
            tasks.append(task_summary(read_task_file(path)))
        except (OSError, ValueError, json.JSONDecodeError) as err:
            logger.warning("Skipping invalid task file %s: %s", path, err)
    tasks.sort(key=lambda item: item.get("updatedAt") or 0, reverse=True)
    return tasks


def remove_path(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


@app.get("/api/tasks")
async def list_tasks():
    """List locally persisted document parsing task summaries."""
    tasks = await run_in_threadpool(list_task_summaries)
    return {"tasks": tasks}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """Return one full locally persisted task."""
    path = task_file_path(task_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        task = await run_in_threadpool(read_task_file, path)
    except (OSError, ValueError, json.JSONDecodeError) as err:
        logger.warning("Failed to read task file %s: %s", path, err)
        raise HTTPException(status_code=500, detail="Failed to read task")
    task["detailLoaded"] = True
    return task


@app.put("/api/tasks/{task_id}")
async def save_task(task_id: str, request: Request):
    """Persist one task to the local project data directory."""
    task = await request.json()
    if not isinstance(task, dict):
        raise HTTPException(status_code=400, detail="Task payload must be a JSON object")
    if task.get("id") != task_id:
        raise HTTPException(status_code=400, detail="Task id mismatch")

    path = task_file_path(task_id)
    await run_in_threadpool(write_task_file, path, task)
    return {"ok": True, "task": task_summary(task)}


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete one locally persisted task."""
    path = task_file_path(task_id)
    await run_in_threadpool(remove_path, path.parent)
    return {"ok": True}


@app.delete("/api/tasks")
async def clear_tasks():
    """Delete all locally persisted tasks."""
    await run_in_threadpool(remove_path, TASK_DATA_DIR)
    await run_in_threadpool(TASK_DATA_DIR.mkdir, parents=True, exist_ok=True)
    return {"ok": True}


@app.post("/api/convert/to-pdf")
async def convert_to_pdf(file: UploadFile = File(...)):
    """Convert PPT/PPTX/DOC/DOCX to PDF using LibreOffice."""
    logger.info("Received conversion request for: %s", file.filename)

    if not shutil.which("soffice"):
        raise HTTPException(
            status_code=500,
            detail="LibreOffice (soffice) not found on server. Please install it to support Office conversion.",
        )

    filename = Path(file.filename or "upload").name
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".ppt", ".pptx", ".doc", ".docx"]:
        raise HTTPException(status_code=400, detail="Only .ppt, .pptx, .doc, and .docx files are supported.")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, filename)

            with open(input_path, "wb") as buffer:
                await run_in_threadpool(shutil.copyfileobj, file.file, buffer)

            cmd = [
                "soffice",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                temp_dir,
                input_path,
            ]

            logger.info("Running conversion command: %s", " ".join(cmd))
            result = await run_in_threadpool(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
            )

            if result.returncode != 0:
                logger.warning("Conversion failed: %s", result.stderr)
                raise HTTPException(status_code=500, detail=f"Conversion failed: {result.stderr}")

            pdfs = [f for f in os.listdir(temp_dir) if f.lower().endswith(".pdf")]
            if not pdfs:
                raise HTTPException(status_code=500, detail="PDF file not generated")

            pdf_path = os.path.join(temp_dir, pdfs[0])
            logger.info("Conversion successful, sending back: %s", pdf_path)

            with open(pdf_path, "rb") as f:
                pdf_content = await run_in_threadpool(f.read)

            return Response(content=pdf_content, media_type="application/pdf")

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="File conversion timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error during conversion")
        raise HTTPException(status_code=500, detail=str(e))


class OCRRequest(BaseModel):
    image: Optional[str] = None
    fileType: Optional[int] = None
    useLayoutDetection: bool = True
    useDocUnwarping: bool = False
    useDocOrientationClassify: bool = False
    useChartRecognition: bool = False
    useSealRecognition: bool = True
    formatBlockContent: bool = True
    showFormulaNumber: bool = True
    markdownIgnoreLabels: List[str] = Field(default_factory=list)
    layoutThreshold: Optional[float] = None
    layoutNms: Optional[bool] = None
    layoutUnclipRatio: Optional[float] = None
    layoutMergeBboxesMode: Optional[str] = None
    repetitionPenalty: Optional[float] = None
    temperature: Optional[float] = None
    topP: Optional[float] = None
    minPixels: Optional[int] = None
    maxPixels: Optional[int] = None
    visualize: Optional[bool] = None


RawOCRInput = Union[bytes, str]


def parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def parse_optional_float(value) -> Optional[float]:
    if value in (None, ""):
        return None
    return float(value)


def parse_optional_int(value) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)


def parse_optional_string(value) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def parse_markdown_ignore_labels(value) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        pass
    return [text]


async def parse_ocr_input(request: Request) -> tuple[OCRRequest, RawOCRInput]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if not upload or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="Missing multipart field: file")

        file_bytes = await upload.read()
        ocr_request = OCRRequest(
            fileType=parse_optional_int(form.get("fileType")),
            useLayoutDetection=parse_bool(form.get("useLayoutDetection"), True),
            useDocUnwarping=parse_bool(form.get("useDocUnwarping"), False),
            useDocOrientationClassify=parse_bool(form.get("useDocOrientationClassify"), False),
            useChartRecognition=parse_bool(form.get("useChartRecognition"), False),
            useSealRecognition=parse_bool(form.get("useSealRecognition"), True),
            formatBlockContent=parse_bool(form.get("formatBlockContent"), True),
            showFormulaNumber=parse_bool(form.get("showFormulaNumber"), True),
            markdownIgnoreLabels=parse_markdown_ignore_labels(form.get("markdownIgnoreLabels")),
            layoutThreshold=parse_optional_float(form.get("layoutThreshold")),
            layoutNms=parse_bool(form.get("layoutNms")) if form.get("layoutNms") is not None else None,
            layoutUnclipRatio=parse_optional_float(form.get("layoutUnclipRatio")),
            layoutMergeBboxesMode=parse_optional_string(form.get("layoutMergeBboxesMode")),
            repetitionPenalty=parse_optional_float(form.get("repetitionPenalty")),
            temperature=parse_optional_float(form.get("temperature")),
            topP=parse_optional_float(form.get("topP")),
            minPixels=parse_optional_int(form.get("minPixels")),
            maxPixels=parse_optional_int(form.get("maxPixels")),
            visualize=parse_bool(form.get("visualize")) if form.get("visualize") is not None else None,
        )
        return ocr_request, file_bytes

    payload = await request.json()
    ocr_request = OCRRequest(**payload)
    if not ocr_request.image:
        raise HTTPException(status_code=400, detail="Missing JSON field: image")
    return ocr_request, ocr_request.image


def normalize_raw_input_to_base64(raw_input: RawOCRInput) -> str:
    if isinstance(raw_input, bytes):
        return base64.b64encode(raw_input).decode("utf-8")
    if "base64," in raw_input:
        return raw_input.split("base64,")[1]
    return raw_input


def raw_input_to_bytes(raw_input: RawOCRInput) -> bytes:
    if isinstance(raw_input, bytes):
        return raw_input
    normalized = raw_input.split("base64,")[1] if "base64," in raw_input else raw_input
    try:
        return base64.b64decode(normalized, validate=True)
    except Exception as err:
        raise HTTPException(status_code=400, detail="Invalid base64 input") from err


def build_pipeline_payload(request: OCRRequest, base64_data: str, file_type: int) -> dict:
    payload = {
        "file": base64_data,
        "fileType": file_type,
        "useLayoutDetection": request.useLayoutDetection,
        "useDocUnwarping": request.useDocUnwarping,
        "useDocOrientationClassify": request.useDocOrientationClassify,
        "useChartRecognition": request.useChartRecognition,
        "useSealRecognition": request.useSealRecognition,
        "formatBlockContent": request.formatBlockContent,
        "showFormulaNumber": request.showFormulaNumber,
        "prettifyMarkdown": True,
    }
    optional_params = [
        "markdownIgnoreLabels",
        "layoutThreshold",
        "layoutNms",
        "layoutUnclipRatio",
        "layoutMergeBboxesMode",
        "repetitionPenalty",
        "temperature",
        "topP",
        "minPixels",
        "maxPixels",
        "visualize",
    ]
    for param in optional_params:
        val = getattr(request, param)
        if val is not None:
            payload[param] = val
    return payload


def parse_pipeline_response(data: dict, image_prefix: str = "") -> dict:
    if "result" not in data or "layoutParsingResults" not in data["result"]:
        logger.warning("Unexpected pipeline response format: %s", data)
        raise HTTPException(status_code=500, detail="Unexpected response format from Pipeline")

    results = data["result"]["layoutParsingResults"]
    full_markdown = ""
    all_images = {}

    for res in results:
        if "markdown" in res and "text" in res["markdown"]:
            md_text = res["markdown"]["text"]
            md_images = res["markdown"].get("images", {})
            if md_images:
                for img_path, img_base64 in md_images.items():
                    key = f"{image_prefix}_{img_path}" if image_prefix else img_path
                    all_images[key] = img_base64
            full_markdown += md_text + "\n\n"

    return {
        "markdown": full_markdown,
        "images": all_images,
        "layoutParsingResults": results,
    }


async def run_ocr_request(ocr_request: OCRRequest, raw_input: RawOCRInput) -> dict:
    base64_data = normalize_raw_input_to_base64(raw_input)
    file_type = ocr_request.fileType

    if file_type is None:
        if isinstance(raw_input, bytes):
            if raw_input.startswith(b"%PDF-"):
                file_type = 0
                logger.info("Auto-detected PDF input")
            else:
                file_type = 1
                logger.info("Auto-detected Image input")
        elif base64_data.startswith("JVBERi0"):
            file_type = 0
            logger.info("Auto-detected PDF input")
        else:
            file_type = 1
            logger.info("Auto-detected Image input")

    if file_type == 1:
        try:
            img_bytes = raw_input_to_bytes(raw_input)
            img = Image.open(io.BytesIO(img_bytes))
            if img.format == "GIF":
                logger.info("GIF detected, converting to static JPEG for OCR")
                img.seek(0)
                rgb_img = img.convert("RGB")
                buffer = io.BytesIO()
                rgb_img.save(buffer, format="JPEG", quality=95)
                base64_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
                logger.info("GIF conversion successful")
        except Exception as gif_err:
            logger.info("GIF conversion skipped: %s", gif_err)

    payload = build_pipeline_payload(ocr_request, base64_data, file_type)

    logger.info("Sending request to Pipeline Service at %s", PADDLE_SERVICE_URL)
    timeout = PADDLE_REQUEST_TIMEOUT if PADDLE_REQUEST_TIMEOUT > 0 else None
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            PADDLE_SERVICE_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        if resp.status_code != 200:
            logger.warning("Service Error (HTTP %s): %s", resp.status_code, resp.text)
            if resp.status_code == 422:
                logger.warning("Validation Error Details: %s", resp.json())
            raise HTTPException(status_code=resp.status_code, detail=f"Upstream error: {resp.text}")

        return parse_pipeline_response(resp.json())


@app.post("/api/paddleocr-vl-1.6")
async def proxy_ocr(request: Request):
    """Proxy request to PaddleOCR-VL Pipeline Service."""
    try:
        ocr_request, raw_image = await parse_ocr_input(request)
        base64_data = normalize_raw_input_to_base64(raw_image)
        if MAX_REQUEST_BYTES > 0 and len(base64_data) > int(MAX_REQUEST_BYTES * 4 / 3) + 1024:
            max_mb = MAX_REQUEST_BYTES / 1024 / 1024
            raise HTTPException(status_code=413, detail=f"OCR input is too large. Max upload size is {max_mb:.0f} MB.")
        logger.info("Received OCR Request. Base64 input size: %s bytes", len(base64_data))
        return await run_ocr_request(ocr_request, raw_image)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Proxy Error")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting server. Target Pipeline: %s", PADDLE_SERVICE_URL)
    uvicorn.run(app, host=PANDOCR_HOST, port=PANDOCR_PORT)
