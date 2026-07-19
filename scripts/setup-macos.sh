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
  echo "Apple Silicon arm64 is required by the official PaddleOCR-VL Apple Silicon guide."
  exit 1
fi

VENV_DIR="${PANDOCR_MACOS_VENV:-.venv-macos}"
VENV_PYTHON_VERSION=""
if [[ -x "$VENV_DIR/bin/python" ]]; then
  VENV_PYTHON_VERSION="$(python_minor_version "$VENV_DIR/bin/python" || true)"
fi

select_supported_macos_python "$VENV_DIR/bin/python"
PYTHON_VERSION="$(python_minor_version "$PYTHON_BIN")"

PADDLEPADDLE_VERSION="${PADDLEPADDLE_VERSION:-3.3.0}"
INSTALL_MLX_VLM="${INSTALL_MLX_VLM:-false}"

echo "Using Python: $($PYTHON_BIN -c 'import sys; print(sys.executable)')"
if [[ -x "$VENV_DIR/bin/python" ]]; then
  if [[ "$VENV_PYTHON_VERSION" == "3.12" || "$VENV_PYTHON_VERSION" == "3.13" ]]; then
    echo "Reusing virtual environment: $VENV_DIR (Python $VENV_PYTHON_VERSION)"
  else
    echo "Recreating $VENV_DIR with Python $PYTHON_VERSION because its Python $VENV_PYTHON_VERSION is unsupported."
    "$PYTHON_BIN" -m venv --clear "$VENV_DIR"
  fi
else
  echo "Creating virtual environment: $VENV_DIR (Python $PYTHON_VERSION)"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install "paddlepaddle==${PADDLEPADDLE_VERSION}" -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
python -m pip install -r requirements-macos.txt
if [[ "$INSTALL_MLX_VLM" == "1" || "$INSTALL_MLX_VLM" == "true" || "$INSTALL_MLX_VLM" == "yes" ]]; then
  python -m pip install -r requirements-macos-mlx.txt
  python scripts/check-mlx-runtime.py
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
