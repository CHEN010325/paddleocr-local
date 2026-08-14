# PaddleOCR Local

**Language / 语言**: [简体中文](README.md) | English

A self-hosted, multi-model document parsing WebUI for images, PDFs, PowerPoint, and Word documents, with Markdown preview and export.

Five isolated models are supported:

- PaddleOCR-VL 1.6
- PP-OCRv6
- Unlimited-OCR
- OvisOCR2
- HPD-Parsing

<img width="1920" height="945" alt="PaddleOCR Local WebUI" src="https://github.com/user-attachments/assets/85a247a0-c796-4a20-b596-1cc4148df964" />

## One-Click Deployment

The installer asks which model to deploy first and downloads and starts only that model. Unselected model weights are not downloaded and use no RAM or VRAM.

### Windows + NVIDIA

Install an NVIDIA driver and GPU-enabled Docker Desktop first.

```powershell
.\windows-one-click.bat
```

Deploy OvisOCR2 directly:

```powershell
.\windows-one-click.bat -Model ovisocr2
```

Deploy HPD-Parsing directly:

```powershell
.\windows-one-click.bat -Model hpd-parsing
```

HPD-Parsing uses its official customized vLLM image and requires an NVIDIA GPU, Linux x86-64 containers, and a driver supporting CUDA 12.8 or newer.

Validate the plan without downloading or starting services:

```powershell
.\windows-one-click.bat -Model ovisocr2 -DryRun
```

### macOS Apple Silicon

Apple M1, M2, M3, and M4 are supported. OvisOCR2 uses MLX by default.

```bash
./macos-one-click.command
```

Deploy OvisOCR2 directly:

```bash
./macos-one-click.command --model ovisocr2
```

Validate the plan without installing or starting services:

```bash
./macos-one-click.command --model ovisocr2 --dry-run
```

For Linux, manual Docker deployment, and advanced settings, see the [deployment guide](DOCKER_DEPLOY.md).

## Start Using It

After deployment, open:

- WebUI: http://localhost:8000
- PaddleOCR-VL: http://localhost:8081/health
- PP-OCRv6: http://localhost:8082/health
- Unlimited-OCR: http://localhost:8083/health
- OvisOCR2: http://localhost:8084/health
- HPD-Parsing: http://localhost:8085/health

A health endpoint is available only while its model is running. On a single GPU, only the selected model is loaded; switching models automatically stops the others to avoid unnecessary VRAM use.

## Features

- Image, PDF, PPT/PPTX, and DOC/DOCX parsing
- Five-model selection and on-demand deployment
- Page-by-page PDF parsing, progress, and persistent history
- Markdown, table, formula, and visual-region rendering
- Side-by-side source and result views
- Markdown, JSON, and extracted-image downloads
- Chinese and English UI

## Documentation

- [Quick Start](QUICKSTART.md)
- [OvisOCR2 deployment and configuration](OVISOCR2_DEPLOY.md)
- [Manual Docker deployment](DOCKER_DEPLOY.md)
- [API reference](api.md)

Repository: [https://github.com/CHEN010325/paddleocr-local](https://github.com/CHEN010325/paddleocr-local)
