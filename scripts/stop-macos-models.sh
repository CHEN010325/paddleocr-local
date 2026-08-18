#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

stop_pid_file() {
  local pid_file="$1" name="$2" expected="$3"
  [[ -f "$pid_file" ]] || return 0
  local pid; pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1; then
    local command; command="$(ps -ww -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$command" == *"$expected"* ]]; then
      echo "Stopping $name ($pid)"
      kill "$pid" 2>/dev/null || true
      for _ in {1..20}; do
        kill -0 "$pid" >/dev/null 2>&1 || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pid_file"
}

stop_pid_file run/paddlex.pid "PaddleX service" "paddlex --serve"
stop_pid_file run/ppocrv6.pid "PP-OCRv6 service" "paddlex --serve"
stop_pid_file run/mlx-vlm.pid "MLX-VLM service" "mlx_vlm.server"
stop_pid_file run/unlimited-ocr.pid "Unlimited-OCR adapter" "unlimited_ocr_adapter:app"
stop_pid_file run/ovisocr2.pid "OvisOCR2 adapter" "ovisocr2_adapter:app"
stop_pid_file run/hpd-parsing.pid "HPD-Parsing adapter" "hpd_parsing_adapter:app"
stop_pid_file run/hpd-parsing-backend.pid "HPD-Parsing MPS backend" "hpd_parsing_macos_server:app"
