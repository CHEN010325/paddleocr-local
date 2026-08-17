import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

os.environ.setdefault("PANDOCR_MODEL_CONTROL", "docker")

import server

CONTROLLER_TOKEN = os.getenv("PANDOCR_MODEL_CONTROLLER_TOKEN", "").strip()
UNSAFE_CONTROLLER_TOKENS = {
    "pandocr-internal-controller-v1",
    "change-this-to-a-random-long-value",
    "请替换为随机长值",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    server.ensure_task_data_dir()
    lease_store_ready = server.load_controller_ocr_leases()
    active_lease_count = server.controller_ocr_lease_count()
    if not lease_store_ready:
        server.logger.error(
            "Controller startup model scheduling is disabled because the OCR lease store is unhealthy"
        )
    elif active_lease_count:
        server.logger.warning(
            "Recovered %d active OCR lease(s); skipping startup model scheduling",
            active_lease_count,
        )
    elif server.model_control_available():
        await server.schedule_model_runtime_activation(server.DEFAULT_RUNTIME_MODEL_ID)
    yield


app = FastAPI(title="PaddleOCR Model Controller", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.middleware("http")
async def authenticate_controller(request: Request, call_next):
    if not CONTROLLER_TOKEN or CONTROLLER_TOKEN in UNSAFE_CONTROLLER_TOKENS:
        return JSONResponse(status_code=503, content={"detail": "Controller token is not configured"})
    supplied = request.headers.get("x-pandocr-controller-token", "")
    if not supplied or not secrets.compare_digest(supplied, CONTROLLER_TOKEN):
        return JSONResponse(status_code=401, content={"detail": "Invalid controller token"})
    return await call_next(request)


@app.get("/health")
async def health():
    lease_store = server.controller_ocr_lease_store_status()
    return {
        "status": "ok" if lease_store["healthy"] else "degraded",
        "controlAvailable": server.model_control_available(),
        "controllerOcrLeaseStore": lease_store,
    }


@app.get("/model-runtime")
async def model_runtime():
    return await server.build_model_runtime_payload()


@app.post("/ocr-leases/acquire")
async def acquire_ocr_lease(request: server.OCRLeaseRequest):
    return await server.acquire_controller_ocr_lease(request.modelId)


@app.delete("/ocr-leases/{lease_id}")
async def release_ocr_lease(lease_id: str):
    released = await server.release_controller_ocr_lease(lease_id)
    return {"ok": True, "released": released}


@app.post("/model-runtime/switch")
async def switch_model(request: server.ModelSwitchRequest):
    await server.schedule_model_runtime_activation(request.modelId)
    return await server.build_model_runtime_payload()


@app.post("/model-runtime/deploy")
async def deploy_model(request: server.ModelDeployRequest):
    await server.schedule_model_runtime_deploy(request.modelId, request.backend)
    return await server.build_model_runtime_payload()


@app.get("/unlimited-ocr/backend")
async def get_backend():
    if not server.ENABLE_UNLIMITED_OCR:
        raise HTTPException(status_code=404, detail="Unlimited-OCR is not enabled")
    return {
        "backend": server.unlimited_ocr_runtime_backend,
        "supportedBackends": sorted(server.UNLIMITED_OCR_SUPPORTED_BACKENDS),
        "runtime": await server.model_runtime_status("unlimited-ocr"),
    }


@app.post("/unlimited-ocr/backend")
async def switch_backend(request: server.UnlimitedOcrBackendRequest):
    await server.schedule_unlimited_ocr_backend_activation(request.backend)
    return await server.build_model_runtime_payload()
