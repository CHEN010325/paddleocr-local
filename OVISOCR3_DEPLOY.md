# OvisOCR3 deployment

OvisOCR3 replaces OvisOCR2 in the existing model slot. All new deployments use
`ATH-MaaS/OvisOCR3`, pinned to `4833d5d2afa6e769ee933f024504d67ace80a24c`.
Source and prompts: https://huggingface.co/ATH-MaaS/OvisOCR3

## One-command launch

Windows with NVIDIA and Docker Desktop:

```powershell
.\ovisocr3-one-click.bat
```

For existing local images use `-SkipBuild -SkipPull`. The shared launcher retains
Docker socket recovery, GPU checks, and exclusive model switching.
The standard `windows-one-click.bat -Model ovisocr3` selection also works.
Linux uses the existing managed deployment with the `ovisocr2` Compose service/profile.

macOS Apple Silicon:

```bash
./macos-ovisocr3-one-click.command
```

The Mac path defaults to MLX-VLM and retains Transformers/MPS fallback. This upgrade
has no new Mac hardware acceptance result; installation/routing checks do not replace
real device tests. Prior OvisOCR2 timings must not be presented as OvisOCR3 results.

## Compatibility and data

The internal model ID, service, API compatibility route, and `OVISOCR2_*` configuration
keys remain accepted so existing task data and automation keep working. The displayed
model and all default weights are OvisOCR3. `ovisocr3` is accepted as a new selection/request
alias; `/api/ovisocr3` is the current public route. Explicit external engine IDs remain unchanged.
Known second-generation repo/revision defaults are upgraded on load, without rewriting old tasks.
`OVISOCR3_MODEL_NAME` / `OVISOCR3_MODEL_REVISION` take precedence over legacy settings.

The isolated image is `pandocr-ovisocr3:latest`. Default caches use `model_cache_ovisocr3/`,
`model_cache_ovisocr3_vllm/`, or `model_cache_ovisocr3_macos/`.
Existing OvisOCR2 weights and tasks are preserved. The optional private runtime environment
`tmp/pandocr-runtime.env` is still prepared through `prepare-runtime-env`; keep it private.

## Output and resources

The default is official structured document parsing: JSON bbox/category/content records
are converted to the workbench's Markdown, normalized 0..1000 layout blocks, and image crops.
Use `OVISOCR3_OUTPUT_FORMAT=markdown` for the official Markdown prompt and the existing
Markdown cleanup behavior. Invalid JSON, missing pages, and truncated output fail explicitly.
HTML is rendered through the workbench's existing sanitizer.

The NVIDIA backend uses official vLLM 0.22.1 CUDA 12.9 wheels, a 512 MiB KV budget,
32768 context, one concurrent sequence, and 16384 output tokens. These defaults are
bounded deployment settings, not a claim about the model's maximum context length.
Mac vision defaults remain conservative; output budget is also 16384.

This workbench exposes document parsing. The model's additional OCR/KIE/figure-caption/
cross-page table task prompts are not separate UI features in this replacement.
