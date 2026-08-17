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

selection_env=(
  env
  -u PANDOCR_ENABLE_PADDLEOCR_VL
  -u PANDOCR_ENABLE_PPOCRV6
  -u PANDOCR_ENABLE_UNLIMITED_OCR
  -u PANDOCR_ENABLE_OVISOCR2
  -u PANDOCR_ACTIVE_MODEL_ON_START
  -u PANDOCR_MODEL_CATALOG
  PANDOCR_MODEL_SELECTION_CHECK_ONLY=1
)

assert_start_selection() {
  local expected="$1"
  shift
  local output
  output="$("${selection_env[@]}" "$@" bash scripts/start-macos.sh)"
  [[ "$output" == *"Selected macOS logical model: $expected"* ]]
  [[ "$output" == *"no services were changed"* ]]
}

assert_start_selection paddleocr-vl-1.6
assert_start_selection paddleocr-vl-1.6 PANDOCR_ENABLE_PADDLEOCR_VL=1
assert_start_selection pp-ocrv6 PANDOCR_ENABLE_PPOCRV6=1
assert_start_selection unlimited-ocr PANDOCR_ENABLE_UNLIMITED_OCR=1
assert_start_selection ovisocr2 PANDOCR_ENABLE_OVISOCR2=1
assert_start_selection ovisocr2 PANDOCR_ACTIVE_MODEL_ON_START=ovisocr2

legacy_ovis_output="$(
  "${selection_env[@]}" \
    PANDOCR_ENABLE_PADDLEOCR_VL=1 \
    PANDOCR_ENABLE_PPOCRV6=1 \
    PANDOCR_ACTIVE_MODEL_ON_START=pp-ocrv6 \
    PANDOCR_MODEL_CATALOG=pp-ocrv6,ovisocr2 \
    bash scripts/start-macos-ovisocr2.sh
)"
[[ "$legacy_ovis_output" == *"Selected macOS logical model: ovisocr2"* ]]
[[ "$legacy_ovis_output" == *"no services were changed"* ]]

if "${selection_env[@]}" PANDOCR_ENABLE_PADDLEOCR_VL=1 PANDOCR_ENABLE_PPOCRV6=1 bash scripts/start-macos.sh >/dev/null 2>&1; then
  echo "start-macos unexpectedly accepted two enabled logical models."
  exit 1
fi

if "${selection_env[@]}" PANDOCR_ENABLE_PADDLEOCR_VL=0 PANDOCR_ENABLE_PPOCRV6=0 PANDOCR_ENABLE_UNLIMITED_OCR=0 PANDOCR_ENABLE_OVISOCR2=0 bash scripts/start-macos.sh >/dev/null 2>&1; then
  echo "start-macos unexpectedly accepted zero enabled logical models."
  exit 1
fi

if "${selection_env[@]}" PANDOCR_ENABLE_PADDLEOCR_VL=1 PANDOCR_ACTIVE_MODEL_ON_START=pp-ocrv6 bash scripts/start-macos.sh >/dev/null 2>&1; then
  echo "start-macos unexpectedly accepted a mismatched active model."
  exit 1
fi

if "${selection_env[@]}" PANDOCR_ENABLE_OVISOCR2=1 PANDOCR_MODEL_CATALOG=ovisocr2,pp-ocrv6 bash scripts/start-macos.sh >/dev/null 2>&1; then
  echo "start-macos unexpectedly accepted more than one catalog model."
  exit 1
fi

if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
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
else
  echo "Skipping Apple-Silicon-only one-click dry runs on this host."
fi

if ./macos-one-click.command --model unsupported --dry-run --no-open >/dev/null 2>&1; then
  echo "Unsupported model unexpectedly succeeded."
  exit 1
fi

echo "macOS model selection tests passed."
