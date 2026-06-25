#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${PANDOCR_MACOS_VENV:-.venv-macos}"
if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
  echo "Virtual environment not found: $VENV_DIR"
  echo "Run: bash scripts/setup-macos.sh"
  exit 1
fi

source "$VENV_DIR/bin/activate"

mkdir -p logs run data/tasks
touch logs/paddlex.log logs/pandocr-web.log logs/mlx-vlm.log

STATE_FILE="run/macos-services.env"
EXPECTED_STATE_FILE="run/macos-services.expected.env"
GENERATED_MLX_PIPELINE="run/pipeline_config_macos_mlx.generated.yaml"
MLX_PIPELINE_TEMPLATE="pipeline_config_macos_mlx.template.yaml"

PADDLEX_HOST="${PADDLEX_HOST:-127.0.0.1}"
PADDLEX_PORT="${PADDLEX_PORT:-8081}"
PANDOCR_MACOS_BACKEND="${PANDOCR_MACOS_BACKEND:-native}"
MLX_HOST="${MLX_HOST:-127.0.0.1}"
MLX_PORT="${MLX_PORT:-8111}"
MLX_MODEL="${MLX_MODEL:-PaddlePaddle/PaddleOCR-VL-1.6}"
PADDLE_OCR_HOST="${PADDLE_OCR_HOST:-127.0.0.1}"
PADDLE_OCR_PORT="${PADDLE_OCR_PORT:-8082}"
PANDOCR_ENABLE_UNLIMITED_OCR="${PANDOCR_ENABLE_UNLIMITED_OCR:-0}"
UNLIMITED_OCR_HOST="${UNLIMITED_OCR_HOST:-127.0.0.1}"
UNLIMITED_OCR_API_PORT="${UNLIMITED_OCR_API_PORT:-8083}"
UNLIMITED_OCR_MACOS_VENV="${UNLIMITED_OCR_MACOS_VENV:-.venv-unlimited-ocr-macos}"
UNLIMITED_OCR_MODEL_NAME="${UNLIMITED_OCR_MODEL_NAME:-sabafallah/Unlimited-OCR-Universal}"
UNLIMITED_OCR_BACKEND="${UNLIMITED_OCR_BACKEND:-transformers}"
UNLIMITED_OCR_SUPPORTED_BACKENDS="${UNLIMITED_OCR_SUPPORTED_BACKENDS:-transformers}"
UNLIMITED_OCR_PRELOAD="${UNLIMITED_OCR_PRELOAD:-0}"
UNLIMITED_OCR_HF_HOME="${UNLIMITED_OCR_HF_HOME:-$ROOT_DIR/model_cache_unlimited_ocr_macos}"
UNLIMITED_OCR_TRANSFORMERS_DEVICE="${UNLIMITED_OCR_TRANSFORMERS_DEVICE:-auto}"
UNLIMITED_OCR_TRANSFORMERS_DTYPE="${UNLIMITED_OCR_TRANSFORMERS_DTYPE:-auto}"
UNLIMITED_OCR_ATTENTION_IMPLEMENTATION="${UNLIMITED_OCR_ATTENTION_IMPLEMENTATION:-eager}"
UNLIMITED_OCR_DISABLE_XET="${UNLIMITED_OCR_DISABLE_XET:-1}"
UNLIMITED_OCR_HF_HUB_DOWNLOAD_TIMEOUT="${UNLIMITED_OCR_HF_HUB_DOWNLOAD_TIMEOUT:-${HF_HUB_DOWNLOAD_TIMEOUT:-120}}"
UNLIMITED_OCR_HF_HUB_ETAG_TIMEOUT="${UNLIMITED_OCR_HF_HUB_ETAG_TIMEOUT:-${HF_HUB_ETAG_TIMEOUT:-30}}"
UNLIMITED_OCR_PDF_DPI="${UNLIMITED_OCR_PDF_DPI:-180}"
UNLIMITED_OCR_MAX_TOKENS="${UNLIMITED_OCR_MAX_TOKENS:-4096}"
UNLIMITED_OCR_STREAM_HEARTBEAT_SECONDS="${UNLIMITED_OCR_STREAM_HEARTBEAT_SECONDS:-20}"
UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY="${UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY:-1}"
UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE="${UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE:-640}"
UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS="${UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS:-4096}"
PANDOCR_HOST="${PANDOCR_HOST:-127.0.0.1}"
PANDOCR_PORT="${PANDOCR_PORT:-8000}"
PADDLE_REQUEST_TIMEOUT="${PADDLE_REQUEST_TIMEOUT:-3600}"
PANDOCR_MAX_UPLOAD_MB="${PANDOCR_MAX_UPLOAD_MB:-512}"
PANDOCR_MAX_CONCURRENT_OCR="${PANDOCR_MAX_CONCURRENT_OCR:-1}"
PANDOCR_ENFORCE_ORIGIN_CHECK="${PANDOCR_ENFORCE_ORIGIN_CHECK:-1}"
PANDOCR_API_TOKEN="${PANDOCR_API_TOKEN:-}"
PANDOCR_ENABLE_API_DOCS="${PANDOCR_ENABLE_API_DOCS:-0}"
PADDLEOCR_VL_MODEL_NAME="${PADDLEOCR_VL_MODEL_NAME:-PaddleOCR-VL-1.6-0.9B}"
PPOCR_V6_MODEL_NAME="${PPOCR_V6_MODEL_NAME:-PP-OCRv6_medium}"
PANDOCR_CORS_ORIGINS="${PANDOCR_CORS_ORIGINS:-http://localhost:${PANDOCR_PORT},http://127.0.0.1:${PANDOCR_PORT}}"
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="${PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK:-True}"
STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-900}"
PADDLEX_PIPELINE_IS_CUSTOM=0
if [[ -n "${PADDLEX_PIPELINE:-}" ]]; then
  PADDLEX_PIPELINE_IS_CUSTOM=1
fi

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|y|Y|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

case "$PANDOCR_MACOS_BACKEND" in
  native)
    PADDLEX_PIPELINE="${PADDLEX_PIPELINE:-PaddleOCR-VL-1.6}"
    ;;
  mlx)
    PADDLEX_PIPELINE="${PADDLEX_PIPELINE:-$GENERATED_MLX_PIPELINE}"
    ;;
  *)
    echo "Unsupported PANDOCR_MACOS_BACKEND: $PANDOCR_MACOS_BACKEND"
    echo "Supported values: native, mlx"
    exit 1
    ;;
esac

generate_mlx_pipeline_config() {
  python - "$MLX_PIPELINE_TEMPLATE" "$GENERATED_MLX_PIPELINE" "$MLX_HOST" "$MLX_PORT" "$MLX_MODEL" <<'PY'
from pathlib import Path
import sys

template_path, output_path, host, port, model = sys.argv[1:]
template = Path(template_path).read_text(encoding="utf-8")
text = template.replace("__MLX_SERVER_URL__", f"http://{host}:{port}/")
text = text.replace("__MLX_MODEL__", model)
Path(output_path).write_text(text, encoding="utf-8")
PY
}

write_expected_state() {
  cat > "$EXPECTED_STATE_FILE" <<EOF
PANDOCR_MACOS_BACKEND=$PANDOCR_MACOS_BACKEND
PADDLEX_PIPELINE=$PADDLEX_PIPELINE
PADDLEX_HOST=$PADDLEX_HOST
PADDLEX_PORT=$PADDLEX_PORT
PANDOCR_HOST=$PANDOCR_HOST
PANDOCR_PORT=$PANDOCR_PORT
PADDLEOCR_VL_MODEL_NAME=$PADDLEOCR_VL_MODEL_NAME
PPOCR_V6_MODEL_NAME=$PPOCR_V6_MODEL_NAME
PANDOCR_MAX_CONCURRENT_OCR=$PANDOCR_MAX_CONCURRENT_OCR
PANDOCR_ENFORCE_ORIGIN_CHECK=$PANDOCR_ENFORCE_ORIGIN_CHECK
PADDLE_OCR_HOST=$PADDLE_OCR_HOST
PADDLE_OCR_PORT=$PADDLE_OCR_PORT
PANDOCR_ENABLE_UNLIMITED_OCR=$PANDOCR_ENABLE_UNLIMITED_OCR
UNLIMITED_OCR_HOST=$UNLIMITED_OCR_HOST
UNLIMITED_OCR_API_PORT=$UNLIMITED_OCR_API_PORT
UNLIMITED_OCR_MACOS_VENV=$UNLIMITED_OCR_MACOS_VENV
UNLIMITED_OCR_MODEL_NAME=$UNLIMITED_OCR_MODEL_NAME
UNLIMITED_OCR_BACKEND=$UNLIMITED_OCR_BACKEND
UNLIMITED_OCR_SUPPORTED_BACKENDS=$UNLIMITED_OCR_SUPPORTED_BACKENDS
UNLIMITED_OCR_PRELOAD=$UNLIMITED_OCR_PRELOAD
UNLIMITED_OCR_HF_HOME=$UNLIMITED_OCR_HF_HOME
UNLIMITED_OCR_TRANSFORMERS_DEVICE=$UNLIMITED_OCR_TRANSFORMERS_DEVICE
UNLIMITED_OCR_TRANSFORMERS_DTYPE=$UNLIMITED_OCR_TRANSFORMERS_DTYPE
UNLIMITED_OCR_ATTENTION_IMPLEMENTATION=$UNLIMITED_OCR_ATTENTION_IMPLEMENTATION
UNLIMITED_OCR_DISABLE_XET=$UNLIMITED_OCR_DISABLE_XET
UNLIMITED_OCR_HF_HUB_DOWNLOAD_TIMEOUT=$UNLIMITED_OCR_HF_HUB_DOWNLOAD_TIMEOUT
UNLIMITED_OCR_HF_HUB_ETAG_TIMEOUT=$UNLIMITED_OCR_HF_HUB_ETAG_TIMEOUT
UNLIMITED_OCR_PDF_DPI=$UNLIMITED_OCR_PDF_DPI
UNLIMITED_OCR_MAX_TOKENS=$UNLIMITED_OCR_MAX_TOKENS
UNLIMITED_OCR_STREAM_HEARTBEAT_SECONDS=$UNLIMITED_OCR_STREAM_HEARTBEAT_SECONDS
UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY=$UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY
UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE=$UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE
UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS=$UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS
MLX_HOST=$MLX_HOST
MLX_PORT=$MLX_PORT
MLX_MODEL=$MLX_MODEL
EOF
}

pid_from_file() {
  local pid_file="$1"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  echo "$pid"
}

is_running() {
  local pid_file="$1"
  local pid
  pid="$(pid_from_file "$pid_file")" || return 1
  kill -0 "$pid" >/dev/null 2>&1
}

is_expected_process() {
  local pid_file="$1"
  local expected="$2"
  local pid
  pid="$(pid_from_file "$pid_file")" || return 1
  kill -0 "$pid" >/dev/null 2>&1 || return 1
  ps -ww -p "$pid" -o command= 2>/dev/null | grep -Fq "$expected"
}

has_running_service() {
  is_running run/pandocr-web.pid || is_running run/paddlex.pid || is_running run/ppocrv6.pid || is_running run/mlx-vlm.pid || is_running run/unlimited-ocr.pid
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
    sleep 3
  done
}

start_detached() {
  local log_file="$1"
  shift
  python - "$ROOT_DIR" "$log_file" "$@" <<'PY'
import os
import subprocess
import sys

root_dir = sys.argv[1]
log_file = sys.argv[2]
command = sys.argv[3:]

os.makedirs(os.path.dirname(log_file), exist_ok=True)
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

if [[ "$PANDOCR_MACOS_BACKEND" == "mlx" && "$PADDLEX_PIPELINE_IS_CUSTOM" == "0" ]]; then
  generate_mlx_pipeline_config
fi

write_expected_state

if has_running_service && ! cmp -s "$STATE_FILE" "$EXPECTED_STATE_FILE"; then
  echo "Existing macOS services use a different configuration; restarting them."
  bash scripts/stop-macos.sh
  if [[ "$PANDOCR_MACOS_BACKEND" == "mlx" && "$PADDLEX_PIPELINE_IS_CUSTOM" == "0" ]]; then
    generate_mlx_pipeline_config
  fi
  write_expected_state
fi

if [[ "$PANDOCR_MACOS_BACKEND" == "mlx" ]]; then
  if ! command -v mlx_vlm.server >/dev/null 2>&1; then
    echo "mlx_vlm.server was not found."
    echo "Install it with: INSTALL_MLX_VLM=1 bash scripts/setup-macos.sh"
    exit 1
  fi

  if is_expected_process run/mlx-vlm.pid "mlx_vlm.server"; then
    echo "MLX-VLM service already running: $(cat run/mlx-vlm.pid)"
  else
    rm -f run/mlx-vlm.pid
    echo "Starting MLX-VLM service on ${MLX_HOST}:${MLX_PORT} with ${MLX_MODEL}"
    : > logs/mlx-vlm.log
    start_detached logs/mlx-vlm.log \
      mlx_vlm.server \
      --host "$MLX_HOST" \
      --port "$MLX_PORT" \
      --model "$MLX_MODEL" \
      > run/mlx-vlm.pid
  fi

  wait_for_http "http://${MLX_HOST}:${MLX_PORT}/v1/models" "MLX-VLM service" || {
    tail -n 80 logs/mlx-vlm.log || true
    exit 1
  }
fi

if is_expected_process run/paddlex.pid "paddlex --serve"; then
  echo "PaddleX service already running: $(cat run/paddlex.pid)"
else
  rm -f run/paddlex.pid
  echo "Starting PaddleX PaddleOCR-VL service on ${PADDLEX_HOST}:${PADDLEX_PORT} with ${PADDLEX_PIPELINE}"
  : > logs/paddlex.log
  PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="$PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK" \
    start_detached logs/paddlex.log \
      paddlex --serve \
      --pipeline "$PADDLEX_PIPELINE" \
      --device cpu \
      --host "$PADDLEX_HOST" \
      --port "$PADDLEX_PORT" \
      > run/paddlex.pid
fi

wait_for_http "http://${PADDLEX_HOST}:${PADDLEX_PORT}/health" "PaddleX service" || {
  tail -n 80 logs/paddlex.log || true
  exit 1
}

if is_expected_process run/ppocrv6.pid "paddlex --serve"; then
  echo "PP-OCRv6 service already running: $(cat run/ppocrv6.pid)"
else
  rm -f run/ppocrv6.pid
  echo "Starting PP-OCRv6 service on ${PADDLE_OCR_HOST}:${PADDLE_OCR_PORT} with pipeline_config_ocr_v6.yaml"
  : > logs/ppocrv6.log
  PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="$PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK" \
    start_detached logs/ppocrv6.log \
      paddlex --serve \
      --pipeline pipeline_config_ocr_v6.yaml \
      --device cpu \
      --host "$PADDLE_OCR_HOST" \
      --port "$PADDLE_OCR_PORT" \
      > run/ppocrv6.pid
fi

wait_for_http "http://${PADDLE_OCR_HOST}:${PADDLE_OCR_PORT}/health" "PP-OCRv6 service" || {
  tail -n 80 logs/ppocrv6.log || true
  exit 1
}

if truthy "$PANDOCR_ENABLE_UNLIMITED_OCR"; then
  if [[ ! -x "$UNLIMITED_OCR_MACOS_VENV/bin/python" ]]; then
    echo "Unlimited-OCR virtual environment not found: $UNLIMITED_OCR_MACOS_VENV"
    echo "Run: bash scripts/setup-macos-unlimited-ocr.sh"
    exit 1
  fi

  mkdir -p "$UNLIMITED_OCR_HF_HOME"
  if is_expected_process run/unlimited-ocr.pid "unlimited_ocr_adapter:app"; then
    echo "Unlimited-OCR adapter already running: $(cat run/unlimited-ocr.pid)"
  else
    rm -f run/unlimited-ocr.pid
    echo "Starting Unlimited-OCR adapter on ${UNLIMITED_OCR_HOST}:${UNLIMITED_OCR_API_PORT} with ${UNLIMITED_OCR_MODEL_NAME}"
    : > logs/unlimited-ocr.log
    HF_HOME="$UNLIMITED_OCR_HF_HOME" \
    HF_HUB_DISABLE_XET="$UNLIMITED_OCR_DISABLE_XET" \
    HF_HUB_DOWNLOAD_TIMEOUT="$UNLIMITED_OCR_HF_HUB_DOWNLOAD_TIMEOUT" \
    HF_HUB_ETAG_TIMEOUT="$UNLIMITED_OCR_HF_HUB_ETAG_TIMEOUT" \
    PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}" \
    UNLIMITED_OCR_MODEL_NAME="$UNLIMITED_OCR_MODEL_NAME" \
    UNLIMITED_OCR_BACKEND="$UNLIMITED_OCR_BACKEND" \
    UNLIMITED_OCR_SUPPORTED_BACKENDS="$UNLIMITED_OCR_SUPPORTED_BACKENDS" \
    UNLIMITED_OCR_PRELOAD="$UNLIMITED_OCR_PRELOAD" \
    UNLIMITED_OCR_TRANSFORMERS_DEVICE="$UNLIMITED_OCR_TRANSFORMERS_DEVICE" \
    UNLIMITED_OCR_TRANSFORMERS_DTYPE="$UNLIMITED_OCR_TRANSFORMERS_DTYPE" \
    UNLIMITED_OCR_ATTENTION_IMPLEMENTATION="$UNLIMITED_OCR_ATTENTION_IMPLEMENTATION" \
    UNLIMITED_OCR_PDF_DPI="$UNLIMITED_OCR_PDF_DPI" \
    UNLIMITED_OCR_MAX_TOKENS="$UNLIMITED_OCR_MAX_TOKENS" \
    UNLIMITED_OCR_STREAM_HEARTBEAT_SECONDS="$UNLIMITED_OCR_STREAM_HEARTBEAT_SECONDS" \
    UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY="$UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY" \
    UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE="$UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE" \
    UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS="$UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS" \
    PANDOCR_RUNTIME_SETTINGS_FILE="$ROOT_DIR/data/runtime-settings.json" \
      start_detached logs/unlimited-ocr.log \
        "$UNLIMITED_OCR_MACOS_VENV/bin/python" -m uvicorn unlimited_ocr_adapter:app \
        --host "$UNLIMITED_OCR_HOST" \
        --port "$UNLIMITED_OCR_API_PORT" \
        > run/unlimited-ocr.pid
  fi

  wait_for_http "http://${UNLIMITED_OCR_HOST}:${UNLIMITED_OCR_API_PORT}/health" "Unlimited-OCR adapter" || {
    tail -n 80 logs/unlimited-ocr.log || true
    exit 1
  }
fi

if is_expected_process run/pandocr-web.pid "server.py"; then
  echo "PaddleOCR Local Web service already running: $(cat run/pandocr-web.pid)"
else
  rm -f run/pandocr-web.pid
  echo "Starting PaddleOCR Local WebUI on ${PANDOCR_HOST}:${PANDOCR_PORT}"
  : > logs/pandocr-web.log
  PADDLE_SERVICE_URL="http://${PADDLEX_HOST}:${PADDLEX_PORT}/layout-parsing" \
  PADDLE_OCR_SERVICE_URL="http://${PADDLE_OCR_HOST}:${PADDLE_OCR_PORT}/ocr" \
  UNLIMITED_OCR_SERVICE_URL="http://${UNLIMITED_OCR_HOST}:${UNLIMITED_OCR_API_PORT}/ocr" \
  PADDLEOCR_VL_MODEL_NAME="$PADDLEOCR_VL_MODEL_NAME" \
  PPOCR_V6_MODEL_NAME="$PPOCR_V6_MODEL_NAME" \
  UNLIMITED_OCR_MODEL_NAME="$UNLIMITED_OCR_MODEL_NAME" \
  UNLIMITED_OCR_BACKEND="$UNLIMITED_OCR_BACKEND" \
  UNLIMITED_OCR_PRELOAD="$UNLIMITED_OCR_PRELOAD" \
  UNLIMITED_OCR_API_PORT="$UNLIMITED_OCR_API_PORT" \
  UNLIMITED_OCR_SUPPORTED_BACKENDS="$UNLIMITED_OCR_SUPPORTED_BACKENDS" \
  UNLIMITED_OCR_PDF_DPI="$UNLIMITED_OCR_PDF_DPI" \
  UNLIMITED_OCR_MAX_TOKENS="$UNLIMITED_OCR_MAX_TOKENS" \
  PADDLE_REQUEST_TIMEOUT="$PADDLE_REQUEST_TIMEOUT" \
  PANDOCR_TASK_DATA_DIR="$ROOT_DIR/data/tasks" \
  PANDOCR_CORS_ORIGINS="$PANDOCR_CORS_ORIGINS" \
  PANDOCR_MAX_UPLOAD_MB="$PANDOCR_MAX_UPLOAD_MB" \
  PANDOCR_MAX_CONCURRENT_OCR="$PANDOCR_MAX_CONCURRENT_OCR" \
  PANDOCR_ENFORCE_ORIGIN_CHECK="$PANDOCR_ENFORCE_ORIGIN_CHECK" \
  PANDOCR_API_TOKEN="$PANDOCR_API_TOKEN" \
  PANDOCR_ENABLE_API_DOCS="$PANDOCR_ENABLE_API_DOCS" \
  PANDOCR_ENABLE_UNLIMITED_OCR="$PANDOCR_ENABLE_UNLIMITED_OCR" \
  PANDOCR_MODEL_CONTROL="${PANDOCR_MODEL_CONTROL:-none}" \
  PANDOCR_HOST="$PANDOCR_HOST" \
  PANDOCR_PORT="$PANDOCR_PORT" \
    start_detached logs/pandocr-web.log python server.py > run/pandocr-web.pid
fi

wait_for_http "http://${PANDOCR_HOST}:${PANDOCR_PORT}/" "PaddleOCR Local Web service" || {
  tail -n 80 logs/pandocr-web.log || true
  exit 1
}

echo "PaddleOCR Local is ready."
echo "WebUI: http://${PANDOCR_HOST}:${PANDOCR_PORT}"
echo "PaddleOCR-VL API: http://${PADDLEX_HOST}:${PADDLEX_PORT}"
echo "PP-OCRv6 API: http://${PADDLE_OCR_HOST}:${PADDLE_OCR_PORT}"
if [[ "$PANDOCR_MACOS_BACKEND" == "mlx" ]]; then
  echo "MLX-VLM: http://${MLX_HOST}:${MLX_PORT}"
fi
if truthy "$PANDOCR_ENABLE_UNLIMITED_OCR"; then
  echo "Unlimited-OCR API: http://${UNLIMITED_OCR_HOST}:${UNLIMITED_OCR_API_PORT}"
fi
cp "$EXPECTED_STATE_FILE" "$STATE_FILE"
