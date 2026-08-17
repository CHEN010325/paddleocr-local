#!/usr/bin/env bash

set -euo pipefail

PANDOCR_HOST="${PANDOCR_HOST:-127.0.0.1}"
PANDOCR_PORT="${PANDOCR_PORT:-8000}"
PADDLEX_HOST="${PADDLEX_HOST:-127.0.0.1}"
PADDLEX_PORT="${PADDLEX_PORT:-8081}"
PADDLE_OCR_HOST="${PADDLE_OCR_HOST:-127.0.0.1}"
PADDLE_OCR_PORT="${PADDLE_OCR_PORT:-8082}"
PANDOCR_ENABLE_UNLIMITED_OCR="${PANDOCR_ENABLE_UNLIMITED_OCR:-0}"
PANDOCR_ENABLE_OVISOCR2="${PANDOCR_ENABLE_OVISOCR2:-0}"
PANDOCR_ENABLE_PADDLEOCR_VL="${PANDOCR_ENABLE_PADDLEOCR_VL:-1}"
PANDOCR_ENABLE_PPOCRV6="${PANDOCR_ENABLE_PPOCRV6:-0}"
UNLIMITED_OCR_HOST="${UNLIMITED_OCR_HOST:-127.0.0.1}"
UNLIMITED_OCR_API_PORT="${UNLIMITED_OCR_API_PORT:-8083}"
OVISOCR2_HOST="${OVISOCR2_HOST:-127.0.0.1}"
OVISOCR2_API_PORT="${OVISOCR2_API_PORT:-8084}"
PANDOCR_MACOS_BACKEND="${PANDOCR_MACOS_BACKEND:-native}"
MLX_HOST="${MLX_HOST:-127.0.0.1}"
MLX_PORT="${MLX_PORT:-8111}"

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|y|Y|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

echo "Testing PaddleOCR Local WebUI..."
curl -fsS "http://${PANDOCR_HOST}:${PANDOCR_PORT}/" >/dev/null
echo "WebUI OK"

echo "Testing model endpoint..."
curl -fsS "http://${PANDOCR_HOST}:${PANDOCR_PORT}/api/models"
echo

if truthy "$PANDOCR_ENABLE_PADDLEOCR_VL"; then
  echo "Testing PaddleOCR-VL API health..."
  curl -fsS "http://${PADDLEX_HOST}:${PADDLEX_PORT}/health"
  echo
fi

if truthy "$PANDOCR_ENABLE_PPOCRV6"; then
  echo "Testing PP-OCRv6 API health..."
  curl -fsS "http://${PADDLE_OCR_HOST}:${PADDLE_OCR_PORT}/health"
  echo
fi

if truthy "$PANDOCR_ENABLE_PADDLEOCR_VL" && [[ "$PANDOCR_MACOS_BACKEND" == "mlx" ]]; then
  echo "Testing MLX-VLM model endpoint..."
  curl -fsS "http://${MLX_HOST}:${MLX_PORT}/v1/models"
  echo
fi

if truthy "$PANDOCR_ENABLE_UNLIMITED_OCR"; then
  echo "Testing Unlimited-OCR adapter health..."
  curl -fsS "http://${UNLIMITED_OCR_HOST}:${UNLIMITED_OCR_API_PORT}/health"
  echo

  echo "Checking WebUI model catalog includes Unlimited-OCR..."
  python - "$PANDOCR_HOST" "$PANDOCR_PORT" <<'PY'
import json
import sys
import urllib.request

host, port = sys.argv[1:]
with urllib.request.urlopen(f"http://{host}:{port}/api/models", timeout=5) as response:
    payload = json.load(response)
ids = {item.get("id") for item in payload.get("data", [])}
if "unlimited-ocr" not in ids:
    raise SystemExit("Unlimited-OCR is missing from /api/models")
print("Unlimited-OCR model catalog OK")
PY
fi

if truthy "$PANDOCR_ENABLE_OVISOCR2"; then
  echo "Testing OvisOCR2 adapter health..."
  curl -fsS "http://${OVISOCR2_HOST}:${OVISOCR2_API_PORT}/health"
  echo

  echo "Checking WebUI model catalog includes OvisOCR2..."
  python - "$PANDOCR_HOST" "$PANDOCR_PORT" <<'PY'
import json
import sys
import urllib.request

host, port = sys.argv[1:]
with urllib.request.urlopen(f"http://{host}:{port}/api/models", timeout=5) as response:
    payload = json.load(response)
ids = {item.get("id") for item in payload.get("data", [])}
if "ovisocr2" not in ids:
    raise SystemExit("OvisOCR2 is missing from /api/models")
print("OvisOCR2 model catalog OK")
PY
fi

echo "macOS services OK"
