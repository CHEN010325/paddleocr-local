#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

bash scripts/setup-macos-ovisocr2.sh
bash scripts/start-macos-ovisocr2.sh

if [[ "${PANDOCR_OPEN_BROWSER:-1}" != "0" ]]; then
  open "http://${PANDOCR_HOST:-127.0.0.1}:${PANDOCR_PORT:-8000}"
fi
