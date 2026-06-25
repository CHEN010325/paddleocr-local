# PaddleOCR Local - PaddleOCR-VL, PP-OCRv6 & Unlimited-OCR WebUI

**语言 / Language**: 简体中文 | [English](README.en.md)

PaddleOCR Local 是一个面向 PaddleOCR-VL、PP-OCRv6 和可选 Unlimited-OCR 的轻量 Web 前端。前端负责文件上传、队列、预览、模型切换和下载，后端 FastAPI 做静态文件服务、Office 转 PDF 和请求代理；OCR 推理由独立模型服务完成，NVIDIA 路线使用 Docker Compose 管理 PaddleOCR 与 Unlimited-OCR 服务，macOS Apple Silicon 路线使用本地 PaddleX/MLX 服务。

<img width="1920" height="945" alt="image" src="https://github.com/user-attachments/assets/85a247a0-c796-4a20-b596-1cc4148df964" />

## 一键部署

这个项目的默认目标是开源自部署：新用户尽量只运行一条命令，脚本自动检查环境、安装依赖、启动服务并打开 WebUI。

macOS Apple Silicon：

```bash
./macos-one-click.command
```

Windows + NVIDIA：

```powershell
.\windows-one-click.bat
```

部署前或失败后可先跑诊断：

```bash
make doctor
```

macOS 会走本地 PaddlePaddle + PaddleX + 可选 MLX-VLM；Windows/NVIDIA 会走 Docker Compose。默认只绑定本机地址，保持本地即开即用。

## 当前架构

```text
Browser
  -> pandocr-web:8000
       - FastAPI
       - static WebUI
       - Office to PDF conversion
       - PaddleOCR-VL request proxy
       - PP-OCRv6 OCR request proxy
       - optional Unlimited-OCR request/stream proxy
  -> PaddleOCR services
       - NVIDIA: docker compose 中的 paddleocr-vl-api + paddleocr-ocr-api + paddleocr-vlm-server
       - NVIDIA optional: unlimited-ocr-api + unlimited-ocr-sglang
       - macOS: 本地 paddlex --serve，可选 mlx_vlm.server
```

NVIDIA Compose 默认保留 4 个核心服务；启用 `unlimited-ocr` profile 后会额外创建 Unlimited-OCR 实验服务：

- `pandocr-web`
- `paddleocr-vl-api`
- `paddleocr-ocr-api`
- `paddleocr-vlm-server`
- `unlimited-ocr-api`，可选实验服务
- `unlimited-ocr-sglang`，仅 SGLang 后端需要

单 GPU Docker 部署默认只热加载一个模型。`pandocr-web` 常驻运行，并通过 Docker socket 按需启停模型容器：选择 `PaddleOCR-VL 1.6` 会启动 `paddleocr-vlm-server` + `paddleocr-vl-api` 并停止 `paddleocr-ocr-api`；选择 `PP-OCRv6` 会反向切换；启用并选择 `Unlimited-OCR` 后会启动 `unlimited-ocr-api`，并按 UI 里选择的 backend 决定是否启动 `unlimited-ocr-sglang`。顶部 UI 会实时轮询显示模型就绪、启动中、待启动或失败状态。

## 功能

- 支持图片、PDF、PPT/PPTX、DOC/DOCX 上传。
- 支持在 `PaddleOCR-VL 1.6` 文档解析、`PP-OCRv6` 文字识别和可选 `Unlimited-OCR` 长文档解析之间自由切换；Docker 单 GPU 部署会按需启停模型，避免多个模型同时占用显存。
- WebUI 支持中文/英文一键切换并记住用户选择，翻译集中维护在 `static/i18n.js`，便于后续扩展更多语言。
- PDF 按页发送给 PaddleOCR-VL，便于对齐官方在线解析结果并稳定保留每页原始 JSON。
- PP-OCRv6 结果使用接近官方的可视化文字层展示：左右页面对齐，上下/左右滚动和缩放同步，识别文字支持复制和纠正，同时保留原始 JSON。
- 可选接入 `Unlimited-OCR`，支持 Transformers / SGLang 后端切换、流式输出、左右同步滚动和 `<|det|>image/chart` 图片块回填。
- 解析任务会持久化到本机 `data/tasks/`，刷新页面后仍可查看历史任务，删除按钮会同步删除本地记录。
- Markdown 预览支持表格横向滚动、KaTeX 数学公式渲染、OCR 结果中的字面量 `\n` 换行修正。
- 支持解析选项：版面检测、图表识别、文档矫正、方向识别、印章识别、公式编号、Markdown 忽略标签等。
- 下载结果时会打包 Markdown 和 OCR 提取图片。

### 可选实验模型：Unlimited-OCR

项目提供了 `Unlimited-OCR` 的第三模型接入路径，适合研究/评测长文档一次性解析能力。NVIDIA Docker 路线保持可选启用；macOS Apple Silicon 一键部署会默认启用隔离的 Unlimited-OCR Transformers/MPS 服务，并作为模型下拉框里的 `Unlimited-OCR` 选项出现。

启用 NVIDIA Docker 版本：

```powershell
# 1. 在 env.txt 或 env.docker 中开启
PANDOCR_ENABLE_UNLIMITED_OCR=1

# 2. 创建可选 profile 下的 Unlimited-OCR 容器
# 只使用 Transformers 时可以只 build unlimited-ocr-api；需要 UI 切 SGLang 时一起 build unlimited-ocr-sglang
docker compose --env-file env.txt --profile unlimited-ocr build unlimited-ocr-api unlimited-ocr-sglang
docker compose --env-file env.txt --profile unlimited-ocr up -d --no-start

# 3. 启动 WebUI，之后可在右上角模型选择器切换到 Unlimited-OCR
docker compose --env-file env.txt start pandocr-web
```

当前默认 `UNLIMITED_OCR_BACKEND=transformers`，对个人电脑更友好，也对齐官方 Transformers `model.infer` / `model.infer_multi` 路径。NVIDIA Docker 版本选择 `Unlimited-OCR` 后，顶部会出现只针对该模型的 Backend 下拉框，可直接在 `Transformers` 和 `SGLang` 间切换；macOS 版本只暴露 `Transformers`，因为 SGLang/FlashAttention 路径依赖 CUDA。上次选择会写入 `data/runtime-settings.json`，下次启动继续使用同一 backend。这个运行态文件已被 Git 忽略，不会污染提交。

Backend 选择建议：

| Backend | 是否优先尝试 | 更适合的机器/场景 | 代价和风险 |
| --- | --- | --- | --- |
| `Transformers` | 是，建议先用它验证 | 个人 NVIDIA 电脑、Windows Docker、RTX 30/40/50，以及希望少踩 kernel/runtime 环境坑的用户。 | 冷启动较慢，因为 Python 进程需要把模型加载进显存；吞吐/并发不是重点，但部署路径最可预期。 |
| `SGLang` | Transformers 跑通后再试 | 常驻 NVIDIA 服务器、空闲显存更充足、希望对比 OpenAI-compatible streaming server 或吞吐能力的用户。 | 更挑环境：依赖官方定制 SGLang wheel、`kernels`、单独的 `unlimited-ocr-sglang` 服务、自定义 logit processor，以及必须和 GPU/CUDA 匹配的 attention backend。 |

大多数用户建议先部署 Transformers：

```powershell
.\windows-one-click.bat -Models unlimited-ocr -UnlimitedOcrBackend transformers
```

确认 Transformers 可用后，再尝试 SGLang：

```powershell
.\windows-one-click.bat -Models unlimited-ocr -UnlimitedOcrBackend sglang
```

也可以后续直接从 WebUI 部署 SGLang：选择 `Unlimited-OCR`，在提示里输入 `sglang`，WebUI 会自动构建/创建缺失的 `unlimited-ocr-sglang` 容器。如果 SGLang 构建失败、启动卡住或健康检查不通过，直接在 Backend 下拉框切回 `Transformers`。

SGLang 环境调参建议：

- 本项目默认 `UNLIMITED_OCR_ATTENTION_BACKEND=flashinfer`，通常比官方示例里的 `fa3` 更适合消费级显卡和 RTX 50 / SM120 这类本地环境。
- 官方 SGLang 示例使用 `--attention-backend fa3`。只有当你的 SGLang wheel、CUDA 版本和 GPU 架构都支持时再改成 `fa3`；否则保持 `flashinfer`。
- 如果 SGLang 显存不足或长时间启动中，先关闭占显存程序、保持 PDF 每批页数为 `1`，或把 `UNLIMITED_OCR_MEM_FRACTION_STATIC` 从 `0.8` 降到 `0.7`；仍不稳定就回到 `Transformers`。
- 如果 SGLang 报 context length 超限，先用单页请求验证。多页 one-shot 更适合研究压测，对上下文长度、显存和页序对齐都更敏感。

Unlimited-OCR 走独立 `unlimited-ocr-api` 容器，`pandocr-web` 只代理 `/api/unlimited-ocr` 和 `/api/unlimited-ocr/stream`，不会把 Unlimited-OCR 的重依赖安装进 WebUI 容器。PDF 会先在 adapter 中按官方 300 DPI 转成页面图片；默认 PDF 每批页数为 1，单页走 `gundam + ngram_window=128`，`no_repeat_ngram_size=35`。如果手动把 PDF 每批页数调大，adapter 会按当前 batch 做多页 one-shot 请求，适合研究压测，但更容易受显存、上下文长度和页面对齐稳定性影响。最终结果会把官方 `<|det|>image/chart [bbox]<|/det|>` 块裁剪回填为 Markdown 图片。

NVIDIA Docker 首次使用 Transformers 或 SGLang 后端时都会下载并加载 `baidu/Unlimited-OCR` 权重；权重缓存持久化在 `model_cache_unlimited_ocr/`，容器重启后不会重新下载，但新 Python 进程仍需要把权重重新加载到显存。默认 `UNLIMITED_OCR_PRELOAD=1` 会在 `unlimited-ocr-api` 容器启动后后台预热 Transformers 模型，WebUI 会等 `modelLoaded=true` 后才显示 Transformers 就绪；切到 SGLang 时会卸载 Transformers 权重并启动 `unlimited-ocr-sglang`。本机默认 `UNLIMITED_OCR_ATTENTION_BACKEND=flashinfer`，如果换到官方 `fa3` 支持的卡，可以改回 `fa3` 再重建/重启 SGLang 容器。

macOS Apple Silicon 版本使用独立虚拟环境 `.venv-unlimited-ocr-macos`，不会把 PyTorch/Transformers 依赖安装进 `.venv-macos`。默认模型为 MPS 兼容的 `sabafallah/Unlimited-OCR-Universal`，默认缓存目录为 `model_cache_unlimited_ocr_macos/`，默认 `UNLIMITED_OCR_PRELOAD=0` 以避免打开 WebUI 时强制下载 6GB+ 权重；第一次真正使用 Unlimited-OCR 解析时会下载并加载模型。Mac 一键启动默认使用 `UNLIMITED_OCR_PDF_DPI=180`、`UNLIMITED_OCR_MAX_TOKENS=4096`、`UNLIMITED_OCR_HF_HUB_DOWNLOAD_TIMEOUT=120` 和 `UNLIMITED_OCR_HF_HUB_ETAG_TIMEOUT=30`，以降低 Apple Silicon MPS 内存压力并减少首次 Hugging Face 大文件下载被短超时打断的概率；如需更高清、更长输出或更长下载超时，可用环境变量覆盖。若要恢复旧的两模型 Mac 启动方式，可运行：

```bash
PANDOCR_ENABLE_UNLIMITED_OCR=0 ./macos-one-click.command
```

### 本机 TITAN V / CUDA 12.6 部署记录

这次在 Windows + NVIDIA TITAN V 12GB、NVIDIA Driver `560.94`、Docker Desktop `28.3.0`、Docker Compose `v2.38.1` 上部署 `Unlimited-OCR` 时，官方 CUDA 12.9 路线会在容器启动前被 NVIDIA runtime 拦截：

```text
nvidia-container-cli: requirement error: unsatisfied condition: cuda>=12.9,
please update your driver to a newer version, or use an earlier cuda container
```

原因是宿主机驱动当前只暴露 CUDA `12.6`，而原始 `Dockerfile.unlimited-ocr` 使用 `nvidia/cuda:12.9.1-cudnn-devel-ubuntu24.04`。为了不强制升级驱动，这里把 `Unlimited-OCR` 的 Transformers 镜像改成 CUDA 12.6 兼容构建：

```dockerfile
ARG CUDA_BASE_IMAGE=nvidia/cuda:12.6.3-cudnn-devel-ubuntu24.04
FROM ${CUDA_BASE_IMAGE}

RUN python -m venv /opt/venv && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
      --index-url https://download.pytorch.org/whl/cu126 \
      torch==2.10.0+cu126 \
      torchvision==0.25.0+cu126 && \
    pip install --no-cache-dir \
      transformers==4.57.1 \
      matplotlib==3.10.8 \
      einops==0.8.2 \
      addict==2.4.0 \
      easydict==1.13 \
      psutil==7.2.2 \
      pymupdf==1.27.2.2 \
      fastapi==0.136.1 \
      uvicorn==0.46.0 \
      httpx==0.28.1 \
      pydantic==2.11.10 \
      python-multipart==0.0.27 \
      Pillow==12.1.1
```

这只改了 `Dockerfile.unlimited-ocr`：保留官方 Unlimited-OCR 其余 Python 依赖版本，单独把 CUDA 基础镜像和 PyTorch wheel 切到 `cu126`。如果后续升级到支持 CUDA 12.9 的 NVIDIA 驱动，也可以把基础镜像和 PyTorch wheel 源改回官方测试组合后重建。

本机使用的部署命令：

```powershell
.\windows-one-click.bat -EnvFile env.docker -Models unlimited-ocr -UnlimitedOcrBackend transformers -NoOpen
```

若一键脚本在等待阶段因为未部署的 PaddleOCR-VL 容器提示 `No such object: paddleocr-vlm-server`，先看真实容器状态；本次实际 `pandocr-web` 和 `unlimited-ocr-api` 已经创建并健康，可直接用同一个临时 env 文件检查和启动：

```powershell
docker compose --env-file tmp\windows-one-click.env ps -a
docker compose --env-file tmp\windows-one-click.env start pandocr-web unlimited-ocr-api
curl http://localhost:8000/api/model-runtime
curl http://localhost:8083/health
```

模型预热完成的标志是 `/health` 中 `modelLoaded=true`、`modelLoading=false`，WebUI 的 `/api/model-runtime` 中 `unlimited-ocr.state=ready`。本次预热完成后显存占用约 `8.3GB / 12GB`。端到端 smoke test 可以用一张小图请求 `/api/unlimited-ocr`，返回 `# HELLO OCR 123` 即说明 WebUI 代理、adapter、Transformers 模型和 GPU 推理都已经打通。

## 部署方式

本项目支持两条部署路径，二者互不混用：

- **NVIDIA Docker 版本**：适合带 NVIDIA GPU 的 Linux/Windows Docker 环境，继续使用官方 PaddleOCR-VL Docker 服务。
- **macOS Apple Silicon 版本**：适合 Apple M1/M2/M3/M4 芯片，按官方 Apple Silicon 文档走本地 PaddlePaddle + PaddleX serving，可选 MLX-VLM 提速。

### 版本一：NVIDIA Docker

Windows + NVIDIA 用户推荐直接使用一键部署脚本：

```powershell
.\windows-one-click.bat
```

它会自动检查 Docker、识别 NVIDIA GPU、选择 `env.txt` 或 `env.docker`，询问当前要部署的模型，只拉取/构建选中的模型服务和 `pandocr-web`，然后启动 WebUI 并等待当前活跃模型健康检查。未部署的模型仍会显示在 WebUI 中，后续可在页面上触发下载/构建和容器创建。失败时会自动打印相关模型服务和 `pandocr-web` 的关键日志。

常用一键部署参数：

```powershell
.\windows-one-click.bat -DryRun
.\windows-one-click.bat -GpuId 1
.\windows-one-click.bat -EnvFile env.docker
.\windows-one-click.bat -Models paddleocr-vl-1.6
.\windows-one-click.bat -Models pp-ocrv6
.\windows-one-click.bat -Models unlimited-ocr -UnlimitedOcrBackend transformers
.\windows-one-click.bat -Models unlimited-ocr -UnlimitedOcrBackend sglang
.\windows-one-click.bat -Models all
```

Windows 一键部署会先让用户选择当前要部署的模型，只拉取/构建选中的模型服务和 `pandocr-web`。未部署的模型仍会显示在 WebUI 的模型下拉框中，状态为“未部署”；用户后续在 UI 中选择该模型时，可以直接触发下载/构建并创建对应容器，不需要回到命令行操作。

也可以继续使用手动部署流程：

先根据显卡型号选择环境文件：

| 显卡 | 推荐环境文件 | 镜像标签 |
| --- | --- | --- |
| RTX 30 系列 | `env.docker` | `latest-nvidia-gpu-offline` |
| RTX 40 系列 | `env.docker` | `latest-nvidia-gpu-offline` |
| RTX 50 系列 / Blackwell | `env.txt` | `latest-nvidia-gpu-sm120-offline` |

下面命令以 RTX 50 系列的 `env.txt` 为例；RTX 30/40 系列用户把命令里的 `env.txt` 换成 `env.docker` 即可。

```powershell
docker compose --env-file env.txt pull paddleocr-vlm-server paddleocr-vl-api
docker compose --env-file env.txt build paddleocr-ocr-api pandocr-web
docker compose --env-file env.txt up -d --no-start
docker compose --env-file env.txt start pandocr-web
```

单 GPU 部署请保持这个 `up -d --no-start` 再 `start pandocr-web` 的顺序。不要直接执行普通的 `docker compose up -d`，否则可能同时热加载多个模型服务，造成显存被抢占。WebUI 打开后，通过右上角模型选择器切换模型即可；前端会调用 `/api/model-runtime/switch`，只启动当前选择的模型容器，停止非活跃模型容器，并让顶部运行状态实时同步真实容器状态。

访问：

- WebUI: http://localhost:8000
- PaddleOCR-VL API health: http://localhost:8081/health，仅在 `PaddleOCR-VL 1.6` 为活跃模型时可用。
- PP-OCRv6 API health: http://localhost:8082/health，仅在 `PP-OCRv6` 为活跃模型时可用。

Compose 默认只把 WebUI 和 OCR API 绑定到 `127.0.0.1`，避免局域网内未授权访问。`pandocr-web` 会挂载 `/var/run/docker.sock` 来启停本 compose 文件里的模型容器，这等同于具备 Docker 主机管理权限；不要在没有额外访问控制的情况下把 WebUI 暴露给不可信网络。

查看状态：

```powershell
docker compose --env-file env.txt ps
curl http://localhost:8000/api/model-runtime
curl http://localhost:8000/api/models
```

常用环境变量：

`env.txt` 是当前 RTX 50 / Blackwell 推荐配置：

```text
API_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
VLM_BACKEND=vllm
VLM_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
PANDOCR_GPU_DEVICE_ID=0
PADDLEOCR_VL_MODEL_NAME=PaddleOCR-VL-1.6-0.9B
PPOCR_V6_MODEL_NAME=PP-OCRv6_medium
UNLIMITED_OCR_MODEL_NAME=baidu/Unlimited-OCR
UNLIMITED_OCR_BACKEND=transformers
UNLIMITED_OCR_PRELOAD=1
UNLIMITED_OCR_SGLANG_PORT=10001
PANDOCR_ENABLE_UNLIMITED_OCR=0
PANDOCR_MODEL_CONTROL=docker
PANDOCR_ACTIVE_MODEL_ON_START=paddleocr-vl-1.6
PANDOCR_MODEL_SWITCH_TIMEOUT=1200
PADDLE_REQUEST_TIMEOUT=3600
PANDOCR_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
PANDOCR_MAX_UPLOAD_MB=512
PANDOCR_MAX_CONCURRENT_OCR=1
PANDOCR_ENFORCE_ORIGIN_CHECK=1
PANDOCR_API_TOKEN=
PANDOCR_ENABLE_API_DOCS=0
```

RTX 30/40 系列等非 Blackwell NVIDIA GPU 使用 `env.docker`，其中两个镜像标签都是 `latest-nvidia-gpu-offline`。

`PANDOCR_API_TOKEN` 为空时保持本机即开即用；如果要把 WebUI 暴露给反向代理、局域网或多人环境，请设置一个长随机 token。`PANDOCR_ENFORCE_ORIGIN_CHECK=1` 会拒绝未加入来源白名单的跨站 API 写请求，但它不能替代 token。前端会在 API 返回 401 时提示输入，并保存在浏览器本地。`PANDOCR_ENABLE_API_DOCS=1` 时才启用 `/docs` 和 `/redoc`，OpenAPI JSON 固定在 `/api/openapi.json`。

常用命令：

```powershell
docker compose --env-file env.txt ps
docker compose --env-file env.txt logs -f pandocr-web
docker compose --env-file env.txt restart pandocr-web
docker compose --env-file env.txt down
```

### 版本二：macOS Apple Silicon

macOS Apple Silicon 按官方 PaddleOCR-VL 文档走手动部署，不使用 NVIDIA Docker Compose 镜像。官方依据：

- PaddleOCR-VL Apple Silicon Usage Tutorial: https://www.paddleocr.ai/main/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.html
- PaddleOCR-VL Usage Tutorial: https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html
- PaddleX Serving Guide: https://paddlepaddle.github.io/PaddleX/3.3/en/pipeline_deploy/serving.html

支持芯片：

- Apple M1 / M2 / M3 / M4 系列芯片（arm64）
- 本项目脚本会检查 `Darwin + arm64`，因此 M1-M4 统一走同一套 Mac 本地部署路径。
- 官方 PaddleOCR-VL Apple Silicon 文档目前说明已在 Apple M4 上完成精度验证；M1/M2/M3 可按同一路径运行，实际速度和稳定性受芯片型号、内存、系统版本和模型缓存状态影响。

Mac 默认启动官方 PaddleX 产线名：

```text
PaddleOCR-VL-1.6
```

不要使用裸 `PaddleOCR-VL` 作为 Mac 默认产线名；在当前 PaddleX 3.6.x 中，裸名对应旧版 v1 配置。`PaddleOCR-VL-1.6` 会使用 `PP-DocLayoutV3`、`PaddleOCR-VL-1.6-0.9B` 和 native PaddlePaddle 后端。

一键部署（推荐）：

```bash
./macos-one-click.command
```

也可以使用等价的 Make 命令：

```bash
make mac-one-click
```

这条一键命令会自动检查 Apple Silicon 环境、安装 macOS 依赖、默认启用 MLX-VLM 提速模式、准备隔离的 Unlimited-OCR MPS 环境、启动 `mlx_vlm.server` / PaddleX API / PP-OCRv6 API / Unlimited-OCR adapter / PaddleOCR Local WebUI、执行健康检查，并自动打开 http://127.0.0.1:8000。

首次启动会下载 `PP-DocLayoutV3`、`PaddleOCR-VL-1.6-0.9B` 和 MLX 模型权重，耗时取决于网络和磁盘速度。Unlimited-OCR 默认不在启动时预热，第一次使用时会下载 `sabafallah/Unlimited-OCR-Universal` 权重并在 MPS 上加载；Mac 默认以 `180 DPI / 4096 max tokens` 解析 PDF，适合本地 MPS 稳定运行。模型缓存完成后，后续再次运行同一条命令会复用已安装环境和已启动服务。

高级手动启动：

```bash
make mac-setup
make mac-up
```

手动启用 Mac Unlimited-OCR：

```bash
make mac-setup-unlimited-ocr
make mac-up-unlimited-ocr
make mac-test-unlimited-ocr
```

完成后访问：

- WebUI: http://127.0.0.1:8000
- PaddleOCR-VL API health: http://127.0.0.1:8081/health

测试、停止和查看日志：

```bash
make mac-test
make mac-down
make mac-logs
```

如果 native 模式太慢，可安装并启用官方 Apple Silicon 文档中的 MLX-VLM 路线：

```bash
make mac-setup-mlx
make mac-down
make mac-up-mlx
make mac-test-mlx
```

MLX 模式会启动三个本地服务：

- `mlx_vlm.server`: `127.0.0.1:8111`
- PaddleX 完整解析 API: `127.0.0.1:8081`
- Unlimited-OCR adapter: `127.0.0.1:8083`，启用 `PANDOCR_ENABLE_UNLIMITED_OCR=1` 时启动。
- PaddleOCR Local WebUI: `127.0.0.1:8000`

若 Hugging Face 下载较慢，可以设置 `HF_TOKEN` 提高 Hugging Face 的限流额度；模型缓存完成后后续启动会快很多。如果要改 MLX 端口，直接设置 `MLX_PORT` 即可；启动脚本会从模板生成 PaddleX 使用的配置。

常用环境变量：

```bash
PANDOCR_HOST=127.0.0.1
PANDOCR_PORT=8000
PADDLEX_HOST=127.0.0.1
PADDLEX_PORT=8081
PADDLEX_PIPELINE=PaddleOCR-VL-1.6
PANDOCR_MACOS_BACKEND=mlx
MLX_HOST=127.0.0.1
MLX_PORT=8111
MLX_MODEL=PaddlePaddle/PaddleOCR-VL-1.6
PADDLEPADDLE_VERSION=3.3.0
STARTUP_TIMEOUT_SECONDS=900
```

端口被占用时，例如把 WebUI 改到 `18000`：

```bash
PANDOCR_PORT=18000 make mac-up
```

本机实测参考（缓存模型后，不包含首次下载和冷启动）：

| 项目 | 结果 |
| --- | --- |
| 设备 | MacBook Pro, Apple M4 Pro, 12 核 CPU（8P+4E）, 24GB 内存 |
| 系统 | macOS 26.5.1, arm64 |
| 环境 | Python 3.12.13, PaddlePaddle 3.3.0, PaddleOCR 3.7.0, PaddleX 3.7.1, mlx-vlm 0.6.3 |
| 启动模式 | `make mac-up-mlx` |
| 测试输入 | 17KB PNG 小图，经 WebUI 后端 `/api/paddleocr-vl-1.6` 端到端请求 |
| 5 次耗时 | 1.73s / 1.74s / 1.75s / 1.76s / 1.78s |
| 平均耗时 | 约 1.75s |

复杂 PDF、表格/公式密集页面、大图和 native 模式会明显更慢。首次运行还需要下载 `PP-DocLayoutV3`、`PaddleOCR-VL-1.6-0.9B` 和 MLX 模型权重，耗时主要取决于网络和磁盘速度。

## 主要接口

- `GET /`：WebUI 首页。
- `GET /api/models`：返回可用模型和对应代理入口。
- `GET /api/model-runtime`：返回当前活跃模型、就绪状态、容器状态和切换任务。
- `POST /api/model-runtime/switch`：Docker 模式下启动目标模型容器并停止非活跃模型容器。
- `POST /api/model-runtime/deploy`：从 WebUI 触发缺失模型的镜像拉取/构建和容器创建，完成后切换到目标模型。
- `GET /api/tasks`：读取本机持久化任务摘要列表，不返回大体积源文件和 OCR 结果。
- `GET /api/tasks/{task_id}`：读取一个任务的完整详情。
- `PUT /api/tasks/{task_id}`：保存一个任务到 `data/tasks/`；`task.json` 只保存轻量元数据，Markdown、OCR JSON、图片和 batch Markdown 拆到 `result.json`，摘要拆到 `summary.json`。
- `DELETE /api/tasks/{task_id}`：删除一个本地任务。
- `DELETE /api/tasks`：清空本地任务历史，只删除合法 task id 子目录，避免误删任务目录外文件。
- `POST /api/convert/to-pdf`：将 PPT/PPTX/DOC/DOCX 转为 PDF。
- `POST /api/paddleocr-vl-1.6`：代理 OCR 请求到 PaddleOCR-VL layout-parsing 服务。
- `POST /api/pp-ocrv6`：代理 OCR 请求到 PP-OCRv6 服务，返回页面图片、识别文字行、坐标框、置信度和原始 JSON。
- `POST /api/unlimited-ocr`：可选代理到 Unlimited-OCR adapter，只有 `PANDOCR_ENABLE_UNLIMITED_OCR=1` 时可用。
- `POST /api/unlimited-ocr/stream`：Unlimited-OCR 流式解析代理，响应类型为 `application/x-ndjson`。
- `GET/POST /api/unlimited-ocr/backend`：读取或切换 Unlimited-OCR 的 `Transformers` / `SGLang` backend。
- `GET /api/openapi.json`：当前 WebUI 后端的 OpenAPI JSON；仓库里的 `paddle-layout-openapi.json` 是上游 Paddle layout-parsing 服务接口。

## 项目结构

```text
.
├── server.py
├── requirements.txt
├── requirements-macos.txt
├── requirements-macos-mlx.txt
├── macos-one-click.command
├── windows-one-click.bat
├── Dockerfile
├── Dockerfile.ocr
├── Dockerfile.unlimited-ocr
├── Dockerfile.unlimited-ocr-sglang
├── docker-compose.yml
├── unlimited_ocr_adapter.py
├── data/                  # 本地任务数据目录，默认不提交
├── env.txt
├── env.docker
├── pipeline_config_ocr_v6.yaml
├── pipeline_config_vllm.yaml
├── pipeline_config_macos_mlx.template.yaml
├── scripts/               # 部署辅助脚本
│   ├── windows-one-click.ps1
├── static/
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── vendor/katex/
├── QUICKSTART.md
├── webui-openapi.json
├── paddle-layout-openapi.json
├── DOCKER_DEPLOY.md
└── PROJECT_SUMMARY.md
```

## 本地开发

本地在 Docker 外运行 `server.py` 时，请设置 `PANDOCR_MODEL_CONTROL=none`，并自行启动模型服务。需要已有 PaddleOCR-VL 服务监听在 `http://localhost:8081/layout-parsing`；如需使用 PP-OCRv6，也需要启动 PaddleX OCR 服务监听在 `http://localhost:8082/ocr`，或设置 `PADDLE_OCR_SERVICE_URL`。

```powershell
pip install -r requirements.txt
python server.py
```

然后打开 http://localhost:8000。

本地质量检查：

```bash
make check
```
