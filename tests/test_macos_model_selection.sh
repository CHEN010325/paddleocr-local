#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

assert_selection() {
  local requested="$1"
  local expected="$2"
  local output
  output="$(./macos-one-click.command --model "$requested" --dry-run --no-open)"
  [[ "$output" == *"Selected model: $expected"* ]]
  [[ "$output" == *"Dry run: no dependencies installed and no services changed."* ]]
}

assert_selection 1 paddleocr-vl-1.6
assert_selection ppocrv6 pp-ocrv6
assert_selection unlimited unlimited-ocr
assert_selection ovis ovisocr2

default_ovis_output="$(env -u OVISOCR2_BACKEND ./macos-one-click.command --model ovisocr2 --dry-run --no-open)"
[[ "$default_ovis_output" == *"OvisOCR2 backend: mlx"* ]]

fallback_ovis_output="$(OVISOCR2_BACKEND=transformers ./macos-one-click.command --model ovisocr2 --dry-run --no-open)"
[[ "$fallback_ovis_output" == *"OvisOCR2 backend: transformers"* ]]

if OVISOCR2_BACKEND=vllm ./macos-one-click.command --model ovisocr2 --dry-run --no-open >/dev/null 2>&1; then
  echo "Unsupported macOS OvisOCR2 backend unexpectedly succeeded."
  exit 1
fi

if ./macos-one-click.command --model unsupported --dry-run --no-open >/dev/null 2>&1; then
  echo "Unsupported model unexpectedly succeeded."
  exit 1
fi

echo "macOS model selection tests passed."
