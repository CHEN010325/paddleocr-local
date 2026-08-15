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
  --fail-under=95

# Keep explicit per-file floors so a well-covered adapter cannot hide a
# regression in another one. HPD starts from its measured legacy baseline and
# should be raised as its platform-specific paths gain tests.
"$PYTHON" -m coverage report --fail-under=95 server.py
"$PYTHON" -m coverage report --fail-under=95 unlimited_ocr_adapter.py
"$PYTHON" -m coverage report --fail-under=95 ovisocr2_adapter.py
"$PYTHON" -m coverage report --fail-under=84 hpd_parsing_adapter.py
"$PYTHON" -m coverage report --fail-under=95 scripts/check-mlx-runtime.py
"$PYTHON" -m coverage report --fail-under=80 controller.py
"$PYTHON" -m coverage report --fail-under=80 office_converter.py

"$PYTHON" -m coverage report --show-missing \
  hpd_parsing_adapter.py \
  server.py \
  unlimited_ocr_adapter.py \
  ovisocr2_adapter.py \
  controller.py \
  office_converter.py \
  scripts/check-mlx-runtime.py

npm run coverage:frontend
