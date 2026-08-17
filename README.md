# PaddleOCR Local

**一套界面，本地运行五种 OCR / 文档解析模型。** 上传图片、PDF、Word 或 PowerPoint，在浏览器中查看原文与结构化结果，并导出 Markdown、JSON 和图片资源。

[English](README.en.md) · [快速开始](QUICKSTART.md) · [CLI](CLI.md) · [硬件兼容表](docs/compatibility.md) · [路线图](ROADMAP.md) · [参与贡献](CONTRIBUTING.md)

![CI](https://github.com/CHEN010325/paddleocr-local/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/github/license/CHEN010325/paddleocr-local)
![Release](https://img.shields.io/github/v/release/CHEN010325/paddleocr-local?include_prereleases)
![Stars](https://img.shields.io/github/stars/CHEN010325/paddleocr-local?style=flat)

<img width="1920" height="945" alt="PaddleOCR Local WebUI" src="https://github.com/user-attachments/assets/85a247a0-c796-4a20-b596-1cc4148df964" />

## 为什么用它

- **完全本地**：文档、解析结果和历史任务保存在自己的机器上。
- **五模型统一入口**：PaddleOCR-VL 1.6、PP-OCRv6、Unlimited-OCR、OvisOCR2、HPD-Parsing。
- **照顾单卡机器**：只启动当前模型，切换时自动释放其他模型占用的显存。
- **跨平台部署**：Windows / Linux NVIDIA Docker，macOS Apple Silicon 原生与 MLX 路径。
- **不只是文本框**：逐页进度、原文对照、表格与公式渲染、任务恢复、结果下载。
- **面向真实部署**：模型显存预检、隔离的模型控制器和 Office 转换器、API Token、依赖锁定与高覆盖率测试。

## 支持的模型

| 模型 | 适合任务 | 推荐硬件 | 特点 |
| --- | --- | --- | --- |
| PaddleOCR-VL 1.6 | 复杂版面、表格、公式 | NVIDIA 12 GB+ | PaddleOCR 文档解析主线 |
| PP-OCRv6 | 普通文字识别、低显存场景 | NVIDIA 4 GB+ | 启动快、资源占用低；CPU Lite 在路线图中 |
| Unlimited-OCR | 长文档、结构化 Markdown | NVIDIA 8 GB+ | 支持 Transformers / SGLang |
| OvisOCR2 | 文档理解、Apple Silicon | NVIDIA 8 GB+ / Apple Silicon | macOS 默认使用 MLX |
| HPD-Parsing | 高质量文档解析 | NVIDIA 8 GB+ | 官方定制 vLLM 运行时 |

数值是项目的启动兼容性参考，不代表所有文档都能在该下限稳定运行。请查看[完整硬件兼容表](docs/compatibility.md)。

## 三分钟开始

### Windows + NVIDIA

提前安装 NVIDIA 驱动和支持 GPU 的 Docker Desktop，然后运行：

```powershell
.\windows-one-click.bat
```

### macOS Apple Silicon

支持 Apple M1、M2、M3、M4，OvisOCR2 默认使用 MLX：

```bash
./macos-one-click.command
```

### Linux / Docker

```bash
cp env.docker env.txt
./build.sh
./deploy.sh
```

部署完成后打开 <http://localhost:8000>。五个逻辑模型分别受 Compose profile 保护；部署脚本只创建未运行的待机容器，由控制器启动一个选定模型。切换时必须先完整停止旧模型并释放显存，确认后才启动新模型，因此任意时刻显存只驻留当前选择的一个逻辑模型；绝不会通过裸 `docker compose up` 同时加载多个模型。首次运行需要下载镜像或模型，耗时取决于网络和所选模型。高级配置请查看 [Docker 部署文档](DOCKER_DEPLOY.md)。

## 主要能力

- 批量上传图片、PDF、PPT/PPTX、DOC/DOCX
- PDF 按页或按批解析，显示进度并从中断处恢复
- 五模型按需部署、显存预检和运行时切换
- Markdown、表格、公式、代码和图片区域渲染
- 原文件与解析结果左右对照、滚动同步
- 历史任务搜索和本地持久化
- Markdown、JSON 与图片资源下载
- 中文 / 英文界面
- FastAPI 接口与 OpenAPI 描述
- CLI、目录批处理、Watch Folder 和多模型对比报告

## 你应该选择哪个模型

- **没有 NVIDIA 显卡**：Apple Silicon 可选 OvisOCR2 MLX；Windows/Linux 纯 CPU 一键路径仍在路线图中。
- **显存只有 8 GB**：优先 PP-OCRv6、OvisOCR2 或 HPD-Parsing 的低显存配置。
- **复杂论文和公式**：优先尝试 PaddleOCR-VL、OvisOCR2、HPD-Parsing。
- **需要确认最佳结果**：使用多模型对比工作流，并把可复现结果提交到 [Benchmark](docs/benchmark.md)。

## 项目质量

- Python：237 项测试，覆盖率门禁 95%+
- 前端：47 项测试，语句和分支覆盖率门禁 95%+
- npm 与全部 Python 依赖清单进行漏洞扫描
- Docker 镜像、Actions 和模型 revision 固定版本
- Web、Docker 控制器和 LibreOffice 转换器隔离运行

详细变化见 [CHANGELOG](CHANGELOG.md)。

## 社区与支持

- 遇到问题：先运行诊断，再按 [Support](SUPPORT.md) 提交日志。
- 想增加模型：查看[模型适配贡献说明](CONTRIBUTING.md#新增模型适配器)。
- 想报告硬件表现：按[兼容性模板](docs/compatibility.md#提交兼容性结果)提交。
- 想参与简单任务：在 Issues 中筛选 `good first issue`。

欢迎 Star、Fork、提交 Issue 和 Pull Request。项目采用 [Apache-2.0](LICENSE) 许可证。

> PaddleOCR Local 是社区项目，并非 PaddlePaddle 官方产品。PaddleOCR 及其他模型名称归各自权利人所有。
