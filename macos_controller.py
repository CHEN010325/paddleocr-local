"""Local macOS model controller.

The macOS runtimes share a single Apple GPU/Unified Memory pool, so only one
model process is kept alive.  This controller provides the same small control
surface as the Docker controller while switching model processes through the
existing start/stop scripts.
"""

import asyncio
import os
import secrets
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ROOT_DIR = Path(__file__).resolve().parent
PORT = int(os.getenv("PANDOCR_MACOS_CONTROLLER_PORT", "8001"))
TOKEN = os.getenv("PANDOCR_MODEL_CONTROLLER_TOKEN", "").strip()
MODEL_IDS = ("paddleocr-vl-1.6", "pp-ocrv6", "ovisocr2", "hpd-parsing")
ACTIVE_MODEL = os.getenv("PANDOCR_ACTIVE_MODEL_ON_START", "paddleocr-vl-1.6")
switch_lock = asyncio.Lock()
operation = {"targetModelId": ACTIVE_MODEL, "state": "idle", "message": ""}

# Import the status/response logic with all logical models visible.  The
# controller itself does not start models during import.
os.environ.setdefault("PANDOCR_MODEL_CONTROL", "none")
os.environ["PANDOCR_MODEL_CATALOG"] = ",".join(MODEL_IDS)
os.environ["PANDOCR_ENABLE_PADDLEOCR_VL"] = "1"
os.environ["PANDOCR_ENABLE_PPOCRV6"] = "1"
os.environ["PANDOCR_ENABLE_HPD_PARSING"] = "1"
os.environ["PANDOCR_ENABLE_OVISOCR2"] = "1"
import server  # noqa: E402


def auth_error(request: Request) -> JSONResponse | None:
    supplied = request.headers.get("x-pandocr-controller-token", "")
    if not TOKEN or not supplied or not secrets.compare_digest(supplied, TOKEN):
        return JSONResponse(status_code=401, content={"detail": "Invalid controller token"})
    return None


def model_env(model_id: str) -> dict[str, str]:
    flags = {
        "PANDOCR_ENABLE_PADDLEOCR_VL": "1" if model_id == "paddleocr-vl-1.6" else "0",
        "PANDOCR_ENABLE_PPOCRV6": "1" if model_id == "pp-ocrv6" else "0",
        "PANDOCR_ENABLE_HPD_PARSING": "1" if model_id == "hpd-parsing" else "0",
        "PANDOCR_ENABLE_OVISOCR2": "1" if model_id == "ovisocr2" else "0",
    }
    return {
        **os.environ,
        **flags,
        "PANDOCR_ACTIVE_MODEL_ON_START": model_id,
        "PANDOCR_MODEL_CATALOG": ",".join(MODEL_IDS),
        "PANDOCR_SKIP_WEB": "1",
        "PANDOCR_MODEL_CONTROL": "none",
        "HPD_PARSING_MAX_TOKENS": os.getenv("HPD_PARSING_MAX_TOKENS", "4096"),
        "HPD_PARSING_DEVICE": os.getenv("HPD_PARSING_DEVICE", "mps"),
    }


async def switch_model(model_id: str) -> None:
    if model_id not in MODEL_IDS:
        raise ValueError(f"Unknown model id: {model_id}")
    async with switch_lock:
        operation.update(targetModelId=model_id, state="switching", message=f"Switching to {model_id}")
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["bash", "scripts/stop-macos-models.sh"],
                cwd=ROOT_DIR,
                env=os.environ.copy(),
                check=True,
                timeout=120,
                capture_output=True,
                text=True,
            )
            await asyncio.to_thread(
                subprocess.run,
                ["bash", "scripts/start-macos.sh"],
                cwd=ROOT_DIR,
                env=model_env(model_id),
                check=True,
                timeout=1200,
                capture_output=True,
                text=True,
            )
            operation.update(state="ready", message=f"{model_id} is ready")
        except Exception as exc:
            operation.update(state="error", message=str(exc))
            raise


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="PaddleOCR Local macOS Model Controller", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    error = auth_error(request)
    if error:
        return error
    return {"status": "ok", "controlAvailable": True}


@app.get("/model-runtime")
async def model_runtime(request: Request):
    error = auth_error(request)
    if error:
        return error
    payload = await server.build_model_runtime_payload()
    payload["controlMode"] = "macos"
    payload["controlAvailable"] = True
    payload["operation"] = dict(operation)
    return payload


@app.post("/model-runtime/switch")
async def switch(request: Request):
    error = auth_error(request)
    if error:
        return error
    body = await request.json()
    model_id = str(body.get("modelId", "")).strip()
    try:
        await switch_model(model_id)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": str(exc), "operation": dict(operation)})
    return await model_runtime(request)


@app.post("/ocr-leases/acquire")
async def acquire_lease(request: Request):
    error = auth_error(request)
    if error:
        return error
    body = await request.json()
    return await server.acquire_controller_ocr_lease(str(body.get("modelId", "")))


@app.delete("/ocr-leases/{lease_id}")
async def release_lease(lease_id: str, request: Request):
    error = auth_error(request)
    if error:
        return error
    return {"ok": True, "released": await server.release_controller_ocr_lease(lease_id)}
