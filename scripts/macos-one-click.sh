#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_DIR="${PANDOCR_MACOS_VENV:-.venv-macos}"
PANDOCR_MACOS_BACKEND="${PANDOCR_MACOS_BACKEND:-mlx}"
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
  printf "\nPandOCR macOS one-click deployment failed.\n"
  printf "Useful logs:\n"
  printf "  logs/pandocr-web.log\n"
  printf "  logs/paddlex.log\n"
  printf "  logs/mlx-vlm.log\n"
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
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    return
  fi

  if command -v python3.12 >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then
    return
  fi

  echo "Python 3 was not found. Please install Python 3.12 or newer, then rerun this command."
  exit 1
}

macos_env_ready() {
  [[ -f "$VENV_DIR/bin/activate" ]] || return 1
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"

  python - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = ["paddle", "paddleocr", "paddlex"]
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

start_services() {
  step "Starting PandOCR services"
  PANDOCR_MACOS_BACKEND="$PANDOCR_MACOS_BACKEND" \
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
    step "Opening PandOCR in your browser"
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

step "PandOCR macOS one-click deployment"
echo "Backend: $PANDOCR_MACOS_BACKEND"
echo "WebUI: $WEB_URL"

run_setup_if_needed
start_services
test_services
open_browser

printf "\nPandOCR is ready: %s\n" "$WEB_URL"
printf "Stop services with: make mac-down\n"
