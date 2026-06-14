# PandOCR - PaddleOCR-VL 1.6 WebUI

**语言 / Language**: [English](README.md) | 简体中文

PandOCR 是一个面向 PaddleOCR-VL 和 PP-OCRv6 的轻量 Web 前端。前端负责文件上传、队列、预览、模型切换和下载，后端 FastAPI 做静态文件服务、Office 转 PDF 和请求代理；OCR 推理由独立 PaddleOCR 服务完成，NVIDIA 路线使用官方 Docker 服务，macOS Apple Silicon 路线使用本地 PaddleX/MLX 服务。

## 当前架构

```text
Browser
  -> pandocr-web:8000
       - FastAPI
       - static WebUI
       - Office to PDF conversion
       - PaddleOCR-VL request proxy
       - PP-OCRv6 OCR request proxy
  -> PaddleOCR services
       - NVIDIA: docker compose 中的 paddleocr-vl-api + paddleocr-ocr-api + paddleocr-vlm-server
       - macOS: 本地 paddlex --serve，可选 mlx_vlm.server
```

NVIDIA Compose 保留 4 个服务：

- `pandocr-web`
- `paddleocr-vl-api`
- `paddleocr-ocr-api`
- `paddleocr-vlm-server`

单 GPU Docker 部署默认只热加载一个模型。`pandocr-web` 常驻运行，并通过 Docker socket 按需启停模型容器：选择 `PaddleOCR-VL 1.6` 会启动 `paddleocr-vlm-server` + `paddleocr-vl-api` 并停止 `paddleocr-ocr-api`；选择 `PP-OCRv6` 会反向切换。顶部 UI 会实时轮询显示模型就绪、启动中、待启动或失败状态。

项目不再包含 rerank/reranker 服务，也不再在 Web 容器里安装 Paddle/PaddleX。

## 功能

- 支持图片、PDF、PPT/PPTX、DOC/DOCX 上传。
- 支持在 `PaddleOCR-VL 1.6` 文档解析和 `PP-OCRv6` 文字识别之间自由切换；Docker 单 GPU 部署会按需启停模型，避免两个模型同时占用显存。
- PDF 按页发送给 PaddleOCR-VL，便于对齐官方在线解析结果并稳定保留每页原始 JSON。
- PP-OCRv6 结果使用接近官方的可视化文字层展示：左右页面对齐，上下/左右滚动和缩放同步，识别文字支持复制和纠正，同时保留原始 JSON。
- 解析任务会持久化到本机 `data/tasks/`，刷新页面后仍可查看历史任务，删除按钮会同步删除本地记录。
- Markdown 预览支持表格横向滚动、KaTeX 数学公式渲染、OCR 结果中的字面量 `\n` 换行修正。
- 支持解析选项：版面检测、图表识别、文档矫正、方向识别、印章识别、公式编号、Markdown 忽略标签等。
- 下载结果时会打包 Markdown 和 OCR 提取图片。

## 部署方式

本项目支持两条部署路径，二者互不混用：

- **NVIDIA Docker 版本**：适合带 NVIDIA GPU 的 Linux/Windows Docker 环境，继续使用官方 PaddleOCR-VL Docker 服务。
- **macOS Apple Silicon 版本**：适合 Apple M1/M2/M3/M4 芯片，按官方 Apple Silicon 文档走本地 PaddlePaddle + PaddleX serving，可选 MLX-VLM 提速。

### 版本一：NVIDIA Docker

Windows + NVIDIA 用户推荐直接使用一键部署脚本：

```powershell
.\windows-one-click.bat
```

它会自动检查 Docker、识别 NVIDIA GPU、选择 `env.txt` 或 `env.docker`、拉取官方 PaddleOCR-VL 镜像、构建 `pandocr-web`、清理旧容器、创建所有模型容器但不会同时启动两个模型，然后启动 WebUI 并等待当前活跃模型健康检查。失败时会自动打印 `paddleocr-vlm-server`、`paddleocr-vl-api`、`paddleocr-ocr-api` 和 `pandocr-web` 的关键日志。

常用一键部署参数：

```powershell
.\windows-one-click.bat -DryRun
.\windows-one-click.bat -GpuId 1
.\windows-one-click.bat -EnvFile env.docker
```

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

单 GPU 部署请保持这个 `up -d --no-start` 再 `start pandocr-web` 的顺序。不要直接执行普通的 `docker compose up -d`，否则可能同时热加载 PaddleOCR-VL 和 PP-OCRv6，造成显存被两个模型抢占。WebUI 打开后，通过右上角模型选择器切换模型即可；前端会调用 `/api/model-runtime/switch`，只启动当前选择的模型容器，停止非活跃模型容器，并让顶部运行状态实时同步真实容器状态。

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
PANDOCR_MODEL_CONTROL=docker
PANDOCR_ACTIVE_MODEL_ON_START=paddleocr-vl-1.6
PANDOCR_MODEL_SWITCH_TIMEOUT=1200
PADDLE_REQUEST_TIMEOUT=3600
PANDOCR_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
PANDOCR_MAX_UPLOAD_MB=512
```

RTX 30/40 系列等非 Blackwell NVIDIA GPU 使用 `env.docker`，其中两个镜像标签都是 `latest-nvidia-gpu-offline`。

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

这条一键命令会自动检查 Apple Silicon 环境、安装 macOS 依赖、默认启用 MLX-VLM 提速模式、启动 `mlx_vlm.server` / PaddleX API / PandOCR WebUI、执行健康检查，并自动打开 http://127.0.0.1:8000。

首次启动会下载 `PP-DocLayoutV3`、`PaddleOCR-VL-1.6-0.9B` 和 MLX 模型权重，耗时取决于网络和磁盘速度。模型缓存完成后，后续再次运行同一条命令会复用已安装环境和已启动服务。

高级手动启动：

```bash
make mac-setup
make mac-up
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
- PandOCR WebUI: `127.0.0.1:8000`

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
| 环境 | Python 3.12.13, PaddlePaddle 3.3.0, PaddleOCR 3.6.0, PaddleX 3.6.1, mlx-vlm 0.6.3 |
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
- `GET /api/tasks`：读取本机持久化任务摘要列表，不返回大体积源文件和 OCR 结果。
- `GET /api/tasks/{task_id}`：读取一个任务的完整详情。
- `PUT /api/tasks/{task_id}`：保存一个任务到 `data/tasks/`。
- `DELETE /api/tasks/{task_id}`：删除一个本地任务。
- `DELETE /api/tasks`：清空本地任务历史。
- `POST /api/convert/to-pdf`：将 PPT/PPTX/DOC/DOCX 转为 PDF。
- `POST /api/paddleocr-vl-1.6`：代理 OCR 请求到 PaddleOCR-VL layout-parsing 服务。
- `POST /api/pp-ocrv6`：代理 OCR 请求到 PP-OCRv6 服务，返回页面图片、识别文字行、坐标框、置信度和原始 JSON。

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
├── docker-compose.yml
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
