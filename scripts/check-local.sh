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

step() {
  printf "\n==> %s\n" "$1"
}

step "Checking Python syntax"
"$PYTHON" -m py_compile \
  server.py \
  pandocr_cli.py \
  controller.py \
  office_converter.py \
  hpd_parsing_adapter.py \
  unlimited_ocr_adapter.py \
  ovisocr2_adapter.py \
  scripts/check-mlx-runtime.py

step "Running Python unit tests"
"$PYTHON" -m pytest -q

step "Checking and testing frontend JavaScript"
node --check static/bootstrap.mjs
node --check static/i18n.js
node --check static/app.js
npm run vendor:check
npm run test:frontend

step "Checking shell script syntax"
bash -n scripts/*.sh deploy.sh build.sh start-vlm.sh test-connection.sh

step "Running macOS Python selection tests"
bash tests/test_macos_python_selection.sh

step "Running macOS logical-model selection tests"
bash tests/test_macos_model_selection.sh

step "Checking generated OpenAPI snapshot"
"$PYTHON" - <<'PY'
import importlib
import json
import os
import tempfile

os.environ["PANDOCR_TASK_DATA_DIR"] = tempfile.mkdtemp()
os.environ["PANDOCR_MODEL_CONTROL"] = "none"
os.environ["PANDOCR_API_TOKEN"] = ""

server = importlib.import_module("server")
current = server.app.openapi()
with open("webui-openapi.json", encoding="utf-8") as stream:
    saved = json.load(stream)

if current != saved:
    raise SystemExit("webui-openapi.json is stale. Regenerate it from server.app.openapi().")
PY

printf "\nAll local checks passed.\n"
