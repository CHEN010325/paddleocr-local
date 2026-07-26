#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=scripts/macos-python.sh
source "$ROOT_DIR/scripts/macos-python.sh"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "OvisOCR2 MLX/Transformers setup requires macOS Apple Silicon."
  exit 1
fi

VENV_DIR="${OVISOCR2_MACOS_VENV:-.venv-ovisocr2-macos}"
select_supported_macos_python "$VENV_DIR/bin/python"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Creating OvisOCR2 virtual environment: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/python" -m pip install -r requirements-macos-ovisocr2.txt

"$VENV_DIR/bin/python" - <<'PY'
import torch
import transformers
import mlx_vlm

if not torch.backends.mps.is_available():
    raise SystemExit("PyTorch MPS is not available on this Mac")
print("torch", torch.__version__, "mps", torch.backends.mps.is_available())
print("transformers", transformers.__version__)
print("mlx-vlm", mlx_vlm.__version__)
PY

echo "OvisOCR2 macOS environment is ready."
