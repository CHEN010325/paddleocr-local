#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${OVISOCR2_MACOS_VENV:-.venv-ovisocr2-macos}"
HOST="${PANDOCR_HOST:-127.0.0.1}"
PORT="${PANDOCR_PORT:-8000}"
OCR_HOST="${OVISOCR2_HOST:-127.0.0.1}"
OCR_PORT="${OVISOCR2_API_PORT:-8084}"
MODEL="${OVISOCR2_MODEL_NAME:-ATH-MaaS/OvisOCR2}"
BACKEND="${OVISOCR2_BACKEND:-mlx}"
HF_HOME="${OVISOCR2_HF_HOME:-$ROOT_DIR/model_cache_ovisocr2_macos}"
STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-900}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "OvisOCR2 environment not found. Run: bash scripts/setup-macos-ovisocr2.sh"
  exit 1
fi

mkdir -p logs run data/tasks "$HF_HOME"

start_detached() {
  local log_file="$1"
  shift
  "$VENV_DIR/bin/python" - "$ROOT_DIR" "$log_file" "$@" <<'PY'
import os
import subprocess
import sys

root_dir, log_file, *command = sys.argv[1:]
with open(log_file, "ab", buffering=0) as stream:
    process = subprocess.Popen(
        command,
        cwd=root_dir,
        stdin=subprocess.DEVNULL,
        stdout=stream,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
        start_new_session=True,
        close_fds=True,
    )
print(process.pid)
PY
}

wait_for_http() {
  local url="$1"
  local name="$2"
  local deadline=$((SECONDS + STARTUP_TIMEOUT_SECONDS))
  until curl -fsS "$url" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "$name did not become ready within ${STARTUP_TIMEOUT_SECONDS}s."
      return 1
    fi
    sleep 2
  done
}

if [[ -f run/ovisocr2.pid ]] && kill -0 "$(cat run/ovisocr2.pid)" >/dev/null 2>&1; then
  echo "OvisOCR2 adapter is already running."
else
  : > logs/ovisocr2.log
  HF_HOME="$HF_HOME" \
  PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}" \
  OVISOCR2_BACKEND="$BACKEND" \
  OVISOCR2_MODEL_NAME="$MODEL" \
  OVISOCR2_MODEL_REVISION="${OVISOCR2_MODEL_REVISION:-65c619d374b55d4152e85150fc1b003700bc1f0c}" \
  OVISOCR2_TRANSFORMERS_DEVICE="${OVISOCR2_TRANSFORMERS_DEVICE:-mps}" \
  OVISOCR2_TRANSFORMERS_DTYPE="${OVISOCR2_TRANSFORMERS_DTYPE:-float16}" \
  OVISOCR2_ATTENTION_IMPLEMENTATION="${OVISOCR2_ATTENTION_IMPLEMENTATION:-eager}" \
  OVISOCR2_PDF_DPI="${OVISOCR2_PDF_DPI:-180}" \
  OVISOCR2_MAX_TOKENS="${OVISOCR2_MAX_TOKENS:-2048}" \
  OVISOCR2_MAX_PIXELS="${OVISOCR2_MAX_PIXELS:-1048576}" \
  OVISOCR2_RESTART_CHECK_INTERVAL="${OVISOCR2_RESTART_CHECK_INTERVAL:-128}" \
    start_detached logs/ovisocr2.log \
      "$VENV_DIR/bin/python" -m uvicorn ovisocr2_adapter:app \
      --host "$OCR_HOST" --port "$OCR_PORT" > run/ovisocr2.pid
fi

wait_for_http "http://${OCR_HOST}:${OCR_PORT}/health" "OvisOCR2 adapter" || {
  tail -n 100 logs/ovisocr2.log
  exit 1
}

if [[ -f run/pandocr-web.pid ]] && kill -0 "$(cat run/pandocr-web.pid)" >/dev/null 2>&1; then
  echo "PaddleOCR Local WebUI is already running."
else
  : > logs/pandocr-web.log
  PANDOCR_MODEL_CATALOG=ovisocr2 \
  PANDOCR_ENABLE_OVISOCR2=1 \
  PANDOCR_MODEL_CONTROL=none \
  PANDOCR_ACTIVE_MODEL_ON_START=ovisocr2 \
  OVISOCR2_MODEL_NAME="$MODEL" \
  OVISOCR2_SERVICE_URL="http://${OCR_HOST}:${OCR_PORT}/ocr" \
  PANDOCR_TASK_DATA_DIR="$ROOT_DIR/data/tasks" \
  PANDOCR_HOST="$HOST" \
  PANDOCR_PORT="$PORT" \
    start_detached logs/pandocr-web.log \
      "$VENV_DIR/bin/python" server.py > run/pandocr-web.pid
fi

wait_for_http "http://${HOST}:${PORT}/api/models" "PaddleOCR Local WebUI" || {
  tail -n 100 logs/pandocr-web.log
  exit 1
}

echo "OvisOCR2 is ready."
echo "WebUI: http://${HOST}:${PORT}"
echo "API: http://${OCR_HOST}:${OCR_PORT}"
