#!/usr/bin/env bash
set -euo pipefail

GPU_ID="${PANDOCR_GPU_DEVICE_ID:-0}"
MODEL_NAME="${PADDLEOCR_VL_MODEL_NAME:-PaddleOCR-VL-1.6-0.9B}"
MIN_TOTAL_MIB="${PANDOCR_VLLM_MIN_TOTAL_MIB:-8192}"
MIN_REQUIRED_MIB="${PANDOCR_VLLM_MIN_REQUIRED_MIB:-6656}"
RESERVE_MIB="${PANDOCR_VLLM_RESERVE_MIB:-512}"
MAX_RATIO="${PANDOCR_VLLM_MAX_RATIO:-0.88}"
CONFIG_PATH="/tmp/vllm_backend_config.yaml"

gpu_memory="$(nvidia-smi --id="$GPU_ID" --query-gpu=memory.total,memory.free --format=csv,noheader,nounits | head -n 1)"

ratio="$(python3 - "$gpu_memory" "$MIN_TOTAL_MIB" "$MIN_REQUIRED_MIB" "$RESERVE_MIB" "$MAX_RATIO" <<'PY'
import math
import sys

gpu_memory, min_total_mib, min_required_mib, reserve_mib, max_ratio = sys.argv[1:]
total_text, free_text = [part.strip() for part in gpu_memory.split(",", 1)]
total_mib = float(total_text)
free_mib = float(free_text)
min_total_mib = float(min_total_mib)
min_required_mib = float(min_required_mib)
reserve_mib = float(reserve_mib)
max_ratio = float(max_ratio)

if total_mib < min_total_mib:
    raise SystemExit(
        f"GPU total memory {total_mib:.0f} MiB is below the required {min_total_mib:.0f} MiB."
    )

usable_mib = free_mib - reserve_mib
if usable_mib < min_required_mib:
    raise SystemExit(
        "Not enough free GPU memory for PaddleOCR-VL: "
        f"free={free_mib:.0f} MiB reserve={reserve_mib:.0f} MiB "
        f"required={min_required_mib:.0f} MiB."
    )

ratio = min(max_ratio, usable_mib / total_mib)
ratio = math.floor(ratio * 100) / 100

min_ratio = min_required_mib / total_mib
if ratio < min_ratio:
    raise SystemExit(
        "Computed vLLM memory ratio is too low: "
        f"ratio={ratio:.2f} minimum={min_ratio:.2f}."
    )

print(f"{ratio:.2f}")
PY
)"

cat > "$CONFIG_PATH" <<EOF
gpu-memory-utilization: $ratio
EOF

export VLLM_GPU_MEMORY_UTILIZATION="$ratio"

echo "Detected GPU $GPU_ID memory: $gpu_memory MiB; using vLLM gpu-memory-utilization=$ratio"

exec paddleocr genai_server \
  --model_name "$MODEL_NAME" \
  --host 0.0.0.0 \
  --port 8080 \
  --backend vllm \
  --backend_config "$CONFIG_PATH"
