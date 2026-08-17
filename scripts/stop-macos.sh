#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

stop_pid_file() {
  local pid_file="$1"
  local name="$2"
  local expected_command="$3"
  if [[ ! -f "$pid_file" ]]; then
    echo "$name is not running."
    return
  fi

  local pid
  pid="$(cat "$pid_file")"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    echo "$name pid file was invalid."
  elif kill -0 "$pid" >/dev/null 2>&1; then
    local command
    command="$(ps -ww -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$command" != *"$expected_command"* ]]; then
      echo "$name pid $pid belongs to another process; leaving it alone."
      rm -f "$pid_file"
      return
    fi

    echo "Stopping $name ($pid)"
    kill "$pid"
    for _ in {1..20}; do
      if ! kill -0 "$pid" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
    if kill -0 "$pid" >/dev/null 2>&1; then
      echo "Force stopping $name ($pid)"
      kill -9 "$pid"
      for _ in {1..5}; do
        if ! kill -0 "$pid" >/dev/null 2>&1; then
          break
        fi
        sleep 1
      done
      if kill -0 "$pid" >/dev/null 2>&1; then
        echo "$name ($pid) did not stop; refusing to start another model." >&2
        return 1
      fi
    fi
  else
    echo "$name pid file was stale."
  fi
  rm -f "$pid_file"
}

stop_pid_file run/pandocr-web.pid "PaddleOCR Local Web service" "server.py"
stop_pid_file run/ovisocr2.pid "OvisOCR2 adapter" "ovisocr2_adapter:app"
stop_pid_file run/unlimited-ocr.pid "Unlimited-OCR adapter" "unlimited_ocr_adapter:app"
stop_pid_file run/paddlex.pid "PaddleX service" "paddlex --serve"
stop_pid_file run/ppocrv6.pid "PP-OCRv6 service" "paddlex --serve"
stop_pid_file run/mlx-vlm.pid "MLX-VLM service" "mlx_vlm.server"

rm -f run/macos-services.env run/macos-services.expected.env
rm -f run/pipeline_config_macos_mlx.generated.yaml
