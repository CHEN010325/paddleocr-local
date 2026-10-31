# OvisOCR3 deployment

OvisOCR3 replaces OvisOCR2 in the existing model slot. All new deployments use
`ATH-MaaS/OvisOCR3`, pinned to `4833d5d2afa6e769ee933f024504d67ace80a24c`.
Source and prompts: https://huggingface.co/ATH-MaaS/OvisOCR3

## One-command launch

Windows with NVIDIA and Docker Desktop:

```powershell
.\ovisocr3-one-click.bat
```

After upgrading to the full task API, rebuild the model image once. For an already
updated local image use `-SkipBuild -SkipPull`. The shared launcher retains
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

Modern NVIDIA hardware uses `pandocr-ovisocr3:latest`. Volta uses the distinct
`pandocr-ovisocr3-transformers-cu126:latest` image, built with
`Dockerfile.ovisocr3-transformers` (PyTorch 2.7.0 CUDA 12.6, Transformers 5.14.1, FP16/SDPA).
The Windows launcher selects this compatible profile automatically; pixel budget is
1,048,576 and idle CUDA caches are released between calls. Default caches use `model_cache_ovisocr3/`,
`model_cache_ovisocr3_vllm/`, or `model_cache_ovisocr3_macos/`.
Existing OvisOCR2 weights and tasks are preserved. The optional private runtime environment
`tmp/pandocr-runtime.env` is still prepared through `prepare-runtime-env`; keep it private.

## Output and resources

The workbench and default API use `document_auto`: page structure and figure descriptions,
content-driven chart/label/field extraction, then document-wide cross-page tables.
There are no task selectors in the workbench. Original source blocks and outputs remain
available; searchable PDF text excludes generated descriptions. Invalid JSON and
truncated primary output fail explicitly; optional refinement failures retain the base result.

The modern NVIDIA vLLM backend uses official vLLM 0.22.1 CUDA 12.9 wheels, a 512 MiB KV budget,
32768 context, one concurrent sequence, and 16384 output tokens. These defaults are
bounded deployment settings, not a claim about the model's maximum context length.
Mac vision defaults remain conservative; output budget is also 16384.

All 18 official tools remain available through diagnostic API parameters. The workbench
selects useful tools internally for document parsing. See the
[automatic workflow and API contracts](OVISOCR3_CAPABILITIES.md).
Full-document enhancement streams original pages, crosses UI batch boundaries and
preserves raw page outputs. Its default 400-page limit is separate from the default
50-page stage-one request limit. Three-page reconstruction requires local Chromium;
the updated Docker and Mac installers include the renderer dependencies and browser.
