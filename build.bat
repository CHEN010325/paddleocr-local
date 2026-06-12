@echo off
REM Build PandOCR Docker images (Windows)

echo Building PandOCR Docker images...

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
        echo VLM_BACKEND=vllm
        echo VLM_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
        echo PADDLEOCR_VL_MODEL_NAME=PaddleOCR-VL-1.6-0.9B
        echo PADDLE_REQUEST_TIMEOUT=3600
    ) > env.txt
)

echo Pulling PaddleOCR-VL images...
docker compose --env-file env.txt pull paddleocr-vlm-server paddleocr-vl-api

echo Building pandocr-web...
docker compose --env-file env.txt build pandocr-web

echo Build complete.
echo.
echo Next:
echo   docker compose --env-file env.txt up -d
echo   docker compose --env-file env.txt logs -f
echo   docker compose --env-file env.txt down
echo.
pause
