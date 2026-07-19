#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=scripts/macos-python.sh
source "$ROOT_DIR/scripts/macos-python.sh"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This setup script is for macOS."
  exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Apple Silicon arm64 is required for the macOS Unlimited-OCR MPS route."
  exit 1
fi

VENV_DIR="${UNLIMITED_OCR_MACOS_VENV:-.venv-unlimited-ocr-macos}"
VENV_PYTHON_VERSION=""
if [[ -x "$VENV_DIR/bin/python" ]]; then
  VENV_PYTHON_VERSION="$(python_minor_version "$VENV_DIR/bin/python" || true)"
fi

select_supported_macos_python "$VENV_DIR/bin/python"
PYTHON_VERSION="$(python_minor_version "$PYTHON_BIN")"

echo "Using Python: $($PYTHON_BIN -c 'import sys; print(sys.executable)')"
if [[ -x "$VENV_DIR/bin/python" ]]; then
  if [[ "$VENV_PYTHON_VERSION" == "3.12" || "$VENV_PYTHON_VERSION" == "3.13" ]]; then
    echo "Reusing Unlimited-OCR virtual environment: $VENV_DIR (Python $VENV_PYTHON_VERSION)"
  else
    echo "Recreating $VENV_DIR with Python $PYTHON_VERSION because its Python ${VENV_PYTHON_VERSION:-unknown} is unsupported."
    "$PYTHON_BIN" -m venv --clear "$VENV_DIR"
  fi
else
  echo "Creating Unlimited-OCR virtual environment: $VENV_DIR (Python $PYTHON_VERSION)"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-macos-unlimited-ocr.txt

python - <<'PY'
import importlib.util
import torch
import transformers

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
if missing:
    raise SystemExit(f"Missing Unlimited-OCR dependencies: {', '.join(missing)}")

print("torch", torch.__version__)
print("transformers", transformers.__version__)
print("mps available", torch.backends.mps.is_available())
PY

echo "macOS Unlimited-OCR setup complete."
echo "Start with: PANDOCR_ENABLE_UNLIMITED_OCR=1 bash scripts/start-macos.sh"
