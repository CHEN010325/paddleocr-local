#!/usr/bin/env bash

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=scripts/macos-python.sh
source "$ROOT_DIR/scripts/macos-python.sh"

failures=0
warnings=0

pass() {
  printf "[OK] %s\n" "$1"
}

warn() {
  warnings=$((warnings + 1))
  printf "[WARN] %s\n" "$1"
}

fail() {
  failures=$((failures + 1))
  printf "[FAIL] %s\n" "$1"
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

check_cmd() {
  local name="$1"
  local hint="$2"
  if has_cmd "$name"; then
    pass "$name found: $(command -v "$name")"
  else
    fail "$name not found. $hint"
  fi
}

check_http() {
  local label="$1"
  local url="$2"
  if has_cmd curl && curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
    pass "$label is reachable at $url"
  else
    warn "$label is not reachable at $url"
  fi
}

check_port_hint() {
  local port="$1"
  local label="$2"
  if has_cmd lsof && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    warn "Port $port is already in use ($label). This is fine if PaddleOCR Local is already running."
  else
    pass "Port $port is free or lsof is unavailable ($label)"
  fi
}

printf "PaddleOCR Local doctor\n"
printf "Repository: %s\n\n" "$ROOT_DIR"

os_name="$(uname -s 2>/dev/null || echo unknown)"
arch_name="$(uname -m 2>/dev/null || echo unknown)"
printf "System: %s %s\n\n" "$os_name" "$arch_name"

check_cmd curl "Install curl or use your OS package manager."

case "$os_name:$arch_name" in
  Darwin:arm64)
    printf "\nmacOS Apple Silicon checks\n"
    supported_python="$(PYTHON_BIN= find_supported_macos_python 2>/dev/null || true)"
    if [[ -n "$supported_python" ]]; then
      pass "supported $("$supported_python" --version 2>&1) found: $supported_python"
    else
      fail "Python 3.12 or 3.13 not found. The one-click installer will use Homebrew to install Python 3.13 when available."
    fi

	    if [[ -x ".venv-macos/bin/python" ]]; then
	      if .venv-macos/bin/python - <<'PY' >/dev/null 2>&1
import importlib.util
raise SystemExit(0 if importlib.util.find_spec("mlx_vlm") else 1)
PY
	      then
	        if .venv-macos/bin/python scripts/check-mlx-runtime.py >/dev/null 2>&1; then
	          pass "macOS MLX runtime is importable and uses the pinned versions."
	        else
	          warn "macOS virtual environment exists but the MLX runtime is incompatible. Rerun ./macos-one-click.command to repair it."
	        fi
	      else
	        pass "macOS virtual environment exists; optional MLX runtime is not installed."
	      fi
	    else
	      warn "macOS virtual environment is not installed yet. The one-click script will create it."
	    fi

	    if [[ -x ".venv-unlimited-ocr-macos/bin/python" ]]; then
	      if .venv-unlimited-ocr-macos/bin/python - <<'PY' >/dev/null 2>&1
import importlib.util
import torch
missing = [
    name
    for name in ["fastapi", "fitz", "PIL", "torch", "torchvision", "transformers", "uvicorn"]
    if importlib.util.find_spec(name) is None
]
raise SystemExit(1 if missing else 0)
PY
	      then
	        pass "macOS Unlimited-OCR virtual environment exists: .venv-unlimited-ocr-macos"
	      else
	        warn "macOS Unlimited-OCR virtual environment exists but dependencies are incomplete."
	      fi
	    else
	      warn "macOS Unlimited-OCR virtual environment is not installed yet. The one-click script will create it."
	    fi
	
	    check_port_hint 8000 "WebUI"
	    check_port_hint 8081 "PaddleOCR-VL API"
	    check_port_hint 8082 "PP-OCRv6 API"
	    check_port_hint 8083 "Unlimited-OCR API"
	    check_port_hint 8111 "MLX-VLM"
	
	    check_http "WebUI" "http://127.0.0.1:8000/"
	    check_http "PaddleOCR-VL API" "http://127.0.0.1:8081/health"
	    check_http "PP-OCRv6 API" "http://127.0.0.1:8082/health"
	    check_http "Unlimited-OCR API" "http://127.0.0.1:8083/health"

    printf "\nRecommended one-click command:\n"
    printf "  ./macos-one-click.command\n"
    ;;

  *)
    printf "\nNVIDIA Docker checks\n"
    check_cmd docker "Install Docker Desktop or Docker Engine."
    if has_cmd docker; then
      if docker compose version >/dev/null 2>&1; then
        pass "docker compose is available"
      else
        fail "docker compose is not available. Install Docker Compose v2."
      fi
      if docker info >/dev/null 2>&1; then
        pass "Docker daemon is running"
      else
        fail "Docker daemon is not running. Start Docker Desktop or the Docker service."
      fi
    fi

    if has_cmd nvidia-smi; then
      pass "nvidia-smi found"
      nvidia-smi --query-gpu=index,name,memory.total,memory.free --format=csv,noheader,nounits 2>/dev/null || warn "nvidia-smi did not return GPU details"
    else
      warn "nvidia-smi not found. NVIDIA Docker deployment requires an NVIDIA GPU and driver."
    fi

    [[ -f "env.txt" ]] && pass "env.txt exists" || fail "env.txt is missing"
    [[ -f "env.docker" ]] && pass "env.docker exists" || fail "env.docker is missing"

    check_port_hint 8000 "WebUI"
    check_port_hint 8081 "PaddleOCR-VL API"
    check_port_hint 8082 "PP-OCRv6 API"

    printf "\nRecommended one-click command on Windows NVIDIA:\n"
    printf "  .\\windows-one-click.bat\n"
    printf "\nManual Docker command starts with:\n"
    printf "  docker compose --env-file env.txt config --quiet\n"
    ;;
esac

printf "\nSummary: %s failure(s), %s warning(s)\n" "$failures" "$warnings"
if (( failures > 0 )); then
  exit 1
fi
