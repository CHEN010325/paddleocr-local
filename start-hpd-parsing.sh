#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH="$(hf download "${HPD_PARSING_MODEL_NAME:-PaddlePaddle/HPD-Parsing}" --quiet)"
export MAX_PATCHES_WITH_RESIZE=true

exec vllm serve "${MODEL_PATH}" \
  --trust-remote-code \
  --host 0.0.0.0 \
  --port 8118 \
  --served-model-name "${HPD_PARSING_SERVED_MODEL_NAME:-HPD-Parsing}" \
  --max-model-len "${HPD_PARSING_MAX_MODEL_LEN:-16384}" \
  --limit-mm-per-prompt '{"image": 1}' \
  --gpu-memory-utilization "${HPD_PARSING_GPU_MEMORY_UTILIZATION:-0.9}" \
  --attention-backend FLASHINFER \
  --attention-config '{"use_prefill_query_quantization":true}' \
  --enable-chunked-prefill \
  --enable-prefix-caching \
  --speculative-config "{\"method\":\"medusa\",\"model\":\"${MODEL_PATH}/P-MTP\",\"num_speculative_tokens\":6}"
