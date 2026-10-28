# PaddleOCR Local

**A local document workbench that turns PDFs, images, and Office files into editable Markdown.** Run six OCR/document-parsing models behind one UI, keep data on your machine, automate with the CLI or watch folder, and create editable or searchable exports.

The default Windows one-click and Linux/Docker deployments show six models for on-demand deployment and switching, with only the selected model running. The optional [external API mode](docs/external-engines.md) shows only the services you configure. Remote services receive the document content you send for recognition.

[简体中文](README.md) · [Quick start](QUICKSTART.md) · [CLI](CLI.md) · [Compatibility](docs/compatibility.md) · [Roadmap](ROADMAP.md) · [Contributing](CONTRIBUTING.md)

![CI](https://github.com/CHEN010325/paddleocr-local/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/github/license/CHEN010325/paddleocr-local)
![Release](https://img.shields.io/github/v/release/CHEN010325/paddleocr-local?include_prereleases)

<img width="1920" height="945" alt="PaddleOCR Local WebUI" src="https://github.com/user-attachments/assets/85a247a0-c796-4a20-b596-1cc4148df964" />

## Unlimited-OCR-2 one-command deployment

With NVIDIA drivers and GPU-enabled Docker Desktop installed, run from the repository root:

```powershell
.\unlimited-ocr2-one-click.bat
```

Detects GPU architecture, VRAM, driver, and Docker GPU support. The 12GB profile selects Transformers;
16GB and larger profiles select SGLang, with context and page batches adjusted to the GPU.
Unsupported configurations receive a clear explanation and alternative entry points.
The first run builds the environment, downloads pinned weights, and opens the WebUI.
Use `-Backend transformers` for native inference. The 16GB defaults use one public OCR job,
CUDA Graphs, and five-page batches. Existing launch commands for other models remain available.
See [configuration and resource limits](docs/unlimited-ocr.md).

## OvisOCR3 one-command deployment

[OvisOCR3](https://huggingface.co/ATH-MaaS/OvisOCR3) replaces OvisOCR2 in the existing
document workbench. With NVIDIA drivers and GPU-enabled Docker Desktop installed,
run from the repository root:

```powershell
.\ovisocr3-one-click.bat
```

The first run checks GPU, driver, and Docker GPU support, builds the environment,
downloads pinned weights, and opens <http://localhost:8000/>. Use `-DryRun` to check
deployment prerequisites, or `-SkipBuild -SkipPull` when local images are already built.
The shared launcher retains known Docker socket recovery, exclusive model switching,
existing model entry points, saved tasks, and model caches.

vLLM is the default NVIDIA backend; Transformers is also supported. Structured document
parsing produces layout boxes, tables, formulas, and cropped image assets. Separate
general-OCR, KIE, figure-captioning, and cross-page-table task interfaces are not included.

Apple Silicon has a dedicated entry point, defaulting to MLX:

```bash
./macos-ovisocr3-one-click.command
```

Both NVIDIA backends were tested on Windows with an RTX 5070 Ti 16 GB. These are
single sample requests after warmup; Office conversion is excluded from parse timings:

| Input | vLLM | Transformers |
| --- | ---: | ---: |
| One image | 1.59 s | 19.19 s |
| Two-page PDF | 4.03 s | 40.75 s |
| Two-page dense scanned PDF | 11.33 s | 86.61 s |

First inference may compile kernels: the first vLLM acceptance request took 56.52 s,
excluding model startup. These local samples do not guarantee speed for other documents.
The macOS scripts are checked, but OvisOCR3 inference has not been validated on Mac hardware.
See [deployment instructions](OVISOCR3_DEPLOY.md) and [validation details](docs/ovisocr3-validation.md).

## Why it exists

For a machine without NVIDIA, run `docker compose -f docker-compose.cpu.yml up -d --build --wait --wait-timeout 1200`
and open http://127.0.0.1:8000. See [CPU deployment and validation scope](docs/cpu.md).
Footnotes and sidebar text are retained by default; explicit ignore switches are available.

- **Local-first**: documents, results, and task history stay on your machine.
- **Six models, one UI**: PaddleOCR-VL 1.6, PP-OCRv6, Unlimited-OCR-2, OvisOCR3, HPD-Parsing, and NaviDC-OCR. Unlimited-OCR-2 replaces the first generation and supports both Transformers and SGLang. Only the selected model runs.
- **Single-GPU friendly**: only the active model runs; switching releases the others' VRAM.
- **Cross-platform**: Windows/Linux NVIDIA Docker plus native and MLX paths for Apple Silicon.
- **Built for documents**: page progress, recovery, source/result comparison, tables, formulas, and editable/searchable exports.
- **Deployment hardening**: VRAM preflight, isolated model control and Office conversion, API tokens, pinned dependencies, and high-coverage tests.

## Who it is for

| Need | Start here |
| --- | --- |
| Keep private documents offline | Local WebUI and on-machine task history |
| Work with limited VRAM | PP-OCRv6 / OvisOCR3 with on-demand switching |
| Parse papers, tables, and formulas | PaddleOCR-VL 1.6 or HPD-Parsing |
| Automate batches | CLI, folder batching, or Watch Folder |
| Use Apple Silicon | Native macOS path or OvisOCR3 MLX |

## Models

| Model | Best for | Suggested hardware | Notes |
| --- | --- | --- | --- |
| PaddleOCR-VL 1.6 | Complex layouts, tables, formulas | NVIDIA 12 GB+ | Main PaddleOCR document pipeline |
| Unlimited-OCR-2 | Continuous multipage documents, layout and tables | NVIDIA 16 GB recommended | Transformers / SGLang backends, dedicated one-command deployment |
| PP-OCRv6 | Text OCR and lower-memory systems | CPU / NVIDIA 4 GB+ | Independent CPU deployment; see [CPU setup](docs/cpu.md) |
| OvisOCR3 | Document understanding, Apple Silicon | NVIDIA 8 GB+ / Apple Silicon | Uses MLX by default on macOS |
| HPD-Parsing | High-quality document parsing | NVIDIA 8 GB+ | Official customized vLLM runtime |
| NaviDC-OCR | Complex documents, layout, tables and formulas | NVIDIA 8 GB+ | Official NaviOCRClient vLLM async two-step pipeline (Transformers backend optional) |

These are startup compatibility guidelines, not guarantees for every document. See the [compatibility guide](docs/compatibility.md).

## Quick start

Windows with NVIDIA:

```powershell
.\windows-one-click.bat -DryRun
.\windows-one-click.bat
```

The launcher starts Docker Desktop when needed and retries the known OTel stale-socket
failure once, preserving model caches and project data. Dry runs never start or repair Docker.

macOS Apple Silicon:

```bash
make doctor
./macos-one-click.command
```

Linux / Docker:

```bash
make doctor
cp env.docker env.txt
./build.sh
./deploy.sh
```

Then open <http://localhost:8000>. Each of the six logical models is guarded by its own Compose profile. The deployment script creates stopped standby containers and lets the controller start exactly one selected model. A switch fully stops the old model and releases its GPU memory before the new model starts, so GPU memory contains only the currently selected logical model at every moment. The scripts never use a bare `docker compose up` that could load multiple models. The first run downloads images or model weights. See [Docker deployment](DOCKER_DEPLOY.md) for advanced configuration, or use the [60-second demo](docs/QUICK_DEMO.md) to exercise export and recovery first.

## Features

- Multi-file image, PDF, PPT/PPTX, DOC/DOCX upload
- Page/batch PDF processing, progress, persistence, and interrupted-task recovery
- Retry failed batches without reprocessing completed batches
- On-demand deployment, VRAM preflight, and runtime switching for six default models
- Hardware-aware model recommendation with one-click switching
- Markdown, table, formula, code, and extracted-image rendering
- Side-by-side source and result views with synchronized scrolling
- Searchable local task history
- Markdown, JSON, extracted assets, editable DOCX, searchable PDF, self-contained HTML, and table CSV/XLSX export
- Chinese and English UI
- FastAPI endpoints and OpenAPI description
- CLI, folder batching, and watch-folder automation

## Quality and security

- 250+ Python tests and 48+ frontend tests with 95%+ coverage gates, including a dedicated exporter floor
- Dependency vulnerability audits in CI
- Pinned container images, Actions, and model revisions
- Isolated Web, Docker controller, and LibreOffice converter services

See [CHANGELOG](CHANGELOG.md), [SECURITY](SECURITY.md), and [SUPPORT](SUPPORT.md).

## Why star this project

- One local entry point for images, PDFs, Word, and PowerPoint instead of separate model-specific setups.
- Strict single-GPU switching so large models do not unexpectedly occupy the GPU together.
- Editable, batch-friendly output that can feed a personal workflow or internal service.
- No default document upload or telemetry; hardware claims are backed by reproducible reports.

Contributions, hardware reports, model adapters, and documentation improvements are welcome. The project is licensed under [Apache-2.0](LICENSE).

> PaddleOCR Local is a community project and is not an official PaddlePaddle product. Product and model names belong to their respective owners.
