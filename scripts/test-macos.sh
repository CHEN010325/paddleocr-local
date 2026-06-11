#!/usr/bin/env bash

set -euo pipefail

PANDOCR_HOST="${PANDOCR_HOST:-127.0.0.1}"
PANDOCR_PORT="${PANDOCR_PORT:-8000}"
PADDLEX_HOST="${PADDLEX_HOST:-127.0.0.1}"
PADDLEX_PORT="${PADDLEX_PORT:-8081}"
PANDOCR_MACOS_BACKEND="${PANDOCR_MACOS_BACKEND:-native}"
MLX_HOST="${MLX_HOST:-127.0.0.1}"
MLX_PORT="${MLX_PORT:-8111}"

echo "Testing PandOCR WebUI..."
curl -fsS "http://${PANDOCR_HOST}:${PANDOCR_PORT}/" >/dev/null
echo "WebUI OK"

echo "Testing model endpoint..."
curl -fsS "http://${PANDOCR_HOST}:${PANDOCR_PORT}/api/models"
echo

echo "Testing PaddleOCR-VL API health..."
curl -fsS "http://${PADDLEX_HOST}:${PADDLEX_PORT}/health"
echo

if [[ "$PANDOCR_MACOS_BACKEND" == "mlx" ]]; then
  echo "Testing MLX-VLM model endpoint..."
  curl -fsS "http://${MLX_HOST}:${MLX_PORT}/v1/models"
  echo
fi

echo "macOS services OK"
