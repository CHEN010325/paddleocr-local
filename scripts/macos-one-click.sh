#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=scripts/macos-python.sh
source "$ROOT_DIR/scripts/macos-python.sh"

REQUESTED_MODEL="${PANDOCR_MODEL:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model|-m)
      [[ $# -ge 2 ]] || { echo "$1 requires a model value."; exit 2; }
      REQUESTED_MODEL="$2"
      shift 2
      ;;
    --no-open)
      PANDOCR_OPEN_BROWSER=0
      shift
      ;;
    --force-setup)
      PANDOCR_ONE_CLICK_FORCE_SETUP=1
      shift
      ;;
    --dry-run)
      PANDOCR_DRY_RUN=1
      shift
      ;;
    --help|-h)
      echo "Usage: ./macos-one-click.command [--model MODEL] [--no-open] [--force-setup] [--dry-run]"
      echo "Models: paddleocr-vl-1.6, pp-ocrv6, ovisocr2, hpd-parsing"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      exit 2
      ;;
  esac
done

resolve_model_id() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    1|vl|paddleocr-vl|paddleocr-vl-1.6|paddleocrvl) echo "paddleocr-vl-1.6" ;;
    2|ppocr|ppocrv6|pp-ocrv6|ocr) echo "pp-ocrv6" ;;
    3|ovis|ovisocr|ovisocr2|ovis-ocr2) echo "ovisocr2" ;;
    4|hpd|hpd-parsing|hpdocr) echo "hpd-parsing" ;;
    *)
      echo "Unsupported model: $1" >&2
      echo "Use paddleocr-vl-1.6, pp-ocrv6, ovisocr2, or hpd-parsing." >&2
      return 1
      ;;
  esac
}

if [[ -z "$REQUESTED_MODEL" ]]; then
  if [[ -t 0 ]]; then
    printf "\nChoose one model to install and start:\n"
    printf "  1) PaddleOCR-VL 1.6  Document parsing (MLX)\n"
    printf "  2) PP-OCRv6          Text recognition\n"
    printf "  3) OvisOCR2          Document parsing (MLX, Transformers fallback)\n"
    printf "  4) HPD-Parsing       Document parsing (Transformers/MPS)\n\n"
    read -r -p "Select a model [1]: " REQUESTED_MODEL
  fi
  REQUESTED_MODEL="${REQUESTED_MODEL:-1}"
fi

ACTIVE_MODEL="$(resolve_model_id "$REQUESTED_MODEL")"
PANDOCR_MODEL_CATALOG="paddleocr-vl-1.6,pp-ocrv6,ovisocr2,hpd-parsing"
PANDOCR_ENABLE_PADDLEOCR_VL=0
PANDOCR_ENABLE_PPOCRV6=0
PANDOCR_ENABLE_HPD_PARSING=0
PANDOCR_ENABLE_OVISOCR2=0
case "$ACTIVE_MODEL" in
  paddleocr-vl-1.6) PANDOCR_ENABLE_PADDLEOCR_VL=1 ;;
  pp-ocrv6) PANDOCR_ENABLE_PPOCRV6=1 ;;
  ovisocr2) PANDOCR_ENABLE_OVISOCR2=1 ;;
  hpd-parsing) PANDOCR_ENABLE_HPD_PARSING=1 ;;
esac

VENV_DIR="${PANDOCR_MACOS_VENV:-.venv-macos}"
PANDOCR_MACOS_BACKEND="${PANDOCR_MACOS_BACKEND:-mlx}"
OVISOCR2_MACOS_VENV="${OVISOCR2_MACOS_VENV:-.venv-ovisocr2-macos}"
OVISOCR2_HOST="${OVISOCR2_HOST:-127.0.0.1}"
OVISOCR2_API_PORT="${OVISOCR2_API_PORT:-8084}"
OVISOCR2_MODEL_NAME="${OVISOCR2_MODEL_NAME:-ATH-MaaS/OvisOCR2}"
OVISOCR2_MODEL_REVISION="${OVISOCR2_MODEL_REVISION:-65c619d374b55d4152e85150fc1b003700bc1f0c}"
OVISOCR2_BACKEND="${OVISOCR2_BACKEND:-mlx}"
OVISOCR2_HF_HOME="${OVISOCR2_HF_HOME:-$ROOT_DIR/model_cache_ovisocr2_macos}"
OVISOCR2_TRANSFORMERS_DEVICE="${OVISOCR2_TRANSFORMERS_DEVICE:-mps}"
OVISOCR2_TRANSFORMERS_DTYPE="${OVISOCR2_TRANSFORMERS_DTYPE:-float16}"
OVISOCR2_ATTENTION_IMPLEMENTATION="${OVISOCR2_ATTENTION_IMPLEMENTATION:-eager}"
OVISOCR2_PDF_DPI="${OVISOCR2_PDF_DPI:-180}"
OVISOCR2_MAX_TOKENS="${OVISOCR2_MAX_TOKENS:-2048}"
OVISOCR2_MAX_PIXELS="${OVISOCR2_MAX_PIXELS:-1048576}"
OVISOCR2_RESTART_CHECK_INTERVAL="${OVISOCR2_RESTART_CHECK_INTERVAL:-128}"
HPD_PARSING_MACOS_VENV="${HPD_PARSING_MACOS_VENV:-.venv-hpd-parsing-macos}"
HPD_PARSING_HOST="${HPD_PARSING_HOST:-127.0.0.1}"
HPD_PARSING_API_PORT="${HPD_PARSING_API_PORT:-8085}"
HPD_PARSING_MODEL_NAME="${HPD_PARSING_MODEL_NAME:-PaddlePaddle/HPD-Parsing}"
HPD_PARSING_HF_HOME="${HPD_PARSING_HF_HOME:-$ROOT_DIR/model_cache_hpd_parsing_macos}"
PANDOCR_HOST="${PANDOCR_HOST:-127.0.0.1}"
PANDOCR_PORT="${PANDOCR_PORT:-8000}"
PADDLEX_HOST="${PADDLEX_HOST:-127.0.0.1}"
PADDLEX_PORT="${PADDLEX_PORT:-8081}"
MLX_HOST="${MLX_HOST:-127.0.0.1}"
MLX_PORT="${MLX_PORT:-8111}"
PANDOCR_OPEN_BROWSER="${PANDOCR_OPEN_BROWSER:-1}"
PANDOCR_ONE_CLICK_FORCE_SETUP="${PANDOCR_ONE_CLICK_FORCE_SETUP:-0}"
PANDOCR_MACOS_CONTROLLER_PORT="${PANDOCR_MACOS_CONTROLLER_PORT:-8001}"
CONTROLLER_TOKEN_FILE="$ROOT_DIR/run/macos-controller.token"
if [[ -z "${PANDOCR_MODEL_CONTROLLER_TOKEN:-}" && -f "$CONTROLLER_TOKEN_FILE" ]]; then
  PANDOCR_MODEL_CONTROLLER_TOKEN="$(cat "$CONTROLLER_TOKEN_FILE")"
fi
PANDOCR_MODEL_CONTROLLER_TOKEN="${PANDOCR_MODEL_CONTROLLER_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"

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
  printf "  logs/hpd-parsing.log\n"
  printf "  logs/ovisocr2.log\n"
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
  local selected_python

  if selected_python="$(find_supported_macos_python "$VENV_DIR/bin/python" "$OVISOCR2_MACOS_VENV/bin/python" "$HPD_PARSING_MACOS_VENV/bin/python" 2>/dev/null)"; then
    PYTHON_BIN="$selected_python"
    export PYTHON_BIN
    return
  fi

  if install_supported_macos_python; then
    unset PYTHON_BIN
    if select_supported_macos_python "$VENV_DIR/bin/python"; then
      return
    fi
  fi

  if command -v brew >/dev/null 2>&1; then
    echo "Homebrew could not provide a compatible Python interpreter."
  else
    echo "Homebrew was not found, so Python 3.13 could not be installed automatically."
    echo "Install Homebrew from https://brew.sh or install Python 3.13 manually."
  fi
  echo "Python 3.12 or 3.13 was not found. Python 3.14 is not currently supported."
  exit 1
}

ovisocr2_env_ready() {
  [[ -x "$OVISOCR2_MACOS_VENV/bin/python" ]] || return 1

  "$OVISOCR2_MACOS_VENV/bin/python" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = ["accelerate", "fastapi", "fitz", "mlx_vlm", "multipart", "PIL", "torch", "transformers", "uvicorn"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(1 if missing or sys.version_info[:2] not in {(3, 12), (3, 13)} else 0)
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
sys.exit(1 if missing or sys.version_info[:2] not in {(3, 12), (3, 13)} else 0)
PY

  command -v paddlex >/dev/null 2>&1 || return 1
  if truthy "$PANDOCR_ENABLE_PADDLEOCR_VL" && [[ "$PANDOCR_MACOS_BACKEND" == "mlx" ]]; then
    command -v mlx_vlm.server >/dev/null 2>&1 || return 1
    python scripts/check-mlx-runtime.py >/dev/null 2>&1 || return 1
  fi
}

run_setup_if_needed() {
  local install_mlx=0
  local setup_python="$PYTHON_BIN"
  if ! truthy "$PANDOCR_ENABLE_PADDLEOCR_VL" && ! truthy "$PANDOCR_ENABLE_PPOCRV6"; then
    return
  fi
  if truthy "$PANDOCR_ENABLE_PADDLEOCR_VL" && [[ "$PANDOCR_MACOS_BACKEND" == "mlx" ]]; then
    install_mlx=1
  fi

  if truthy "$PANDOCR_ONE_CLICK_FORCE_SETUP"; then
    step "Installing macOS dependencies"
    PYTHON_BIN="$setup_python" INSTALL_MLX_VLM="$install_mlx" PANDOCR_MACOS_VENV="$VENV_DIR" bash scripts/setup-macos.sh
    return
  fi

  if macos_env_ready; then
    step "macOS dependencies are already installed"
    return
  fi

  step "Installing macOS dependencies"
  PYTHON_BIN="$setup_python" INSTALL_MLX_VLM="$install_mlx" PANDOCR_MACOS_VENV="$VENV_DIR" bash scripts/setup-macos.sh
}

run_ovisocr2_setup_if_needed() {
  local setup_python="$PYTHON_BIN"
  if ! truthy "$PANDOCR_ENABLE_OVISOCR2"; then
    return
  fi

  if truthy "$PANDOCR_ONE_CLICK_FORCE_SETUP" || ! ovisocr2_env_ready; then
    step "Installing macOS OvisOCR2 dependencies"
    PYTHON_BIN="$setup_python" OVISOCR2_MACOS_VENV="$OVISOCR2_MACOS_VENV" bash scripts/setup-macos-ovisocr2.sh
  else
    step "macOS OvisOCR2 dependencies are already installed"
  fi
}

run_hpd_parsing_setup_if_needed() {
  if [[ "$ACTIVE_MODEL" != "hpd-parsing" ]]; then
    return
  fi
  if truthy "$PANDOCR_ONE_CLICK_FORCE_SETUP" || [[ ! -x "$HPD_PARSING_MACOS_VENV/bin/python" ]]; then
    step "Installing macOS HPD-Parsing dependencies"
    PYTHON_BIN="$PYTHON_BIN" HPD_PARSING_MACOS_VENV="$HPD_PARSING_MACOS_VENV" bash scripts/setup-macos-hpd-parsing.sh
  else
    step "macOS HPD-Parsing dependencies are already installed"
  fi
}

start_services() {
  step "Starting PaddleOCR Local services"
  mkdir -p run
  printf '%s' "$PANDOCR_MODEL_CONTROLLER_TOKEN" > "$CONTROLLER_TOKEN_FILE"
  if ! kill -0 "$(cat run/macos-controller.pid 2>/dev/null)" >/dev/null 2>&1; then
    echo "Starting macOS model controller on 127.0.0.1:${PANDOCR_MACOS_CONTROLLER_PORT}"
    : > logs/macos-controller.log
    CONTROLLER_PID="$(PANDOCR_MODEL_CONTROL=none \
    PANDOCR_MODEL_CATALOG="$PANDOCR_MODEL_CATALOG" \
    PANDOCR_ENABLE_PADDLEOCR_VL=1 \
    PANDOCR_ENABLE_PPOCRV6=1 \
    PANDOCR_ENABLE_HPD_PARSING=1 \
    PANDOCR_ENABLE_OVISOCR2=1 \
    PANDOCR_ACTIVE_MODEL_ON_START="$ACTIVE_MODEL" \
    PANDOCR_MODEL_CONTROLLER_TOKEN="$PANDOCR_MODEL_CONTROLLER_TOKEN" \
    PANDOCR_MACOS_CONTROLLER_PORT="$PANDOCR_MACOS_CONTROLLER_PORT" \
    PADDLE_SERVICE_URL="http://127.0.0.1:${PADDLEX_PORT}/layout-parsing" \
    PADDLE_OCR_SERVICE_URL="http://127.0.0.1:8082/ocr" \
    HPD_PARSING_SERVICE_URL="http://127.0.0.1:${HPD_PARSING_API_PORT}/ocr" \
    OVISOCR2_SERVICE_URL="http://127.0.0.1:${OVISOCR2_API_PORT}/ocr" \
      "$PYTHON_BIN" - "$ROOT_DIR" "$ROOT_DIR/logs/macos-controller.log" "$PANDOCR_MACOS_CONTROLLER_PORT" <<'PY'
import os
import subprocess
import sys

root_dir, log_file, port = sys.argv[1:]
with open(log_file, "ab", buffering=0) as stream:
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "macos_controller:app", "--host", "127.0.0.1", "--port", port],
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
    )"
    printf '%s\n' "$CONTROLLER_PID" > run/macos-controller.pid
  fi
  for _ in {1..30}; do
    curl -fsS -H "X-Pandocr-Controller-Token:$PANDOCR_MODEL_CONTROLLER_TOKEN" \
      "http://127.0.0.1:${PANDOCR_MACOS_CONTROLLER_PORT}/health" >/dev/null 2>&1 && break
    sleep 1
  done
  PANDOCR_MACOS_BACKEND="$PANDOCR_MACOS_BACKEND" \
  PANDOCR_ENABLE_PADDLEOCR_VL="$PANDOCR_ENABLE_PADDLEOCR_VL" \
  PANDOCR_ENABLE_PPOCRV6="$PANDOCR_ENABLE_PPOCRV6" \
  PANDOCR_ENABLE_HPD_PARSING="$PANDOCR_ENABLE_HPD_PARSING" \
  PANDOCR_ENABLE_OVISOCR2="$PANDOCR_ENABLE_OVISOCR2" \
  OVISOCR2_MACOS_VENV="$OVISOCR2_MACOS_VENV" \
  OVISOCR2_HOST="$OVISOCR2_HOST" \
  OVISOCR2_API_PORT="$OVISOCR2_API_PORT" \
  OVISOCR2_MODEL_NAME="$OVISOCR2_MODEL_NAME" \
  OVISOCR2_MODEL_REVISION="$OVISOCR2_MODEL_REVISION" \
  OVISOCR2_HF_HOME="$OVISOCR2_HF_HOME" \
  OVISOCR2_BACKEND="$OVISOCR2_BACKEND" \
  OVISOCR2_TRANSFORMERS_DEVICE="$OVISOCR2_TRANSFORMERS_DEVICE" \
  OVISOCR2_TRANSFORMERS_DTYPE="$OVISOCR2_TRANSFORMERS_DTYPE" \
  OVISOCR2_ATTENTION_IMPLEMENTATION="$OVISOCR2_ATTENTION_IMPLEMENTATION" \
  OVISOCR2_PDF_DPI="$OVISOCR2_PDF_DPI" \
  OVISOCR2_MAX_TOKENS="$OVISOCR2_MAX_TOKENS" \
  OVISOCR2_MAX_PIXELS="$OVISOCR2_MAX_PIXELS" \
  OVISOCR2_RESTART_CHECK_INTERVAL="$OVISOCR2_RESTART_CHECK_INTERVAL" \
  HPD_PARSING_MACOS_VENV="$HPD_PARSING_MACOS_VENV" \
  HPD_PARSING_HOST="$HPD_PARSING_HOST" \
  HPD_PARSING_API_PORT="$HPD_PARSING_API_PORT" \
  HPD_PARSING_MODEL_NAME="$HPD_PARSING_MODEL_NAME" \
  HPD_PARSING_HF_HOME="$HPD_PARSING_HF_HOME" \
  PANDOCR_MODEL_CATALOG="$PANDOCR_MODEL_CATALOG" \
  PANDOCR_ACTIVE_MODEL_ON_START="$ACTIVE_MODEL" \
  PANDOCR_MODEL_CONTROL=remote \
  PANDOCR_MODEL_CONTROLLER_URL="http://127.0.0.1:${PANDOCR_MACOS_CONTROLLER_PORT}" \
  PANDOCR_MODEL_CONTROLLER_TOKEN="$PANDOCR_MODEL_CONTROLLER_TOKEN" \
  PANDOCR_SKIP_WEB=0 \
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
  PANDOCR_ENABLE_PADDLEOCR_VL="$PANDOCR_ENABLE_PADDLEOCR_VL" \
  PANDOCR_ENABLE_PPOCRV6="$PANDOCR_ENABLE_PPOCRV6" \
  PANDOCR_ENABLE_HPD_PARSING="$PANDOCR_ENABLE_HPD_PARSING" \
  PANDOCR_ENABLE_OVISOCR2="$PANDOCR_ENABLE_OVISOCR2" \
  HPD_PARSING_HOST="$HPD_PARSING_HOST" \
  HPD_PARSING_API_PORT="$HPD_PARSING_API_PORT" \
  OVISOCR2_HOST="$OVISOCR2_HOST" \
  OVISOCR2_API_PORT="$OVISOCR2_API_PORT" \
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

if [[ "$ACTIVE_MODEL" == "ovisocr2" ]]; then
  case "$OVISOCR2_BACKEND" in
    mlx|transformers) ;;
    *)
      echo "Unsupported OVISOCR2_BACKEND on macOS: $OVISOCR2_BACKEND"
      echo "Supported values: mlx, transformers"
      exit 1
      ;;
  esac
fi

step "PaddleOCR Local macOS one-click deployment"
echo "Backend: $PANDOCR_MACOS_BACKEND"
echo "Selected model: $ACTIVE_MODEL"
if [[ "$ACTIVE_MODEL" == "ovisocr2" ]]; then
  echo "OvisOCR2 backend: $OVISOCR2_BACKEND"
fi
echo "WebUI: $WEB_URL"

if truthy "${PANDOCR_DRY_RUN:-0}"; then
  echo "Dry run: no dependencies installed and no services changed."
  exit 0
fi

run_setup_if_needed
run_ovisocr2_setup_if_needed
run_hpd_parsing_setup_if_needed
start_services
test_services
open_browser

printf "\nPaddleOCR Local is ready: %s\n" "$WEB_URL"
printf "Stop services with: make mac-down\n"
