#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH="$(hf download "${HPD_PARSING_MODEL_NAME:-PaddlePaddle/HPD-Parsing}" --quiet)"
export MAX_PATCHES_WITH_RESIZE=true

GPU_MEMORY_UTILIZATION="${HPD_PARSING_GPU_MEMORY_UTILIZATION:-auto}"
if [[ "$GPU_MEMORY_UTILIZATION" == "auto" ]]; then
  TOTAL_GPU_MEMORY_MIB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | sed -n '1p' | tr -d '[:space:]')"
  TARGET_GPU_MEMORY_MIB="${HPD_PARSING_GPU_MEMORY_TARGET_MIB:-6656}"
  GPU_MEMORY_UTILIZATION="$(awk -v total="$TOTAL_GPU_MEMORY_MIB" -v target="$TARGET_GPU_MEMORY_MIB" 'BEGIN {
    value = target / total
    if (value > 0.9) value = 0.9
    if (value < 0.1) value = 0.1
    printf "%.3f", value
  }')"
fi
echo "HPD-Parsing GPU memory utilization: ${GPU_MEMORY_UTILIZATION}" >&2

exec vllm serve "${MODEL_PATH}" \
  --trust-remote-code \
  --host 0.0.0.0 \
  --port 8118 \
  --served-model-name "${HPD_PARSING_SERVED_MODEL_NAME:-HPD-Parsing}" \
  --max-model-len "${HPD_PARSING_MAX_MODEL_LEN:-16384}" \
  --limit-mm-per-prompt '{"image": 1}' \
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" \
  --attention-backend FLASHINFER \
  --attention-config '{"use_prefill_query_quantization":true}' \
  --enable-chunked-prefill \
  --enable-prefix-caching \
  --speculative-config "{\"method\":\"medusa\",\"model\":\"${MODEL_PATH}/P-MTP\",\"num_speculative_tokens\":6}"
