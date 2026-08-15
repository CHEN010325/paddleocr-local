@echo off
REM Build PaddleOCR Local Docker images (Windows)

echo Building PaddleOCR Local Docker images...

docker info >nul 2>&1
if errorlevel 1 (
    echo Docker is not running. Please start Docker Desktop first.
    pause
    exit /b 1
)

if not exist "env.txt" (
    echo env.txt does not exist. Creating RTX 50 / Blackwell defaults...
    (
        echo API_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
        echo API_IMAGE_DIGEST=@sha256:0971c409d1cab2b12aa17b76855e36ac8eb9fb1adc97dbeea15e9b09432a4a3b
        echo VLM_BACKEND=vllm
        echo VLM_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
        echo VLM_IMAGE_DIGEST=@sha256:bffd525308facf5dba2f8eca44ab476704a0ae3bfdcba25f77655973e4c0a7ca
        echo PANDOCR_GPU_DEVICE_ID=0
        echo PADDLEOCR_VL_MODEL_NAME=PaddleOCR-VL-1.6-0.9B
        echo PPOCR_V6_MODEL_NAME=PP-OCRv6_medium
        echo PANDOCR_MODEL_CONTROL=docker
        echo PANDOCR_MODEL_CONTROLLER_TOKEN=change-this-to-a-random-long-value
        echo PANDOCR_ACTIVE_MODEL_ON_START=paddleocr-vl-1.6
        echo PANDOCR_MODEL_SWITCH_TIMEOUT=1200
        echo PADDLE_REQUEST_TIMEOUT=3600
        echo PANDOCR_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
        echo PANDOCR_MAX_UPLOAD_MB=512
        echo PANDOCR_MAX_HTTP_BODY_MB=0
        echo PANDOCR_API_TOKEN=
        echo PANDOCR_ENABLE_API_DOCS=0
        echo UNLIMITED_OCR_MODEL_REVISION=07dea832e22aefee32ad281d4b80551282e1c168
        echo UNLIMITED_OCR_MAX_RENDER_PIXELS=60000000
        echo OVISOCR2_MODEL_REVISION=65c619d374b55d4152e85150fc1b003700bc1f0c
    ) > env.txt
)

echo Pulling PaddleOCR-VL images...
docker compose --env-file env.txt pull paddleocr-vlm-server paddleocr-vl-api

echo Building local images...
docker compose --env-file env.txt build paddleocr-ocr-api pandocr-web pandocr-office-converter

echo Build complete.
echo.
echo Next:
echo   docker compose --env-file env.txt up -d --no-start
echo   docker compose --env-file env.txt start pandocr-controller pandocr-office-converter pandocr-web
echo   docker compose --env-file env.txt logs -f
echo   docker compose --env-file env.txt down
echo.
pause
