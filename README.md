# PaddleOCR Local

**语言 / Language**：简体中文 | [English](README.en.md)

一个可本地部署的多模型文档解析 WebUI，支持上传图片、PDF、PPT、Word，查看解析结果并导出 Markdown。

支持五个独立模型：

- PaddleOCR-VL 1.6
- PP-OCRv6
- Unlimited-OCR
- OvisOCR2
- HPD-Parsing

<img width="1920" height="945" alt="PaddleOCR Local WebUI" src="https://github.com/user-attachments/assets/85a247a0-c796-4a20-b596-1cc4148df964" />

## 一键部署

安装脚本会让你选择首次部署的模型，并且只下载和启动所选模型。其他模型不会下载权重，也不会占用内存或显存。

### Windows + NVIDIA

需要提前安装 NVIDIA 驱动和支持 GPU 的 Docker Desktop。

```powershell
.\windows-one-click.bat
```

直接部署 OvisOCR2：

```powershell
.\windows-one-click.bat -Model ovisocr2
```

直接部署 HPD-Parsing：

```powershell
.\windows-one-click.bat -Model hpd-parsing
```

HPD-Parsing 使用官方定制 vLLM 镜像，需要 NVIDIA GPU、Linux x86-64 容器和支持 CUDA 12.8+ 的驱动。

只检查配置，不下载或启动：

```powershell
.\windows-one-click.bat -Model ovisocr2 -DryRun
```

### macOS Apple Silicon

支持 Apple M1、M2、M3、M4，OvisOCR2 默认使用 MLX。

```bash
./macos-one-click.command
```

直接部署 OvisOCR2：

```bash
./macos-one-click.command --model ovisocr2
```

只检查配置，不安装或启动：

```bash
./macos-one-click.command --model ovisocr2 --dry-run
```

Linux、手动 Docker 部署和高级参数请查看 [部署文档](DOCKER_DEPLOY.md)。

## 开始使用

部署完成后打开：

- WebUI：http://localhost:8000
- PaddleOCR-VL：http://localhost:8081/health
- PP-OCRv6：http://localhost:8082/health
- Unlimited-OCR：http://localhost:8083/health
- OvisOCR2：http://localhost:8084/health
- HPD-Parsing：http://localhost:8085/health

健康检查地址只会在对应模型运行时可用。单 GPU 环境默认只加载当前选择的模型，切换模型时会自动停止其他模型，避免同时占用显存。

## 主要功能

- 图片、PDF、PPT/PPTX、DOC/DOCX 解析
- 五模型自由选择和按需部署
- PDF 逐页解析、进度显示和历史任务保存
- Markdown、表格、公式和图片区域展示
- 原文件与解析结果左右对照
- Markdown、JSON 和图片资源下载
- 中文、英文界面切换

## 更多文档

- [快速开始](QUICKSTART.md)
- [OvisOCR2 部署与参数](OVISOCR2_DEPLOY.md)
- [Docker 手动部署](DOCKER_DEPLOY.md)
- [API 说明](api.md)
- [安全策略](SECURITY.md)

Docker 部署已将 WebUI、Docker 模型控制和 LibreOffice 转换拆分隔离。局域网或公网部署前，请务必设置 `PANDOCR_API_TOKEN`，同时把 `PANDOCR_MODEL_CONTROLLER_TOKEN` 改成随机长值。

项目地址：[https://github.com/CHEN010325/paddleocr-local](https://github.com/CHEN010325/paddleocr-local)
