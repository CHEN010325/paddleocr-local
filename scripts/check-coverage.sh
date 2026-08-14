#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="$PYTHON_BIN"
elif [[ -x ".venv-macos/bin/python" ]]; then
  PYTHON=".venv-macos/bin/python"
else
  PYTHON="python3"
fi

"$PYTHON" -m coverage erase
"$PYTHON" -m coverage run --branch -m pytest -q
"$PYTHON" -m coverage report \
  --show-missing \
  --fail-under=95 \
  hpd_parsing_adapter.py \
  server.py \
  unlimited_ocr_adapter.py \
  ovisocr2_adapter.py \
  scripts/check-mlx-runtime.py

npm run coverage:frontend
