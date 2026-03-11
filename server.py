import os
import base64
import httpx
import docker
import subprocess
import tempfile
import shutil
import io
from PIL import Image
from typing import List, Optional
from pathlib import Path
from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

# Docker Client
try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"Warning: Could not connect to Docker daemon: {e}")
    docker_client = None

# Service Groups
SERVICE_GROUPS = {
    "ocr": ["paddleocr-vlm-server", "paddleocr-vl-api"],
    "rerank": ["reranker-server", "rerank-api"]
}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Target the Docker Compose Pipeline Service (Standard Port 8081 mapped to 8080)
PADDLE_SERVICE_URL = os.getenv("PADDLE_SERVICE_URL", "http://localhost:8081/layout-parsing")

# Create directory for OCR images
OCR_IMAGES_DIR = Path("static/ocr_images")
OCR_IMAGES_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

# Reranker 服务地址 (Docker 内部网络可以使用服务名 reranker-server)
RERANKER_SERVICE_URL = os.getenv("RERANKER_SERVICE_URL", "http://reranker-server:8000/v1/score")
RERANKER_MODEL_NAME = os.getenv("RERANKER_MODEL_NAME", "Qwen/Qwen3-Reranker-0.6B")

# Qwen3-Reranker Prompt 模板
RERANK_PREFIX = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
RERANK_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
RERANK_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"

class RerankRequest(BaseModel):
    query: str
    documents: List[str]
    top_k: Optional[int] = None

@app.post("/api/rerank")
async def proxy_rerank(request: RerankRequest):
    """
    Rerank documents using Qwen3-Reranker model.
    Automatically handles prompt formatting.
    """
    try:
        # 1. 构造带指令的输入
        text_1_list = []
        text_2_list = []
        
        for doc in request.documents:
            # 格式化 Query 和 Document
            query_part = f"{RERANK_PREFIX}<Instruct>: {RERANK_INSTRUCTION}\n<Query>: {request.query}\n"
            doc_part = f"<Document>: {doc}{RERANK_SUFFIX}"
            
            text_1_list.append(query_part)
            text_2_list.append(doc_part)
            
        # 2. 构造 vLLM Request Payload
        payload = {
            "model": RERANKER_MODEL_NAME,
            "text_1": text_1_list,
            "text_2": text_2_list
        }
        
        # 3. 发送请求给 Reranker 服务
        # 使用 httpx 异步调用
        print(f"Sending request to Reranker Service at {RERANKER_SERVICE_URL}...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(RERANKER_SERVICE_URL, json=payload)
            
            if resp.status_code != 200:
                print(f"Reranker Error: {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail=f"Reranker service error: {resp.text}")
                
            result = resp.json()
            
        scores_data = result.get('data', [])
        
        # 4. 解析结果并排序
        reranked_results = []
        for item in scores_data:
            idx = item.get('index')
            score = item.get('score')
            if idx is not None and idx < len(request.documents):
                reranked_results.append({
                    "index": idx,
                    "score": score,
                    "document": request.documents[idx]
                })
        
        # Sort by score descending
        reranked_results.sort(key=lambda x: x["score"], reverse=True)
        
        # If top_k is specified, slice the results
        if request.top_k:
            reranked_results = reranked_results[:request.top_k]
            
        return {"results": reranked_results}
        
    except Exception as e:
        print(f"Error in rerank proxy: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def read_root():
    return FileResponse("static/index.html")

@app.get("/api/models")
async def get_models():
    """Mock response for frontend compatibility"""
    return {"data": [{"id": "PaddleOCR-VL-1.5-0.9B"}]}

@app.get("/api/services/status")
async def get_services_status():
    """Get the running status of OCR and Rerank services"""
    if not docker_client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    
    status = {}
    for group_name, container_names in SERVICE_GROUPS.items():
        group_status = "stopped"
        running_count = 0
        for name in container_names:
            try:
                container = docker_client.containers.get(name)
                if container.status == "running":
                    running_count += 1
            except docker.errors.NotFound:
                continue
        
        if running_count == len(container_names):
            group_status = "running"
        elif running_count > 0:
            group_status = "partial"
            
        status[group_name] = group_status
    return status

@app.post("/api/services/{group}/{action}")
async def manage_service(group: str, action: str):
    """Start or Stop a service group (ocr or rerank)"""
    if not docker_client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    
    if group not in SERVICE_GROUPS:
        raise HTTPException(status_code=400, detail=f"Invalid service group: {group}")
    
    if action not in ["start", "stop", "restart"]:
        raise HTTPException(status_code=400, detail=f"Invalid action: {action}")
    
    container_names = SERVICE_GROUPS[group]
    results = []
    
    # Reverse stop order to respect dependencies if needed, 
    # but for simple start/stop it's fine.
    target_names = container_names if action != "stop" else reversed(container_names)
    
    for name in target_names:
        try:
            container = docker_client.containers.get(name)
            if action == "start":
                container.start()
            elif action == "stop":
                container.stop()
            elif action == "restart":
                container.restart()
            results.append({"name": name, "status": "success"})
        except docker.errors.NotFound:
            results.append({"name": name, "status": "not_found"})
        except Exception as e:
            results.append({"name": name, "status": "error", "message": str(e)})
            
    return {"group": group, "action": action, "results": results}

@app.post("/api/convert/to-pdf")
async def convert_to_pdf(file: UploadFile = File(...)):
    """Convert PPT/PPTX to PDF using LibreOffice"""
    print(f"Received conversion request for: {file.filename}")
    
    # Check if soffice is available
    if not shutil.which("soffice"):
        raise HTTPException(status_code=500, detail="LibreOffice (soffice) not found on server. Please install it to support PPT/PPTX conversion.")
    
    # Validate file extension
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.ppt', '.pptx', '.doc', '.docx']:
        raise HTTPException(status_code=400, detail="Only .ppt, .pptx, .doc, and .docx files are supported.")

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = os.path.join(temp_dir, file.filename)
            
            # Save uploaded file
            with open(input_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            
            # Run conversion
            # soffice --headless --convert-to pdf --outdir <outdir> <input>
            cmd = [
                "soffice",
                "--headless",
                "--convert-to", "pdf",
                "--outdir", temp_dir,
                input_path
            ]
            
            print(f"Running conversion command: {' '.join(cmd)}")
            # On Windows, soffice might need full path or shell=True, but in Docker (Linux) it should be in PATH
            # Use subprocess.run
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            
            if result.returncode != 0:
                print(f"Conversion failed: {result.stderr}")
                # Sometimes soffice returns non-zero but works? No, usually strict.
                raise HTTPException(status_code=500, detail=f"Conversion failed: {result.stderr}")
            
            # Find the output PDF
            pdfs = [f for f in os.listdir(temp_dir) if f.lower().endswith(".pdf")]
            
            if not pdfs:
                raise HTTPException(status_code=500, detail="PDF file not generated")
                
            pdf_path = os.path.join(temp_dir, pdfs[0])
            print(f"Conversion successful, sending back: {pdf_path}")
            
            with open(pdf_path, "rb") as f:
                pdf_content = f.read()
                
            return Response(content=pdf_content, media_type="application/pdf")
            
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="File conversion timed out")
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Error during conversion: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

class OCRRequest(BaseModel):
    image: str # Base64 string
    fileType: Optional[int] = None # 0 for PDF, 1 for Image. If None, auto-detect
    useLayoutDetection: bool = True
    useDocUnwarping: bool = False
    useDocOrientationClassify: bool = False
    useChartRecognition: bool = False
    useSealRecognition: bool = True
    formatBlockContent: bool = False
    showFormulaNumber: bool = True
    markdownIgnoreLabels: List[str] = []
    # Advanced parameters
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

@app.post("/api/paddleocr-vl-1.5")
async def proxy_ocr(request: OCRRequest):
    """Proxy request to PaddleOCR-VL Pipeline Service"""
    print(f"Received OCR Request. Image size: {len(request.image)} bytes")
    try:
        # Clean Base64 String
        base64_data = request.image
        if "base64," in base64_data:
            base64_data = base64_data.split("base64,")[1]

        # Determine file type
        file_type = request.fileType
        if file_type is None:
            # Auto-detect PDF by header (JVBERi0 is Base64 for %PDF-)
            if base64_data.startswith("JVBERi0"):
                file_type = 0
                print("Auto-detected PDF input")
            else:
                file_type = 1
                print("Auto-detected Image input")
        
        if file_type == 1:
            try:
                img_bytes = base64.b64decode(base64_data)
                img = Image.open(io.BytesIO(img_bytes))
                if img.format == "GIF":
                    print("GIF detected, converting to static JPEG for OCR...")
                    img.seek(0)
                    rgb_img = img.convert("RGB")
                    buffer = io.BytesIO()
                    rgb_img.save(buffer, format="JPEG", quality=95)
                    base64_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
                    print("GIF conversion successful")
            except Exception as gif_err:
                print(f"GIF conversion skipped: {gif_err}")

        # Construct Payload for the Official Pipeline API
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
            "prettifyMarkdown": True
        }
        
        # Add optional parameters if provided
        optional_params = [
            "markdownIgnoreLabels", "layoutThreshold", "layoutNms", 
            "layoutUnclipRatio", "layoutMergeBboxesMode", "repetitionPenalty", 
            "temperature", "topP", "minPixels", "maxPixels", "visualize"
        ]
        for param in optional_params:
            val = getattr(request, param)
            if val is not None:
                payload[param] = val
        
        print(f"Sending request to Pipeline Service at {PADDLE_SERVICE_URL}...")
        # print(f"Payload keys: {list(payload.keys())}") # For debugging
        
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                PADDLE_SERVICE_URL,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            if resp.status_code != 200:
                print(f"Service Error (HTTP {resp.status_code}): {resp.text}")
                # Provide a more helpful error if it's the specific OpenCV error
                if resp.status_code == 422:
                    print(f"Validation Error Details: {resp.json()}")
                raise HTTPException(status_code=resp.status_code, detail=f"Upstream error: {resp.text}")
            
            data = resp.json()
            
            # Parse Official Response Format
            if "result" in data and "layoutParsingResults" in data["result"]:
                results = data["result"]["layoutParsingResults"]
                full_markdown = ""
                all_images = {} # To hold all base64 images from all pages
                
                for res in results:
                    if "markdown" in res and "text" in res["markdown"]:
                        md_text = res["markdown"]["text"]
                        md_images = res["markdown"].get("images", {})
                        
                        # Just collect images and keep original paths in markdown
                        if md_images:
                            for img_path, img_base64 in md_images.items():
                                # We'll return the original path as key for the client to map
                                all_images[img_path] = img_base64
                        
                        full_markdown += md_text + "\n\n"
                
                return {
                    "markdown": full_markdown,
                    "images": all_images
                }
            else:
                print(f"Unexpected Format: {data}")
                raise HTTPException(status_code=500, detail="Unexpected response format from Pipeline")
            
    except Exception as e:
        print(f"Proxy Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    print(f"Starting server... Target Pipeline: {PADDLE_SERVICE_URL}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
