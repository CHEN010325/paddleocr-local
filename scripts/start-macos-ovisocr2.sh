#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Keep this compatibility entry point on the same fail-closed launcher used by
# every other macOS model. User-provided selection flags are intentionally
# overridden so this script can never start OvisOCR2 beside another model.
export PANDOCR_ENABLE_PADDLEOCR_VL=0
export PANDOCR_ENABLE_PPOCRV6=0
export PANDOCR_ENABLE_UNLIMITED_OCR=0
export PANDOCR_ENABLE_OVISOCR2=1
export PANDOCR_ACTIVE_MODEL_ON_START=ovisocr2
export PANDOCR_MODEL_CATALOG=ovisocr2

exec bash scripts/start-macos.sh
