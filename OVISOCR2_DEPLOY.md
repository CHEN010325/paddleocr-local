# OvisOCR2 integration

This project exposes `ATH-MaaS/OvisOCR2` as an optional document-parsing model in the existing model selector.
It runs in an isolated vLLM 0.22.1 container and does not add GPU dependencies to `pandocr-web`.
The image uses the official CUDA 12.9 vLLM wheel because vLLM's default 0.22.1 image requires CUDA 13.0.

## Requirements

- NVIDIA GPU with about 8 GB or more free VRAM (16 GB recommended for long pages)
- A recent NVIDIA driver and Docker Desktop/Engine with NVIDIA Container Toolkit
- Internet access for the first image pull and Hugging Face model download

The runtime is tuned for this project's single-page, serial OCR workflow:

- model: `ATH-MaaS/OvisOCR2`
- vLLM: `0.22.1`
- fixed KV cache: `512 MiB`
- maximum context length: `32768`
- maximum concurrent sequences: `1`
- max output tokens: `8192`
- GDN prefill backend: `triton`
- deterministic PyTorch sampler (`VLLM_USE_FLASHINFER_SAMPLER=0`)
- image pixel range: `448 x 448` to `2880 x 2880`

## Deploy

For RTX 50 / Blackwell, use `env.txt`. For RTX 30/40, use `env.docker`.

Windows one-click deployment:

```powershell
.\windows-one-click.bat -Model ovisocr2
```

To prepare all four models while making OvisOCR2 the first active model:

```powershell
.\windows-one-click.bat -Models all -ActiveModel ovisocr2
```

Manual Compose deployment:

```powershell
docker compose --env-file env.txt --profile ovisocr2 build ovisocr2-api pandocr-web
docker compose --env-file env.txt --profile ovisocr2 up -d --no-start
docker compose --env-file env.txt start pandocr-web
```

Open `http://localhost:8000`, select **OvisOCR2**, and confirm deployment if its runtime is shown as missing.
The WebUI will create/start `ovisocr2-api`, wait for the model to load, and stop the other OCR model containers on a single-GPU setup.

To deploy and start OvisOCR2 directly:

```powershell
$env:PANDOCR_ACTIVE_MODEL_ON_START = "ovisocr2"
docker compose --env-file env.txt --profile ovisocr2 up -d --build ovisocr2-api pandocr-web
Remove-Item Env:PANDOCR_ACTIVE_MODEL_ON_START
```

The one-command deployment temporarily overrides the WebUI startup model so it does not stop OvisOCR2
while the adapter is loading.

Check readiness:

```powershell
curl.exe http://localhost:8084/health
```

The first start downloads the model to `model_cache_ovisocr2/` and stores vLLM/Torch compile artifacts in
`model_cache_ovisocr2_vllm/`. Both caches persist across container recreation.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PANDOCR_ENABLE_OVISOCR2` | `1` in provided env files | Adds OvisOCR2 to the WebUI model catalog |
| `OVISOCR2_MODEL_NAME` | `ATH-MaaS/OvisOCR2` | Hugging Face model id or mounted local path |
| `OVISOCR2_KV_CACHE_MEMORY_MB` | `512` | Fixed KV cache allocation for one 32K sequence |
| `OVISOCR2_STARTUP_MEMORY_FRACTION` | `0.50` | vLLM startup free-memory threshold only; fixed KV cache sizing remains 512 MiB |
| `OVISOCR2_MAX_MODEL_LEN` | `32768` | Maximum input and output context length |
| `OVISOCR2_MAX_NUM_SEQS` | `1` | Maximum concurrent vLLM sequences |
| `OVISOCR2_MAX_TOKENS` | `8192` | Maximum generated Markdown tokens |
| `OVISOCR2_PDF_DPI` | `200` | PDF rasterization DPI inside the adapter |
| `OVISOCR2_MAX_PAGES_PER_REQUEST` | `50` | Safety limit for one adapter request |
| `OVISOCR2_API_PORT` | `8084` | Host-only adapter port |
| `OVISOCR2_GDN_PREFILL_BACKEND` | `triton` | vLLM GDN prefill backend |

OvisOCR2 image tags are converted to cropped `ocr_images/ovisocr2_*.jpg` assets so the existing Markdown preview and ZIP export can render visual regions.
