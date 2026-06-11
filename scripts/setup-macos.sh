#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This setup script is for macOS."
  exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "Apple Silicon arm64 is required by the official PaddleOCR-VL Apple Silicon guide."
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="python3.12"
  else
    PYTHON_BIN="python3"
  fi
fi

VENV_DIR="${PANDOCR_MACOS_VENV:-.venv-macos}"
PADDLEPADDLE_VERSION="${PADDLEPADDLE_VERSION:-3.3.0}"
INSTALL_MLX_VLM="${INSTALL_MLX_VLM:-false}"

echo "Using Python: $($PYTHON_BIN -c 'import sys; print(sys.executable)')"
echo "Creating virtual environment: $VENV_DIR"
"$PYTHON_BIN" -m venv "$VENV_DIR"

source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install "paddlepaddle==${PADDLEPADDLE_VERSION}" -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
python -m pip install -r requirements-macos.txt
if [[ "$INSTALL_MLX_VLM" == "1" || "$INSTALL_MLX_VLM" == "true" || "$INSTALL_MLX_VLM" == "yes" ]]; then
  python -m pip install -r requirements-macos-mlx.txt
fi
paddlex --install serving -y

python - <<'PY'
import paddle
import paddleocr
import paddlex

print("paddle", paddle.__version__)
print("paddleocr", getattr(paddleocr, "__version__", "unknown"))
print("paddlex", getattr(paddlex, "__version__", "unknown"))
paddle.utils.run_check()
PY

echo "macOS setup complete."
echo "Start with: bash scripts/start-macos.sh"
echo "For MLX-VLM acceleration, run: INSTALL_MLX_VLM=1 bash scripts/setup-macos.sh"
