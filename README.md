# PaddleOCR Local

**把 PDF、图片和 Office 文档变成可编辑 Markdown 的本地文档工作台。** 一套界面运行五种 OCR / 文档解析模型，结果留在自己的机器上，并支持批处理、CLI、Apple Silicon 以及可编辑、可搜索导出。

Windows 一键部署与 Linux/Docker 默认显示五个模型，按需部署和切换，只运行当前选中的模型。已有独立模型服务的用户，可选用[外部 API 模式](docs/external-engines.md)：该模式只显示自行配置的服务；远程服务会接收待识别内容。

[English](README.en.md) · [快速开始](QUICKSTART.md) · [CLI](CLI.md) · [硬件兼容表](docs/compatibility.md) · [路线图](ROADMAP.md) · [参与贡献](CONTRIBUTING.md)

![CI](https://github.com/CHEN010325/paddleocr-local/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/github/license/CHEN010325/paddleocr-local)
![Release](https://img.shields.io/github/v/release/CHEN010325/paddleocr-local?include_prereleases)
![Stars](https://img.shields.io/github/stars/CHEN010325/paddleocr-local?style=flat)

<img width="1920" height="945" alt="PaddleOCR Local WebUI" src="https://github.com/user-attachments/assets/85a247a0-c796-4a20-b596-1cc4148df964" />

## 为什么用它

- **完全本地**：文档、解析结果和历史任务保存在自己的机器上。
- **五模型统一入口**：PaddleOCR-VL 1.6、PP-OCRv6、OvisOCR2、HPD-Parsing、NaviDC-OCR。Unlimited-OCR 默认不启动；Windows/Linux Docker 默认目录不展示，macOS 可按需选择实验适配器。
- **照顾单卡机器**：只启动当前模型，切换时自动释放其他模型占用的显存。
- **跨平台部署**：Windows / Linux NVIDIA Docker，macOS Apple Silicon 原生与 MLX 路径。
- **不只是文本框**：逐页进度、原文对照、表格与公式渲染、任务恢复、可编辑与可搜索导出。
- **面向真实部署**：模型显存预检、隔离的模型控制器和 Office 转换器、API Token、依赖锁定与高覆盖率测试。

## 适合谁

| 你的需求 | 推荐入口 |
| --- | --- |
| 私密文档不想上传云端 | 本地 WebUI，任务和结果保存在本机 |
| 单卡显存有限 | PP-OCRv6 / OvisOCR2，并按需切换模型 |
| 论文、表格、公式解析 | PaddleOCR-VL 1.6 或 HPD-Parsing |
| 批量自动化 | CLI、目录批处理或 Watch Folder |
| Apple Silicon | macOS 原生路径或 OvisOCR2 MLX |

## 支持的模型

| 模型 | 适合任务 | 推荐硬件 | 特点 |
| --- | --- | --- | --- |
| PaddleOCR-VL 1.6 | 复杂版面、表格、公式 | NVIDIA 12 GB+ | PaddleOCR 文档解析主线 |
| PP-OCRv6 | 普通文字识别、低显存场景 | CPU / NVIDIA 4 GB+ | 提供独立无 GPU 部署；见 [CPU 入口](docs/cpu.md) |
| OvisOCR2 | 文档理解、Apple Silicon | NVIDIA 8 GB+ / Apple Silicon | macOS 默认使用 MLX |
| HPD-Parsing | 高质量文档解析 | NVIDIA 8 GB+ | 官方定制 vLLM 运行时 |
| NaviDC-OCR | 复杂文档、版面、表格与公式 | NVIDIA 8 GB+ | 官方 NaviOCRClient vLLM 异步两阶段流程（可选 Transformers 后端） |

数值是项目的启动兼容性参考，不代表所有文档都能在该下限稳定运行。请查看[完整硬件兼容表](docs/compatibility.md)。

## 三分钟开始

### Windows + NVIDIA

提前安装 NVIDIA 驱动和支持 GPU 的 Docker Desktop，然后运行：

```powershell
.\windows-one-click.bat -DryRun
.\windows-one-click.bat
```

### 无 NVIDIA 显卡（CPU）

```sh
docker compose -f docker-compose.cpu.yml up -d --build --wait --wait-timeout 1200
```

使用 PP-OCRv6 识别图片和 PDF。无需 CUDA 或 GPU 容器运行时。
首次运行下载模型，完整说明与验证范围见 [CPU 部署](docs/cpu.md)。

### macOS Apple Silicon

支持 Apple M1、M2、M3、M4，OvisOCR2 默认使用 MLX：

```bash
make doctor
./macos-one-click.command
```

### Linux / Docker

```bash
make doctor
cp env.docker env.txt
./build.sh
./deploy.sh
```

部署完成后打开 <http://localhost:8000>。五个默认逻辑模型分别受 Compose profile 保护；部署脚本只创建未运行的待机容器，由控制器启动一个选定模型。切换时必须先完整停止旧模型并释放显存，确认后才启动新模型，因此任意时刻显存只驻留当前选择的一个逻辑模型；绝不会通过裸 `docker compose up` 同时加载多个模型。首次运行需要下载镜像或模型，耗时取决于网络和所选模型。高级配置请查看 [Docker 部署文档](DOCKER_DEPLOY.md)。想先快速演示导出与恢复流程，可使用 [60 秒快速演示](docs/QUICK_DEMO.md)。

## 主要能力

- 批量上传图片、PDF、PPT/PPTX、DOC/DOCX
- PDF 按页或按批解析，显示进度并从中断处恢复
- 失败批次单独重试，不必重复解析已完成批次
- 五模型按需部署、显存预检和运行时切换
- 根据显存与模型能力给出推荐模型，并支持一键切换
- Markdown、表格、公式、代码和图片区域渲染
- 原文件与解析结果左右对照、滚动同步
- 历史任务搜索和本地持久化
- Markdown、JSON、图片资源、可编辑 DOCX、可搜索 PDF、离线单文件 HTML 与表格 CSV / XLSX 下载
- 中文 / 英文界面
- FastAPI 接口与 OpenAPI 描述
- CLI、目录批处理和 Watch Folder

## 你应该选择哪个模型

- **没有 NVIDIA 显卡**：Apple Silicon 可选 OvisOCR2 MLX；Windows/Linux 可使用 [PP-OCRv6 CPU 入口](docs/cpu.md)。
- **显存只有 8 GB**：优先采用 WebUI 显存预检给出的推荐模型；通常会推荐 PP-OCRv6，避免重模型在低显存模式下勉强启动。
- **复杂论文和公式**：优先尝试 PaddleOCR-VL、OvisOCR2、HPD-Parsing。

## 项目质量

- Python：250+ 项测试，整体及核心导出模块覆盖率门禁 95%+
- 前端：48+ 项测试，语句和分支覆盖率门禁 95%+
- npm 与全部 Python 依赖清单进行漏洞扫描
- Docker 镜像、Actions 和模型 revision 固定版本
- Web、Docker 控制器和 LibreOffice 转换器隔离运行

详细变化见 [CHANGELOG](CHANGELOG.md)。

## 为什么值得 Star

- 一个本地入口覆盖图片、PDF、Word 和 PowerPoint，而不是为每个模型维护一套命令。
- 单 GPU 严格串行切换，避免多个大模型意外同时占满显存。
- 输出可继续编辑、批量处理和接入 API，适合个人工作流与内部工具。
- 不默认上传文档或收集遥测；硬件兼容性按真实报告逐步补充。

## 社区与支持

- 遇到问题：先运行诊断，再按 [Support](SUPPORT.md) 提交日志。
- 想增加模型：查看[模型适配贡献说明](CONTRIBUTING.md#新增模型适配器)。
- 想报告硬件表现：按[兼容性模板](docs/compatibility.md#提交兼容性结果)提交。
- 想参与简单任务：在 Issues 中筛选 `good first issue`。

欢迎 Star、Fork、提交 Issue 和 Pull Request。项目采用 [Apache-2.0](LICENSE) 许可证。

> PaddleOCR Local 是社区项目，并非 PaddlePaddle 官方产品。PaddleOCR 及其他模型名称归各自权利人所有。
