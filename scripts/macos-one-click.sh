#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${PANDOCR_MACOS_VENV:-.venv-macos}"
PANDOCR_MACOS_BACKEND="${PANDOCR_MACOS_BACKEND:-mlx}"
PANDOCR_ENABLE_UNLIMITED_OCR="${PANDOCR_ENABLE_UNLIMITED_OCR:-1}"
UNLIMITED_OCR_MACOS_VENV="${UNLIMITED_OCR_MACOS_VENV:-.venv-unlimited-ocr-macos}"
UNLIMITED_OCR_HOST="${UNLIMITED_OCR_HOST:-127.0.0.1}"
UNLIMITED_OCR_API_PORT="${UNLIMITED_OCR_API_PORT:-8083}"
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
PADDLEX_HOST="${PADDLEX_HOST:-127.0.0.1}"
PADDLEX_PORT="${PADDLEX_PORT:-8081}"
MLX_HOST="${MLX_HOST:-127.0.0.1}"
MLX_PORT="${MLX_PORT:-8111}"
PANDOCR_OPEN_BROWSER="${PANDOCR_OPEN_BROWSER:-1}"
PANDOCR_ONE_CLICK_FORCE_SETUP="${PANDOCR_ONE_CLICK_FORCE_SETUP:-0}"

WEB_URL="http://${PANDOCR_HOST}:${PANDOCR_PORT}"

step() {
  printf "\n==> %s\n" "$1"
}

fail_with_logs() {
  local exit_code=$?
  printf "\nPaddleOCR Local macOS one-click deployment failed.\n"
  printf "Useful logs:\n"
  printf "  logs/pandocr-web.log\n"
  printf "  logs/paddlex.log\n"
  printf "  logs/mlx-vlm.log\n"
  printf "  logs/unlimited-ocr.log\n"
  exit "$exit_code"
}

truthy() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

check_apple_silicon() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This one-click installer only supports macOS Apple Silicon."
    exit 1
  fi

  if [[ "$(uname -m)" != "arm64" ]]; then
    echo "This one-click installer requires Apple Silicon arm64."
    exit 1
  fi
}

ensure_python_available() {
  if [[ -x "$VENV_DIR/bin/python" ]] && { ! truthy "$PANDOCR_ENABLE_UNLIMITED_OCR" || [[ -x "$UNLIMITED_OCR_MACOS_VENV/bin/python" ]]; }; then
    return
  fi

  if command -v python3.12 >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then
    return
  fi

  echo "Python 3 was not found. Please install Python 3.12 or newer, then rerun this command."
  exit 1
}

unlimited_ocr_env_ready() {
  [[ -x "$UNLIMITED_OCR_MACOS_VENV/bin/python" ]] || return 1

  "$UNLIMITED_OCR_MACOS_VENV/bin/python" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = [
    "fastapi",
    "fitz",
    "httpx",
    "multipart",
    "PIL",
    "psutil",
    "torch",
    "torchvision",
    "transformers",
    "uvicorn",
]
missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(1 if missing else 0)
PY
}

macos_env_ready() {
  [[ -f "$VENV_DIR/bin/activate" ]] || return 1
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"

  python - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = [
    "fastapi",
    "httpx",
    "multipart",
    "pydantic",
    "PIL",
    "pypdf",
    "uvicorn",
    "paddle",
    "paddleocr",
    "paddlex",
]
missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(1 if missing else 0)
PY

  command -v paddlex >/dev/null 2>&1 || return 1
  if [[ "$PANDOCR_MACOS_BACKEND" == "mlx" ]]; then
    command -v mlx_vlm.server >/dev/null 2>&1 || return 1
  fi
}

run_setup_if_needed() {
  local install_mlx=0
  if [[ "$PANDOCR_MACOS_BACKEND" == "mlx" ]]; then
    install_mlx=1
  fi

  if truthy "$PANDOCR_ONE_CLICK_FORCE_SETUP"; then
    step "Installing macOS dependencies"
    INSTALL_MLX_VLM="$install_mlx" PANDOCR_MACOS_VENV="$VENV_DIR" bash scripts/setup-macos.sh
    return
  fi

  if macos_env_ready; then
    step "macOS dependencies are already installed"
    return
  fi

  step "Installing macOS dependencies"
  INSTALL_MLX_VLM="$install_mlx" PANDOCR_MACOS_VENV="$VENV_DIR" bash scripts/setup-macos.sh
}

run_unlimited_ocr_setup_if_needed() {
  if ! truthy "$PANDOCR_ENABLE_UNLIMITED_OCR"; then
    return
  fi

  if truthy "$PANDOCR_ONE_CLICK_FORCE_SETUP"; then
    step "Installing macOS Unlimited-OCR dependencies"
    UNLIMITED_OCR_MACOS_VENV="$UNLIMITED_OCR_MACOS_VENV" bash scripts/setup-macos-unlimited-ocr.sh
    return
  fi

  if unlimited_ocr_env_ready; then
    step "macOS Unlimited-OCR dependencies are already installed"
    return
  fi

  step "Installing macOS Unlimited-OCR dependencies"
  UNLIMITED_OCR_MACOS_VENV="$UNLIMITED_OCR_MACOS_VENV" bash scripts/setup-macos-unlimited-ocr.sh
}

start_services() {
  step "Starting PaddleOCR Local services"
  PANDOCR_MACOS_BACKEND="$PANDOCR_MACOS_BACKEND" \
  PANDOCR_ENABLE_UNLIMITED_OCR="$PANDOCR_ENABLE_UNLIMITED_OCR" \
  UNLIMITED_OCR_MACOS_VENV="$UNLIMITED_OCR_MACOS_VENV" \
  UNLIMITED_OCR_HOST="$UNLIMITED_OCR_HOST" \
  UNLIMITED_OCR_API_PORT="$UNLIMITED_OCR_API_PORT" \
  UNLIMITED_OCR_MODEL_NAME="$UNLIMITED_OCR_MODEL_NAME" \
  UNLIMITED_OCR_BACKEND="$UNLIMITED_OCR_BACKEND" \
  UNLIMITED_OCR_SUPPORTED_BACKENDS="$UNLIMITED_OCR_SUPPORTED_BACKENDS" \
  UNLIMITED_OCR_PRELOAD="$UNLIMITED_OCR_PRELOAD" \
  UNLIMITED_OCR_HF_HOME="$UNLIMITED_OCR_HF_HOME" \
  UNLIMITED_OCR_TRANSFORMERS_DEVICE="$UNLIMITED_OCR_TRANSFORMERS_DEVICE" \
  UNLIMITED_OCR_TRANSFORMERS_DTYPE="$UNLIMITED_OCR_TRANSFORMERS_DTYPE" \
  UNLIMITED_OCR_ATTENTION_IMPLEMENTATION="$UNLIMITED_OCR_ATTENTION_IMPLEMENTATION" \
  UNLIMITED_OCR_DISABLE_XET="$UNLIMITED_OCR_DISABLE_XET" \
  UNLIMITED_OCR_HF_HUB_DOWNLOAD_TIMEOUT="$UNLIMITED_OCR_HF_HUB_DOWNLOAD_TIMEOUT" \
  UNLIMITED_OCR_HF_HUB_ETAG_TIMEOUT="$UNLIMITED_OCR_HF_HUB_ETAG_TIMEOUT" \
  UNLIMITED_OCR_PDF_DPI="$UNLIMITED_OCR_PDF_DPI" \
  UNLIMITED_OCR_MAX_TOKENS="$UNLIMITED_OCR_MAX_TOKENS" \
  UNLIMITED_OCR_STREAM_HEARTBEAT_SECONDS="$UNLIMITED_OCR_STREAM_HEARTBEAT_SECONDS" \
  UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY="$UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY" \
  UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE="$UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_IMAGE_SIZE" \
  UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS="$UNLIMITED_OCR_TRANSFORMERS_MPS_OOM_RETRY_MAX_TOKENS" \
  PANDOCR_HOST="$PANDOCR_HOST" \
  PANDOCR_PORT="$PANDOCR_PORT" \
  PADDLEX_HOST="$PADDLEX_HOST" \
  PADDLEX_PORT="$PADDLEX_PORT" \
  MLX_HOST="$MLX_HOST" \
  MLX_PORT="$MLX_PORT" \
    bash scripts/start-macos.sh
}

test_services() {
  step "Checking service health"
  PANDOCR_MACOS_BACKEND="$PANDOCR_MACOS_BACKEND" \
  PANDOCR_ENABLE_UNLIMITED_OCR="$PANDOCR_ENABLE_UNLIMITED_OCR" \
  UNLIMITED_OCR_HOST="$UNLIMITED_OCR_HOST" \
  UNLIMITED_OCR_API_PORT="$UNLIMITED_OCR_API_PORT" \
  PANDOCR_HOST="$PANDOCR_HOST" \
  PANDOCR_PORT="$PANDOCR_PORT" \
  PADDLEX_HOST="$PADDLEX_HOST" \
  PADDLEX_PORT="$PADDLEX_PORT" \
  MLX_HOST="$MLX_HOST" \
  MLX_PORT="$MLX_PORT" \
    bash scripts/test-macos.sh
}

open_browser() {
  if truthy "$PANDOCR_OPEN_BROWSER" && command -v open >/dev/null 2>&1; then
    step "Opening PaddleOCR Local in your browser"
    open "$WEB_URL"
  fi
}

trap fail_with_logs ERR

check_apple_silicon
ensure_python_available

case "$PANDOCR_MACOS_BACKEND" in
  native|mlx) ;;
  *)
    echo "Unsupported PANDOCR_MACOS_BACKEND: $PANDOCR_MACOS_BACKEND"
    echo "Supported values: native, mlx"
    exit 1
    ;;
esac

step "PaddleOCR Local macOS one-click deployment"
echo "Backend: $PANDOCR_MACOS_BACKEND"
echo "Unlimited-OCR: $PANDOCR_ENABLE_UNLIMITED_OCR"
echo "WebUI: $WEB_URL"

run_setup_if_needed
run_unlimited_ocr_setup_if_needed
start_services
test_services
open_browser

printf "\nPaddleOCR Local is ready: %s\n" "$WEB_URL"
printf "Stop services with: make mac-down\n"
