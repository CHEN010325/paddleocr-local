#!/bin/bash

set -e

echo "Building PaddleOCR Local Docker images..."

if ! docker info > /dev/null 2>&1; then
    echo "Docker is not running. Please start Docker first."
    exit 1
fi

if ! docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi > /dev/null 2>&1; then
    echo "Warning: Docker GPU support was not detected. PaddleOCR-VL may not run correctly."
    read -r -p "Continue? (y/N) " reply
    if [[ ! "$reply" =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

if [ ! -f "env.txt" ]; then
    echo "env.txt does not exist. Creating RTX 50 / Blackwell defaults..."
    cat > env.txt << EOF
API_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
API_IMAGE_DIGEST=@sha256:0971c409d1cab2b12aa17b76855e36ac8eb9fb1adc97dbeea15e9b09432a4a3b
VLM_BACKEND=vllm
VLM_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
VLM_IMAGE_DIGEST=@sha256:bffd525308facf5dba2f8eca44ab476704a0ae3bfdcba25f77655973e4c0a7ca
PANDOCR_GPU_DEVICE_ID=0
PANDOCR_APP_VERSION=0.2.0
PANDOCR_GIT_COMMIT=
PADDLEOCR_VL_MODEL_NAME=PaddleOCR-VL-1.6-0.9B
PPOCR_V6_MODEL_NAME=PP-OCRv6_medium
PANDOCR_MODEL_CONTROL=docker
PANDOCR_MODEL_CONTROLLER_TOKEN=change-this-to-a-random-long-value
PANDOCR_ACTIVE_MODEL_ON_START=paddleocr-vl-1.6
PANDOCR_MODEL_SWITCH_TIMEOUT=1200
PANDOCR_GPU_RELEASE_TIMEOUT=60
PANDOCR_GPU_RELEASE_STABLE_SAMPLES=3
PANDOCR_GPU_RELEASE_SAMPLE_INTERVAL_MS=500
PANDOCR_GPU_RELEASE_TOLERANCE_MIB=128
PADDLE_REQUEST_TIMEOUT=3600
PANDOCR_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
PANDOCR_MAX_UPLOAD_MB=512
PANDOCR_MAX_HTTP_BODY_MB=0
PANDOCR_API_TOKEN=
PANDOCR_ENABLE_API_DOCS=0
UNLIMITED_OCR_MODEL_REVISION=07dea832e22aefee32ad281d4b80551282e1c168
UNLIMITED_OCR_MAX_RENDER_PIXELS=60000000
OVISOCR2_MODEL_REVISION=65c619d374b55d4152e85150fc1b003700bc1f0c
EOF
fi

RUNTIME_ENV="$(bash ./scripts/prepare-runtime-env.sh env.txt tmp/pandocr-runtime.env)"
unset PANDOCR_MODEL_CONTROLLER_TOKEN
COMPOSE=(docker compose --env-file env.txt --env-file "$RUNTIME_ENV")

echo "Pulling PaddleOCR-VL images..."
"${COMPOSE[@]}" --profile paddleocr-vl pull paddleocr-vlm-server paddleocr-vl-api

echo "Building local images..."
"${COMPOSE[@]}" --profile pp-ocrv6 --profile navidc-ocr build paddleocr-ocr-api navidc-ocr-api pandocr-web pandocr-office-converter

echo "Build complete."
echo ""
echo "Next:"
echo "  ./deploy.sh"
echo "  # deploy.sh creates both core profiles stopped, then lets the controller start exactly one model."
echo "  docker compose --env-file env.txt --env-file tmp/pandocr-runtime.env --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr logs -f"
echo "  docker compose --env-file env.txt --env-file tmp/pandocr-runtime.env --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr down"
