# PandOCR - PaddleOCR-VL 1.6 WebUI

**Language / 语言**: English | [简体中文](README.zh-CN.md)

PandOCR is a lightweight Web frontend for PaddleOCR-VL and PP-OCRv6. The frontend handles file upload, queueing, preview, model switching, and download, while the FastAPI backend serves static files, converts Office files to PDF, and proxies requests. OCR inference runs in separate PaddleOCR services. The NVIDIA path uses official Docker services, and the macOS Apple Silicon path uses local PaddleX/MLX services.

## Current Architecture

```text
Browser
  -> pandocr-web:8000
       - FastAPI
       - static WebUI
       - Office to PDF conversion
       - PaddleOCR-VL request proxy
       - PP-OCRv6 OCR request proxy
  -> PaddleOCR services
       - NVIDIA: paddleocr-vl-api + paddleocr-ocr-api + paddleocr-vlm-server in docker compose
       - macOS: local paddlex --serve, optionally with mlx_vlm.server
```

The NVIDIA Compose stack keeps four services:

- `pandocr-web`
- `paddleocr-vl-api`
- `paddleocr-ocr-api`
- `paddleocr-vlm-server`

For single-GPU machines, the Docker deployment keeps only one OCR model hot-loaded by default. `pandocr-web` stays online and controls the model containers through the Docker socket: selecting `PaddleOCR-VL 1.6` starts `paddleocr-vlm-server` + `paddleocr-vl-api` and stops `paddleocr-ocr-api`; selecting `PP-OCRv6` does the reverse. The UI polls this runtime state in real time.

The project no longer includes rerank/reranker services, and it no longer installs Paddle/PaddleX inside the Web container.

## Features

- Supports image, PDF, PPT/PPTX, and DOC/DOCX uploads.
- Supports model switching between `PaddleOCR-VL 1.6` document parsing and `PP-OCRv6` text OCR, with Docker-based on-demand start/stop for single-GPU deployments.
- Sends PDFs to PaddleOCR-VL page by page, making it easier to compare with the official online parsing result and reliably keep the raw JSON for each page.
- Renders PP-OCRv6 results with an official-style visual OCR layer: source/result pages stay aligned, scrolling and zooming are synchronized, recognized text can be copied or corrected, and raw JSON remains available.
- Persists parsing tasks locally under `data/tasks/`, so history remains available after refreshing the page. Deleting a task also removes the local record.
- Markdown preview supports horizontally scrollable tables, KaTeX math rendering, and correction for literal `\n` line breaks in OCR output.
- Supports parsing options including layout detection, chart recognition, document rectification, orientation recognition, seal recognition, formula numbering, and Markdown tag ignoring.
- Downloads package both Markdown output and OCR-extracted images.

## Deployment

This project supports two deployment paths. Do not mix them:

- **NVIDIA Docker version**: for Linux/Windows Docker environments with an NVIDIA GPU, using the official PaddleOCR-VL Docker services.
- **macOS Apple Silicon version**: for Apple M1/M2/M3/M4 chips, following the official Apple Silicon flow with local PaddlePaddle + PaddleX serving, optionally accelerated by MLX-VLM.

### Option 1: NVIDIA Docker

For Windows NVIDIA users, the recommended path is the one-click script:

```powershell
.\windows-one-click.bat
```

It checks Docker, detects the NVIDIA GPU, selects `env.txt` or `env.docker`, pulls the official PaddleOCR-VL images, builds `pandocr-web`, clears old containers, creates all model containers without starting both models, starts the WebUI, waits for the active model health check, and prints key `paddleocr-vlm-server`, `paddleocr-vl-api`, `paddleocr-ocr-api`, and `pandocr-web` logs on failure.

Useful one-click options:

```powershell
.\windows-one-click.bat -DryRun
.\windows-one-click.bat -GpuId 1
.\windows-one-click.bat -EnvFile env.docker
```

Manual deployment is still available:

Choose the environment file based on your GPU model:

| GPU | Recommended env file | Image tag |
| --- | --- | --- |
| RTX 30 series | `env.docker` | `latest-nvidia-gpu-offline` |
| RTX 40 series | `env.docker` | `latest-nvidia-gpu-offline` |
| RTX 50 series / Blackwell | `env.txt` | `latest-nvidia-gpu-sm120-offline` |

The commands below use `env.txt` for RTX 50 series as an example. For RTX 30/40 series, replace `env.txt` with `env.docker`.

```powershell
docker compose --env-file env.txt pull paddleocr-vlm-server paddleocr-vl-api
docker compose --env-file env.txt build paddleocr-ocr-api pandocr-web
docker compose --env-file env.txt up -d --no-start
docker compose --env-file env.txt start pandocr-web
```

Keep this `up -d --no-start` then `start pandocr-web` order for single-GPU deployments. Starting the whole compose stack with a plain `docker compose up -d` can hot-load PaddleOCR-VL and PP-OCRv6 at the same time and waste VRAM. After the WebUI is online, use the top-right model selector to switch models; the UI calls `/api/model-runtime/switch`, starts only the selected model containers, stops the inactive model containers, and keeps the runtime badge synchronized with the real container state.

Open:

- WebUI: http://localhost:8000
- PaddleOCR-VL API health: http://localhost:8081/health, available when `PaddleOCR-VL 1.6` is the active model.
- PP-OCRv6 API health: http://localhost:8082/health, available when `PP-OCRv6` is the active model.

By default, Compose binds the WebUI and OCR APIs only to `127.0.0.1` to avoid unauthorized LAN access. `pandocr-web` mounts `/var/run/docker.sock` so it can start and stop only the model containers defined in this compose file; treat this as Docker host management access and do not expose the WebUI to untrusted networks without additional controls.

Check status:

```powershell
docker compose --env-file env.txt ps
curl http://localhost:8000/api/model-runtime
curl http://localhost:8000/api/models
```

Common environment variables:

`env.txt` is the current recommended configuration for RTX 50 / Blackwell:

```text
API_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
VLM_BACKEND=vllm
VLM_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
PANDOCR_GPU_DEVICE_ID=0
PADDLEOCR_VL_MODEL_NAME=PaddleOCR-VL-1.6-0.9B
PPOCR_V6_MODEL_NAME=PP-OCRv6_medium
PANDOCR_MODEL_CONTROL=docker
PANDOCR_ACTIVE_MODEL_ON_START=paddleocr-vl-1.6
PANDOCR_MODEL_SWITCH_TIMEOUT=1200
PADDLE_REQUEST_TIMEOUT=3600
PANDOCR_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
PANDOCR_MAX_UPLOAD_MB=512
```

RTX 30/40 series and other non-Blackwell NVIDIA GPUs should use `env.docker`, where both image tags are `latest-nvidia-gpu-offline`.

Useful commands:

```powershell
docker compose --env-file env.txt ps
docker compose --env-file env.txt logs -f pandocr-web
docker compose --env-file env.txt restart pandocr-web
docker compose --env-file env.txt down
```

### Option 2: macOS Apple Silicon

macOS Apple Silicon follows the official PaddleOCR-VL documentation for local deployment and does not use the NVIDIA Docker Compose images. References:

- PaddleOCR-VL Apple Silicon Usage Tutorial: https://www.paddleocr.ai/main/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.html
- PaddleOCR-VL Usage Tutorial: https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html
- PaddleX Serving Guide: https://paddlepaddle.github.io/PaddleX/3.3/en/pipeline_deploy/serving.html

Supported chips:

- Apple M1 / M2 / M3 / M4 series chips (arm64)
- The project scripts check for `Darwin + arm64`, so M1-M4 use the same local Mac deployment path.
- The official PaddleOCR-VL Apple Silicon documentation currently states that accuracy validation has been completed on Apple M4. M1/M2/M3 can use the same path, but actual speed and stability depend on the chip model, memory, system version, and model cache state.

The default official PaddleX pipeline name on Mac is:

```text
PaddleOCR-VL-1.6
```

Do not use the bare `PaddleOCR-VL` name as the Mac default pipeline. In current PaddleX 3.6.x, the bare name maps to the older v1 configuration. `PaddleOCR-VL-1.6` uses `PP-DocLayoutV3`, `PaddleOCR-VL-1.6-0.9B`, and the native PaddlePaddle backend.

One-click deployment (recommended):

```bash
./macos-one-click.command
```

Equivalent Make command:

```bash
make mac-one-click
```

This one-click command checks the Apple Silicon environment, installs macOS dependencies, enables MLX-VLM acceleration by default, starts `mlx_vlm.server` / PaddleX API / PandOCR WebUI, runs health checks, and opens http://127.0.0.1:8000 automatically.

The first startup downloads `PP-DocLayoutV3`, `PaddleOCR-VL-1.6-0.9B`, and MLX model weights. The time required depends on network and disk speed. After the model cache is ready, subsequent runs of the same command reuse the installed environment and running services.

Advanced manual startup:

```bash
make mac-setup
make mac-up
```

Then open:

- WebUI: http://127.0.0.1:8000
- PaddleOCR-VL API health: http://127.0.0.1:8081/health

Test, stop, and view logs:

```bash
make mac-test
make mac-down
make mac-logs
```

If native mode is too slow, install and enable the MLX-VLM path from the official Apple Silicon documentation:

```bash
make mac-setup-mlx
make mac-down
make mac-up-mlx
make mac-test-mlx
```

MLX mode starts three local services:

- `mlx_vlm.server`: `127.0.0.1:8111`
- PaddleX full parsing API: `127.0.0.1:8081`
- PandOCR WebUI: `127.0.0.1:8000`

If Hugging Face downloads are slow, set `HF_TOKEN` to improve Hugging Face rate limits. Startup is much faster after models are cached. To change the MLX port, set `MLX_PORT`; the startup scripts generate the PaddleX configuration from the template.

Common environment variables:

```bash
PANDOCR_HOST=127.0.0.1
PANDOCR_PORT=8000
PADDLEX_HOST=127.0.0.1
PADDLEX_PORT=8081
PADDLEX_PIPELINE=PaddleOCR-VL-1.6
PANDOCR_MACOS_BACKEND=mlx
MLX_HOST=127.0.0.1
MLX_PORT=8111
MLX_MODEL=PaddlePaddle/PaddleOCR-VL-1.6
PADDLEPADDLE_VERSION=3.3.0
STARTUP_TIMEOUT_SECONDS=900
```

If the port is occupied, for example to move the WebUI to `18000`:

```bash
PANDOCR_PORT=18000 make mac-up
```

Local benchmark reference after model caching, excluding first download and cold startup:

| Item | Result |
| --- | --- |
| Device | MacBook Pro, Apple M4 Pro, 12-core CPU (8P+4E), 24GB memory |
| System | macOS 26.5.1, arm64 |
| Environment | Python 3.12.13, PaddlePaddle 3.3.0, PaddleOCR 3.6.0, PaddleX 3.6.1, mlx-vlm 0.6.3 |
| Startup mode | `make mac-up-mlx` |
| Test input | 17KB PNG image, end-to-end request through the WebUI backend `/api/paddleocr-vl-1.6` |
| Five runs | 1.73s / 1.74s / 1.75s / 1.76s / 1.78s |
| Average time | About 1.75s |

Complex PDFs, table/formula-heavy pages, large images, and native mode will be noticeably slower. The first run also needs to download `PP-DocLayoutV3`, `PaddleOCR-VL-1.6-0.9B`, and MLX model weights, with time mainly determined by network and disk speed.

## Main APIs

- `GET /`: WebUI home page.
- `GET /api/models`: Returns available models and their proxy endpoints.
- `GET /api/model-runtime`: Returns active model, readiness, container state, and current switch operation.
- `POST /api/model-runtime/switch`: Starts the selected model containers and stops the inactive model containers when Docker model control is enabled.
- `GET /api/tasks`: Reads the local persistent task summary list without returning large source files or OCR results.
- `GET /api/tasks/{task_id}`: Reads the full details of one task.
- `PUT /api/tasks/{task_id}`: Saves one task to `data/tasks/`.
- `DELETE /api/tasks/{task_id}`: Deletes one local task.
- `DELETE /api/tasks`: Clears local task history.
- `POST /api/convert/to-pdf`: Converts PPT/PPTX/DOC/DOCX to PDF.
- `POST /api/paddleocr-vl-1.6`: Proxies OCR requests to the PaddleOCR-VL layout-parsing service.
- `POST /api/pp-ocrv6`: Proxies OCR requests to the PP-OCRv6 service and returns page images, recognized text lines, boxes, scores, and raw JSON.

## Project Structure

```text
.
|-- server.py
|-- requirements.txt
|-- requirements-macos.txt
|-- requirements-macos-mlx.txt
|-- macos-one-click.command
|-- windows-one-click.bat
|-- Dockerfile
|-- Dockerfile.ocr
|-- docker-compose.yml
|-- data/                  # Local task data directory, not committed by default
|-- env.txt
|-- env.docker
|-- pipeline_config_ocr_v6.yaml
|-- pipeline_config_vllm.yaml
|-- pipeline_config_macos_mlx.template.yaml
|-- scripts/               # Deployment helper scripts
|   |-- windows-one-click.ps1
|-- static/
|   |-- index.html
|   |-- app.js
|   |-- style.css
|   `-- vendor/katex/
|-- QUICKSTART.md
|-- DOCKER_DEPLOY.md
`-- PROJECT_SUMMARY.md
```

## Local Development

When running `server.py` locally outside Docker, set `PANDOCR_MODEL_CONTROL=none` and start the model services yourself. You need an existing PaddleOCR-VL service listening at `http://localhost:8081/layout-parsing`. To use PP-OCRv6 locally, also start a PaddleX OCR service at `http://localhost:8082/ocr` or set `PADDLE_OCR_SERVICE_URL`.

```powershell
pip install -r requirements.txt
python server.py
```

Then open http://localhost:8000.
