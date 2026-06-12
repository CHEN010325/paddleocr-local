#!/bin/bash

set -e

echo "Building PandOCR Docker images..."

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
VLM_BACKEND=vllm
VLM_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
PADDLEOCR_VL_MODEL_NAME=PaddleOCR-VL-1.6-0.9B
PADDLE_REQUEST_TIMEOUT=3600
EOF
fi

echo "Pulling PaddleOCR-VL images..."
docker compose --env-file env.txt pull paddleocr-vlm-server paddleocr-vl-api

echo "Building pandocr-web..."
docker compose --env-file env.txt build pandocr-web

echo "Build complete."
echo ""
echo "Next:"
echo "  docker compose --env-file env.txt up -d"
echo "  docker compose --env-file env.txt logs -f"
echo "  docker compose --env-file env.txt down"
