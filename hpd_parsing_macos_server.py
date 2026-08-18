"""HPD-Parsing Transformers/MPS OpenAI-compatible server for Apple Silicon."""

import base64
import io
import os
import sys
import threading
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException
from PIL import Image
from transformers import AutoModel, AutoTokenizer

MODEL_ID = os.getenv("HPD_PARSING_MODEL_NAME", "PaddlePaddle/HPD-Parsing")
MODEL_NAME = os.getenv("HPD_PARSING_SERVED_MODEL_NAME", "HPD-Parsing")
DEVICE = os.getenv("HPD_PARSING_DEVICE", "mps" if torch.backends.mps.is_available() else "cpu")
DTYPE = getattr(torch, os.getenv("HPD_PARSING_DTYPE", "float16"))
MAX_TOKENS = int(os.getenv("HPD_PARSING_MAX_TOKENS", "4096"))
USE_MTP = os.getenv("HPD_PARSING_USE_MTP", "0").lower() in {"1", "true", "yes"}
MODEL_CACHE = os.getenv("HPD_PARSING_HF_HOME", str(Path("model_cache_hpd_parsing_macos").resolve()))
os.environ.setdefault("HF_HOME", MODEL_CACHE)
os.environ.setdefault("MAX_PATCHES_WITH_RESIZE", "true")

app = FastAPI(title="HPD-Parsing macOS runtime", docs_url=None, redoc_url=None)
_model = None
_tokenizer = None
_preprocess = None
_lock = threading.Lock()


def load_runtime():
    global _model, _tokenizer, _preprocess
    if _model is not None:
        return
    # HPD ships image_preprocess.py with the checkpoint; load it from the local
    # snapshot so the official dynamic tiling path is used on macOS as well.
    from huggingface_hub import snapshot_download
    # HPD's custom InternVL class targets Transformers 4.x; Transformers 5.x
    # expects this attribute during generation.
    import transformers.modeling_utils as modeling_utils
    if not hasattr(modeling_utils.PreTrainedModel, "all_tied_weights_keys"):
        modeling_utils.PreTrainedModel.all_tied_weights_keys = {}

    model_path = snapshot_download(MODEL_ID, cache_dir=MODEL_CACHE)
    if model_path not in sys.path:
        sys.path.insert(0, model_path)
    import image_preprocess

    _preprocess = image_preprocess
    _model = AutoModel.from_pretrained(
        model_path,
        trust_remote_code=True,
        torch_dtype=DTYPE,
        low_cpu_mem_usage=True,
    ).eval().to(DEVICE)
    if not isinstance(getattr(_model, "all_tied_weights_keys", None), dict):
        _model.all_tied_weights_keys = {}
    _tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True, use_fast=False)
    if USE_MTP and hasattr(_model, "load_mtp_weights"):
        _model.load_mtp_weights()


def decode_image(url: str) -> Image.Image:
    if "," not in url:
        raise ValueError("Only data URLs are supported")
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")


def generate(image: Image.Image, prompt: str) -> str:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png") as file:
        image.save(file, format="PNG")
        file.flush()
        pixel_values = _preprocess.load_image(file.name).to(dtype=DTYPE, device=DEVICE)
    return _model.generate_hpd(
        _tokenizer,
        pixel_values,
        prompt,
        {"max_new_tokens": MAX_TOKENS},
        use_mtp=USE_MTP,
        num_speculative_tokens=6,
        batch_children=False,
    )


@app.get("/health")
async def health():
    if _model is None:
        return {"status": "starting", "model": MODEL_NAME, "backend": "transformers-mps"}
    return {"status": "ok", "model": MODEL_NAME, "backend": "transformers-mps"}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "paddlepaddle"}]}


@app.post("/v1/chat/completions")
async def chat(payload: dict):
    messages = payload.get("messages") or []
    content = messages[-1].get("content") if messages else None
    if not isinstance(content, list):
        raise HTTPException(status_code=400, detail="HPD requires image content")
    image_url = next((item.get("image_url", {}).get("url") for item in content if item.get("type") == "image_url"), None)
    prompt = next((item.get("text", "document parsing with fork.") for item in content if item.get("type") == "text"), "document parsing with fork.")
    if not image_url:
        raise HTTPException(status_code=400, detail="Missing image_url")
    try:
        image = decode_image(image_url)
        with _lock:
            load_runtime()
            text = generate(image, prompt)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"HPD-Parsing MPS inference failed: {exc}") from exc
    return {"id": "hpd-macos", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": str(text)}, "finish_reason": "stop"}]}
