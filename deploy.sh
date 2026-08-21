#!/bin/bash

set -e

echo "Deploying PaddleOCR Local..."

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "${SCRIPT_DIR}"
BASE_ENV="${SCRIPT_DIR}/env.txt"
RUNTIME_ENV="${SCRIPT_DIR}/tmp/pandocr-runtime.env"

if [ ! -f "${BASE_ENV}" ]; then
    echo "env.txt does not exist. Run build.sh or create the env file first."
    exit 1
fi

# Persist one untracked credential so controller/Web recreations from later
# shells keep using the same token. The helper rejects empty/placeholders and
# refuses an accidental environment-based rotation.
bash "${SCRIPT_DIR}/scripts/prepare-runtime-env.sh" "${BASE_ENV}" "${RUNTIME_ENV}" >/dev/null
unset PANDOCR_MODEL_CONTROLLER_TOKEN

COMPOSE=(docker compose --env-file "${BASE_ENV}" --env-file "${RUNTIME_ENV}" --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr)
CORE_SERVICES=(pandocr-controller pandocr-office-converter pandocr-web paddleocr-vlm-server paddleocr-vl-api paddleocr-ocr-api navidc-ocr-api)

"${COMPOSE[@]}" up -d --no-start --force-recreate "${CORE_SERVICES[@]}"
"${COMPOSE[@]}" start pandocr-controller pandocr-office-converter pandocr-web

echo "Waiting for services..."
sleep 5

echo ""
echo "Service status:"
"${COMPOSE[@]}" ps

echo ""
echo "Health checks:"

if curl -f http://localhost:8000/ > /dev/null 2>&1; then
    echo "pandocr-web (8000) OK"
else
    echo "pandocr-web (8000) not ready"
fi

if curl -f http://localhost:8081/health > /dev/null 2>&1; then
    echo "paddleocr-vl-api (8081) OK"
else
    echo "paddleocr-vl-api (8081) stopped or starting"
fi

if curl -f http://localhost:8082/health > /dev/null 2>&1; then
    echo "paddleocr-ocr-api (8082) OK"
else
    echo "paddleocr-ocr-api (8082) stopped or starting"
fi

echo ""
echo "Done."
echo "WebUI: http://localhost:8000"
echo "VL API:  http://localhost:8081"
echo "OCR API: http://localhost:8082"
echo "Only the selected model runs. On a switch, pandocr-controller fully stops the old model and releases GPU memory before starting the new one."
echo ""
echo "Useful commands:"
echo "  docker compose --env-file \"${BASE_ENV}\" --env-file \"${RUNTIME_ENV}\" --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr logs -f"
echo "  docker compose --env-file \"${BASE_ENV}\" --env-file \"${RUNTIME_ENV}\" --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr logs -f pandocr-web"
echo "  docker compose --env-file \"${BASE_ENV}\" --env-file \"${RUNTIME_ENV}\" --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr restart pandocr-web"
echo "  docker compose --env-file \"${BASE_ENV}\" --env-file \"${RUNTIME_ENV}\" --profile paddleocr-vl --profile pp-ocrv6 --profile navidc-ocr down"
