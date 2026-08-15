import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

MAX_REQUEST_BYTES = int(float(os.getenv("PANDOCR_MAX_UPLOAD_MB", "512")) * 1024 * 1024)
UPLOAD_CHUNK_SIZE = 1024 * 1024
SUPPORTED_EXTENSIONS = {".ppt", ".pptx", ".doc", ".docx"}

app = FastAPI(title="PaddleOCR Office Converter", docs_url=None, redoc_url=None, openapi_url=None)


async def write_upload(file: UploadFile, target: Path) -> None:
    total = 0
    with target.open("wb") as stream:
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            total += len(chunk)
            if MAX_REQUEST_BYTES > 0 and total > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Office file exceeds the upload limit")
            stream.write(chunk)


@app.get("/health")
async def health():
    return {"status": "ok", "soffice": bool(shutil.which("soffice"))}


@app.post("/convert")
async def convert(file: UploadFile = File(...)):
    if not shutil.which("soffice"):
        raise HTTPException(status_code=503, detail="LibreOffice is unavailable")
    filename = Path(file.filename or "upload").name
    if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported Office file type")
    with tempfile.TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / filename
        await write_upload(file, input_path)
        try:
            result = await run_in_threadpool(
                subprocess.run,
                ["soffice", "--headless", "--convert-to", "pdf", "--outdir", temp_dir, str(input_path)],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired as error:
            raise HTTPException(status_code=504, detail="Office conversion timed out") from error
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="Office conversion failed")
        outputs = list(Path(temp_dir).glob("*.pdf"))
        if not outputs:
            raise HTTPException(status_code=500, detail="PDF file was not generated")
        return Response(content=outputs[0].read_bytes(), media_type="application/pdf")
