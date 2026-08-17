import os
import asyncio
import base64
import httpx
import subprocess
import tempfile
import shutil
import io
import json
import math
import re
import logging
import time
import secrets
import uuid
import contextlib
import tarfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from PIL import Image
from typing import List, Optional, Union
from urllib.parse import quote, urlsplit
from fastapi import FastAPI, HTTPException, File, UploadFile, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger("pandocr")
logging.basicConfig(level=os.getenv("PANDOCR_LOG_LEVEL", "INFO"))
logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_csv_env(name: str, default: str) -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_bool_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


UNLIMITED_OCR_KNOWN_BACKENDS = {"transformers", "sglang"}
UNLIMITED_OCR_SUPPORTED_BACKENDS = {
    item.strip().lower()
    for item in os.getenv("UNLIMITED_OCR_SUPPORTED_BACKENDS", "transformers,sglang").split(",")
    if item.strip().lower() in UNLIMITED_OCR_KNOWN_BACKENDS
} or {"transformers"}


def normalize_unlimited_ocr_backend(value: str | None, fallback: str | None = None) -> str:
    backend = str(value or fallback or "").strip().lower()
    if backend in UNLIMITED_OCR_SUPPORTED_BACKENDS:
        return backend
    fallback_backend = str(fallback or "").strip().lower()
    if fallback_backend in UNLIMITED_OCR_SUPPORTED_BACKENDS:
        return fallback_backend
    supported = ", ".join(sorted(UNLIMITED_OCR_SUPPORTED_BACKENDS))
    raise HTTPException(status_code=400, detail=f"Unsupported Unlimited-OCR backend. Use one of: {supported}.")


def parse_positive_int_env(name: str, default: str) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return max(1, int(default))


def parse_nonnegative_int_env(name: str, default: str) -> int:
    try:
        return max(0, int(os.getenv(name, default)))
    except ValueError:
        return max(0, int(default))


def parse_positive_float_env(name: str, default: str) -> float:
    try:
        return max(0.001, float(os.getenv(name, default)))
    except ValueError:
        return max(0.001, float(default))


def parse_nonnegative_float_env(name: str, default: str) -> float:
    try:
        return max(0.0, float(os.getenv(name, default)))
    except ValueError:
        return max(0.0, float(default))


PADDLE_SERVICE_URL = os.getenv("PADDLE_SERVICE_URL", "http://localhost:8081/layout-parsing")
VLM_BACKEND = os.getenv("VLM_BACKEND", "vllm")
VLM_IMAGE_TAG_SUFFIX = os.getenv("VLM_IMAGE_TAG_SUFFIX", "latest-nvidia-gpu-offline")
API_IMAGE_TAG_SUFFIX = os.getenv("API_IMAGE_TAG_SUFFIX", "latest-nvidia-gpu-offline")
VLM_IMAGE_DIGEST = os.getenv("VLM_IMAGE_DIGEST", "").strip()
API_IMAGE_DIGEST = os.getenv("API_IMAGE_DIGEST", "").strip()
PANDOCR_GPU_DEVICE_ID = os.getenv("PANDOCR_GPU_DEVICE_ID", "0")
PADDLEOCR_VL_MODEL_NAME = os.getenv("PADDLEOCR_VL_MODEL_NAME", "PaddleOCR-VL-1.6-0.9B")
PANDOCR_VLLM_MIN_TOTAL_MIB = os.getenv("PANDOCR_VLLM_MIN_TOTAL_MIB", "11264")
PANDOCR_VLLM_MIN_REQUIRED_MIB = os.getenv("PANDOCR_VLLM_MIN_REQUIRED_MIB", "6656")
PANDOCR_VLLM_RESERVE_MIB = os.getenv("PANDOCR_VLLM_RESERVE_MIB", "512")
PANDOCR_VLLM_MAX_RATIO = os.getenv("PANDOCR_VLLM_MAX_RATIO", "0.88")
PADDLE_OCR_SERVICE_URL = os.getenv("PADDLE_OCR_SERVICE_URL", "http://localhost:8082/ocr")
PPOCR_V6_MODEL_NAME = os.getenv("PPOCR_V6_MODEL_NAME", "PP-OCRv6_medium")
PADDLE_REQUEST_TIMEOUT = float(os.getenv("PADDLE_REQUEST_TIMEOUT", "3600"))
UNLIMITED_OCR_SERVICE_URL = os.getenv("UNLIMITED_OCR_SERVICE_URL", "http://localhost:8083/ocr")
UNLIMITED_OCR_MODEL_NAME = os.getenv("UNLIMITED_OCR_MODEL_NAME", "baidu/Unlimited-OCR")
UNLIMITED_OCR_MODEL_REVISION = os.getenv(
    "UNLIMITED_OCR_MODEL_REVISION", "07dea832e22aefee32ad281d4b80551282e1c168"
)
UNLIMITED_OCR_SERVED_MODEL_NAME = os.getenv("UNLIMITED_OCR_SERVED_MODEL_NAME", "Unlimited-OCR")
UNLIMITED_OCR_BACKEND = normalize_unlimited_ocr_backend(os.getenv("UNLIMITED_OCR_BACKEND"), "transformers")
UNLIMITED_OCR_PRELOAD = os.getenv("UNLIMITED_OCR_PRELOAD", "1")
UNLIMITED_OCR_API_PORT = os.getenv("UNLIMITED_OCR_API_PORT", "8083")
UNLIMITED_OCR_SGLANG_PORT = os.getenv("UNLIMITED_OCR_SGLANG_PORT", "10000")
UNLIMITED_OCR_ATTENTION_BACKEND = os.getenv("UNLIMITED_OCR_ATTENTION_BACKEND", "flashinfer")
UNLIMITED_OCR_PAGE_SIZE = os.getenv("UNLIMITED_OCR_PAGE_SIZE", "1")
UNLIMITED_OCR_MEM_FRACTION_STATIC = os.getenv("UNLIMITED_OCR_MEM_FRACTION_STATIC", "0.8")
UNLIMITED_OCR_CONTEXT_LENGTH = os.getenv("UNLIMITED_OCR_CONTEXT_LENGTH", "32768")
UNLIMITED_OCR_REQUEST_TIMEOUT = os.getenv("UNLIMITED_OCR_REQUEST_TIMEOUT", "1200")
UNLIMITED_OCR_PDF_DPI = os.getenv("UNLIMITED_OCR_PDF_DPI", "300")
UNLIMITED_OCR_MAX_PAGES_PER_REQUEST = os.getenv("UNLIMITED_OCR_MAX_PAGES_PER_REQUEST", "50")
UNLIMITED_OCR_MAX_RENDER_PIXELS = os.getenv("UNLIMITED_OCR_MAX_RENDER_PIXELS", "60000000")
UNLIMITED_OCR_SINGLE_IMAGE_MODE = os.getenv("UNLIMITED_OCR_SINGLE_IMAGE_MODE", "gundam")
UNLIMITED_OCR_MULTI_IMAGE_MODE = os.getenv("UNLIMITED_OCR_MULTI_IMAGE_MODE", "base")
UNLIMITED_OCR_MAX_TOKENS = os.getenv("UNLIMITED_OCR_MAX_TOKENS", "32768")
UNLIMITED_OCR_SGLANG_MAX_TOKENS = os.getenv("UNLIMITED_OCR_SGLANG_MAX_TOKENS", "28672")
OVISOCR2_SERVICE_URL = os.getenv("OVISOCR2_SERVICE_URL", "http://localhost:8084/ocr")
OVISOCR2_MODEL_NAME = os.getenv("OVISOCR2_MODEL_NAME", "ATH-MaaS/OvisOCR2")
OVISOCR2_MODEL_REVISION = os.getenv(
    "OVISOCR2_MODEL_REVISION", "65c619d374b55d4152e85150fc1b003700bc1f0c"
)
OVISOCR2_API_PORT = os.getenv("OVISOCR2_API_PORT", "8084")
OVISOCR2_KV_CACHE_MEMORY_MB = os.getenv("OVISOCR2_KV_CACHE_MEMORY_MB", "512")
OVISOCR2_STARTUP_MEMORY_FRACTION = os.getenv("OVISOCR2_STARTUP_MEMORY_FRACTION", "0.50")
OVISOCR2_MAX_MODEL_LEN = os.getenv("OVISOCR2_MAX_MODEL_LEN", "32768")
OVISOCR2_MAX_NUM_SEQS = os.getenv("OVISOCR2_MAX_NUM_SEQS", "1")
OVISOCR2_MAX_TOKENS = os.getenv("OVISOCR2_MAX_TOKENS", "8192")
OVISOCR2_PDF_DPI = os.getenv("OVISOCR2_PDF_DPI", "200")
OVISOCR2_MAX_PAGES_PER_REQUEST = os.getenv("OVISOCR2_MAX_PAGES_PER_REQUEST", "50")
OVISOCR2_GDN_PREFILL_BACKEND = os.getenv("OVISOCR2_GDN_PREFILL_BACKEND", "triton")
HPD_PARSING_SERVICE_URL = os.getenv("HPD_PARSING_SERVICE_URL", "http://localhost:8085/ocr")
HPD_PARSING_MODEL_NAME = os.getenv("HPD_PARSING_MODEL_NAME", "PaddlePaddle/HPD-Parsing")
HPD_PARSING_IMAGE = os.getenv(
    "HPD_PARSING_IMAGE",
    "ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/"
    "hpd-parsing-vllm:latest-nvidia-gpu@"
    "sha256:87496aa5dd702a7df6c70bb00c29d5bf8d1a0f0505d66b613fafa6f71cd72de2",
)
HPD_PARSING_API_PORT = os.getenv("HPD_PARSING_API_PORT", "8085")
HPD_PARSING_SERVED_MODEL_NAME = os.getenv("HPD_PARSING_SERVED_MODEL_NAME", "HPD-Parsing")
HPD_PARSING_MAX_TOKENS = os.getenv("HPD_PARSING_MAX_TOKENS", "8000")
HPD_PARSING_MAX_MODEL_LEN = os.getenv("HPD_PARSING_MAX_MODEL_LEN", "16384")
HPD_PARSING_GPU_MEMORY_UTILIZATION = os.getenv("HPD_PARSING_GPU_MEMORY_UTILIZATION", "auto")
HPD_PARSING_GPU_MEMORY_TARGET_MIB = os.getenv("HPD_PARSING_GPU_MEMORY_TARGET_MIB", "6656")
HPD_PARSING_PDF_DPI = os.getenv("HPD_PARSING_PDF_DPI", "200")
HPD_PARSING_MAX_PAGES_PER_REQUEST = os.getenv("HPD_PARSING_MAX_PAGES_PER_REQUEST", "50")
HPD_PARSING_MAX_CONCURRENCY = os.getenv("HPD_PARSING_MAX_CONCURRENCY", "1")
HPD_PARSING_REQUEST_TIMEOUT = os.getenv("HPD_PARSING_REQUEST_TIMEOUT", "1200")
UNLIMITED_OCR_SGLANG_WHEEL_URL = os.getenv(
    "UNLIMITED_OCR_SGLANG_WHEEL_URL",
    "https://huggingface.co/baidu/Unlimited-OCR/resolve/07dea832e22aefee32ad281d4b80551282e1c168/wheel/sglang-0.0.0.dev11416%2Bg92e8bb79e-py3-none-any.whl?download=true#sha256=2644a1f349c55f0ca822e70a70679c98475754ec4722c3be1b18a72bac477cd5",
)
PROJECT_ROOT = Path(__file__).resolve().parent
TASK_DATA_DIR = Path(os.getenv("PANDOCR_TASK_DATA_DIR", "data/tasks")).resolve()
DEFAULT_RUNTIME_SETTINGS_DIR = TASK_DATA_DIR.parent if TASK_DATA_DIR.name == "tasks" else TASK_DATA_DIR
RUNTIME_SETTINGS_FILE = Path(
    os.getenv("PANDOCR_RUNTIME_SETTINGS_FILE", str(DEFAULT_RUNTIME_SETTINGS_DIR / "runtime-settings.json"))
).resolve()
CONTROLLER_OCR_LEASE_STORE_ENABLED = parse_bool_env(
    "PANDOCR_CONTROLLER_OCR_LEASE_STORE_ENABLED", "0"
)
CONTROLLER_OCR_LEASE_STORE_FILE = Path(
    os.getenv(
        "PANDOCR_CONTROLLER_OCR_LEASE_STORE_FILE",
        str(DEFAULT_RUNTIME_SETTINGS_DIR / "controller-ocr-leases.json"),
    )
).resolve()
CONTROLLER_OCR_LEASE_STORE_VERSION = 1
MAX_REQUEST_BYTES = int(float(os.getenv("PANDOCR_MAX_UPLOAD_MB", "512")) * 1024 * 1024)
MAX_HTTP_BODY_BYTES = int(
    float(os.getenv("PANDOCR_MAX_HTTP_BODY_MB", "0")) * 1024 * 1024
) or (int(MAX_REQUEST_BYTES * 4 / 3) + 2 * 1024 * 1024 if MAX_REQUEST_BYTES > 0 else 0)
PANDOCR_HOST = os.getenv("PANDOCR_HOST", "127.0.0.1")
PANDOCR_PORT = int(os.getenv("PANDOCR_PORT", "8000"))
APP_VERSION = os.getenv("PANDOCR_APP_VERSION", "0.2.0").strip() or "0.2.0"
APP_COMMIT = os.getenv("PANDOCR_GIT_COMMIT", "").strip()
MODEL_CONTROL_MODE = os.getenv("PANDOCR_MODEL_CONTROL", "docker").strip().lower()
MODEL_CONTROLLER_URL = os.getenv("PANDOCR_MODEL_CONTROLLER_URL", "").rstrip("/")
MODEL_CONTROLLER_TOKEN = os.getenv("PANDOCR_MODEL_CONTROLLER_TOKEN", "").strip()
OFFICE_CONVERTER_URL = os.getenv("PANDOCR_OFFICE_CONVERTER_URL", "").strip()
MODEL_RUNTIME_STARTUP = os.getenv("PANDOCR_ACTIVE_MODEL_ON_START", "paddleocr-vl-1.6").strip()
DOCKER_SOCKET_PATH = os.getenv("PANDOCR_DOCKER_SOCKET", "/var/run/docker.sock")
MODEL_SWITCH_TIMEOUT = float(os.getenv("PANDOCR_MODEL_SWITCH_TIMEOUT", "1200"))
GPU_PREFLIGHT_CACHE_SECONDS = float(os.getenv("PANDOCR_GPU_PREFLIGHT_CACHE_SECONDS", "300"))
GPU_RELEASE_TIMEOUT = parse_positive_float_env("PANDOCR_GPU_RELEASE_TIMEOUT", "60")
GPU_RELEASE_STABLE_SAMPLES = parse_positive_int_env("PANDOCR_GPU_RELEASE_STABLE_SAMPLES", "3")
GPU_RELEASE_SAMPLE_INTERVAL_SECONDS = (
    parse_nonnegative_float_env("PANDOCR_GPU_RELEASE_SAMPLE_INTERVAL_MS", "500") / 1000.0
)
GPU_RELEASE_TOLERANCE_MIB = parse_nonnegative_int_env("PANDOCR_GPU_RELEASE_TOLERANCE_MIB", "128")
API_TOKEN = os.getenv("PANDOCR_API_TOKEN", "").strip()
ENABLE_API_DOCS = parse_bool_env("PANDOCR_ENABLE_API_DOCS", "0")
ENFORCE_ORIGIN_CHECK = parse_bool_env("PANDOCR_ENFORCE_ORIGIN_CHECK", "1")
ENABLE_UNLIMITED_OCR = parse_bool_env("PANDOCR_ENABLE_UNLIMITED_OCR", "0")
ENABLE_OVISOCR2 = parse_bool_env("PANDOCR_ENABLE_OVISOCR2", "0")
ENABLE_HPD_PARSING = parse_bool_env("PANDOCR_ENABLE_HPD_PARSING", "0")
MODEL_CATALOG_ENV = os.getenv("PANDOCR_MODEL_CATALOG", "").strip()
MAX_CONCURRENT_OCR = parse_positive_int_env("PANDOCR_MAX_CONCURRENT_OCR", "1")
CONTROLLER_OCR_LEASE_TTL_SECONDS = parse_nonnegative_int_env(
    "PANDOCR_CONTROLLER_OCR_LEASE_TTL_SECONDS", "0"
)
TASK_STORE_MARKER = ".pandocr-task-store"
TASK_RESULT_FILE = "result.json"
TASK_SUMMARY_FILE = "summary.json"
UPLOAD_CHUNK_SIZE = 1024 * 1024
CORS_ORIGINS = parse_csv_env(
    "PANDOCR_CORS_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000",
)


def load_runtime_settings() -> dict:
    try:
        if not RUNTIME_SETTINGS_FILE.exists():
            return {}
        data = json.loads(RUNTIME_SETTINGS_FILE.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("Failed to read runtime settings: %s", RUNTIME_SETTINGS_FILE, exc_info=True)
        return {}


def save_runtime_settings(updates: dict) -> None:
    try:
        settings = load_runtime_settings()
        settings.update(updates)
        RUNTIME_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = RUNTIME_SETTINGS_FILE.with_suffix(".tmp")
        temp_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(RUNTIME_SETTINGS_FILE)
    except Exception:
        logger.warning("Failed to write runtime settings: %s", RUNTIME_SETTINGS_FILE, exc_info=True)


def initial_unlimited_ocr_backend() -> str:
    settings = load_runtime_settings()
    persisted_backend = settings.get("unlimitedOcrBackend")
    return normalize_unlimited_ocr_backend(persisted_backend, UNLIMITED_OCR_BACKEND)


def parse_model_catalog() -> list[str]:
    supported = {"paddleocr-vl-1.6", "pp-ocrv6", "unlimited-ocr", "ovisocr2", "hpd-parsing"}
    if MODEL_CATALOG_ENV:
        ids = [model_id for model_id in parse_csv_env("PANDOCR_MODEL_CATALOG", "") if model_id in supported]
    else:
        ids = ["paddleocr-vl-1.6", "pp-ocrv6"]
        if ENABLE_UNLIMITED_OCR:
            ids.append("unlimited-ocr")
        if ENABLE_OVISOCR2:
            ids.append("ovisocr2")
        if ENABLE_HPD_PARSING:
            ids.append("hpd-parsing")

    unique_ids = []
    for model_id in ids:
        if model_id not in unique_ids:
            unique_ids.append(model_id)
    return unique_ids or ["paddleocr-vl-1.6"]


MODEL_CATALOG_IDS = parse_model_catalog()
ENABLE_UNLIMITED_OCR = ENABLE_UNLIMITED_OCR or "unlimited-ocr" in MODEL_CATALOG_IDS
ENABLE_OVISOCR2 = ENABLE_OVISOCR2 or "ovisocr2" in MODEL_CATALOG_IDS
ENABLE_HPD_PARSING = ENABLE_HPD_PARSING or "hpd-parsing" in MODEL_CATALOG_IDS

# Keep the complete five-model container universe independent from the enabled
# catalog.  A container left behind by an older or differently configured
# deployment still consumes the same GPU and must participate in exclusivity
# checks and switch cleanup.
MODEL_RUNTIME_CONTAINER_GROUPS = {
    "paddleocr-vl-1.6": {
        "containers": ["paddleocr-vlm-server", "paddleocr-vl-api"],
        "start_order": ["paddleocr-vlm-server", "paddleocr-vl-api"],
        "stop_order": ["paddleocr-vl-api", "paddleocr-vlm-server"],
    },
    "pp-ocrv6": {
        "containers": ["paddleocr-ocr-api"],
        "start_order": ["paddleocr-ocr-api"],
        "stop_order": ["paddleocr-ocr-api"],
    },
    "unlimited-ocr": {
        "containers": ["unlimited-ocr-api", "unlimited-ocr-sglang"],
        "start_order": ["unlimited-ocr-api"],
        "stop_order": ["unlimited-ocr-sglang", "unlimited-ocr-api"],
    },
    "ovisocr2": {
        "containers": ["ovisocr2-api"],
        "start_order": ["ovisocr2-api"],
        "stop_order": ["ovisocr2-api"],
    },
    "hpd-parsing": {
        "containers": ["hpd-parsing-server", "hpd-parsing-api"],
        "start_order": ["hpd-parsing-server", "hpd-parsing-api"],
        "stop_order": ["hpd-parsing-api", "hpd-parsing-server"],
    },
}

MODEL_MINIMUM_FREE_MIB = {
    "paddleocr-vl-1.6": 6656,
    "pp-ocrv6": 4096,
    "unlimited-ocr": 6656,
    "ovisocr2": 6656,
    "hpd-parsing": 6656,
}

MODEL_RUNTIME_CONFIG = {
    "paddleocr-vl-1.6": {
        "containers": ["paddleocr-vlm-server", "paddleocr-vl-api"],
        "start_order": ["paddleocr-vlm-server", "paddleocr-vl-api"],
        "stop_order": ["paddleocr-vl-api", "paddleocr-vlm-server"],
        "health_url": PADDLE_SERVICE_URL.rsplit("/", 1)[0] + "/health",
        "gpu_memory": {
            "minimum_mib": 11264,
            "minimum_free_mib": MODEL_MINIMUM_FREE_MIB["paddleocr-vl-1.6"],
            "recommended_mib": 15360,
            "low_memory_env": [
                "PANDOCR_VLLM_MIN_REQUIRED_MIB=6656",
                "PANDOCR_VLLM_RESERVE_MIB=512",
                "PANDOCR_MAX_CONCURRENT_OCR=1",
            ],
        },
    },
    "pp-ocrv6": {
        "containers": ["paddleocr-ocr-api"],
        "start_order": ["paddleocr-ocr-api"],
        "stop_order": ["paddleocr-ocr-api"],
        "health_url": PADDLE_OCR_SERVICE_URL.rsplit("/", 1)[0] + "/health",
        "gpu_memory": {
            "minimum_mib": 4096,
            "minimum_free_mib": MODEL_MINIMUM_FREE_MIB["pp-ocrv6"],
            "recommended_mib": 6144,
            "low_memory_env": ["PANDOCR_MAX_CONCURRENT_OCR=1"],
        },
    },
}

if ENABLE_UNLIMITED_OCR:
    MODEL_RUNTIME_CONFIG["unlimited-ocr"] = {
        "containers": ["unlimited-ocr-api"],
        "start_order": ["unlimited-ocr-api"],
        "stop_order": ["unlimited-ocr-sglang", "unlimited-ocr-api"],
        "health_url": UNLIMITED_OCR_SERVICE_URL.rsplit("/", 1)[0] + "/health",
        "gpu_memory": {
            "minimum_mib": 7680,
            "minimum_free_mib": MODEL_MINIMUM_FREE_MIB["unlimited-ocr"],
            "recommended_mib": 11264,
            "low_memory_env": [
                "UNLIMITED_OCR_BACKEND=transformers",
                "UNLIMITED_OCR_MAX_TOKENS=8192",
                "PANDOCR_MAX_CONCURRENT_OCR=1",
            ],
        },
    }

if ENABLE_OVISOCR2:
    MODEL_RUNTIME_CONFIG["ovisocr2"] = {
        "containers": ["ovisocr2-api"],
        "start_order": ["ovisocr2-api"],
        "stop_order": ["ovisocr2-api"],
        "health_url": OVISOCR2_SERVICE_URL.rsplit("/", 1)[0] + "/health",
        "gpu_memory": {
            "minimum_mib": 7680,
            "minimum_free_mib": MODEL_MINIMUM_FREE_MIB["ovisocr2"],
            "recommended_mib": 15360,
            "low_memory_env": [
                "OVISOCR2_KV_CACHE_MEMORY_MB=256",
                "OVISOCR2_MAX_TOKENS=4096",
                "OVISOCR2_MAX_NUM_SEQS=1",
            ],
        },
    }

if ENABLE_HPD_PARSING:
    MODEL_RUNTIME_CONFIG["hpd-parsing"] = {
        "containers": ["hpd-parsing-server", "hpd-parsing-api"],
        "start_order": ["hpd-parsing-server", "hpd-parsing-api"],
        "stop_order": ["hpd-parsing-api", "hpd-parsing-server"],
        "health_url": HPD_PARSING_SERVICE_URL.rsplit("/", 1)[0] + "/health",
        "gpu_memory": {
            "minimum_mib": 7680,
            "minimum_free_mib": MODEL_MINIMUM_FREE_MIB["hpd-parsing"],
            "recommended_mib": 11264,
            "low_memory_env": [
                "HPD_PARSING_GPU_MEMORY_UTILIZATION=auto",
                "HPD_PARSING_GPU_MEMORY_TARGET_MIB=6144",
                "HPD_PARSING_MAX_MODEL_LEN=8192",
                "HPD_PARSING_MAX_TOKENS=4096",
                "HPD_PARSING_MAX_CONCURRENCY=1",
            ],
        },
    }

DEFAULT_RUNTIME_FALLBACK_MODEL_ID = next(
    (model_id for model_id in MODEL_CATALOG_IDS if model_id in MODEL_RUNTIME_CONFIG),
    next(iter(MODEL_RUNTIME_CONFIG)),
)
DEFAULT_RUNTIME_MODEL_ID = (
    MODEL_RUNTIME_STARTUP
    if MODEL_RUNTIME_STARTUP in MODEL_RUNTIME_CONFIG and MODEL_RUNTIME_STARTUP in MODEL_CATALOG_IDS
    else DEFAULT_RUNTIME_FALLBACK_MODEL_ID
)

model_runtime_lock = asyncio.Lock()
ocr_semaphore = asyncio.Semaphore(MAX_CONCURRENT_OCR)
model_runtime_operation = {
    "targetModelId": DEFAULT_RUNTIME_MODEL_ID,
    "state": "idle",
    "message": "",
    "startedAt": None,
    "updatedAt": None,
    "diagnostics": None,
}
model_runtime_task: asyncio.Task | None = None
unlimited_ocr_backend_task: asyncio.Task | None = None
unlimited_ocr_runtime_backend = initial_unlimited_ocr_backend()
ocr_active_count = 0
controller_ocr_leases: dict[str, dict] = {}
controller_ocr_lease_store_loaded = not CONTROLLER_OCR_LEASE_STORE_ENABLED
controller_ocr_lease_store_error: str | None = None
gpu_preflight_cache: dict = {"updated_at": 0.0, "data": None}


class ModelSwitchRequest(BaseModel):
    modelId: str


class ModelDeployRequest(BaseModel):
    modelId: str
    backend: str | None = None


class UnlimitedOcrBackendRequest(BaseModel):
    backend: str


class OCRLeaseRequest(BaseModel):
    modelId: str


def model_catalog() -> list[dict]:
    models_by_id = {
        "paddleocr-vl-1.6": {
            "id": "paddleocr-vl-1.6",
            "name": PADDLEOCR_VL_MODEL_NAME,
            "label": "PaddleOCR-VL 1.6",
            "kind": "document_parsing",
            "endpoint": "/api/paddleocr-vl-1.6",
        },
        "pp-ocrv6": {
            "id": "pp-ocrv6",
            "name": PPOCR_V6_MODEL_NAME,
            "label": "PP-OCRv6",
            "kind": "text_ocr",
            "endpoint": "/api/pp-ocrv6",
        },
        "unlimited-ocr": {
            "id": "unlimited-ocr",
            "name": UNLIMITED_OCR_MODEL_NAME,
            "label": "Unlimited-OCR",
            "kind": "document_parsing",
            "endpoint": "/api/unlimited-ocr",
        },
        "ovisocr2": {
            "id": "ovisocr2",
            "name": OVISOCR2_MODEL_NAME,
            "label": "OvisOCR2",
            "kind": "document_parsing",
            "endpoint": "/api/ovisocr2",
        },
        "hpd-parsing": {
            "id": "hpd-parsing",
            "name": HPD_PARSING_MODEL_NAME,
            "label": "HPD-Parsing",
            "kind": "document_parsing",
            "endpoint": "/api/hpd-parsing",
        },
    }
    return [
        models_by_id[model_id]
        for model_id in MODEL_CATALOG_IDS
        if model_id in models_by_id and model_id in MODEL_RUNTIME_CONFIG
    ]


def model_control_available() -> bool:
    return MODEL_CONTROL_MODE == "docker" and Path(DOCKER_SOCKET_PATH).exists()


async def controller_api_request(method: str, path: str, **kwargs) -> dict:
    if MODEL_CONTROL_MODE != "remote" or not MODEL_CONTROLLER_URL:
        raise HTTPException(status_code=503, detail="Remote model controller is not configured")
    headers = dict(kwargs.pop("headers", {}))
    if MODEL_CONTROLLER_TOKEN:
        headers["X-Pandocr-Controller-Token"] = MODEL_CONTROLLER_TOKEN
    try:
        async with httpx.AsyncClient(timeout=MODEL_SWITCH_TIMEOUT) as client:
            response = await client.request(method, f"{MODEL_CONTROLLER_URL}{path}", headers=headers, **kwargs)
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail=f"Model controller is unavailable: {error}") from error
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail or "Model controller request failed")
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Invalid model controller response")
    return payload


async def docker_api_request(method: str, path: str, *, timeout: float = 30, **request_kwargs) -> httpx.Response:
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET_PATH)
    async with httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=timeout) as client:
        return await client.request(method, path, **request_kwargs)


def decode_docker_log_stream(content: bytes) -> str:
    """Decode Docker's multiplexed stdout/stderr stream, falling back to plain text."""
    chunks: list[bytes] = []
    offset = 0
    while offset + 8 <= len(content) and content[offset] in {0, 1, 2}:
        size = int.from_bytes(content[offset + 4 : offset + 8], "big")
        end = offset + 8 + size
        if end > len(content):
            break
        chunks.append(content[offset + 8 : end])
        offset = end
    raw = b"".join(chunks) if chunks and offset == len(content) else content
    return raw.decode("utf-8", errors="replace").replace("\x00", "").strip()


async def docker_container_logs(name: str, tail: int = 120, *, timestamps: bool = True) -> str:
    response = await docker_api_request(
        "GET",
        f"/containers/{quote(name, safe='')}/logs?stdout=1&stderr=1&tail={max(1, tail)}&timestamps={1 if timestamps else 0}",
    )
    if response.status_code == 404:
        return ""
    response.raise_for_status()
    return decode_docker_log_stream(response.content)[-12000:]


async def gpu_probe_image(preferred_model_id: str | None = None) -> str | None:
    model_ids = ([preferred_model_id] if preferred_model_id in MODEL_RUNTIME_CONFIG else []) + [
        model_id for model_id in MODEL_RUNTIME_CONFIG if model_id != preferred_model_id
    ]
    for model_id in model_ids:
        for service_name in MODEL_RUNTIME_CONFIG[model_id].get("containers", []):
            image = docker_image_name_for(service_name)
            if await docker_image_exists(image):
                return image
    return None


def gpu_compatibility(gpus: list[dict]) -> dict:
    selected = gpus[0] if gpus else None
    total_mib = int(selected.get("totalMiB") or 0) if selected else 0
    models: dict[str, dict] = {}
    runnable_model_ids: list[str] = []
    for model_id, config in MODEL_RUNTIME_CONFIG.items():
        requirement = config.get("gpu_memory") or {}
        minimum = int(requirement.get("minimum_mib") or 0)
        minimum_free = int(requirement.get("minimum_free_mib") or minimum)
        recommended = int(requirement.get("recommended_mib") or minimum)
        supported = bool(selected and total_mib >= minimum)
        if supported:
            runnable_model_ids.append(model_id)
        models[model_id] = {
            "supported": supported,
            "level": "recommended" if supported and total_mib >= recommended else ("low-memory" if supported else "unsupported"),
            "minimumMiB": minimum,
            "minimumFreeMiB": minimum_free,
            "recommendedMiB": recommended,
            "lowMemoryEnv": list(requirement.get("low_memory_env") or []),
        }
    return {"models": models, "runnableModelIds": runnable_model_ids}


async def probe_gpu_preflight(preferred_model_id: str | None = None, *, refresh: bool = False) -> dict:
    now = time.monotonic()
    cached = gpu_preflight_cache.get("data")
    if not refresh and cached and now - float(gpu_preflight_cache.get("updated_at") or 0) < GPU_PREFLIGHT_CACHE_SECONDS:
        return cached
    if not model_control_available():
        return {"status": "unavailable", "reason": "Docker model control is not available"}

    image = await gpu_probe_image(preferred_model_id)
    if not image:
        data = {
            "status": "unavailable",
            "reason": "No deployed GPU image is available for the nvidia-smi preflight probe",
            "models": gpu_compatibility([])["models"],
            "runnableModelIds": [],
        }
        gpu_preflight_cache.update({"updated_at": now, "data": data})
        return data

    name = f"pandocr-gpu-preflight-{uuid.uuid4().hex[:10]}"
    try:
        create_response = await docker_api_request(
            "POST",
            f"/containers/create?name={name}",
            timeout=60,
            json={
                "Image": image,
                "Entrypoint": ["nvidia-smi"],
                "Cmd": [
                    "--query-gpu=index,name,memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                "HostConfig": {
                    "NetworkMode": "none",
                    "DeviceRequests": model_device_requests(),
                },
            },
        )
        if create_response.status_code >= 400:
            raise RuntimeError(f"Docker GPU probe create failed: {create_response.text}")
        start_response = await docker_api_request("POST", f"/containers/{name}/start", timeout=60)
        if start_response.status_code not in {204, 304}:
            raise RuntimeError(f"Docker GPU probe start failed: {start_response.text}")
        wait_response = await docker_api_request("POST", f"/containers/{name}/wait", timeout=60)
        wait_response.raise_for_status()
        exit_code = int((wait_response.json() or {}).get("StatusCode", 1))
        output = await docker_container_logs(name, tail=20, timestamps=False)
        if exit_code != 0:
            raise RuntimeError(output or f"nvidia-smi exited with code {exit_code}")

        gpus = []
        for line in output.splitlines():
            parts = [part.strip() for part in line.rsplit(",", 3)]
            if len(parts) != 4 or not parts[0].isdigit():
                continue
            try:
                gpus.append({
                    "index": int(parts[0]),
                    "deviceId": PANDOCR_GPU_DEVICE_ID,
                    "name": parts[1],
                    "totalMiB": int(float(parts[2])),
                    "freeMiB": int(float(parts[3])),
                })
            except ValueError:
                continue
        if not gpus:
            raise RuntimeError(f"Could not parse nvidia-smi output: {output[-1000:]}")
        data = {"status": "ready", "probeImage": image, "gpus": gpus, **gpu_compatibility(gpus)}
        gpu_preflight_cache.update({"updated_at": now, "data": data})
        return data
    except Exception as err:
        data = {
            "status": "unavailable",
            "reason": str(err),
            "models": gpu_compatibility([])["models"],
            "runnableModelIds": [],
        }
        gpu_preflight_cache.update({"updated_at": now, "data": data})
        return data
    finally:
        with contextlib.suppress(Exception):
            await docker_api_request("DELETE", f"/containers/{name}?force=1", timeout=30)


async def ensure_model_gpu_compatible(model_id: str) -> dict:
    preflight = await probe_gpu_preflight(model_id, refresh=True)
    if preflight.get("status") != "ready":
        raise RuntimeError(
            "GPU preflight failed before model startup: "
            f"{preflight.get('reason') or 'nvidia-smi is unavailable'}. "
            "Check NVIDIA Container Toolkit and run: docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi"
        )
    compatibility = (preflight.get("models") or {}).get(model_id) or {}
    if not compatibility.get("supported"):
        gpu = (preflight.get("gpus") or [{}])[0]
        runnable = ", ".join(preflight.get("runnableModelIds") or []) or "none"
        raise RuntimeError(
            f"GPU preflight rejected {model_id}: {gpu.get('name', 'GPU')} has "
            f"{gpu.get('totalMiB', 0)} MiB VRAM, but this model needs at least "
            f"{compatibility.get('minimumMiB', 0)} MiB. Runnable models: {runnable}."
        )
    return preflight


def selected_gpu_from_preflight(preflight: dict) -> dict:
    """Resolve the GPU exposed to the probe without silently choosing another device."""
    gpus = preflight.get("gpus")
    if not isinstance(gpus, list) or not gpus:
        raise RuntimeError("GPU release gate could not identify the selected GPU")

    requested_device = str(PANDOCR_GPU_DEVICE_ID).strip()
    for gpu in gpus:
        if not isinstance(gpu, dict):
            continue
        if str(gpu.get("deviceId", "")).strip() == requested_device:
            return gpu
        if str(gpu.get("index", "")).strip() == requested_device:
            return gpu

    # Docker's DeviceRequests normally exposes exactly one selected GPU and
    # remaps it to index 0 inside the probe container.  Accept that unambiguous
    # result, but never guess when multiple devices are visible.
    if len(gpus) == 1 and isinstance(gpus[0], dict):
        return gpus[0]
    raise RuntimeError(
        f"GPU release gate did not return selected device {PANDOCR_GPU_DEVICE_ID}; "
        f"visible devices={len(gpus)}"
    )


async def wait_for_stable_gpu_release(model_id: str, timeout: float | None = None) -> dict:
    """Fail closed until selected-GPU free VRAM is high and stable.

    Every accepted window contains consecutive fresh nvidia-smi probes.  A low
    sample resets the window; a high but unstable sample starts a new window.
    Probe errors or malformed data fail immediately instead of allowing a model
    start without evidence that the previous runtime released its VRAM.
    """
    config = MODEL_RUNTIME_CONFIG.get(model_id) or {}
    requirement = config.get("gpu_memory") or {}
    minimum_free_mib = int(requirement.get("minimum_free_mib") or 0)
    if minimum_free_mib <= 0:
        raise RuntimeError(f"GPU release gate has no minimum_free_mib configured for {model_id}")

    requested_timeout = GPU_RELEASE_TIMEOUT if timeout is None else float(timeout)
    effective_timeout = min(requested_timeout, GPU_RELEASE_TIMEOUT, MODEL_SWITCH_TIMEOUT)
    if effective_timeout <= 0:
        raise RuntimeError(f"GPU release gate has no remaining timeout for {model_id}")

    required_samples = max(1, int(GPU_RELEASE_STABLE_SAMPLES))
    tolerance_mib = max(0, int(GPU_RELEASE_TOLERANCE_MIB))
    deadline = time.monotonic() + effective_timeout
    stable_samples: list[int] = []
    observed_samples: list[int] = []
    selected_gpu: dict = {}

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            preflight = await asyncio.wait_for(
                probe_gpu_preflight(model_id, refresh=True),
                timeout=remaining,
            )
        except TimeoutError as error:
            raise TimeoutError(
                f"GPU release gate timed out while probing selected GPU {PANDOCR_GPU_DEVICE_ID} "
                f"for {model_id}"
            ) from error
        if preflight.get("status") != "ready":
            raise RuntimeError(
                "GPU release gate failed closed before model startup: "
                f"{preflight.get('reason') or 'fresh nvidia-smi probe is unavailable'}"
            )
        selected_gpu = selected_gpu_from_preflight(preflight)
        free_value = selected_gpu.get("freeMiB")
        try:
            free_mib = int(float(free_value))
        except (TypeError, ValueError) as error:
            raise RuntimeError(
                "GPU release gate failed closed because selected-GPU freeMiB is missing or invalid"
            ) from error

        observed_samples.append(free_mib)
        if free_mib < minimum_free_mib:
            stable_samples = []
        else:
            candidate = [*stable_samples, free_mib]
            if max(candidate) - min(candidate) <= tolerance_mib:
                stable_samples = candidate[-required_samples:]
            else:
                stable_samples = [free_mib]

        if len(stable_samples) >= required_samples:
            return {
                "modelId": model_id,
                "deviceId": selected_gpu.get("deviceId", PANDOCR_GPU_DEVICE_ID),
                "minimumFreeMiB": minimum_free_mib,
                "stableSamples": stable_samples,
                "observedSamples": observed_samples,
                "toleranceMiB": tolerance_mib,
            }

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(GPU_RELEASE_SAMPLE_INTERVAL_SECONDS, remaining))

    last_free_mib = observed_samples[-1] if observed_samples else "unknown"
    raise TimeoutError(
        f"GPU release gate timed out for {model_id}: selected GPU {PANDOCR_GPU_DEVICE_ID} "
        f"needs {minimum_free_mib} MiB free in {required_samples} stable samples "
        f"(tolerance {tolerance_mib} MiB); last free={last_free_mib} MiB"
    )


async def model_failure_diagnostics(model_id: str, error: Exception) -> dict:
    log_entries = []
    for container_name in MODEL_RUNTIME_CONFIG.get(model_id, {}).get("containers", []):
        with contextlib.suppress(Exception):
            content = await docker_container_logs(container_name)
            if content:
                log_entries.append({
                    "container": container_name,
                    "command": f"docker logs --tail 200 {container_name}",
                    "tail": content,
                })
    return {
        "error": str(error),
        "logs": log_entries,
        "logCommands": [
            f"docker logs --tail 200 {name}"
            for name in MODEL_RUNTIME_CONFIG.get(model_id, {}).get("containers", [])
        ],
    }


async def inspect_container(name: str) -> dict:
    if not model_control_available():
        return {
            "name": name,
            "exists": False,
            "running": False,
            "state": "unknown",
            "health": "unknown",
        }

    response = await docker_api_request("GET", f"/containers/{name}/json")
    if response.status_code == 404:
        return {
            "name": name,
            "exists": False,
            "running": False,
            "state": "missing",
            "health": "missing",
        }
    response.raise_for_status()
    payload = response.json()
    state = payload.get("State") or {}
    health = state.get("Health") or {}
    return {
        "name": name,
        "exists": True,
        "running": bool(state.get("Running")),
        "state": state.get("Status") or "unknown",
        "health": health.get("Status") or "none",
    }


async def docker_container_action(name: str, action: str) -> None:
    if not model_control_available():
        raise RuntimeError("Docker model control is not available")
    if action == "stop":
        response = await docker_api_request("POST", f"/containers/{name}/stop?t=20", timeout=45)
        if response.status_code in {204, 304, 404}:
            return
    elif action == "start":
        response = await docker_api_request("POST", f"/containers/{name}/start", timeout=45)
        if response.status_code in {204, 304}:
            return
    else:
        raise ValueError(f"Unsupported container action: {action}")
    if response.status_code >= 400:
        raise RuntimeError(f"Docker {action} failed for {name}: {response.text}")


def docker_image_name_for(service_name: str) -> str:
    if service_name == "paddleocr-vlm-server":
        return f"ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-genai-{VLM_BACKEND}-server:{VLM_IMAGE_TAG_SUFFIX}{VLM_IMAGE_DIGEST}"
    if service_name == "paddleocr-vl-api":
        return f"ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-vl:{API_IMAGE_TAG_SUFFIX}{API_IMAGE_DIGEST}"
    if service_name == "paddleocr-ocr-api":
        return "pandocr-ocr-api:latest"
    if service_name == "unlimited-ocr-api":
        return "pandocr-unlimited-ocr-transformers:latest"
    if service_name == "unlimited-ocr-sglang":
        return "pandocr-unlimited-ocr-sglang:latest"
    if service_name == "ovisocr2-api":
        return "pandocr-ovisocr2:latest"
    if service_name == "hpd-parsing-server":
        return HPD_PARSING_IMAGE
    if service_name == "hpd-parsing-api":
        return "pandocr-hpd-parsing-adapter:latest"
    raise ValueError(f"Unknown service image: {service_name}")


def split_docker_image_ref(image: str) -> tuple[str, str]:
    if "@sha256:" in image:
        return image, ""
    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon > last_slash:
        return image[:last_colon], image[last_colon + 1 :]
    return image, "latest"


async def docker_image_exists(image: str) -> bool:
    if not model_control_available():
        return False
    response = await docker_api_request("GET", f"/images/{quote(image, safe='')}/json")
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return True


async def docker_pull_image(image: str) -> None:
    if await docker_image_exists(image):
        return
    repository, tag = split_docker_image_ref(image)
    path = f"/images/create?fromImage={quote(repository, safe='')}"
    if tag:
        path += f"&tag={quote(tag, safe='')}"
    response = await docker_api_request("POST", path, timeout=3600)
    if response.status_code >= 400:
        raise RuntimeError(f"Docker pull failed for {image}: {response.text}")


def dockerfile_path_for(service_name: str) -> Path:
    dockerfile_names = {
        "paddleocr-ocr-api": "Dockerfile.ocr",
        "unlimited-ocr-api": "Dockerfile.unlimited-ocr",
        "unlimited-ocr-sglang": "Dockerfile.unlimited-ocr-sglang",
        "ovisocr2-api": "Dockerfile.ovisocr2",
        "hpd-parsing-api": "Dockerfile.hpd-parsing-adapter",
    }
    dockerfile_name = dockerfile_names.get(service_name)
    if not dockerfile_name:
        raise ValueError(f"No Dockerfile for {service_name}")
    dockerfile_path = PROJECT_ROOT / dockerfile_name
    if not dockerfile_path.is_file():
        raise RuntimeError(f"Missing {dockerfile_name}; cannot build {service_name} from the WebUI.")
    return dockerfile_path


def docker_build_args_for(service_name: str) -> dict[str, str]:
    if service_name == "paddleocr-ocr-api":
        return {"API_IMAGE_TAG_SUFFIX": API_IMAGE_TAG_SUFFIX, "API_IMAGE_DIGEST": API_IMAGE_DIGEST}
    if service_name == "unlimited-ocr-sglang":
        return {"UNLIMITED_OCR_SGLANG_WHEEL_URL": UNLIMITED_OCR_SGLANG_WHEEL_URL}
    return {}


def make_docker_build_context(service_name: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        dockerfile_data = dockerfile_path_for(service_name).read_bytes()
        dockerfile_info = tarfile.TarInfo("Dockerfile")
        dockerfile_info.size = len(dockerfile_data)
        tar.addfile(dockerfile_info, io.BytesIO(dockerfile_data))

        adapter_names = {
            "unlimited-ocr-api": "unlimited_ocr_adapter.py",
            "unlimited-ocr-sglang": "unlimited_ocr_adapter.py",
            "ovisocr2-api": "ovisocr2_adapter.py",
            "hpd-parsing-api": "hpd_parsing_adapter.py",
        }
        if service_name in adapter_names:
            adapter_name = adapter_names[service_name]
            adapter_path = PROJECT_ROOT / adapter_name
            adapter_data = adapter_path.read_bytes()
            adapter_info = tarfile.TarInfo(adapter_name)
            adapter_info.size = len(adapter_data)
            tar.addfile(adapter_info, io.BytesIO(adapter_data))

    return buffer.getvalue()


async def docker_build_image(service_name: str) -> None:
    image = docker_image_name_for(service_name)
    if await docker_image_exists(image):
        return
    context = make_docker_build_context(service_name)
    query = f"/build?t={quote(image, safe='')}&pull=1&rm=1"
    build_args = docker_build_args_for(service_name)
    if build_args:
        query += f"&buildargs={quote(json.dumps(build_args), safe='')}"
    response = await docker_api_request(
        "POST",
        query,
        timeout=7200,
        content=context,
        headers={"Content-Type": "application/x-tar"},
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Docker build failed for {image}: {response.text}")
    for line in response.text.splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict) and event.get("error"):
            raise RuntimeError(f"Docker build failed for {image}: {event.get('error')}")


async def docker_inspect_self() -> dict:
    response = await docker_api_request("GET", "/containers/pandocr-web/json")
    if response.status_code == 404:
        return {}
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


async def docker_network_name() -> str:
    data = await docker_inspect_self()
    networks = ((data.get("NetworkSettings") or {}).get("Networks") or {})
    if not isinstance(networks, dict) or not networks:
        return "paddleocr-vl-webui_paddleocr-network"
    for name in networks:
        if "paddleocr-network" in name:
            return name
    return next(iter(networks))


async def docker_host_repo_root() -> str:
    data = await docker_inspect_self()
    mounts = data.get("Mounts") or []
    for mount in mounts:
        if mount.get("Destination") == "/app/static" and mount.get("Source"):
            return str(Path(mount["Source"]).parent)
        if mount.get("Destination") == "/app/server.py" and mount.get("Source"):
            return str(Path(mount["Source"]).parent)
        if mount.get("Destination") == "/app/data" and mount.get("Source"):
            # Release images only bind the repository's data directory into
            # the WebUI container.  Its host-side parent is still the checkout
            # root needed when the controller creates model containers.
            return str(Path(mount["Source"]).parent)
    return str(PROJECT_ROOT)


def bind_path(host_root: str, name: str, target: str, readonly: bool = False) -> str:
    suffix = ":ro" if readonly else ""
    return f"{host_root}/{name}:{target}{suffix}"


def model_device_requests() -> list[dict]:
    return [
        {
            "Driver": "nvidia",
            "DeviceIDs": [PANDOCR_GPU_DEVICE_ID],
            "Capabilities": [["gpu"]],
        }
    ]


def healthcheck(test: str, start_period_seconds: int) -> dict:
    return {
        "Test": ["CMD-SHELL", test],
        "Interval": 30_000_000_000,
        "Timeout": 10_000_000_000,
        "Retries": 5,
        "StartPeriod": start_period_seconds * 1_000_000_000,
    }


def host_config(
    *,
    network_name: str,
    binds: list[str],
    port_bindings: dict | None = None,
    shm_size: int | None = None,
) -> dict:
    config = {
        "Binds": binds,
        "NetworkMode": network_name,
        "RestartPolicy": {"Name": "unless-stopped"},
        "DeviceRequests": model_device_requests(),
    }
    if port_bindings:
        config["PortBindings"] = port_bindings
    if shm_size:
        config["ShmSize"] = shm_size
    return config


async def docker_create_container(name: str, payload: dict) -> None:
    existing = await inspect_container(name)
    if existing["exists"]:
        return
    response = await docker_api_request(
        "POST",
        f"/containers/create?name={quote(name, safe='')}",
        timeout=120,
        json=payload,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Docker create failed for {name}: {response.text}")


def container_payload_for(service_name: str, *, host_root: str, network_name: str) -> dict:
    image = docker_image_name_for(service_name)
    if service_name == "paddleocr-vlm-server":
        return {
            "Image": image,
            "Cmd": ["/bin/bash", "/home/paddleocr/start-vlm.sh"],
            "Env": [
                "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True",
                f"PADDLEOCR_VL_MODEL_NAME={PADDLEOCR_VL_MODEL_NAME}",
                f"PANDOCR_GPU_DEVICE_ID={PANDOCR_GPU_DEVICE_ID}",
                f"PANDOCR_VLLM_MIN_TOTAL_MIB={PANDOCR_VLLM_MIN_TOTAL_MIB}",
                f"PANDOCR_VLLM_MIN_REQUIRED_MIB={PANDOCR_VLLM_MIN_REQUIRED_MIB}",
                f"PANDOCR_VLLM_RESERVE_MIB={PANDOCR_VLLM_RESERVE_MIB}",
                f"PANDOCR_VLLM_MAX_RATIO={PANDOCR_VLLM_MAX_RATIO}",
            ],
            "User": "root",
            "HostConfig": host_config(
                network_name=network_name,
                binds=[
                    bind_path(host_root, "model_cache", "/home/paddleocr/.paddlex"),
                    bind_path(host_root, "model_cache_ocr", "/home/paddleocr/.paddleocr"),
                    bind_path(host_root, "start-vlm.sh", "/home/paddleocr/start-vlm.sh", readonly=True),
                ],
            ),
            "Healthcheck": healthcheck("curl -f http://localhost:8080/health || exit 1", 900),
        }
    if service_name == "paddleocr-vl-api":
        return {
            "Image": image,
            "Cmd": ["/bin/bash", "-c", f"paddlex --serve --pipeline /home/paddleocr/pipeline_config_{VLM_BACKEND}.yaml"],
            "Env": [
                f"VLM_BACKEND={VLM_BACKEND}",
                "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True",
            ],
            "User": "root",
            "ExposedPorts": {"8080/tcp": {}},
            "HostConfig": host_config(
                network_name=network_name,
                binds=[
                    bind_path(host_root, "model_cache", "/home/paddleocr/.paddlex"),
                    bind_path(host_root, "model_cache_ocr", "/home/paddleocr/.paddleocr"),
                    bind_path(host_root, "pipeline_config_vllm.yaml", "/home/paddleocr/pipeline_config_vllm.yaml", readonly=True),
                ],
                port_bindings={"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8081"}]},
            ),
            "Healthcheck": healthcheck("curl -f http://localhost:8080/health || exit 1", 300),
        }
    if service_name == "paddleocr-ocr-api":
        return {
            "Image": image,
            "Cmd": ["/bin/bash", "-c", "paddlex --serve --pipeline /home/paddleocr/pipeline_config_ocr_v6.yaml --host 0.0.0.0 --port 8080"],
            "Env": ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True"],
            "User": "root",
            "ExposedPorts": {"8080/tcp": {}},
            "HostConfig": host_config(
                network_name=network_name,
                binds=[
                    bind_path(host_root, "model_cache_ppocrv6", "/home/paddleocr/.paddlex"),
                    bind_path(host_root, "model_cache_ppocrv6_ocr", "/home/paddleocr/.paddleocr"),
                    bind_path(host_root, "pipeline_config_ocr_v6.yaml", "/home/paddleocr/pipeline_config_ocr_v6.yaml", readonly=True),
                ],
                port_bindings={"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8082"}]},
            ),
            "Healthcheck": healthcheck("curl -f http://localhost:8080/health || exit 1", 300),
        }
    if service_name == "unlimited-ocr-api":
        return {
            "Image": image,
            "Cmd": ["uvicorn", "unlimited_ocr_adapter:app", "--host", "0.0.0.0", "--port", "8080"],
            "Env": [
                "HF_HOME=/root/.cache/huggingface",
                f"UNLIMITED_OCR_BACKEND={unlimited_ocr_runtime_backend}",
                f"UNLIMITED_OCR_PRELOAD={UNLIMITED_OCR_PRELOAD}",
                "UNLIMITED_OCR_SGLANG_URL=http://unlimited-ocr-sglang:10000",
                f"UNLIMITED_OCR_MODEL_NAME={UNLIMITED_OCR_MODEL_NAME}",
                f"UNLIMITED_OCR_MODEL_REVISION={UNLIMITED_OCR_MODEL_REVISION}",
                f"UNLIMITED_OCR_SERVED_MODEL_NAME={UNLIMITED_OCR_SERVED_MODEL_NAME}",
                f"UNLIMITED_OCR_REQUEST_TIMEOUT={UNLIMITED_OCR_REQUEST_TIMEOUT}",
                f"UNLIMITED_OCR_PDF_DPI={UNLIMITED_OCR_PDF_DPI}",
                f"UNLIMITED_OCR_MAX_PAGES_PER_REQUEST={UNLIMITED_OCR_MAX_PAGES_PER_REQUEST}",
                f"UNLIMITED_OCR_MAX_RENDER_PIXELS={UNLIMITED_OCR_MAX_RENDER_PIXELS}",
                f"UNLIMITED_OCR_SINGLE_IMAGE_MODE={UNLIMITED_OCR_SINGLE_IMAGE_MODE}",
                f"UNLIMITED_OCR_MULTI_IMAGE_MODE={UNLIMITED_OCR_MULTI_IMAGE_MODE}",
                f"UNLIMITED_OCR_MAX_TOKENS={UNLIMITED_OCR_MAX_TOKENS}",
                f"UNLIMITED_OCR_SGLANG_MAX_TOKENS={UNLIMITED_OCR_SGLANG_MAX_TOKENS}",
                "PANDOCR_RUNTIME_SETTINGS_FILE=/app/data/runtime-settings.json",
            ],
            "User": "root",
            "ExposedPorts": {"8080/tcp": {}},
            "HostConfig": host_config(
                network_name=network_name,
                binds=[
                    bind_path(host_root, "model_cache_unlimited_ocr", "/root/.cache/huggingface"),
                    bind_path(host_root, "data", "/app/data"),
                ],
                port_bindings={"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": UNLIMITED_OCR_API_PORT}]},
            ),
            "Healthcheck": healthcheck("curl -f http://localhost:8080/health || exit 1", 60),
        }
    if service_name == "unlimited-ocr-sglang":
        return {
            "Image": image,
            "Cmd": [
                "python3",
                "-m",
                "sglang.launch_server",
                "--model",
                UNLIMITED_OCR_MODEL_NAME,
                "--revision",
                UNLIMITED_OCR_MODEL_REVISION,
                "--served-model-name",
                UNLIMITED_OCR_SERVED_MODEL_NAME,
                "--attention-backend",
                UNLIMITED_OCR_ATTENTION_BACKEND,
                "--page-size",
                UNLIMITED_OCR_PAGE_SIZE,
                "--mem-fraction-static",
                UNLIMITED_OCR_MEM_FRACTION_STATIC,
                "--context-length",
                UNLIMITED_OCR_CONTEXT_LENGTH,
                "--enable-custom-logit-processor",
                "--disable-overlap-schedule",
                "--skip-server-warmup",
                "--host",
                "0.0.0.0",
                "--port",
                "10000",
            ],
            "Env": [
                "HF_HOME=/root/.cache/huggingface",
                f"CUDA_VISIBLE_DEVICES={PANDOCR_GPU_DEVICE_ID}",
            ],
            "User": "root",
            "ExposedPorts": {"10000/tcp": {}},
            "HostConfig": host_config(
                network_name=network_name,
                binds=[bind_path(host_root, "model_cache_unlimited_ocr", "/root/.cache/huggingface")],
                port_bindings={"10000/tcp": [{"HostIp": "127.0.0.1", "HostPort": UNLIMITED_OCR_SGLANG_PORT}]},
                shm_size=34_359_738_368,
            ),
            "Healthcheck": healthcheck("curl -f http://localhost:10000/health || exit 1", 900),
        }
    if service_name == "ovisocr2-api":
        return {
            "Image": image,
            "Cmd": ["uvicorn", "ovisocr2_adapter:app", "--host", "0.0.0.0", "--port", "8080"],
            "Env": [
                "HF_HOME=/root/.cache/huggingface",
                f"CUDA_VISIBLE_DEVICES={PANDOCR_GPU_DEVICE_ID}",
                "VLLM_USE_FLASHINFER_SAMPLER=0",
                f"OVISOCR2_MODEL_NAME={OVISOCR2_MODEL_NAME}",
                f"OVISOCR2_MODEL_REVISION={OVISOCR2_MODEL_REVISION}",
                f"OVISOCR2_KV_CACHE_MEMORY_MB={OVISOCR2_KV_CACHE_MEMORY_MB}",
                f"OVISOCR2_STARTUP_MEMORY_FRACTION={OVISOCR2_STARTUP_MEMORY_FRACTION}",
                f"OVISOCR2_MAX_MODEL_LEN={OVISOCR2_MAX_MODEL_LEN}",
                f"OVISOCR2_MAX_NUM_SEQS={OVISOCR2_MAX_NUM_SEQS}",
                f"OVISOCR2_MAX_TOKENS={OVISOCR2_MAX_TOKENS}",
                f"OVISOCR2_PDF_DPI={OVISOCR2_PDF_DPI}",
                f"OVISOCR2_MAX_PAGES_PER_REQUEST={OVISOCR2_MAX_PAGES_PER_REQUEST}",
                f"OVISOCR2_GDN_PREFILL_BACKEND={OVISOCR2_GDN_PREFILL_BACKEND}",
            ],
            "User": "root",
            "ExposedPorts": {"8080/tcp": {}},
            "HostConfig": host_config(
                network_name=network_name,
                binds=[
                    bind_path(host_root, "model_cache_ovisocr2", "/root/.cache/huggingface"),
                    bind_path(host_root, "model_cache_ovisocr2_vllm", "/root/.cache/vllm"),
                ],
                port_bindings={"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": OVISOCR2_API_PORT}]},
                shm_size=17_179_869_184,
            ),
            "Healthcheck": healthcheck("curl -f http://localhost:8080/health || exit 1", 900),
        }
    if service_name == "hpd-parsing-server":
        return {
            "Image": image,
            "User": "root",
            "Entrypoint": ["/bin/bash"],
            "Cmd": ["/home/hpd/start-pandocr.sh"],
            "Env": [
                "HF_HOME=/home/hpd/.cache/huggingface",
                "MAX_PATCHES_WITH_RESIZE=true",
                f"CUDA_VISIBLE_DEVICES={PANDOCR_GPU_DEVICE_ID}",
                f"HPD_PARSING_MODEL_NAME={HPD_PARSING_MODEL_NAME}",
                f"HPD_PARSING_SERVED_MODEL_NAME={HPD_PARSING_SERVED_MODEL_NAME}",
                f"HPD_PARSING_MAX_MODEL_LEN={HPD_PARSING_MAX_MODEL_LEN}",
                f"HPD_PARSING_GPU_MEMORY_UTILIZATION={HPD_PARSING_GPU_MEMORY_UTILIZATION}",
                f"HPD_PARSING_GPU_MEMORY_TARGET_MIB={HPD_PARSING_GPU_MEMORY_TARGET_MIB}",
            ],
            "ExposedPorts": {"8118/tcp": {}},
            "HostConfig": host_config(
                network_name=network_name,
                binds=[
                    bind_path(host_root, "model_cache_hpd_parsing", "/home/hpd/.cache/huggingface"),
                    bind_path(host_root, "start-hpd-parsing.sh", "/home/hpd/start-pandocr.sh", readonly=True),
                ],
                shm_size=34_359_738_368,
            ),
            "Healthcheck": healthcheck(
                "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8118/health')\" || exit 1",
                900,
            ),
        }
    if service_name == "hpd-parsing-api":
        return {
            "Image": image,
            "Cmd": ["uvicorn", "hpd_parsing_adapter:app", "--host", "0.0.0.0", "--port", "8080"],
            "Env": [
                "HPD_PARSING_SERVER_URL=http://hpd-parsing-server:8118",
                f"HPD_PARSING_SERVED_MODEL_NAME={HPD_PARSING_SERVED_MODEL_NAME}",
                f"HPD_PARSING_MAX_TOKENS={HPD_PARSING_MAX_TOKENS}",
                f"HPD_PARSING_PDF_DPI={HPD_PARSING_PDF_DPI}",
                f"HPD_PARSING_MAX_PAGES_PER_REQUEST={HPD_PARSING_MAX_PAGES_PER_REQUEST}",
                f"HPD_PARSING_MAX_CONCURRENCY={HPD_PARSING_MAX_CONCURRENCY}",
                f"HPD_PARSING_REQUEST_TIMEOUT={HPD_PARSING_REQUEST_TIMEOUT}",
            ],
            "ExposedPorts": {"8080/tcp": {}},
            "HostConfig": host_config(
                network_name=network_name,
                binds=[],
                port_bindings={"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": HPD_PARSING_API_PORT}]},
            ),
            "Healthcheck": healthcheck(
                "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/health')\" || exit 1",
                30,
            ),
        }
    raise ValueError(f"Unknown deploy service: {service_name}")


async def ensure_runtime_service_created(service_name: str) -> None:
    if service_name in {"paddleocr-vlm-server", "paddleocr-vl-api", "hpd-parsing-server"}:
        await docker_pull_image(docker_image_name_for(service_name))
    else:
        await docker_build_image(service_name)
    network_name = await docker_network_name()
    host_root = await docker_host_repo_root()
    await docker_create_container(
        service_name,
        container_payload_for(service_name, host_root=host_root, network_name=network_name),
    )


def services_for_model_deploy(model_id: str, backend: str | None = None) -> list[str]:
    if model_id == "paddleocr-vl-1.6":
        return ["paddleocr-vlm-server", "paddleocr-vl-api"]
    if model_id == "pp-ocrv6":
        return ["paddleocr-ocr-api"]
    if model_id == "unlimited-ocr":
        services = ["unlimited-ocr-api"]
        if normalize_unlimited_ocr_backend(backend, unlimited_ocr_runtime_backend) == "sglang":
            services.insert(0, "unlimited-ocr-sglang")
        return services
    if model_id == "ovisocr2":
        return ["ovisocr2-api"]
    if model_id == "hpd-parsing":
        return ["hpd-parsing-server", "hpd-parsing-api"]
    raise ValueError(f"Unknown model id: {model_id}")


async def ensure_model_runtime_created(model_id: str, backend: str | None = None) -> None:
    for service_name in services_for_model_deploy(model_id, backend):
        await ensure_runtime_service_created(service_name)


async def fetch_http_health(url: str) -> tuple[bool, dict]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
        data = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            data = {}
        return 200 <= response.status_code < 300, data
    except Exception:
        return False, {}


async def check_http_health(url: str) -> bool:
    ok, _ = await fetch_http_health(url)
    return ok


def model_health_ready_state(model_id: str, health_ok: bool, health_data: dict) -> tuple[bool, str]:
    if not health_ok:
        return False, "unknown"
    if model_id == "unlimited-ocr":
        if unlimited_ocr_runtime_backend == "sglang":
            sglang = health_data.get("sglang") if isinstance(health_data.get("sglang"), dict) else {}
            return (True, "ready") if sglang.get("ready") else (False, "starting")

        transformers = health_data.get("transformers") if isinstance(health_data.get("transformers"), dict) else health_data
        if transformers.get("modelError"):
            return False, "error"
        if transformers.get("preloadEnabled"):
            if transformers.get("modelLoaded"):
                return True, "ready"
            if transformers.get("modelLoading"):
                return False, "warming"
            return False, "starting"
    return True, "ready"


async def enrich_unlimited_ocr_runtime_status(model_id: str, status: dict) -> dict:
    if model_id != "unlimited-ocr":
        return status
    status["unlimitedOcrBackend"] = unlimited_ocr_runtime_backend
    status["unlimitedOcrSupportedBackends"] = sorted(UNLIMITED_OCR_SUPPORTED_BACKENDS)
    if model_control_available():
        sglang_container = await inspect_container("unlimited-ocr-sglang")
        status["sglangContainer"] = sglang_container
        # The SGLang worker is intentionally not part of the adapter's required
        # container list because the Transformers backend does not need it.  It
        # still represents a running Unlimited-OCR runtime and must not be
        # hidden if the adapter stopped unexpectedly.
        if sglang_container.get("running") and not status.get("running"):
            status["running"] = True
            status["ready"] = False
            status["state"] = "partial"
    return status


async def model_runtime_status(model_id: str) -> dict:
    if MODEL_CONTROL_MODE == "remote":
        payload = await controller_api_request("GET", "/model-runtime")
        status = (payload.get("models") or {}).get(model_id)
        if not isinstance(status, dict):
            raise HTTPException(status_code=502, detail=f"Model controller omitted runtime state for {model_id}")
        return status
    config = MODEL_RUNTIME_CONFIG[model_id]
    containers = [await inspect_container(name) for name in config["containers"]]
    if not model_control_available():
        health_ok, health_data = await fetch_http_health(config["health_url"])
        ready, health_state = model_health_ready_state(model_id, health_ok, health_data)
        return await enrich_unlimited_ocr_runtime_status(model_id, {
            "id": model_id,
            "containers": containers,
            "running": health_ok,
            "ready": ready,
            "state": health_state if health_ok else "unknown",
            "healthUrl": config["health_url"],
            "health": health_data,
        })

    any_running = any(container["running"] for container in containers)
    all_running = all(container["running"] for container in containers)
    any_missing = any(not container["exists"] for container in containers)
    health_ok, health_data = await fetch_http_health(config["health_url"]) if all_running else (False, {})
    ready, health_state = model_health_ready_state(model_id, health_ok, health_data)

    if any_missing:
        state = "missing"
    elif health_ok:
        state = health_state
    elif any_running:
        state = "starting" if all_running else "partial"
    else:
        state = "stopped"

    return await enrich_unlimited_ocr_runtime_status(model_id, {
        "id": model_id,
        "containers": containers,
        "running": any_running,
        "ready": ready if all_running else False,
        "state": state,
        "healthUrl": config["health_url"],
        "health": health_data,
    })


async def runtime_exclusivity_snapshot(statuses: dict[str, dict] | None = None) -> dict:
    """Return logical running/ready models, including disabled residual containers."""
    resolved_statuses = statuses
    if resolved_statuses is None:
        resolved_statuses = {
            model_id: await model_runtime_status(model_id)
            for model_id in MODEL_RUNTIME_CONFIG
        }

    running_model_ids = [
        model_id
        for model_id, status in resolved_statuses.items()
        if isinstance(status, dict) and status.get("running")
    ]
    ready_model_ids = [
        model_id
        for model_id, status in resolved_statuses.items()
        if isinstance(status, dict) and status.get("ready")
    ]

    # model_runtime_status already covers enabled groups.  Inspect only static
    # groups omitted from the current catalog so old deployments cannot evade
    # the logical-model exclusivity invariant.
    if model_control_available():
        for model_id in MODEL_RUNTIME_CONTAINER_GROUPS:
            if model_id in resolved_statuses:
                continue
            for container_name in model_runtime_container_names(model_id):
                if (await inspect_container(container_name)).get("running"):
                    running_model_ids.append(model_id)
                    break

    return {
        "runningModelIds": running_model_ids,
        "readyModelIds": ready_model_ids,
        "exclusivityViolation": len(running_model_ids) > 1 or len(ready_model_ids) > 1,
    }


async def build_model_runtime_payload() -> dict:
    if MODEL_CONTROL_MODE == "remote":
        payload = await controller_api_request("GET", "/model-runtime")
        payload["controlMode"] = "remote"
        models = payload.get("models") if isinstance(payload.get("models"), dict) else {}
        payload.setdefault(
            "readyModelIds",
            [model_id for model_id, status in models.items() if isinstance(status, dict) and status.get("ready")],
        )
        payload.setdefault(
            "runningModelIds",
            [model_id for model_id, status in models.items() if isinstance(status, dict) and status.get("running")],
        )
        payload.setdefault(
            "exclusivityViolation",
            len(payload["runningModelIds"]) > 1 or len(payload["readyModelIds"]) > 1,
        )
        controller_lease_count = int(payload.get("controllerOcrLeaseCount") or 0)
        payload["controllerOcrLeaseCount"] = controller_lease_count
        payload["ocrActiveCount"] = max(ocr_active_count, controller_lease_count)
        payload["maxConcurrentOcr"] = MAX_CONCURRENT_OCR
        return payload
    models = {
        model_id: await model_runtime_status(model_id)
        for model_id in MODEL_RUNTIME_CONFIG
    }
    exclusivity = await runtime_exclusivity_snapshot(models)
    ready_models = exclusivity["readyModelIds"]
    running_models = exclusivity["runningModelIds"]
    active_model = ready_models[0] if ready_models else (running_models[0] if running_models else None)
    controller_lease_count = controller_ocr_lease_count()
    control_available = model_control_available()
    gpu_preflight = (
        await probe_gpu_preflight(active_model or DEFAULT_RUNTIME_MODEL_ID)
        if control_available
        else None
    )
    return {
        "controlMode": MODEL_CONTROL_MODE,
        "controlAvailable": control_available,
        "activeModelId": active_model,
        "defaultModelId": DEFAULT_RUNTIME_MODEL_ID,
        "unlimitedOcrBackend": unlimited_ocr_runtime_backend,
        "unlimitedOcrSupportedBackends": sorted(UNLIMITED_OCR_SUPPORTED_BACKENDS),
        "operation": dict(model_runtime_operation),
        "ocrActiveCount": ocr_active_count + controller_lease_count,
        "controllerOcrLeaseCount": controller_lease_count,
        "controllerOcrLeaseStore": controller_ocr_lease_store_status(),
        "maxConcurrentOcr": MAX_CONCURRENT_OCR,
        "runningModelIds": running_models,
        "readyModelIds": ready_models,
        "exclusivityViolation": exclusivity["exclusivityViolation"],
        "gpuPreflight": gpu_preflight,
        "models": models,
    }


def set_model_runtime_operation(state: str, message: str = "", target_model_id: str | None = None) -> None:
    now = time.time()
    if target_model_id:
        model_runtime_operation["targetModelId"] = target_model_id
    model_runtime_operation["state"] = state
    model_runtime_operation["message"] = message
    model_runtime_operation["updatedAt"] = now
    if state == "switching":
        model_runtime_operation["startedAt"] = now
        model_runtime_operation["diagnostics"] = None


def controller_ocr_lease_store_status() -> dict:
    if not CONTROLLER_OCR_LEASE_STORE_ENABLED:
        state = "disabled"
    elif not controller_ocr_lease_store_loaded:
        state = "error" if controller_ocr_lease_store_error else "not-loaded"
    elif controller_ocr_lease_store_error:
        state = "error"
    else:
        state = "ready"
    return {
        "enabled": CONTROLLER_OCR_LEASE_STORE_ENABLED,
        "state": state,
        "healthy": state in {"disabled", "ready"},
        "error": controller_ocr_lease_store_error,
    }


def _reject_duplicate_controller_lease_json_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key in lease store: {key!r}")
        result[key] = value
    return result


def _validate_controller_ocr_lease(lease_id: str, raw_lease: object) -> dict:
    if not isinstance(lease_id, str) or not lease_id:
        raise ValueError("lease id must be a non-empty string")
    if not isinstance(raw_lease, dict):
        raise ValueError(f"lease {lease_id!r} must be an object")
    if raw_lease.get("leaseId") != lease_id:
        raise ValueError(f"lease {lease_id!r} has a mismatched leaseId")
    model_id = raw_lease.get("modelId")
    if not isinstance(model_id, str) or not model_id:
        raise ValueError(f"lease {lease_id!r} has an invalid modelId")

    created_at = raw_lease.get("createdAt")
    if (
        isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not math.isfinite(float(created_at))
    ):
        raise ValueError(f"lease {lease_id!r} has an invalid createdAt")
    expires_at = raw_lease.get("expiresAt")
    if expires_at is not None and (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
        or not math.isfinite(float(expires_at))
    ):
        raise ValueError(f"lease {lease_id!r} has an invalid expiresAt")
    if expires_at is not None and float(expires_at) < float(created_at):
        raise ValueError(f"lease {lease_id!r} expires before it was created")

    return {
        "leaseId": lease_id,
        "modelId": model_id,
        "createdAt": float(created_at),
        "expiresAt": float(expires_at) if expires_at is not None else None,
    }


def _controller_ocr_lease_store_payload(leases: dict[str, dict]) -> dict:
    return {
        "version": CONTROLLER_OCR_LEASE_STORE_VERSION,
        "leases": {lease_id: dict(lease) for lease_id, lease in leases.items()},
    }


def _persist_controller_ocr_leases(leases: dict[str, dict]) -> None:
    """Durably replace the controller-only lease snapshot before publishing it in memory."""
    global controller_ocr_lease_store_error
    if not CONTROLLER_OCR_LEASE_STORE_ENABLED:
        return
    if not controller_ocr_lease_store_loaded:
        raise RuntimeError("controller OCR lease store was not loaded successfully")

    path = CONTROLLER_OCR_LEASE_STORE_FILE
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            _controller_ocr_lease_store_payload(leases),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            with contextlib.suppress(OSError):
                os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        controller_ocr_lease_store_error = None
    except Exception as error:
        controller_ocr_lease_store_error = (
            f"Failed to persist controller OCR leases: {type(error).__name__}: {error}"
        )
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise


def _commit_controller_ocr_leases(leases: dict[str, dict]) -> None:
    next_leases = {lease_id: dict(lease) for lease_id, lease in leases.items()}
    _persist_controller_ocr_leases(next_leases)
    controller_ocr_leases.clear()
    controller_ocr_leases.update(next_leases)


def load_controller_ocr_leases() -> bool:
    """Restore controller leases before any startup model scheduling is allowed."""
    global controller_ocr_lease_store_error, controller_ocr_lease_store_loaded
    if not CONTROLLER_OCR_LEASE_STORE_ENABLED:
        controller_ocr_lease_store_loaded = True
        controller_ocr_lease_store_error = None
        return True

    try:
        if CONTROLLER_OCR_LEASE_STORE_FILE.exists():
            raw_store = json.loads(
                CONTROLLER_OCR_LEASE_STORE_FILE.read_text(encoding="utf-8-sig"),
                object_pairs_hook=_reject_duplicate_controller_lease_json_keys,
            )
            if not isinstance(raw_store, dict):
                raise ValueError("lease store root must be an object")
            if (
                isinstance(raw_store.get("version"), bool)
                or raw_store.get("version") != CONTROLLER_OCR_LEASE_STORE_VERSION
            ):
                raise ValueError("unsupported controller OCR lease store version")
            raw_leases = raw_store.get("leases")
            if not isinstance(raw_leases, dict):
                raise ValueError("lease store leases must be an object")
            restored = {
                lease_id: _validate_controller_ocr_lease(lease_id, raw_lease)
                for lease_id, raw_lease in raw_leases.items()
            }
        else:
            restored = {}
    except Exception as error:
        controller_ocr_lease_store_loaded = False
        controller_ocr_lease_store_error = (
            f"Failed to load controller OCR leases: {type(error).__name__}: {error}"
        )
        logger.error(
            "Controller OCR lease store is unavailable; model operations remain fail-closed: %s",
            CONTROLLER_OCR_LEASE_STORE_FILE,
            exc_info=True,
        )
        return False

    controller_ocr_leases.clear()
    controller_ocr_leases.update(restored)
    controller_ocr_lease_store_loaded = True
    controller_ocr_lease_store_error = None
    if not prune_controller_ocr_leases():
        return False
    logger.info("Restored %d controller OCR lease(s)", len(controller_ocr_leases))
    return True


def require_controller_ocr_lease_store_ready() -> None:
    if CONTROLLER_OCR_LEASE_STORE_ENABLED and not controller_ocr_lease_store_loaded:
        detail = controller_ocr_lease_store_error or "Controller OCR lease store is not loaded"
        raise HTTPException(
            status_code=503,
            detail=f"{detail}. Model operations are fail-closed until the store is repaired.",
        )


def prune_controller_ocr_leases(now: float | None = None) -> bool:
    if CONTROLLER_OCR_LEASE_STORE_ENABLED and not controller_ocr_lease_store_loaded:
        return False
    current = time.time() if now is None else now
    expired = [
        lease_id
        for lease_id, lease in controller_ocr_leases.items()
        if lease.get("expiresAt") is not None and float(lease["expiresAt"]) <= current
    ]
    if not expired:
        return True
    next_leases = {
        lease_id: lease
        for lease_id, lease in controller_ocr_leases.items()
        if lease_id not in expired
    }
    try:
        _commit_controller_ocr_leases(next_leases)
    except Exception:
        logger.exception(
            "Failed to persist pruning of %d expired controller OCR lease(s); retaining them fail-closed",
            len(expired),
        )
        return False
    logger.warning("Expired %d stale controller OCR lease(s)", len(expired))
    return True


def controller_ocr_lease_count() -> int:
    prune_controller_ocr_leases()
    return len(controller_ocr_leases)


async def acquire_controller_ocr_lease(model_id: str) -> dict:
    """Atomically reserve the active model against controller switches."""
    if model_id not in MODEL_RUNTIME_CONFIG:
        raise HTTPException(status_code=400, detail="Unknown model id")

    async with model_runtime_lock:
        require_controller_ocr_lease_store_ready()
        if not prune_controller_ocr_leases():
            raise HTTPException(
                status_code=503,
                detail="Controller OCR lease cleanup could not be persisted; OCR remains fail-closed.",
            )
        if model_runtime_task and not model_runtime_task.done():
            raise HTTPException(status_code=409, detail="Model runtime is switching. Wait before starting OCR.")
        if unlimited_ocr_backend_task and not unlimited_ocr_backend_task.done():
            raise HTTPException(status_code=409, detail="Unlimited-OCR backend is switching. Wait before starting OCR.")
        if model_runtime_operation.get("state") == "switching":
            raise HTTPException(status_code=409, detail="Model runtime is switching. Wait before starting OCR.")
        leased_model_ids = {str(lease.get("modelId") or "") for lease in controller_ocr_leases.values()}
        if leased_model_ids and leased_model_ids != {model_id}:
            raise HTTPException(status_code=409, detail="Another model has active OCR work.")
        if len(controller_ocr_leases) >= MAX_CONCURRENT_OCR:
            raise HTTPException(status_code=429, detail="The controller OCR concurrency limit is reached.")

        statuses = {
            runtime_model_id: await model_runtime_status(runtime_model_id)
            for runtime_model_id in MODEL_RUNTIME_CONFIG
        }
        exclusivity = await runtime_exclusivity_snapshot(statuses)
        if exclusivity["exclusivityViolation"]:
            running = ", ".join(exclusivity["runningModelIds"]) or "none"
            ready = ", ".join(exclusivity["readyModelIds"]) or "none"
            raise HTTPException(
                status_code=409,
                detail=f"Model exclusivity violation: running={running}; ready={ready}",
            )
        status = statuses[model_id]
        if not status.get("ready"):
            raise HTTPException(status_code=503, detail=f"{model_id} is not ready")
        if exclusivity["runningModelIds"] != [model_id] or exclusivity["readyModelIds"] != [model_id]:
            raise HTTPException(
                status_code=503,
                detail=f"{model_id} is not the unique running and ready model",
            )

        lease_id = uuid.uuid4().hex
        created_at = time.time()
        expires_at = (
            created_at + CONTROLLER_OCR_LEASE_TTL_SECONDS
            if CONTROLLER_OCR_LEASE_TTL_SECONDS > 0
            else None
        )
        lease = {
            "leaseId": lease_id,
            "modelId": model_id,
            "createdAt": created_at,
            "expiresAt": expires_at,
        }
        next_leases = dict(controller_ocr_leases)
        next_leases[lease_id] = lease
        try:
            _commit_controller_ocr_leases(next_leases)
        except Exception as error:
            logger.exception("Failed to persist controller OCR lease acquisition")
            raise HTTPException(
                status_code=503,
                detail="Controller OCR lease could not be persisted; OCR did not start.",
            ) from error
        return dict(lease)


async def release_controller_ocr_lease(lease_id: str) -> bool:
    async with model_runtime_lock:
        require_controller_ocr_lease_store_ready()
        if not prune_controller_ocr_leases():
            raise HTTPException(
                status_code=503,
                detail="Controller OCR lease cleanup could not be persisted; release remains fail-closed.",
            )
        if lease_id not in controller_ocr_leases:
            return False
        next_leases = dict(controller_ocr_leases)
        next_leases.pop(lease_id)
        try:
            _commit_controller_ocr_leases(next_leases)
        except Exception as error:
            logger.exception("Failed to persist controller OCR lease release")
            raise HTTPException(
                status_code=503,
                detail="Controller OCR lease release could not be persisted; the lease remains active.",
            ) from error
        return True


async def wait_model_ready(model_id: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await model_runtime_status(model_id)
        if status["ready"]:
            return
        await asyncio.sleep(3)
    raise TimeoutError(f"Timed out waiting for {model_id} to become ready")


async def wait_container_runtime_ready(container_name: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await inspect_container(container_name)
        if not status["exists"]:
            raise RuntimeError(f"Docker container {container_name} is missing. Run docker compose up --no-start first.")
        if status["running"] and status["health"] in {"healthy", "none"}:
            return
        await asyncio.sleep(3)
    raise TimeoutError(f"Timed out waiting for Docker container {container_name} to become healthy")


def unlimited_ocr_adapter_base_url() -> str:
    return UNLIMITED_OCR_SERVICE_URL.rsplit("/", 1)[0]


async def call_unlimited_ocr_adapter_control(path: str, *, timeout: float | None = None) -> dict:
    control_timeout = timeout if timeout is not None else MODEL_SWITCH_TIMEOUT
    async with httpx.AsyncClient(timeout=control_timeout) as client:
        response = await client.post(f"{unlimited_ocr_adapter_base_url()}{path}")
    if response.status_code >= 400:
        raise RuntimeError(f"Unlimited-OCR adapter control failed ({response.status_code}): {response.text}")
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def wait_unlimited_ocr_backend_ready(backend: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await model_runtime_status("unlimited-ocr")
        if status.get("ready") and status.get("unlimitedOcrBackend") == backend:
            return
        await asyncio.sleep(3)
    raise TimeoutError(f"Timed out waiting for Unlimited-OCR {backend} backend to become ready")


async def wait_unlimited_ocr_adapter_http(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health_ok, _ = await fetch_http_health(unlimited_ocr_adapter_base_url() + "/health")
        if health_ok:
            return
        await asyncio.sleep(2)
    raise TimeoutError("Timed out waiting for Unlimited-OCR adapter API")


async def ensure_unlimited_ocr_backend_runtime(
    backend: str,
    timeout: float,
    *,
    gpu_release_gate_passed: bool = False,
) -> None:
    await wait_unlimited_ocr_adapter_http(timeout)
    docker_control = model_control_available()
    if docker_control:
        # Backend changes can be requested independently of a logical-model
        # switch.  Refuse to start either Unlimited worker if any other model
        # group is resident, even when free VRAM would otherwise look adequate.
        await assert_non_target_model_containers_stopped("unlimited-ocr")
    if backend == "sglang":
        await call_unlimited_ocr_adapter_control("/backend/transformers/unload", timeout=min(180, timeout))
        if docker_control:
            await ensure_runtime_service_created("unlimited-ocr-sglang")
            # The Transformers unload happened after any outer model-switch
            # gate, so SGLang always needs fresh release evidence of its own.
            await wait_for_stable_gpu_release("unlimited-ocr", timeout=timeout)
            await docker_container_action("unlimited-ocr-sglang", "start")
            await wait_container_runtime_ready("unlimited-ocr-sglang", timeout)
        await wait_unlimited_ocr_backend_ready("sglang", timeout)
        return

    if docker_control:
        await docker_container_action("unlimited-ocr-sglang", "stop")
        await assert_containers_stopped_twice(
            [("unlimited-ocr:unlimited-ocr-sglang", "unlimited-ocr-sglang")],
            context="Unlimited-OCR backend release check failed; SGLang is still running",
        )
        if not gpu_release_gate_passed:
            await wait_for_stable_gpu_release("unlimited-ocr", timeout=timeout)
    await call_unlimited_ocr_adapter_control("/backend/transformers/preload", timeout=timeout)
    await wait_unlimited_ocr_backend_ready("transformers", timeout)


def model_runtime_container_names(model_id: str) -> list[str]:
    names: list[str] = []
    for config in (
        MODEL_RUNTIME_CONFIG.get(model_id) or {},
        MODEL_RUNTIME_CONTAINER_GROUPS.get(model_id) or {},
    ):
        for key in ("containers", "stop_order", "start_order"):
            for name in config.get(key, []):
                if name not in names:
                    names.append(name)
    return names


def model_runtime_group_ids() -> list[str]:
    return list(dict.fromkeys([*MODEL_RUNTIME_CONTAINER_GROUPS, *MODEL_RUNTIME_CONFIG]))


def model_runtime_stop_order(model_id: str) -> list[str]:
    names: list[str] = []
    for config in (
        MODEL_RUNTIME_CONFIG.get(model_id) or {},
        MODEL_RUNTIME_CONTAINER_GROUPS.get(model_id) or {},
    ):
        for name in config.get("stop_order", []):
            if name not in names:
                names.append(name)
    return names or list(reversed(model_runtime_container_names(model_id)))


async def stop_non_target_model_containers(target_model_id: str) -> None:
    errors: list[str] = []
    for model_id in model_runtime_group_ids():
        if model_id == target_model_id:
            continue
        for container_name in model_runtime_stop_order(model_id):
            try:
                await docker_container_action(container_name, "stop")
            except Exception as stop_error:
                errors.append(f"{container_name}: {stop_error}")
                logger.exception("Failed to stop non-target model container %s", container_name)
    if errors:
        raise RuntimeError("Failed to stop all non-target model containers: " + "; ".join(errors))


async def assert_containers_stopped_twice(
    containers: list[tuple[str, str]],
    *,
    context: str,
) -> None:
    """Require two consecutive Docker inspect samples with Running=false."""
    if not containers:
        return
    for sample_index in range(2):
        running: list[str] = []
        for label, container_name in containers:
            status = await inspect_container(container_name)
            if status.get("running"):
                running.append(label)
        if running:
            raise RuntimeError(f"{context}: " + ", ".join(running))
        if sample_index == 0:
            await asyncio.sleep(GPU_RELEASE_SAMPLE_INTERVAL_SECONDS)


async def assert_non_target_model_containers_stopped(target_model_id: str) -> None:
    containers: list[tuple[str, str]] = []
    for model_id in model_runtime_group_ids():
        if model_id == target_model_id:
            continue
        for container_name in model_runtime_container_names(model_id):
            entry = (f"{model_id}:{container_name}", container_name)
            if entry not in containers:
                containers.append(entry)
    await assert_containers_stopped_twice(
        containers,
        context="Model exclusivity check failed; non-target containers are still running",
    )


async def assert_target_model_ready_and_exclusive(target_model_id: str) -> None:
    statuses = {
        model_id: await model_runtime_status(model_id)
        for model_id in MODEL_RUNTIME_CONFIG
    }
    exclusivity = await runtime_exclusivity_snapshot(statuses)
    running_model_ids = exclusivity["runningModelIds"]
    ready_model_ids = exclusivity["readyModelIds"]
    if running_model_ids != [target_model_id] or ready_model_ids != [target_model_id]:
        raise RuntimeError(
            f"Model switch verification failed for {target_model_id}; "
            f"running={','.join(running_model_ids) or 'none'}; "
            f"ready={','.join(ready_model_ids) or 'none'}"
        )


async def cleanup_model_runtime_containers(model_id: str) -> list[str]:
    """Best-effort fail-closed cleanup after a target startup failure."""
    errors: list[str] = []
    for container_name in model_runtime_stop_order(model_id):
        try:
            await docker_container_action(container_name, "stop")
        except Exception as cleanup_error:
            errors.append(f"{container_name}: {cleanup_error}")
            logger.exception("Failed to clean up model container %s", container_name)
    return errors


async def target_model_is_unique_running_and_ready(model_id: str) -> bool:
    """Return true only when an already-loaded target is the sole GPU runtime."""
    config = MODEL_RUNTIME_CONFIG.get(model_id) or {}
    if not config.get("containers") or not config.get("health_url"):
        return False
    statuses = {
        runtime_model_id: await model_runtime_status(runtime_model_id)
        for runtime_model_id in MODEL_RUNTIME_CONFIG
    }
    exclusivity = await runtime_exclusivity_snapshot(statuses)
    return (
        exclusivity.get("runningModelIds") == [model_id]
        and exclusivity.get("readyModelIds") == [model_id]
        and not exclusivity.get("exclusivityViolation")
    )


async def stop_target_model_containers_for_restart(model_id: str) -> None:
    """Fully release an unhealthy/partial target before its clean restart."""
    config = MODEL_RUNTIME_CONFIG.get(model_id) or {}
    if not config.get("containers"):
        return
    errors: list[str] = []
    for container_name in model_runtime_stop_order(model_id):
        try:
            await docker_container_action(container_name, "stop")
        except Exception as stop_error:
            errors.append(f"{container_name}: {stop_error}")
            logger.exception("Failed to stop target model container %s before restart", container_name)
    if errors:
        raise RuntimeError("Failed to stop target model for a clean restart: " + "; ".join(errors))

    await assert_containers_stopped_twice(
        [
            (f"{model_id}:{container_name}", container_name)
            for container_name in model_runtime_container_names(model_id)
        ],
        context="Target restart release check failed; target containers are still running",
    )


async def activate_model_runtime(model_id: str) -> bool:
    if model_id not in MODEL_RUNTIME_CONFIG:
        raise ValueError(f"Unknown model id: {model_id}")
    if not model_control_available():
        raise RuntimeError("Docker model control is not available")

    async with model_runtime_lock:
        set_model_runtime_operation("switching", f"Switching to {model_id}", model_id)
        switch_started_at = time.monotonic()
        target_start_attempted = False
        retained_ready_target = False
        non_target_exclusivity_confirmed = False
        try:
            await stop_non_target_model_containers(model_id)
            await assert_non_target_model_containers_stopped(model_id)
            non_target_exclusivity_confirmed = True

            # An idempotent request for the already unique, ready target must
            # not compare free VRAM against a model that is intentionally still
            # loaded.  Re-verify exclusivity after the two stopped samples and
            # leave the healthy target untouched.
            if await target_model_is_unique_running_and_ready(model_id):
                retained_ready_target = True
                await assert_target_model_ready_and_exclusive(model_id)
                set_model_runtime_operation("ready", f"{model_id} is ready", model_id)
                return True

            # A partial or backend-mismatched target can itself retain VRAM.
            # Stop and double-inspect it before the release samples so the gate
            # measures a genuinely empty hand-off, then perform a clean start.
            await stop_target_model_containers_for_restart(model_id)

            # Probe only after other logical runtimes are proven stopped.  This
            # both prevents a failed preflight from preserving an exclusivity
            # violation and avoids measuring the target alongside old GPU work.
            if MODEL_RUNTIME_CONFIG[model_id].get("gpu_memory"):
                await ensure_model_gpu_compatible(model_id)
                remaining_timeout = MODEL_SWITCH_TIMEOUT - (time.monotonic() - switch_started_at)
                await wait_for_stable_gpu_release(model_id, timeout=remaining_timeout)
            for container_name in MODEL_RUNTIME_CONFIG[model_id]["start_order"]:
                remaining_timeout = max(3, MODEL_SWITCH_TIMEOUT - (time.monotonic() - switch_started_at))
                target_start_attempted = True
                await docker_container_action(container_name, "start")
                await wait_container_runtime_ready(container_name, remaining_timeout)

            if model_id == "unlimited-ocr":
                remaining_timeout = max(3, MODEL_SWITCH_TIMEOUT - (time.monotonic() - switch_started_at))
                await ensure_unlimited_ocr_backend_runtime(
                    unlimited_ocr_runtime_backend,
                    remaining_timeout,
                    gpu_release_gate_passed=True,
                )

            remaining_timeout = max(3, MODEL_SWITCH_TIMEOUT - (time.monotonic() - switch_started_at))
            await wait_model_ready(model_id, remaining_timeout)
            await assert_non_target_model_containers_stopped(model_id)
            await assert_target_model_ready_and_exclusive(model_id)
            set_model_runtime_operation("ready", f"{model_id} is ready", model_id)
            return True
        except Exception as err:
            logger.exception("Model runtime switch failed")
            cleanup_target = (
                target_start_attempted
                or retained_ready_target
                or not non_target_exclusivity_confirmed
            )
            cleanup_errors = await cleanup_model_runtime_containers(model_id) if cleanup_target else []
            message = str(err)
            if cleanup_errors:
                message += "; cleanup failed: " + "; ".join(cleanup_errors)
            set_model_runtime_operation("error", message, model_id)
            model_runtime_operation["diagnostics"] = await model_failure_diagnostics(model_id, err)
            return False


async def schedule_model_runtime_activation(model_id: str) -> None:
    global model_runtime_task
    if model_id not in MODEL_RUNTIME_CONFIG:
        raise HTTPException(status_code=400, detail="Unknown model id")
    if not model_control_available():
        raise HTTPException(status_code=503, detail="Docker model control is not available")
    async with model_runtime_lock:
        require_controller_ocr_lease_store_ready()
        if ocr_active_count > 0 or controller_ocr_lease_count() > 0:
            raise HTTPException(status_code=409, detail="OCR is running. Wait for the active task before switching models.")
        if model_runtime_task and not model_runtime_task.done():
            raise HTTPException(status_code=409, detail="Model runtime is already busy. Wait for it to finish.")
        if unlimited_ocr_backend_task and not unlimited_ocr_backend_task.done():
            raise HTTPException(status_code=409, detail="Unlimited-OCR backend is switching. Wait for it to finish.")
        set_model_runtime_operation("switching", f"Switching to {model_id}", model_id)
        model_runtime_task = asyncio.create_task(activate_model_runtime(model_id))


async def deploy_and_activate_model_runtime(model_id: str, backend: str | None = None) -> None:
    global unlimited_ocr_runtime_backend
    previous_backend = unlimited_ocr_runtime_backend
    requested_backend: str | None = None
    try:
        if model_id == "unlimited-ocr" and backend:
            requested_backend = normalize_unlimited_ocr_backend(backend)
            unlimited_ocr_runtime_backend = requested_backend
        set_model_runtime_operation("switching", f"Deploying {model_id}", model_id)
        await ensure_model_runtime_created(model_id, backend)
        activated = await activate_model_runtime(model_id)
        if activated is False:
            raise RuntimeError(
                model_runtime_operation.get("message")
                or f"{model_id} failed final runtime verification"
            )
        if requested_backend:
            save_runtime_settings({"unlimitedOcrBackend": requested_backend})
    except Exception as err:
        logger.exception("Model runtime deployment failed")
        if requested_backend:
            unlimited_ocr_runtime_backend = previous_backend
            with contextlib.suppress(Exception):
                save_runtime_settings({"unlimitedOcrBackend": previous_backend})
        set_model_runtime_operation("error", str(err), model_id)
        model_runtime_operation["diagnostics"] = await model_failure_diagnostics(model_id, err)


async def schedule_model_runtime_deploy(model_id: str, backend: str | None = None) -> None:
    global model_runtime_task
    if model_id not in MODEL_RUNTIME_CONFIG:
        raise HTTPException(status_code=400, detail="Unknown model id")
    if not model_control_available():
        raise HTTPException(status_code=503, detail="Docker model control is not available")
    async with model_runtime_lock:
        require_controller_ocr_lease_store_ready()
        if ocr_active_count > 0 or controller_ocr_lease_count() > 0:
            raise HTTPException(status_code=409, detail="OCR is running. Wait for the active task before deploying models.")
        if model_runtime_task and not model_runtime_task.done():
            raise HTTPException(status_code=409, detail="Model runtime is already busy. Wait for it to finish.")
        if unlimited_ocr_backend_task and not unlimited_ocr_backend_task.done():
            raise HTTPException(status_code=409, detail="Unlimited-OCR backend is switching. Wait for it to finish.")
        set_model_runtime_operation("switching", f"Deploying {model_id}", model_id)
        model_runtime_task = asyncio.create_task(deploy_and_activate_model_runtime(model_id, backend))


async def activate_unlimited_ocr_backend(backend: str) -> None:
    global unlimited_ocr_runtime_backend
    previous_backend = unlimited_ocr_runtime_backend
    async with model_runtime_lock:
        set_model_runtime_operation("switching", f"Switching Unlimited-OCR backend to {backend}", "unlimited-ocr")
        switch_started_at = time.monotonic()
        unlimited_ocr_runtime_backend = backend
        try:
            status = await model_runtime_status("unlimited-ocr")
            if status.get("running"):
                remaining_timeout = max(3, MODEL_SWITCH_TIMEOUT - (time.monotonic() - switch_started_at))
                await ensure_unlimited_ocr_backend_runtime(backend, remaining_timeout)
            save_runtime_settings({"unlimitedOcrBackend": backend})
            set_model_runtime_operation("ready", f"Unlimited-OCR {backend} backend is ready", "unlimited-ocr")
        except Exception as err:
            logger.exception("Unlimited-OCR backend switch failed")
            unlimited_ocr_runtime_backend = previous_backend
            with contextlib.suppress(Exception):
                remaining_timeout = max(3, MODEL_SWITCH_TIMEOUT - (time.monotonic() - switch_started_at))
                await ensure_unlimited_ocr_backend_runtime(previous_backend, remaining_timeout)
            set_model_runtime_operation("error", str(err), "unlimited-ocr")


async def schedule_unlimited_ocr_backend_activation(backend: str) -> None:
    global unlimited_ocr_backend_task
    if not ENABLE_UNLIMITED_OCR:
        raise HTTPException(status_code=404, detail="Unlimited-OCR is not enabled")
    resolved_backend = normalize_unlimited_ocr_backend(backend)
    async with model_runtime_lock:
        require_controller_ocr_lease_store_ready()
        if ocr_active_count > 0 or controller_ocr_lease_count() > 0:
            raise HTTPException(status_code=409, detail="OCR is running. Wait for the active task before switching backends.")
        if model_runtime_task and not model_runtime_task.done():
            raise HTTPException(status_code=409, detail="Model runtime is switching. Wait for it to finish before switching backends.")
        if unlimited_ocr_backend_task and not unlimited_ocr_backend_task.done():
            raise HTTPException(status_code=409, detail="Unlimited-OCR backend is already switching.")
        if unlimited_ocr_runtime_backend == resolved_backend:
            save_runtime_settings({"unlimitedOcrBackend": resolved_backend})
            return
        set_model_runtime_operation("switching", f"Switching Unlimited-OCR backend to {resolved_backend}", "unlimited-ocr")
        unlimited_ocr_backend_task = asyncio.create_task(activate_unlimited_ocr_backend(resolved_backend))


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_task_data_dir()
    if model_control_available():
        await schedule_model_runtime_activation(DEFAULT_RUNTIME_MODEL_ID)
    yield


app = FastAPI(
    title="PaddleOCR Local WebUI",
    version=APP_VERSION,
    docs_url="/docs" if ENABLE_API_DOCS else None,
    redoc_url="/redoc" if ENABLE_API_DOCS else None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials="*" not in CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


SAFE_API_METHODS = {"GET", "HEAD", "OPTIONS"}
BROWSER_CODE_ASSET_PATHS = {
    "/static/app.js",
    "/static/bootstrap.mjs",
    "/static/i18n.js",
    "/static/index.html",
    "/static/style.css",
}


def normalize_origin(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return ""
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def configured_origins_for_request(request: Request) -> set[str]:
    origins = {normalize_origin(origin) for origin in CORS_ORIGINS if origin != "*"}
    return {origin for origin in origins if origin}


def request_origin_is_allowed(request: Request) -> bool:
    if not ENFORCE_ORIGIN_CHECK or not request.url.path.startswith("/api/"):
        return True
    if request.method in SAFE_API_METHODS:
        return True
    origin = request.headers.get("origin")
    if not origin:
        return True
    if "*" in CORS_ORIGINS:
        return True
    return normalize_origin(origin) in configured_origins_for_request(request)


@app.middleware("http")
async def enforce_request_security(request: Request, call_next):
    if not request_origin_is_allowed(request):
        return JSONResponse(status_code=403, content={"detail": "Cross-origin API request is not allowed"})

    if API_TOKEN and request.url.path.startswith("/api/") and not request_is_authenticated(request):
        return JSONResponse(status_code=401, content={"detail": "Missing or invalid API token"})

    if request.method in {"POST", "PUT", "PATCH"} and MAX_HTTP_BODY_BYTES > 0:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_HTTP_BODY_BYTES:
                    max_mb = MAX_HTTP_BODY_BYTES / 1024 / 1024
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request body is too large. Max HTTP body size is {max_mb:.0f} MB."},
                    )
            except ValueError:
                pass

    response = await call_next(request)
    if request.url.path in BROWSER_CODE_ASSET_PATHS:
        # Module imports can otherwise survive a normal browser reload after a
        # container upgrade. Revalidate the small application assets so the UI
        # and the running API always come from the same deployed revision.
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/") and not API_TOKEN:
        response.headers.setdefault("X-Pandocr-Auth-Warning", "PANDOCR_API_TOKEN is not set")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "worker-src 'self' blob:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'",
    )
    return response


@app.get("/")
async def read_root():
    return FileResponse(
        "static/index.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/models")
async def get_models():
    """Return OCR models available through this proxy."""
    return {
        "default": DEFAULT_RUNTIME_MODEL_ID,
        "data": model_catalog(),
        "version": APP_VERSION,
        "commit": APP_COMMIT or None,
        "maxUploadBytes": MAX_REQUEST_BYTES,
        "authRequired": bool(API_TOKEN),
        "originProtection": ENFORCE_ORIGIN_CHECK,
        "apiDocsEnabled": ENABLE_API_DOCS,
        "openapiUrl": app.openapi_url,
        "maxConcurrentOcr": MAX_CONCURRENT_OCR,
    }


@app.get("/api/model-runtime")
async def get_model_runtime():
    return await build_model_runtime_payload()


@app.post("/api/model-runtime/switch")
async def switch_model_runtime(request: ModelSwitchRequest):
    if MODEL_CONTROL_MODE == "remote":
        async with model_runtime_lock:
            if ocr_active_count > 0:
                raise HTTPException(status_code=409, detail="OCR is running. Wait before switching models.")
        await controller_api_request("POST", "/model-runtime/switch", json=request.model_dump())
        return await build_model_runtime_payload()
    await schedule_model_runtime_activation(request.modelId)
    return await build_model_runtime_payload()


@app.post("/api/model-runtime/deploy")
async def deploy_model_runtime(request: ModelDeployRequest):
    if MODEL_CONTROL_MODE == "remote":
        async with model_runtime_lock:
            if ocr_active_count > 0:
                raise HTTPException(status_code=409, detail="OCR is running. Wait before deploying models.")
        await controller_api_request("POST", "/model-runtime/deploy", json=request.model_dump())
        return await build_model_runtime_payload()
    await schedule_model_runtime_deploy(request.modelId, request.backend)
    return await build_model_runtime_payload()


@app.get("/api/unlimited-ocr/backend")
async def get_unlimited_ocr_backend():
    if not ENABLE_UNLIMITED_OCR:
        raise HTTPException(status_code=404, detail="Unlimited-OCR is not enabled")
    if MODEL_CONTROL_MODE == "remote":
        return await controller_api_request("GET", "/unlimited-ocr/backend")
    return {
        "backend": unlimited_ocr_runtime_backend,
        "supportedBackends": sorted(UNLIMITED_OCR_SUPPORTED_BACKENDS),
        "runtime": await model_runtime_status("unlimited-ocr"),
    }


@app.post("/api/unlimited-ocr/backend")
async def switch_unlimited_ocr_backend(request: UnlimitedOcrBackendRequest):
    if MODEL_CONTROL_MODE == "remote":
        async with model_runtime_lock:
            if ocr_active_count > 0:
                raise HTTPException(status_code=409, detail="OCR is running. Wait before switching backends.")
        await controller_api_request("POST", "/unlimited-ocr/backend", json=request.model_dump())
        return await build_model_runtime_payload()
    await schedule_unlimited_ocr_backend_activation(request.backend)
    return await build_model_runtime_payload()


def request_is_authenticated(request: Request) -> bool:
    if not API_TOKEN:
        return True
    header = request.headers.get("authorization", "")
    token = ""
    if header.lower().startswith("bearer "):
        token = header.split(" ", 1)[1].strip()
    token = token or request.headers.get("x-pandocr-token", "").strip()
    return bool(token) and secrets.compare_digest(token, API_TOKEN)


def validate_task_data_dir() -> None:
    task_dir = TASK_DATA_DIR.resolve()
    forbidden = {
        Path(task_dir.anchor).resolve(),
        PROJECT_ROOT.resolve(),
        PROJECT_ROOT.parent.resolve(),
        Path.home().resolve(),
    }
    if task_dir in forbidden:
        raise RuntimeError(f"Unsafe PANDOCR_TASK_DATA_DIR: {task_dir}")


def ensure_task_data_dir() -> None:
    validate_task_data_dir()
    TASK_DATA_DIR.mkdir(parents=True, exist_ok=True)
    marker = TASK_DATA_DIR / TASK_STORE_MARKER
    if not marker.exists():
        marker.write_text("PaddleOCR Local task store\n", encoding="utf-8")


def safe_task_id(task_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", task_id or ""):
        raise HTTPException(status_code=400, detail="Invalid task id")
    return task_id


def task_file_path(task_id: str) -> Path:
    return TASK_DATA_DIR / safe_task_id(task_id) / "task.json"


def task_summary_path(task_id: str) -> Path:
    return task_dir_path(task_id) / TASK_SUMMARY_FILE


def task_result_path(task_id: str) -> Path:
    return task_dir_path(task_id) / TASK_RESULT_FILE


def task_dir_path(task_id: str) -> Path:
    return TASK_DATA_DIR / safe_task_id(task_id)


def task_source_path(task_id: str) -> Path:
    return task_dir_path(task_id) / "source.bin"


def task_source_url(task_id: str) -> str:
    return f"/api/tasks/{safe_task_id(task_id)}/source"


def split_task_for_storage(task: dict) -> tuple[dict, dict | None]:
    """Keep task.json as metadata and move heavy OCR results into result.json."""
    task_id = task.get("id")
    source_url = task.get("sourceUrl")
    has_external_source = bool(source_url) or (isinstance(task_id, str) and task_source_path(task_id).exists())

    stored = dict(task)
    stored.pop("detailLoaded", None)
    preserve_result = bool(stored.pop("_preserveResult", False))

    result_payload = {}
    for key in ("markdown", "images", "ocrResults"):
        if key in stored:
            result_payload[key] = stored.pop(key)

    if has_external_source:
        stored["sourceUrl"] = source_url or task_source_url(task_id)
        stored.pop("sourceDataUrl", None)

    batches = stored.get("batches") if isinstance(stored.get("batches"), list) else []
    compact_batches = []
    batch_markdown = {}
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        compact = dict(batch)
        compact.pop("payloadDataUrl", None)
        compact.pop("payloadBlob", None)
        if "markdown" in compact:
            batch_id = compact.get("id")
            if batch_id:
                batch_markdown[str(batch_id)] = compact.pop("markdown")
            else:
                compact.pop("markdown", None)
        compact_batches.append(compact)
    if batch_markdown:
        result_payload["batchMarkdown"] = batch_markdown

    has_result_payload = any(
        bool(result_payload.get(key))
        for key in ("markdown", "images", "ocrResults", "batchMarkdown")
    )
    if preserve_result and not has_result_payload and isinstance(task_id, str):
        previous_state = {}
        previous_path = task_file_path(task_id)
        if previous_path.exists():
            try:
                previous = read_task_file(previous_path)
                previous_state = previous.get("_resultState") if isinstance(previous.get("_resultState"), dict) else {}
            except (OSError, ValueError, json.JSONDecodeError):
                previous_state = {}
        stored["batches"] = compact_batches
        stored["_storage"] = {
            "version": 2,
            "resultPath": TASK_RESULT_FILE if task_result_path(task_id).exists() else None,
        }
        stored["_resultState"] = previous_state
        return stored, None

    stored["batches"] = compact_batches
    stored["_storage"] = {
        "version": 2,
        "resultPath": TASK_RESULT_FILE if has_result_payload else None,
    }
    stored["_resultState"] = {
        "hasMarkdown": bool(result_payload.get("markdown") or result_payload.get("batchMarkdown")),
        "hasImages": bool(result_payload.get("images")),
        "hasOcrResults": bool(result_payload.get("ocrResults")),
    }
    return stored, result_payload


def task_summary(task: dict) -> dict:
    batches = task.get("batches") if isinstance(task.get("batches"), list) else []
    result_state = task.get("_resultState") if isinstance(task.get("_resultState"), dict) else {}
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
        "sourceUrl": task.get("sourceUrl"),
        "modelId": task.get("modelId"),
        "modelName": task.get("modelName"),
        "comparisonGroupId": task.get("comparisonGroupId"),
        "comparisonSourceName": task.get("comparisonSourceName"),
        "benchmark": task.get("benchmark"),
        "error": task.get("error"),
        "completedPages": completed_pages,
        "batchCount": len(batches),
        "hasMarkdown": bool(result_state.get("hasMarkdown") or task.get("markdown")),
        "hasOcrResults": bool(result_state.get("hasOcrResults") or task.get("ocrResults")),
        "detailLoaded": False,
    }


def read_json_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def read_task_file(path: Path) -> dict:
    return read_json_file(path)


def write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    temp_path.replace(path)


def write_task_bundle(task_id: str, task: dict) -> dict:
    ensure_task_data_dir()
    stored_task, result_payload = split_task_for_storage(task)
    task_dir = task_dir_path(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)

    result_path = task_result_path(task_id)
    if result_payload is None:
        pass
    elif stored_task.get("_storage", {}).get("resultPath"):
        write_json_file(result_path, result_payload)
    elif result_path.exists():
        result_path.unlink()

    write_json_file(task_file_path(task_id), stored_task)
    summary = task_summary(stored_task)
    write_json_file(task_summary_path(task_id), summary)
    return stored_task


def hydrate_task_detail(task_id: str, task: dict) -> dict:
    storage = task.get("_storage") if isinstance(task.get("_storage"), dict) else {}
    result_name = storage.get("resultPath") or TASK_RESULT_FILE
    result_path = task_dir_path(task_id) / result_name
    if result_path.exists():
        try:
            result_payload = read_json_file(result_path)
            for key in ("markdown", "images", "ocrResults"):
                if key in result_payload:
                    task[key] = result_payload[key]
            batch_markdown = result_payload.get("batchMarkdown")
            if isinstance(batch_markdown, dict) and isinstance(task.get("batches"), list):
                for batch in task["batches"]:
                    if isinstance(batch, dict) and batch.get("id") in batch_markdown:
                        batch["markdown"] = batch_markdown[batch["id"]]
        except (OSError, ValueError, json.JSONDecodeError) as err:
            logger.warning("Failed to hydrate task result %s: %s", result_path, err)

    task.setdefault("markdown", "")
    task.setdefault("images", {})
    task.setdefault("ocrResults", [])
    return task


def task_needs_compaction(task: dict) -> bool:
    if any(key in task for key in ("markdown", "images", "ocrResults", "detailLoaded")):
        return True
    batches = task.get("batches") if isinstance(task.get("batches"), list) else []
    return any(
        isinstance(batch, dict) and any(key in batch for key in ("markdown", "payloadDataUrl", "payloadBlob"))
        for batch in batches
    )


def task_sort_timestamp(task: dict) -> float:
    value = task.get("updatedAt") or task.get("createdAt")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        try:
            return float(text)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0
    return 0


def list_task_summaries() -> list[dict]:
    ensure_task_data_dir()
    tasks = []
    for path in TASK_DATA_DIR.glob("*/task.json"):
        try:
            summary_path = path.parent / TASK_SUMMARY_FILE
            if summary_path.exists():
                tasks.append(read_json_file(summary_path))
                continue

            task = read_task_file(path)
            if task.get("id") == path.parent.name and task_needs_compaction(task):
                task = write_task_bundle(path.parent.name, task)
            summary = task_summary(task)
            write_json_file(summary_path, summary)
            tasks.append(summary)
        except (OSError, ValueError, json.JSONDecodeError) as err:
            logger.warning("Skipping invalid task file %s: %s", path, err)
    tasks.sort(key=task_sort_timestamp, reverse=True)
    return tasks


def remove_task_dir(task_id: str) -> None:
    ensure_task_data_dir()
    path = task_dir_path(task_id).resolve()
    if path.parent != TASK_DATA_DIR:
        raise HTTPException(status_code=400, detail="Invalid task path")
    if path.exists():
        shutil.rmtree(path)


def clear_task_dirs() -> None:
    ensure_task_data_dir()
    for path in TASK_DATA_DIR.iterdir():
        if path.is_dir() and re.fullmatch(r"[A-Za-z0-9_-]{6,80}", path.name):
            shutil.rmtree(path)


async def read_upload_bytes(file: UploadFile, max_bytes: int | None = None) -> bytes:
    chunks = []
    total = 0
    limit = max_bytes if max_bytes and max_bytes > 0 else None
    while True:
        chunk = await file.read(UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if limit and total > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Uploaded file is too large. Max upload size is {limit / 1024 / 1024:.0f} MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def write_upload_to_path(file: UploadFile, path: Path, max_bytes: int | None = None) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    limit = max_bytes if max_bytes and max_bytes > 0 else None
    try:
        with path.open("wb") as buffer:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if limit and total > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Uploaded file is too large. Max upload size is {limit / 1024 / 1024:.0f} MB.",
                    )
                buffer.write(chunk)
    except Exception:
        if path.exists():
            path.unlink()
        raise
    return total


def extract_pdf_pages(source_path: Path, start_page: int, end_page: int) -> bytes:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(source_path))
    total_pages = len(reader.pages)
    if total_pages <= 0:
        raise ValueError("Source PDF has no pages")
    if start_page < 1 or end_page < start_page or start_page > total_pages:
        raise ValueError(f"Invalid page range {start_page}-{end_page} for {total_pages} pages")

    end_page = min(end_page, total_pages)
    writer = PdfWriter()
    for page_index in range(start_page - 1, end_page):
        writer.add_page(reader.pages[page_index])

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def clone_task_source_file(source_task_id: str, target_task_id: str) -> int:
    """Copy a persisted source locally for an independent comparison task."""
    source_path = task_source_path(source_task_id)
    if not source_path.exists():
        raise FileNotFoundError("Task source not found")
    target_path = task_source_path(target_task_id)
    if target_path.exists():
        raise FileExistsError("Target task source already exists")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    return target_path.stat().st_size


@app.post("/api/tasks/{task_id}/source")
async def upload_task_source(task_id: str, file: UploadFile = File(...)):
    """Persist the original uploaded source outside task.json."""
    source_path = task_source_path(task_id)
    temp_path = source_path.with_suffix(".tmp")
    size = await write_upload_to_path(file, temp_path, MAX_REQUEST_BYTES)
    temp_path.replace(source_path)
    return {
        "ok": True,
        "url": task_source_url(task_id),
        "size": size,
        "filename": Path(file.filename or "source").name,
        "contentType": file.content_type or "application/octet-stream",
    }


@app.get("/api/tasks/{task_id}/source")
async def get_task_source(task_id: str):
    """Return the original uploaded source file for previewing or resumable parsing."""
    source_path = task_source_path(task_id)
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Task source not found")

    media_type = "application/octet-stream"
    filename = "source"
    task_path = task_file_path(task_id)
    if task_path.exists():
        try:
            task = await run_in_threadpool(read_task_file, task_path)
            media_type = task.get("mimeType") or media_type
            filename = task.get("originalName") or task.get("name") or filename
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    return FileResponse(source_path, media_type=media_type, filename=filename)


@app.post("/api/tasks/{source_task_id}/clone-source/{target_task_id}")
async def clone_task_source(source_task_id: str, target_task_id: str):
    """Clone a task source without sending a large local document through the browser again."""
    if source_task_id == target_task_id:
        raise HTTPException(status_code=400, detail="Source and target task ids must differ")
    try:
        size = await run_in_threadpool(clone_task_source_file, source_task_id, target_task_id)
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except FileExistsError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except OSError as err:
        logger.exception("Failed to clone task source")
        raise HTTPException(status_code=500, detail=f"Failed to clone task source: {err}") from err
    return {"ok": True, "url": task_source_url(target_task_id), "size": size}


@app.get("/api/tasks/{task_id}/source/pages")
async def get_task_source_pages(
    task_id: str,
    start_page: int = Query(..., ge=1),
    end_page: int = Query(..., ge=1),
):
    """Return a compact PDF containing only a page range from the source PDF."""
    source_path = task_source_path(task_id)
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Task source not found")
    if end_page < start_page:
        raise HTTPException(status_code=400, detail="end_page must be greater than or equal to start_page")

    try:
        pdf_content = await run_in_threadpool(extract_pdf_pages, source_path, start_page, end_page)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception as err:
        logger.exception("Failed to extract PDF pages")
        raise HTTPException(status_code=500, detail=f"Failed to extract PDF pages: {err}") from err

    return Response(content=pdf_content, media_type="application/pdf")


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
    if task_source_path(task_id).exists() and not task.get("sourceUrl"):
        task["sourceUrl"] = task_source_url(task_id)
    task = hydrate_task_detail(task_id, task)
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

    stored_task = await run_in_threadpool(write_task_bundle, task_id, task)
    return {"ok": True, "task": task_summary(stored_task)}


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete one locally persisted task."""
    await run_in_threadpool(remove_task_dir, task_id)
    return {"ok": True}


@app.delete("/api/tasks")
async def clear_tasks():
    """Delete all locally persisted tasks."""
    await run_in_threadpool(clear_task_dirs)
    return {"ok": True}


@app.post("/api/convert/to-pdf")
async def convert_to_pdf(file: UploadFile = File(...)):
    """Convert PPT/PPTX/DOC/DOCX to PDF using LibreOffice."""
    logger.info("Received conversion request for: %s", file.filename)

    if OFFICE_CONVERTER_URL:
        filename = Path(file.filename or "upload").name
        content = await read_upload_bytes(file, MAX_REQUEST_BYTES)
        timeout = None if PADDLE_REQUEST_TIMEOUT <= 0 else PADDLE_REQUEST_TIMEOUT
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    OFFICE_CONVERTER_URL,
                    files={"file": (filename, content, file.content_type or "application/octet-stream")},
                )
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail=f"Office converter is unavailable: {error}") from error
        if response.status_code != 200:
            try:
                detail = response.json().get("detail")
            except (ValueError, AttributeError):
                detail = response.text
            raise HTTPException(status_code=response.status_code, detail=detail or "Office conversion failed")
        return Response(content=response.content, media_type="application/pdf")

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
            await write_upload_to_path(file, Path(input_path), MAX_REQUEST_BYTES)

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
    modelId: Optional[str] = None
    fileType: Optional[int] = None
    useLayoutDetection: bool = True
    useDocUnwarping: bool = False
    useDocOrientationClassify: bool = False
    useTextlineOrientation: bool = False
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

UNIFIED_PARSE_MODEL_IDS = [
    "paddleocr-vl-1.6",
    "pp-ocrv6",
    "unlimited-ocr",
    "ovisocr2",
    "hpd-parsing",
]


def unified_parse_openapi_extra() -> dict:
    """Describe both body formats accepted by the raw Request parser."""
    json_schema = OCRRequest.model_json_schema()
    json_properties = json_schema.setdefault("properties", {})
    json_properties["image"] = {
        "type": "string",
        "description": "Base64 data or a data URL containing the image/PDF payload.",
    }
    json_properties["modelId"] = {
        "type": "string",
        "enum": UNIFIED_PARSE_MODEL_IDS,
        "description": "An enabled OCR model ID. Omit to use the server default.",
    }
    json_schema["required"] = ["image"]

    multipart_properties = {
        "file": {
            "type": "string",
            "format": "binary",
            "description": "Image or PDF file to parse.",
        },
        **{
            key: value
            for key, value in json_properties.items()
            if key != "image"
        },
    }
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": multipart_properties,
                    }
                },
                "application/json": {"schema": json_schema},
            },
        },
        "responses": {
            "400": {"description": "Invalid input or unknown/disabled modelId"},
            "409": {"description": "Model runtime is switching or busy"},
            "413": {"description": "OCR input exceeds the configured size limit"},
            "503": {"description": "Selected model is not ready"},
        },
    }


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
        labels: List[str] = []
        for item in value:
            labels.extend(parse_markdown_ignore_labels(item))
        return labels
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

        file_bytes = await read_upload_bytes(upload, MAX_REQUEST_BYTES)
        markdown_ignore_labels = (
            form.getlist("markdownIgnoreLabels")
            if hasattr(form, "getlist")
            else form.get("markdownIgnoreLabels")
        )
        ocr_request = OCRRequest(
            modelId=parse_optional_string(form.get("modelId")),
            fileType=parse_optional_int(form.get("fileType")),
            useLayoutDetection=parse_bool(form.get("useLayoutDetection"), True),
            useDocUnwarping=parse_bool(form.get("useDocUnwarping"), False),
            useDocOrientationClassify=parse_bool(form.get("useDocOrientationClassify"), False),
            useTextlineOrientation=parse_bool(form.get("useTextlineOrientation"), False),
            useChartRecognition=parse_bool(form.get("useChartRecognition"), False),
            useSealRecognition=parse_bool(form.get("useSealRecognition"), True),
            formatBlockContent=parse_bool(form.get("formatBlockContent"), True),
            showFormulaNumber=parse_bool(form.get("showFormulaNumber"), True),
            markdownIgnoreLabels=parse_markdown_ignore_labels(markdown_ignore_labels),
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

    body = await request.body()
    if MAX_REQUEST_BYTES > 0 and len(body) > MAX_REQUEST_BYTES:
        max_mb = MAX_REQUEST_BYTES / 1024 / 1024
        raise HTTPException(status_code=413, detail=f"Request body is too large. Max upload size is {max_mb:.0f} MB.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as err:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from err
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


def prepare_service_input(ocr_request: OCRRequest, raw_input: RawOCRInput) -> tuple[str, int]:
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

    return base64_data, file_type


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


def build_ppocr_payload(request: OCRRequest, base64_data: str, file_type: int) -> dict:
    payload = {
        "file": base64_data,
        "fileType": file_type,
        "useDocOrientationClassify": request.useDocOrientationClassify,
        "useDocUnwarping": request.useDocUnwarping,
        "useTextlineOrientation": request.useTextlineOrientation,
    }
    if request.visualize is not None:
        payload["visualize"] = request.visualize
    return payload


def build_unlimited_ocr_payload(request: OCRRequest, base64_data: str, file_type: int) -> dict:
    payload = {
        "file": base64_data,
        "fileType": file_type,
        "backend": unlimited_ocr_runtime_backend,
    }
    optional_params = [
        "temperature",
        "topP",
        "visualize",
    ]
    for param in optional_params:
        val = getattr(request, param)
        if val is not None:
            payload[param] = val
    return payload


def build_ovisocr2_payload(request: OCRRequest, base64_data: str, file_type: int) -> dict:
    return {
        "file": base64_data,
        "fileType": file_type,
    }


def build_hpd_parsing_payload(base64_data: str, file_type: int) -> dict:
    return {
        "file": base64_data,
        "fileType": file_type,
    }


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


def as_jsonable(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {key: as_jsonable(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [as_jsonable(item) for item in value]
    return value


def pick_indexed_value(values, index):
    if isinstance(values, list) and index < len(values):
        return as_jsonable(values[index])
    return None


def extract_ppocr_lines(pruned_result: dict) -> list[dict]:
    texts = pruned_result.get("rec_texts") if isinstance(pruned_result.get("rec_texts"), list) else []
    scores = pruned_result.get("rec_scores") if isinstance(pruned_result.get("rec_scores"), list) else []
    boxes = pruned_result.get("rec_boxes")
    polys = pruned_result.get("rec_polys")
    if hasattr(boxes, "tolist"):
        boxes = boxes.tolist()
    if hasattr(polys, "tolist"):
        polys = polys.tolist()

    lines = []
    for index, text in enumerate(texts):
        line = {
            "text": str(text),
            "score": pick_indexed_value(scores, index),
        }
        box = pick_indexed_value(boxes, index)
        poly = pick_indexed_value(polys, index)
        if box is not None:
            line["box"] = box
        if poly is not None:
            line["poly"] = poly
        lines.append(line)
    return lines


def parse_ppocr_response(data: dict) -> dict:
    if "result" not in data or "ocrResults" not in data["result"]:
        logger.warning("Unexpected PP-OCR response format: %s", data)
        raise HTTPException(status_code=500, detail="Unexpected response format from PP-OCR service")

    pages = []
    full_markdown_parts = []
    for page_index, page_result in enumerate(data["result"]["ocrResults"]):
        pruned = page_result.get("prunedResult") if isinstance(page_result, dict) else {}
        if not isinstance(pruned, dict):
            pruned = {}
        pruned = as_jsonable(pruned)
        lines = extract_ppocr_lines(pruned)
        markdown_text = "\n".join(line["text"] for line in lines if line.get("text"))
        if markdown_text:
            full_markdown_parts.append(markdown_text)

        pages.append(
            {
                "model": PPOCR_V6_MODEL_NAME,
                "parser": "pp-ocrv6",
                "page_index": pruned.get("page_index", page_index),
                "pageImage": page_result.get("inputImage") if isinstance(page_result, dict) else None,
                "markdown": {
                    "text": markdown_text,
                    "images": {},
                },
                "ocrLines": lines,
                "prunedResult": pruned,
            }
        )

    return {
        "markdown": "\n\n".join(full_markdown_parts),
        "images": {},
        "layoutParsingResults": pages,
    }


UNLIMITED_OCR_DET_RE = re.compile(r"<\|det\|>\s*([A-Za-z_][\w-]*)\s*(\[[^\]]*\])?\s*<\|/det\|>")
UNLIMITED_OCR_SKIP_MARKDOWN_LABELS = {"header", "footer", "number", "page_number", "page_num"}
UNLIMITED_OCR_CAPTION_LABELS = {"image_caption", "figure_caption", "table_caption"}
UNLIMITED_OCR_TITLE_LABELS = {"title", "section_title"}


def compact_markdown_block(text: str) -> str:
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def format_unlimited_ocr_block(label: str, content: str, *, seen_title: bool) -> tuple[str, bool]:
    normalized_label = label.lower().strip()
    text = compact_markdown_block(content)
    if not text or normalized_label in UNLIMITED_OCR_SKIP_MARKDOWN_LABELS:
        return "", seen_title

    if normalized_label in UNLIMITED_OCR_TITLE_LABELS:
        level = "##" if seen_title else "#"
        return f"{level} {text}", True

    if normalized_label in UNLIMITED_OCR_CAPTION_LABELS:
        return f"*{text}*", seen_title

    if normalized_label in {"formula", "display_formula"}:
        return f"$$\n{text}\n$$", seen_title

    if normalized_label in {"image", "chart"}:
        return f"**{normalized_label.replace('_', ' ').title()}:** {text}", seen_title

    return text, seen_title


def clean_unlimited_ocr_markdown(markdown: str) -> str:
    text = str(markdown).replace("\r\n", "\n").replace("\r", "\n")
    if "<|det|>" not in text:
        return compact_markdown_block(text)

    matches = list(UNLIMITED_OCR_DET_RE.finditer(text))
    if not matches:
        return compact_markdown_block(re.sub(r"<\|/?det\|>", "", text))

    blocks = []
    prefix = compact_markdown_block(text[: matches[0].start()])
    if prefix:
        blocks.append(prefix)

    seen_title = False
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block, seen_title = format_unlimited_ocr_block(match.group(1), text[match.end() : next_start], seen_title=seen_title)
        if block:
            blocks.append(block)

    return compact_markdown_block("\n\n".join(blocks))


def parse_unlimited_ocr_response(data: dict) -> dict:
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="Unexpected response format from Unlimited-OCR service")

    markdown = data.get("markdown")
    if markdown is None:
        markdown = data.get("text") or data.get("result") or ""
    markdown = clean_unlimited_ocr_markdown(str(markdown))

    images = data.get("images") if isinstance(data.get("images"), dict) else {}
    results = data.get("layoutParsingResults")
    if not isinstance(results, list):
        results = [
            {
                "model": UNLIMITED_OCR_MODEL_NAME,
                "parser": "unlimited-ocr",
                "markdown": {
                    "text": str(markdown),
                    "images": images,
                },
            }
        ]
    else:
        normalized_results = []
        for result in results:
            if not isinstance(result, dict):
                normalized_results.append(result)
                continue
            normalized_result = dict(result)
            result_markdown = normalized_result.get("markdown")
            if isinstance(result_markdown, dict):
                normalized_markdown = dict(result_markdown)
                normalized_markdown["text"] = clean_unlimited_ocr_markdown(str(normalized_markdown.get("text", "")))
                normalized_result["markdown"] = normalized_markdown
            normalized_results.append(normalized_result)
        results = normalized_results

    return {
        "markdown": markdown,
        "images": images,
        "layoutParsingResults": results,
    }


async def acquire_ocr_slot(model_id: str, not_ready_message: str) -> str | None:
    global ocr_active_count
    await ocr_semaphore.acquire()
    controller_lease_id: str | None = None
    try:
        async with model_runtime_lock:
            operation = model_runtime_operation
            if operation.get("state") == "switching":
                target = operation.get("targetModelId") or "requested model"
                raise HTTPException(status_code=409, detail=f"Model runtime is switching to {target}. Try again when it is ready.")
            if MODEL_CONTROL_MODE == "remote":
                lease = await controller_api_request(
                    "POST", "/ocr-leases/acquire", json={"modelId": model_id}
                )
                controller_lease_id = str(lease.get("leaseId") or "")
                if not controller_lease_id:
                    raise HTTPException(status_code=502, detail="Model controller omitted the OCR lease id")
            else:
                runtime = await model_runtime_status(model_id)
                if not runtime["ready"]:
                    raise HTTPException(status_code=503, detail=not_ready_message)
            ocr_active_count += 1
        return controller_lease_id
    except Exception:
        if controller_lease_id:
            with contextlib.suppress(Exception):
                await controller_api_request(
                    "DELETE", f"/ocr-leases/{quote(controller_lease_id, safe='')}"
                )
        ocr_semaphore.release()
        raise


async def release_ocr_slot(controller_lease_id: str | None = None) -> None:
    global ocr_active_count
    async with model_runtime_lock:
        try:
            if MODEL_CONTROL_MODE == "remote" and controller_lease_id:
                await controller_api_request(
                    "DELETE", f"/ocr-leases/{quote(controller_lease_id, safe='')}"
                )
        except Exception:
            # Keep the controller lease fail-closed.  By default leases do not
            # expire, so a transient release failure cannot permit an unsafe
            # switch while OCR may still be using GPU memory.
            logger.exception("Failed to release controller OCR lease %s", controller_lease_id)
        finally:
            ocr_active_count = max(0, ocr_active_count - 1)
    ocr_semaphore.release()


class OCRSlotReleaseGuard:
    """Run controller/local OCR-slot cleanup once across competing stream exits."""

    def __init__(self, controller_lease_id: str | None = None):
        self.controller_lease_id = controller_lease_id
        self._lock = asyncio.Lock()
        self._release_task: asyncio.Task | None = None

    async def release_once(self) -> None:
        async with self._lock:
            if self._release_task is None:
                self._release_task = asyncio.create_task(
                    release_ocr_slot(self.controller_lease_id)
                )
            release_task = self._release_task
        # A disconnect may cancel the body iterator while the response itself
        # is also unwinding.  Shield the single cleanup task so either caller
        # can finish waiting for the same controller/semaphore release.
        await asyncio.shield(release_task)


class OCRSlotStreamingResponse(StreamingResponse):
    """Tie an acquired OCR slot to the complete ASGI response lifecycle."""

    def __init__(self, *args, release_guard: OCRSlotReleaseGuard, **kwargs):
        super().__init__(*args, **kwargs)
        self._release_guard = release_guard

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                close_iterator = getattr(self.body_iterator, "aclose", None)
                if close_iterator is not None:
                    await close_iterator()
            except Exception:
                logger.exception("Failed to close Unlimited-OCR response iterator")
            finally:
                await self._release_guard.release_once()


async def run_ocr_request(ocr_request: OCRRequest, raw_input: RawOCRInput) -> dict:
    controller_lease_id = await acquire_ocr_slot(
        "paddleocr-vl-1.6",
        "PaddleOCR-VL service is not ready. Switch to this model and wait for it to become ready.",
    )
    try:
        base64_data, file_type = prepare_service_input(ocr_request, raw_input)
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
    finally:
        await release_ocr_slot(controller_lease_id)


async def run_ppocrv6_request(ocr_request: OCRRequest, raw_input: RawOCRInput) -> dict:
    controller_lease_id = await acquire_ocr_slot(
        "pp-ocrv6",
        "PP-OCRv6 service is not ready. Switch to this model and wait for it to become ready.",
    )
    try:
        base64_data, file_type = prepare_service_input(ocr_request, raw_input)
        payload = build_ppocr_payload(ocr_request, base64_data, file_type)

        logger.info("Sending request to PP-OCR service at %s", PADDLE_OCR_SERVICE_URL)
        timeout = PADDLE_REQUEST_TIMEOUT if PADDLE_REQUEST_TIMEOUT > 0 else None
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                PADDLE_OCR_SERVICE_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if resp.status_code != 200:
                logger.warning("PP-OCR Service Error (HTTP %s): %s", resp.status_code, resp.text)
                if resp.status_code == 422:
                    logger.warning("PP-OCR Validation Error Details: %s", resp.json())
                raise HTTPException(status_code=resp.status_code, detail=f"Upstream PP-OCR error: {resp.text}")

            return parse_ppocr_response(resp.json())
    finally:
        await release_ocr_slot(controller_lease_id)


async def run_unlimited_ocr_request(ocr_request: OCRRequest, raw_input: RawOCRInput) -> dict:
    if not ENABLE_UNLIMITED_OCR:
        raise HTTPException(status_code=404, detail="Unlimited-OCR is not enabled")

    controller_lease_id = await acquire_ocr_slot(
        "unlimited-ocr",
        "Unlimited-OCR service is not ready. Switch to this model and wait for it to become ready.",
    )
    try:
        base64_data, file_type = prepare_service_input(ocr_request, raw_input)
        payload = build_unlimited_ocr_payload(ocr_request, base64_data, file_type)

        logger.info("Sending request to Unlimited-OCR adapter at %s", UNLIMITED_OCR_SERVICE_URL)
        timeout = PADDLE_REQUEST_TIMEOUT if PADDLE_REQUEST_TIMEOUT > 0 else None
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                UNLIMITED_OCR_SERVICE_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if resp.status_code != 200:
                logger.warning("Unlimited-OCR Service Error (HTTP %s): %s", resp.status_code, resp.text)
                raise HTTPException(status_code=resp.status_code, detail=f"Upstream Unlimited-OCR error: {resp.text}")

            return parse_unlimited_ocr_response(resp.json())
    finally:
        await release_ocr_slot(controller_lease_id)


async def run_ovisocr2_request(ocr_request: OCRRequest, raw_input: RawOCRInput) -> dict:
    if not ENABLE_OVISOCR2:
        raise HTTPException(status_code=404, detail="OvisOCR2 is not enabled")

    controller_lease_id = await acquire_ocr_slot(
        "ovisocr2",
        "OvisOCR2 service is not ready. Switch to this model and wait for it to become ready.",
    )
    try:
        base64_data, file_type = prepare_service_input(ocr_request, raw_input)
        payload = build_ovisocr2_payload(ocr_request, base64_data, file_type)

        logger.info("Sending request to OvisOCR2 adapter at %s", OVISOCR2_SERVICE_URL)
        timeout = PADDLE_REQUEST_TIMEOUT if PADDLE_REQUEST_TIMEOUT > 0 else None
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                OVISOCR2_SERVICE_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                logger.warning("OvisOCR2 Service Error (HTTP %s): %s", resp.status_code, resp.text)
                raise HTTPException(status_code=resp.status_code, detail=f"Upstream OvisOCR2 error: {resp.text}")
            data = resp.json()
            if not isinstance(data, dict) or "layoutParsingResults" not in data:
                raise HTTPException(status_code=500, detail="Unexpected response format from OvisOCR2")
            return data
    finally:
        await release_ocr_slot(controller_lease_id)


async def run_hpd_parsing_request(ocr_request: OCRRequest, raw_input: RawOCRInput) -> dict:
    if not ENABLE_HPD_PARSING:
        raise HTTPException(status_code=404, detail="HPD-Parsing is not enabled")

    controller_lease_id = await acquire_ocr_slot(
        "hpd-parsing",
        "HPD-Parsing service is not ready. Switch to this model and wait for it to become ready.",
    )
    try:
        base64_data, file_type = prepare_service_input(ocr_request, raw_input)
        payload = build_hpd_parsing_payload(base64_data, file_type)
        logger.info("Sending request to HPD-Parsing adapter at %s", HPD_PARSING_SERVICE_URL)
        timeout = PADDLE_REQUEST_TIMEOUT if PADDLE_REQUEST_TIMEOUT > 0 else None
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(HPD_PARSING_SERVICE_URL, json=payload)
        if response.status_code != 200:
            logger.warning("HPD-Parsing Service Error (HTTP %s): %s", response.status_code, response.text)
            raise HTTPException(status_code=response.status_code, detail=f"Upstream HPD-Parsing error: {response.text}")
        data = response.json()
        if not isinstance(data, dict) or "layoutParsingResults" not in data:
            raise HTTPException(status_code=500, detail="Unexpected response format from HPD-Parsing")
        return data
    finally:
        await release_ocr_slot(controller_lease_id)


async def stream_unlimited_ocr_events(
    ocr_request: OCRRequest,
    raw_input: RawOCRInput,
    controller_lease_id: str | None = None,
    release_guard: OCRSlotReleaseGuard | None = None,
):
    slot_release = release_guard or OCRSlotReleaseGuard(controller_lease_id)
    try:
        base64_data, file_type = prepare_service_input(ocr_request, raw_input)
        payload = build_unlimited_ocr_payload(ocr_request, base64_data, file_type)
        stream_url = UNLIMITED_OCR_SERVICE_URL.rsplit("/", 1)[0] + "/ocr/stream"
        timeout = PADDLE_REQUEST_TIMEOUT if PADDLE_REQUEST_TIMEOUT > 0 else None

        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                stream_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    yield json.dumps({"type": "error", "detail": f"Upstream Unlimited-OCR error: {body}"}, ensure_ascii=False) + "\n"
                    return
                async for line in resp.aiter_lines():
                    if line:
                        yield line + "\n"
    except Exception as err:
        logger.exception("Unlimited-OCR stream proxy failed")
        yield json.dumps({"type": "error", "detail": str(err)}, ensure_ascii=False) + "\n"
    finally:
        await slot_release.release_once()


def validate_proxy_input_size(raw_input: RawOCRInput) -> int:
    base64_data = normalize_raw_input_to_base64(raw_input)
    if MAX_REQUEST_BYTES > 0 and len(base64_data) > int(MAX_REQUEST_BYTES * 4 / 3) + 1024:
        max_mb = MAX_REQUEST_BYTES / 1024 / 1024
        raise HTTPException(status_code=413, detail=f"OCR input is too large. Max upload size is {max_mb:.0f} MB.")
    return len(base64_data)


@app.post("/api/parse", openapi_extra=unified_parse_openapi_extra())
async def parse_with_selected_model(request: Request):
    """Stable model-agnostic OCR endpoint selected by the modelId field."""
    try:
        ocr_request, raw_input = await parse_ocr_input(request)
        model_id = ocr_request.modelId or DEFAULT_RUNTIME_MODEL_ID
        validate_proxy_input_size(raw_input)
        runners = {
            "paddleocr-vl-1.6": run_ocr_request,
            "pp-ocrv6": run_ppocrv6_request,
            "unlimited-ocr": run_unlimited_ocr_request,
            "ovisocr2": run_ovisocr2_request,
            "hpd-parsing": run_hpd_parsing_request,
        }
        runner = runners.get(model_id)
        if runner is None or model_id not in MODEL_CATALOG_IDS:
            raise HTTPException(status_code=400, detail=f"Unknown or disabled modelId: {model_id}")
        logger.info("Received unified OCR request for model %s", model_id)
        return await runner(ocr_request, raw_input)
    except HTTPException:
        raise
    except Exception as err:
        logger.exception("Unified OCR endpoint error")
        raise HTTPException(status_code=500, detail=str(err)) from err


@app.post("/api/paddleocr-vl-1.6")
async def proxy_paddleocr_vl(request: Request):
    """Proxy request to PaddleOCR-VL Pipeline Service."""
    try:
        ocr_request, raw_image = await parse_ocr_input(request)
        base64_size = validate_proxy_input_size(raw_image)
        logger.info("Received PaddleOCR-VL request. Base64 input size: %s bytes", base64_size)
        return await run_ocr_request(ocr_request, raw_image)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("PaddleOCR-VL Proxy Error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/pp-ocrv6")
async def proxy_ppocrv6(request: Request):
    """Proxy request to PP-OCRv6 OCR Pipeline Service."""
    try:
        ocr_request, raw_image = await parse_ocr_input(request)
        base64_size = validate_proxy_input_size(raw_image)
        logger.info("Received PP-OCRv6 request. Base64 input size: %s bytes", base64_size)
        return await run_ppocrv6_request(ocr_request, raw_image)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("PP-OCRv6 Proxy Error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/unlimited-ocr")
async def proxy_unlimited_ocr(request: Request):
    """Proxy request to the optional Unlimited-OCR adapter service."""
    try:
        ocr_request, raw_image = await parse_ocr_input(request)
        base64_size = validate_proxy_input_size(raw_image)
        logger.info("Received Unlimited-OCR request. Base64 input size: %s bytes", base64_size)
        return await run_unlimited_ocr_request(ocr_request, raw_image)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unlimited-OCR Proxy Error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/ovisocr2")
async def proxy_ovisocr2(request: Request):
    """Proxy request to the optional OvisOCR2 vLLM adapter service."""
    try:
        ocr_request, raw_image = await parse_ocr_input(request)
        base64_size = validate_proxy_input_size(raw_image)
        logger.info("Received OvisOCR2 request. Base64 input size: %s bytes", base64_size)
        return await run_ovisocr2_request(ocr_request, raw_image)
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("OvisOCR2 Proxy Error")
        raise HTTPException(status_code=500, detail=str(error))


@app.post("/api/hpd-parsing")
async def proxy_hpd_parsing(request: Request):
    """Proxy a document to the optional official HPD-Parsing runtime."""
    try:
        ocr_request, raw_image = await parse_ocr_input(request)
        base64_size = validate_proxy_input_size(raw_image)
        logger.info("Received HPD-Parsing request. Base64 input size: %s bytes", base64_size)
        return await run_hpd_parsing_request(ocr_request, raw_image)
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("HPD-Parsing Proxy Error")
        raise HTTPException(status_code=500, detail=str(error))


@app.post("/api/unlimited-ocr/stream")
async def proxy_unlimited_ocr_stream(request: Request):
    try:
        ocr_request, raw_image = await parse_ocr_input(request)
        base64_size = validate_proxy_input_size(raw_image)
        logger.info("Received streaming Unlimited-OCR request. Base64 input size: %s bytes", base64_size)
        if not ENABLE_UNLIMITED_OCR:
            raise HTTPException(status_code=404, detail="Unlimited-OCR is not enabled")
        controller_lease_id = await acquire_ocr_slot(
            "unlimited-ocr",
            "Unlimited-OCR service is not ready. Switch to this model and wait for it to become ready.",
        )
        release_guard = OCRSlotReleaseGuard(controller_lease_id)
        return OCRSlotStreamingResponse(
            stream_unlimited_ocr_events(
                ocr_request,
                raw_image,
                controller_lease_id,
                release_guard,
            ),
            release_guard=release_guard,
            media_type="application/x-ndjson",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unlimited-OCR Stream Proxy Error")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting server. Target Pipeline: %s", PADDLE_SERVICE_URL)
    uvicorn.run(app, host=PANDOCR_HOST, port=PANDOCR_PORT)
