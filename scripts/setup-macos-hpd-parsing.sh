#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
source "$ROOT_DIR/scripts/macos-python.sh"
VENV_DIR="${HPD_PARSING_MACOS_VENV:-.venv-hpd-parsing-macos}"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "HPD-Parsing macOS setup requires Apple Silicon."
  exit 1
fi
select_supported_macos_python "$VENV_DIR/bin/python"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/python" -m pip install -r requirements-macos-hpd-parsing.txt
"$VENV_DIR/bin/python" - <<'PY'
import torch
if not torch.backends.mps.is_available():
    raise SystemExit("PyTorch MPS is not available on this Mac")
print("HPD-Parsing macOS environment ready; torch", torch.__version__)
PY
