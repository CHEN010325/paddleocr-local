# PaddleOCR Local

**One local workbench for five OCR and document-parsing models.** Upload images, PDFs, Word documents, or PowerPoint files, inspect source and structured output side by side, and export Markdown, JSON, and extracted assets.

[简体中文](README.md) · [Quick start](QUICKSTART.md) · [CLI](CLI.md) · [Compatibility](docs/compatibility.md) · [Roadmap](ROADMAP.md) · [Contributing](CONTRIBUTING.md)

![CI](https://github.com/CHEN010325/paddleocr-local/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/github/license/CHEN010325/paddleocr-local)
![Release](https://img.shields.io/github/v/release/CHEN010325/paddleocr-local?include_prereleases)

<img width="1920" height="945" alt="PaddleOCR Local WebUI" src="https://github.com/user-attachments/assets/85a247a0-c796-4a20-b596-1cc4148df964" />

## Why it exists

- **Local-first**: documents, results, and task history stay on your machine.
- **Five models, one UI**: PaddleOCR-VL 1.6, PP-OCRv6, Unlimited-OCR, OvisOCR2, and HPD-Parsing.
- **Single-GPU friendly**: only the active model runs; switching releases the others' VRAM.
- **Cross-platform**: Windows/Linux NVIDIA Docker plus native and MLX paths for Apple Silicon.
- **Built for documents**: page progress, recovery, source/result comparison, tables, formulas, and exports.
- **Deployment hardening**: VRAM preflight, isolated model control and Office conversion, API tokens, pinned dependencies, and high-coverage tests.

## Models

| Model | Best for | Suggested hardware | Notes |
| --- | --- | --- | --- |
| PaddleOCR-VL 1.6 | Complex layouts, tables, formulas | NVIDIA 12 GB+ | Main PaddleOCR document pipeline |
| PP-OCRv6 | Text OCR and lower-memory systems | NVIDIA 4 GB+ | Fast startup; CPU Lite remains on the roadmap |
| Unlimited-OCR | Long structured documents | NVIDIA 8 GB+ | Transformers and SGLang backends |
| OvisOCR2 | Document understanding, Apple Silicon | NVIDIA 8 GB+ / Apple Silicon | Uses MLX by default on macOS |
| HPD-Parsing | High-quality document parsing | NVIDIA 8 GB+ | Official customized vLLM runtime |

These are startup compatibility guidelines, not guarantees for every document. See the [compatibility guide](docs/compatibility.md).

## Quick start

Windows with NVIDIA:

```powershell
.\windows-one-click.bat
```

macOS Apple Silicon:

```bash
./macos-one-click.command
```

Linux / Docker:

```bash
cp env.docker env.txt
./build.sh
./deploy.sh
```

Then open <http://localhost:8000>. Each logical model is guarded by its own Compose profile. The deployment script creates stopped standby containers and lets the controller start exactly one selected model. A switch fully stops the old model and releases its GPU memory before the new model starts, so GPU memory contains only the currently selected logical model at every moment. The scripts never use a bare `docker compose up` that could load multiple models. The first run downloads images or model weights. See [Docker deployment](DOCKER_DEPLOY.md) for advanced configuration.

## Features

- Multi-file image, PDF, PPT/PPTX, DOC/DOCX upload
- Page/batch PDF processing, progress, persistence, and interrupted-task recovery
- On-demand deployment, VRAM preflight, and runtime switching for five models
- Markdown, table, formula, code, and extracted-image rendering
- Side-by-side source and result views with synchronized scrolling
- Searchable local task history
- Markdown, JSON, and extracted-asset downloads
- Chinese and English UI
- FastAPI endpoints and OpenAPI description
- CLI, folder batching, watch-folder automation, and multi-model reports

## Quality and security

- 234 Python tests and 46 frontend tests with 95%+ coverage gates
- Dependency vulnerability audits in CI
- Pinned container images, Actions, and model revisions
- Isolated Web, Docker controller, and LibreOffice converter services

See [CHANGELOG](CHANGELOG.md), [SECURITY](SECURITY.md), and [SUPPORT](SUPPORT.md).

Contributions, hardware reports, model adapters, and documentation improvements are welcome. The project is licensed under [Apache-2.0](LICENSE).

> PaddleOCR Local is a community project and is not an official PaddlePaddle product. Product and model names belong to their respective owners.
