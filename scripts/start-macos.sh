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
PANDOCR_HOST="${PANDOCR_HOST:-127.0.0.1}"
PANDOCR_PORT="${PANDOCR_PORT:-8000}"
PADDLE_REQUEST_TIMEOUT="${PADDLE_REQUEST_TIMEOUT:-3600}"
PANDOCR_MAX_UPLOAD_MB="${PANDOCR_MAX_UPLOAD_MB:-512}"
PADDLEOCR_VL_MODEL_NAME="${PADDLEOCR_VL_MODEL_NAME:-PaddleOCR-VL-1.6-0.9B}"
PANDOCR_CORS_ORIGINS="${PANDOCR_CORS_ORIGINS:-http://localhost:${PANDOCR_PORT},http://127.0.0.1:${PANDOCR_PORT}}"
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="${PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK:-True}"
STARTUP_TIMEOUT_SECONDS="${STARTUP_TIMEOUT_SECONDS:-900}"
PADDLEX_PIPELINE_IS_CUSTOM=0
if [[ -n "${PADDLEX_PIPELINE:-}" ]]; then
  PADDLEX_PIPELINE_IS_CUSTOM=1
fi

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
  ps -p "$pid" -o command= 2>/dev/null | grep -Fq "$expected"
}

has_running_service() {
  is_running run/pandocr-web.pid || is_running run/paddlex.pid || is_running run/mlx-vlm.pid
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
    nohup mlx_vlm.server \
      --host "$MLX_HOST" \
      --port "$MLX_PORT" \
      --model "$MLX_MODEL" \
      > logs/mlx-vlm.log 2>&1 &
    echo $! > run/mlx-vlm.pid
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
  PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="$PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK" \
    nohup paddlex --serve \
      --pipeline "$PADDLEX_PIPELINE" \
      --device cpu \
      --host "$PADDLEX_HOST" \
      --port "$PADDLEX_PORT" \
      > logs/paddlex.log 2>&1 &
  echo $! > run/paddlex.pid
fi

wait_for_http "http://${PADDLEX_HOST}:${PADDLEX_PORT}/health" "PaddleX service" || {
  tail -n 80 logs/paddlex.log || true
  exit 1
}

if is_expected_process run/pandocr-web.pid "server.py"; then
  echo "PandOCR Web service already running: $(cat run/pandocr-web.pid)"
else
  rm -f run/pandocr-web.pid
  echo "Starting PandOCR WebUI on ${PANDOCR_HOST}:${PANDOCR_PORT}"
  PADDLE_SERVICE_URL="http://${PADDLEX_HOST}:${PADDLEX_PORT}/layout-parsing" \
  PADDLEOCR_VL_MODEL_NAME="$PADDLEOCR_VL_MODEL_NAME" \
  PADDLE_REQUEST_TIMEOUT="$PADDLE_REQUEST_TIMEOUT" \
  PANDOCR_TASK_DATA_DIR="$ROOT_DIR/data/tasks" \
  PANDOCR_CORS_ORIGINS="$PANDOCR_CORS_ORIGINS" \
  PANDOCR_MAX_UPLOAD_MB="$PANDOCR_MAX_UPLOAD_MB" \
  PANDOCR_HOST="$PANDOCR_HOST" \
  PANDOCR_PORT="$PANDOCR_PORT" \
    nohup python server.py > logs/pandocr-web.log 2>&1 &
  echo $! > run/pandocr-web.pid
fi

wait_for_http "http://${PANDOCR_HOST}:${PANDOCR_PORT}/" "PandOCR Web service" || {
  tail -n 80 logs/pandocr-web.log || true
  exit 1
}

echo "PandOCR is ready."
echo "WebUI: http://${PANDOCR_HOST}:${PANDOCR_PORT}"
echo "PaddleOCR-VL API: http://${PADDLEX_HOST}:${PADDLEX_PORT}"
if [[ "$PANDOCR_MACOS_BACKEND" == "mlx" ]]; then
  echo "MLX-VLM: http://${MLX_HOST}:${MLX_PORT}"
fi
cp "$EXPECTED_STATE_FILE" "$STATE_FILE"
