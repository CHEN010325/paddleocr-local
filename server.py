import os
import asyncio
import base64
import httpx
import subprocess
import tempfile
import shutil
import io
import json
import re
import logging
import time
import secrets
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


PADDLE_SERVICE_URL = os.getenv("PADDLE_SERVICE_URL", "http://localhost:8081/layout-parsing")
VLM_BACKEND = os.getenv("VLM_BACKEND", "vllm")
VLM_IMAGE_TAG_SUFFIX = os.getenv("VLM_IMAGE_TAG_SUFFIX", "latest-nvidia-gpu-offline")
API_IMAGE_TAG_SUFFIX = os.getenv("API_IMAGE_TAG_SUFFIX", "latest-nvidia-gpu-offline")
PANDOCR_GPU_DEVICE_ID = os.getenv("PANDOCR_GPU_DEVICE_ID", "0")
PADDLEOCR_VL_MODEL_NAME = os.getenv("PADDLEOCR_VL_MODEL_NAME", "PaddleOCR-VL-1.6-0.9B")
PADDLE_OCR_SERVICE_URL = os.getenv("PADDLE_OCR_SERVICE_URL", "http://localhost:8082/ocr")
PPOCR_V6_MODEL_NAME = os.getenv("PPOCR_V6_MODEL_NAME", "PP-OCRv6_medium")
PADDLE_REQUEST_TIMEOUT = float(os.getenv("PADDLE_REQUEST_TIMEOUT", "3600"))
UNLIMITED_OCR_SERVICE_URL = os.getenv("UNLIMITED_OCR_SERVICE_URL", "http://localhost:8083/ocr")
UNLIMITED_OCR_MODEL_NAME = os.getenv("UNLIMITED_OCR_MODEL_NAME", "baidu/Unlimited-OCR")
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
UNLIMITED_OCR_SINGLE_IMAGE_MODE = os.getenv("UNLIMITED_OCR_SINGLE_IMAGE_MODE", "gundam")
UNLIMITED_OCR_MULTI_IMAGE_MODE = os.getenv("UNLIMITED_OCR_MULTI_IMAGE_MODE", "base")
UNLIMITED_OCR_MAX_TOKENS = os.getenv("UNLIMITED_OCR_MAX_TOKENS", "32768")
UNLIMITED_OCR_SGLANG_MAX_TOKENS = os.getenv("UNLIMITED_OCR_SGLANG_MAX_TOKENS", "28672")
OVISOCR2_SERVICE_URL = os.getenv("OVISOCR2_SERVICE_URL", "http://localhost:8084/ocr")
OVISOCR2_MODEL_NAME = os.getenv("OVISOCR2_MODEL_NAME", "ATH-MaaS/OvisOCR2")
OVISOCR2_API_PORT = os.getenv("OVISOCR2_API_PORT", "8084")
OVISOCR2_KV_CACHE_MEMORY_MB = os.getenv("OVISOCR2_KV_CACHE_MEMORY_MB", "512")
OVISOCR2_STARTUP_MEMORY_FRACTION = os.getenv("OVISOCR2_STARTUP_MEMORY_FRACTION", "0.50")
OVISOCR2_MAX_MODEL_LEN = os.getenv("OVISOCR2_MAX_MODEL_LEN", "32768")
OVISOCR2_MAX_NUM_SEQS = os.getenv("OVISOCR2_MAX_NUM_SEQS", "1")
OVISOCR2_MAX_TOKENS = os.getenv("OVISOCR2_MAX_TOKENS", "8192")
OVISOCR2_PDF_DPI = os.getenv("OVISOCR2_PDF_DPI", "200")
OVISOCR2_MAX_PAGES_PER_REQUEST = os.getenv("OVISOCR2_MAX_PAGES_PER_REQUEST", "50")
OVISOCR2_GDN_PREFILL_BACKEND = os.getenv("OVISOCR2_GDN_PREFILL_BACKEND", "triton")
UNLIMITED_OCR_SGLANG_WHEEL_URL = os.getenv(
    "UNLIMITED_OCR_SGLANG_WHEEL_URL",
    "https://github.com/baidu/Unlimited-OCR/raw/main/wheel/sglang-0.0.0.dev11416%2Bg92e8bb79e-py3-none-any.whl",
)
PROJECT_ROOT = Path(__file__).resolve().parent
TASK_DATA_DIR = Path(os.getenv("PANDOCR_TASK_DATA_DIR", "data/tasks")).resolve()
DEFAULT_RUNTIME_SETTINGS_DIR = TASK_DATA_DIR.parent if TASK_DATA_DIR.name == "tasks" else TASK_DATA_DIR
RUNTIME_SETTINGS_FILE = Path(
    os.getenv("PANDOCR_RUNTIME_SETTINGS_FILE", str(DEFAULT_RUNTIME_SETTINGS_DIR / "runtime-settings.json"))
).resolve()
MAX_REQUEST_BYTES = int(float(os.getenv("PANDOCR_MAX_UPLOAD_MB", "512")) * 1024 * 1024)
PANDOCR_HOST = os.getenv("PANDOCR_HOST", "0.0.0.0")
PANDOCR_PORT = int(os.getenv("PANDOCR_PORT", "8000"))
MODEL_CONTROL_MODE = os.getenv("PANDOCR_MODEL_CONTROL", "docker").strip().lower()
MODEL_RUNTIME_STARTUP = os.getenv("PANDOCR_ACTIVE_MODEL_ON_START", "paddleocr-vl-1.6").strip()
DOCKER_SOCKET_PATH = os.getenv("PANDOCR_DOCKER_SOCKET", "/var/run/docker.sock")
MODEL_SWITCH_TIMEOUT = float(os.getenv("PANDOCR_MODEL_SWITCH_TIMEOUT", "1200"))
API_TOKEN = os.getenv("PANDOCR_API_TOKEN", "").strip()
ENABLE_API_DOCS = parse_bool_env("PANDOCR_ENABLE_API_DOCS", "0")
ENFORCE_ORIGIN_CHECK = parse_bool_env("PANDOCR_ENFORCE_ORIGIN_CHECK", "1")
ENABLE_UNLIMITED_OCR = parse_bool_env("PANDOCR_ENABLE_UNLIMITED_OCR", "0")
ENABLE_OVISOCR2 = parse_bool_env("PANDOCR_ENABLE_OVISOCR2", "0")
MODEL_CATALOG_ENV = os.getenv("PANDOCR_MODEL_CATALOG", "").strip()
MAX_CONCURRENT_OCR = parse_positive_int_env("PANDOCR_MAX_CONCURRENT_OCR", "1")
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
    supported = {"paddleocr-vl-1.6", "pp-ocrv6", "unlimited-ocr", "ovisocr2"}
    if MODEL_CATALOG_ENV:
        ids = [model_id for model_id in parse_csv_env("PANDOCR_MODEL_CATALOG", "") if model_id in supported]
    else:
        ids = ["paddleocr-vl-1.6", "pp-ocrv6"]
        if ENABLE_UNLIMITED_OCR:
            ids.append("unlimited-ocr")
        if ENABLE_OVISOCR2:
            ids.append("ovisocr2")

    unique_ids = []
    for model_id in ids:
        if model_id not in unique_ids:
            unique_ids.append(model_id)
    return unique_ids or ["paddleocr-vl-1.6"]


MODEL_CATALOG_IDS = parse_model_catalog()
ENABLE_UNLIMITED_OCR = ENABLE_UNLIMITED_OCR or "unlimited-ocr" in MODEL_CATALOG_IDS
ENABLE_OVISOCR2 = ENABLE_OVISOCR2 or "ovisocr2" in MODEL_CATALOG_IDS

MODEL_RUNTIME_CONFIG = {
    "paddleocr-vl-1.6": {
        "containers": ["paddleocr-vlm-server", "paddleocr-vl-api"],
        "start_order": ["paddleocr-vlm-server", "paddleocr-vl-api"],
        "stop_order": ["paddleocr-vl-api", "paddleocr-vlm-server"],
        "health_url": PADDLE_SERVICE_URL.rsplit("/", 1)[0] + "/health",
    },
    "pp-ocrv6": {
        "containers": ["paddleocr-ocr-api"],
        "start_order": ["paddleocr-ocr-api"],
        "stop_order": ["paddleocr-ocr-api"],
        "health_url": PADDLE_OCR_SERVICE_URL.rsplit("/", 1)[0] + "/health",
    },
}

if ENABLE_UNLIMITED_OCR:
    MODEL_RUNTIME_CONFIG["unlimited-ocr"] = {
        "containers": ["unlimited-ocr-api"],
        "start_order": ["unlimited-ocr-api"],
        "stop_order": ["unlimited-ocr-sglang", "unlimited-ocr-api"],
        "health_url": UNLIMITED_OCR_SERVICE_URL.rsplit("/", 1)[0] + "/health",
    }

if ENABLE_OVISOCR2:
    MODEL_RUNTIME_CONFIG["ovisocr2"] = {
        "containers": ["ovisocr2-api"],
        "start_order": ["ovisocr2-api"],
        "stop_order": ["ovisocr2-api"],
        "health_url": OVISOCR2_SERVICE_URL.rsplit("/", 1)[0] + "/health",
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
}
model_runtime_task: asyncio.Task | None = None
unlimited_ocr_backend_task: asyncio.Task | None = None
unlimited_ocr_runtime_backend = initial_unlimited_ocr_backend()
ocr_active_count = 0


class ModelSwitchRequest(BaseModel):
    modelId: str


class ModelDeployRequest(BaseModel):
    modelId: str
    backend: str | None = None


class UnlimitedOcrBackendRequest(BaseModel):
    backend: str


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
    }
    return [
        models_by_id[model_id]
        for model_id in MODEL_CATALOG_IDS
        if model_id in models_by_id and model_id in MODEL_RUNTIME_CONFIG
    ]


def model_control_available() -> bool:
    return MODEL_CONTROL_MODE == "docker" and Path(DOCKER_SOCKET_PATH).exists()


async def docker_api_request(method: str, path: str, *, timeout: float = 30, **request_kwargs) -> httpx.Response:
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET_PATH)
    async with httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=timeout) as client:
        return await client.request(method, path, **request_kwargs)


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
        return f"ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-genai-{VLM_BACKEND}-server:{VLM_IMAGE_TAG_SUFFIX}"
    if service_name == "paddleocr-vl-api":
        return f"ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-vl:{API_IMAGE_TAG_SUFFIX}"
    if service_name == "paddleocr-ocr-api":
        return "pandocr-ocr-api:latest"
    if service_name == "unlimited-ocr-api":
        return "pandocr-unlimited-ocr-transformers:latest"
    if service_name == "unlimited-ocr-sglang":
        return "pandocr-unlimited-ocr-sglang:latest"
    if service_name == "ovisocr2-api":
        return "pandocr-ovisocr2:latest"
    raise ValueError(f"Unknown service image: {service_name}")


def split_docker_image_ref(image: str) -> tuple[str, str]:
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
    path = f"/images/create?fromImage={quote(repository, safe='')}&tag={quote(tag, safe='')}"
    response = await docker_api_request("POST", path, timeout=3600)
    if response.status_code >= 400:
        raise RuntimeError(f"Docker pull failed for {image}: {response.text}")


def dockerfile_path_for(service_name: str) -> Path:
    dockerfile_names = {
        "paddleocr-ocr-api": "Dockerfile.ocr",
        "unlimited-ocr-api": "Dockerfile.unlimited-ocr",
        "unlimited-ocr-sglang": "Dockerfile.unlimited-ocr-sglang",
        "ovisocr2-api": "Dockerfile.ovisocr2",
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
        return {"API_IMAGE_TAG_SUFFIX": API_IMAGE_TAG_SUFFIX}
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
                f"UNLIMITED_OCR_SERVED_MODEL_NAME={UNLIMITED_OCR_SERVED_MODEL_NAME}",
                f"UNLIMITED_OCR_REQUEST_TIMEOUT={UNLIMITED_OCR_REQUEST_TIMEOUT}",
                f"UNLIMITED_OCR_PDF_DPI={UNLIMITED_OCR_PDF_DPI}",
                f"UNLIMITED_OCR_MAX_PAGES_PER_REQUEST={UNLIMITED_OCR_MAX_PAGES_PER_REQUEST}",
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
    raise ValueError(f"Unknown deploy service: {service_name}")


async def ensure_runtime_service_created(service_name: str) -> None:
    if service_name in {"paddleocr-vlm-server", "paddleocr-vl-api"}:
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
        status["sglangContainer"] = await inspect_container("unlimited-ocr-sglang")
    return status


async def model_runtime_status(model_id: str) -> dict:
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


async def build_model_runtime_payload() -> dict:
    models = {
        model_id: await model_runtime_status(model_id)
        for model_id in MODEL_RUNTIME_CONFIG
    }
    ready_models = [model_id for model_id, status in models.items() if status["ready"]]
    running_models = [model_id for model_id, status in models.items() if status["running"]]
    active_model = ready_models[0] if ready_models else (running_models[0] if running_models else None)
    return {
        "controlMode": MODEL_CONTROL_MODE,
        "controlAvailable": model_control_available(),
        "activeModelId": active_model,
        "defaultModelId": DEFAULT_RUNTIME_MODEL_ID,
        "unlimitedOcrBackend": unlimited_ocr_runtime_backend,
        "unlimitedOcrSupportedBackends": sorted(UNLIMITED_OCR_SUPPORTED_BACKENDS),
        "operation": dict(model_runtime_operation),
        "ocrActiveCount": ocr_active_count,
        "maxConcurrentOcr": MAX_CONCURRENT_OCR,
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


async def ensure_unlimited_ocr_backend_runtime(backend: str, timeout: float) -> None:
    await wait_unlimited_ocr_adapter_http(timeout)
    if backend == "sglang":
        await call_unlimited_ocr_adapter_control("/backend/transformers/unload", timeout=min(180, timeout))
        if model_control_available():
            await ensure_runtime_service_created("unlimited-ocr-sglang")
            await docker_container_action("unlimited-ocr-sglang", "start")
            await wait_container_runtime_ready("unlimited-ocr-sglang", timeout)
        await wait_unlimited_ocr_backend_ready("sglang", timeout)
        return

    if model_control_available():
        await docker_container_action("unlimited-ocr-sglang", "stop")
    await call_unlimited_ocr_adapter_control("/backend/transformers/preload", timeout=timeout)
    await wait_unlimited_ocr_backend_ready("transformers", timeout)


async def activate_model_runtime(model_id: str) -> None:
    if model_id not in MODEL_RUNTIME_CONFIG:
        raise ValueError(f"Unknown model id: {model_id}")
    if not model_control_available():
        raise RuntimeError("Docker model control is not available")

    async with model_runtime_lock:
        set_model_runtime_operation("switching", f"Switching to {model_id}", model_id)
        switch_started_at = time.monotonic()
        try:
            for other_model_id, config in MODEL_RUNTIME_CONFIG.items():
                if other_model_id == model_id:
                    continue
                for container_name in config["stop_order"]:
                    await docker_container_action(container_name, "stop")

            for container_name in MODEL_RUNTIME_CONFIG[model_id]["start_order"]:
                remaining_timeout = max(3, MODEL_SWITCH_TIMEOUT - (time.monotonic() - switch_started_at))
                await docker_container_action(container_name, "start")
                await wait_container_runtime_ready(container_name, remaining_timeout)

            if model_id == "unlimited-ocr":
                remaining_timeout = max(3, MODEL_SWITCH_TIMEOUT - (time.monotonic() - switch_started_at))
                await ensure_unlimited_ocr_backend_runtime(unlimited_ocr_runtime_backend, remaining_timeout)

            remaining_timeout = max(3, MODEL_SWITCH_TIMEOUT - (time.monotonic() - switch_started_at))
            await wait_model_ready(model_id, remaining_timeout)
            set_model_runtime_operation("ready", f"{model_id} is ready", model_id)
        except Exception as err:
            logger.exception("Model runtime switch failed")
            set_model_runtime_operation("error", str(err), model_id)


async def schedule_model_runtime_activation(model_id: str) -> None:
    global model_runtime_task
    if model_id not in MODEL_RUNTIME_CONFIG:
        raise HTTPException(status_code=400, detail="Unknown model id")
    if not model_control_available():
        raise HTTPException(status_code=503, detail="Docker model control is not available")
    async with model_runtime_lock:
        if ocr_active_count > 0:
            raise HTTPException(status_code=409, detail="OCR is running. Wait for the active task before switching models.")
        if model_runtime_task and not model_runtime_task.done():
            model_runtime_task.cancel()
        set_model_runtime_operation("switching", f"Switching to {model_id}", model_id)
        model_runtime_task = asyncio.create_task(activate_model_runtime(model_id))


async def deploy_and_activate_model_runtime(model_id: str, backend: str | None = None) -> None:
    global unlimited_ocr_runtime_backend
    try:
        if model_id == "unlimited-ocr" and backend:
            unlimited_ocr_runtime_backend = normalize_unlimited_ocr_backend(backend)
            save_runtime_settings({"unlimitedOcrBackend": unlimited_ocr_runtime_backend})
        set_model_runtime_operation("switching", f"Deploying {model_id}", model_id)
        await ensure_model_runtime_created(model_id, backend)
        await activate_model_runtime(model_id)
    except Exception as err:
        logger.exception("Model runtime deployment failed")
        set_model_runtime_operation("error", str(err), model_id)


async def schedule_model_runtime_deploy(model_id: str, backend: str | None = None) -> None:
    global model_runtime_task
    if model_id not in MODEL_RUNTIME_CONFIG:
        raise HTTPException(status_code=400, detail="Unknown model id")
    if not model_control_available():
        raise HTTPException(status_code=503, detail="Docker model control is not available")
    async with model_runtime_lock:
        if ocr_active_count > 0:
            raise HTTPException(status_code=409, detail="OCR is running. Wait for the active task before deploying models.")
        if model_runtime_task and not model_runtime_task.done():
            raise HTTPException(status_code=409, detail="Model runtime is already busy. Wait for it to finish.")
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
        if ocr_active_count > 0:
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
    version="0.2.0",
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
    request_origin = f"{request.url.scheme}://{request.url.netloc}".lower()
    origins.add(request_origin)
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

    response = await call_next(request)
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
        "maxUploadBytes": MAX_REQUEST_BYTES,
        "authRequired": bool(API_TOKEN),
        "originProtection": ENFORCE_ORIGIN_CHECK,
        "maxConcurrentOcr": MAX_CONCURRENT_OCR,
    }


@app.get("/api/model-runtime")
async def get_model_runtime():
    return await build_model_runtime_payload()


@app.post("/api/model-runtime/switch")
async def switch_model_runtime(request: ModelSwitchRequest):
    await schedule_model_runtime_activation(request.modelId)
    return await build_model_runtime_payload()


@app.post("/api/model-runtime/deploy")
async def deploy_model_runtime(request: ModelDeployRequest):
    await schedule_model_runtime_deploy(request.modelId, request.backend)
    return await build_model_runtime_payload()


@app.get("/api/unlimited-ocr/backend")
async def get_unlimited_ocr_backend():
    if not ENABLE_UNLIMITED_OCR:
        raise HTTPException(status_code=404, detail="Unlimited-OCR is not enabled")
    return {
        "backend": unlimited_ocr_runtime_backend,
        "supportedBackends": sorted(UNLIMITED_OCR_SUPPORTED_BACKENDS),
        "runtime": await model_runtime_status("unlimited-ocr"),
    }


@app.post("/api/unlimited-ocr/backend")
async def switch_unlimited_ocr_backend(request: UnlimitedOcrBackendRequest):
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

        file_bytes = await read_upload_bytes(upload, MAX_REQUEST_BYTES)
        ocr_request = OCRRequest(
            fileType=parse_optional_int(form.get("fileType")),
            useLayoutDetection=parse_bool(form.get("useLayoutDetection"), True),
            useDocUnwarping=parse_bool(form.get("useDocUnwarping"), False),
            useDocOrientationClassify=parse_bool(form.get("useDocOrientationClassify"), False),
            useTextlineOrientation=parse_bool(form.get("useTextlineOrientation"), False),
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


async def acquire_ocr_slot(model_id: str, not_ready_message: str) -> None:
    global ocr_active_count
    await ocr_semaphore.acquire()
    try:
        async with model_runtime_lock:
            operation = model_runtime_operation
            if operation.get("state") == "switching":
                target = operation.get("targetModelId") or "requested model"
                raise HTTPException(status_code=409, detail=f"Model runtime is switching to {target}. Try again when it is ready.")
            runtime = await model_runtime_status(model_id)
            if not runtime["ready"]:
                raise HTTPException(status_code=503, detail=not_ready_message)
            ocr_active_count += 1
    except Exception:
        ocr_semaphore.release()
        raise


async def release_ocr_slot() -> None:
    global ocr_active_count
    async with model_runtime_lock:
        ocr_active_count = max(0, ocr_active_count - 1)
    ocr_semaphore.release()


async def run_ocr_request(ocr_request: OCRRequest, raw_input: RawOCRInput) -> dict:
    await acquire_ocr_slot(
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
        await release_ocr_slot()


async def run_ppocrv6_request(ocr_request: OCRRequest, raw_input: RawOCRInput) -> dict:
    await acquire_ocr_slot(
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
        await release_ocr_slot()


async def run_unlimited_ocr_request(ocr_request: OCRRequest, raw_input: RawOCRInput) -> dict:
    if not ENABLE_UNLIMITED_OCR:
        raise HTTPException(status_code=404, detail="Unlimited-OCR is not enabled")

    await acquire_ocr_slot(
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
        await release_ocr_slot()


async def run_ovisocr2_request(ocr_request: OCRRequest, raw_input: RawOCRInput) -> dict:
    if not ENABLE_OVISOCR2:
        raise HTTPException(status_code=404, detail="OvisOCR2 is not enabled")

    await acquire_ocr_slot(
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
        await release_ocr_slot()


async def stream_unlimited_ocr_events(ocr_request: OCRRequest, raw_input: RawOCRInput):
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
        await release_ocr_slot()


def validate_proxy_input_size(raw_input: RawOCRInput) -> int:
    base64_data = normalize_raw_input_to_base64(raw_input)
    if MAX_REQUEST_BYTES > 0 and len(base64_data) > int(MAX_REQUEST_BYTES * 4 / 3) + 1024:
        max_mb = MAX_REQUEST_BYTES / 1024 / 1024
        raise HTTPException(status_code=413, detail=f"OCR input is too large. Max upload size is {max_mb:.0f} MB.")
    return len(base64_data)


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


@app.post("/api/unlimited-ocr/stream")
async def proxy_unlimited_ocr_stream(request: Request):
    try:
        ocr_request, raw_image = await parse_ocr_input(request)
        base64_size = validate_proxy_input_size(raw_image)
        logger.info("Received streaming Unlimited-OCR request. Base64 input size: %s bytes", base64_size)
        if not ENABLE_UNLIMITED_OCR:
            raise HTTPException(status_code=404, detail="Unlimited-OCR is not enabled")
        await acquire_ocr_slot(
            "unlimited-ocr",
            "Unlimited-OCR service is not ready. Switch to this model and wait for it to become ready.",
        )
        return StreamingResponse(
            stream_unlimited_ocr_events(ocr_request, raw_image),
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
