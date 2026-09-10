"""User-managed OCR services, independent of the UI and model controllers.

Adapters accept file bytes and options and return the WebUI's document schema.
Adding a wire protocol requires one adapter and an ADAPTERS registry entry.
"""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator


class EngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
    label: str = Field(min_length=1, max_length=120)
    protocol: Literal["openai", "paddle", "unified"]
    url: str
    model: str = ""
    apiKeyEnv: str = ""
    healthUrl: str | None = None
    timeout: float = Field(default=300, gt=0, le=7200, allow_inf_nan=False)
    maxTokens: int = Field(default=8192, ge=1, le=131072)
    prompt: str = "Recognize all text in this document image and return Markdown. Preserve tables and formulas."
    pdfDpi: int = Field(default=150, ge=72, le=300)
    maxPages: int = Field(default=50, ge=1, le=500)
    maxPixels: int = Field(default=20000000, ge=1, le=60000000)

    @model_validator(mode="after")
    def validate_connection(self):
        for value in (self.url, self.healthUrl):
            if value is None:
                continue
            parsed = urlsplit(value)
            # Accessing port validates malformed and out-of-range ports now,
            # instead of crashing the first recognition request.
            parsed.port
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                raise ValueError("Service URLs must be HTTP(S), without credentials or fragments")
        if self.protocol == "openai" and not self.model.strip():
            raise ValueError("OpenAI-compatible engines require a model name")
        return self

    def headers(self):
        if not self.apiKeyEnv:
            return {}
        key = os.getenv(self.apiKeyEnv, "").strip()
        if not key:
            raise HTTPException(503, "OCR service API key environment variable is not set")
        return {"Authorization": f"Bearer {key}"}

    def public(self):
        # Connection URLs and credentials are deliberately server-side only.
        return {
            "id": self.id, "name": self.model or self.label, "label": self.label,
            "kind": "document_parsing", "endpoint": "/api/parse",
            "managed": False, "protocol": self.protocol,
            "capabilities": {"pdf": True, "streaming": False,
                             "layoutOptions": self.protocol == "paddle"},
        }


class EngineSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: str
    engines: list[EngineConfig] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_ids(self):
        ids = [engine.id for engine in self.engines]
        if len(set(ids)) != len(ids) or self.default not in ids:
            raise ValueError("Engine IDs must be unique and default must name an engine")
        return self


def load_engine_settings(path: str) -> EngineSettings | None:
    if not path:
        return None
    # Invalid explicit configuration must fail startup, never deploy a fallback model.
    return EngineSettings.model_validate(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def document_result(pages: list[dict]) -> dict:
    images = {}
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("markdown"), dict) or not isinstance(page["markdown"].get("text"), str):
            raise HTTPException(502, "OCR service returned an invalid document page")
        page_images = page["markdown"].get("images", {})
        if not isinstance(page_images, dict):
            raise HTTPException(502, "OCR service returned invalid page images")
        images.update(page_images)
    return {"markdown": "\n\n".join(page["markdown"]["text"] for page in pages),
            "images": images, "layoutParsingResults": pages}


async def post_json(client, config, payload):
    try:
        response = await client.post(config.url, json=payload, headers=config.headers())
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        return data
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "OCR service request timed out") from exc
    except httpx.HTTPStatusError as exc:
        # Do not expose upstream bodies, which can contain connection credentials.
        raise HTTPException(502, f"OCR service returned HTTP {exc.response.status_code}") from exc
    except httpx.RequestError as exc:
        raise HTTPException(502, "Could not connect to OCR service") from exc
    except ValueError as exc:
        raise HTTPException(502, "OCR service returned invalid JSON") from exc


async def document_adapter(client, config, content, file_type, options):
    payload = {**options, "file": base64.b64encode(content).decode(), "fileType": file_type}
    payload.pop("image", None)
    payload.pop("modelId", None)
    if config.protocol == "unified":
        payload["image"] = payload.pop("file")
    if config.model and config.protocol == "unified":
        payload["modelId"] = config.model
    data = await post_json(client, config, payload)
    result = data.get("result", {}) if config.protocol == "paddle" else data
    pages = result.get("layoutParsingResults") if isinstance(result, dict) else None
    if not isinstance(pages, list) or not pages:
        raise HTTPException(502, "OCR service returned no document pages")
    normalized = document_result(pages)
    if config.protocol == "unified":
        images = data.get("images", {})
        if not isinstance(images, dict):
            raise HTTPException(502, "OCR service returned invalid document images")
        normalized["images"].update(images)
    return normalized


def image_pages(content, file_type, config):
    """Render lazily, limiting page count and pixels before allocating pixmaps."""
    if file_type == 0:
        import fitz
        try:
            document = fitz.open(stream=content, filetype="pdf")
        except Exception as exc:
            raise HTTPException(400, "Invalid PDF") from exc
        with document:
            if document.needs_pass or not len(document):
                raise HTTPException(400, "PDF is encrypted or empty")
            if len(document) > config.maxPages:
                raise HTTPException(413, "PDF exceeds engine maxPages; reduce the batch size")
            scale = config.pdfDpi / 72
            for page in document:
                if (int(page.rect.width * scale) + 1) * (int(page.rect.height * scale) + 1) > config.maxPixels:
                    raise HTTPException(413, "PDF page exceeds engine maxPixels")
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                yield pix.tobytes("png"), pix.width, pix.height
    else:
        try:
            with Image.open(io.BytesIO(content)) as source:
                if source.width * source.height > config.maxPixels:
                    raise HTTPException(413, "Image exceeds engine maxPixels")
                output = io.BytesIO()
                source.convert("RGB").save(output, format="PNG")
                yield output.getvalue(), source.width, source.height
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, "Invalid or oversized image") from exc


async def openai_adapter(client, config, content, file_type, options):
    from starlette.concurrency import run_in_threadpool
    pages = []
    iterator = image_pages(content, file_type, config)
    try:
        while True:
            page = await run_in_threadpool(next, iterator, None)
            if page is None:
                break
            png, width, height = page
            data = await post_json(client, config, {
                "model": config.model, "max_tokens": config.maxTokens,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": config.prompt},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                ]}],
            })
            try:
                text = data["choices"][0]["message"]["content"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("Missing text")
                if data["choices"][0].get("finish_reason") == "length":
                    raise HTTPException(502, "OCR output was truncated; increase maxTokens or reduce page size")
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise HTTPException(502, "OCR service returned no recognition text") from exc
            pages.append({"pageIndex": len(pages), "width": width, "height": height,
                          "markdown": {"text": text, "images": {}}, "prunedResult": {}})
    finally:
        iterator.close()
    return document_result(pages)


ADAPTERS = {"openai": openai_adapter, "paddle": document_adapter, "unified": document_adapter}


async def recognize(config, content, file_type, options):
    if file_type not in (0, 1):
        raise HTTPException(400, "fileType must be 0 (PDF) or 1 (image)")
    async with httpx.AsyncClient(timeout=config.timeout, follow_redirects=False) as client:
        try:
            return await asyncio.wait_for(
                ADAPTERS[config.protocol](client, config, content, file_type, options),
                timeout=config.timeout,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "OCR service request timed out") from exc


async def engine_status(config):
    ready = True
    state = "configured"
    try:
        headers = config.headers()
        if config.healthUrl:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                response = await client.get(config.healthUrl, headers=headers)
                ready = response.is_success
                state = "ready" if ready else "unavailable"
    except (httpx.RequestError, HTTPException):
        ready, state = False, "unavailable"
    return {"id": config.id, "managed": False, "ready": ready, "running": ready,
            "state": state, "containers": []}
