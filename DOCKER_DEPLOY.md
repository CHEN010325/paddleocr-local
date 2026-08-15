# Docker 部署说明

## 服务组成

`docker-compose.yml` 包含常驻 WebUI、隔离的内部控制/转换服务、PaddleOCR 服务和三个可选 profile：

| 服务 | 作用 | 对外端口 |
| --- | --- | --- |
| `paddleocr-vlm-server` | VLLM 推理，加载 `PaddleOCR-VL-1.6-0.9B` | 无 |
| `paddleocr-vl-api` | PaddleX layout-parsing API | `8081:8080` |
| `paddleocr-ocr-api` | PaddleX OCR API，默认使用 PP-OCRv6 | `8082:8080` |
| `unlimited-ocr-api` | Unlimited-OCR 适配服务（可选） | `8083:8080` |
| `unlimited-ocr-sglang` | Unlimited-OCR SGLang 推理（按 backend 可选） | `10000:10000` |
| `ovisocr2-api` | OvisOCR2 独立 vLLM 推理（可选） | `8084:8080` |
| `hpd-parsing-server` | HPD-Parsing 官方定制 vLLM 推理（可选） | 无 |
| `hpd-parsing-api` | HPD-Parsing 图片/PDF 与 Markdown 适配服务（可选） | `8085:8080` |
| `pandocr-controller` | 内部白名单模型控制，独占 Docker socket | 无 |
| `pandocr-office-converter` | 非 root、只读运行的 LibreOffice 转换器 | 无 |
| `pandocr-web` | WebUI、FastAPI 代理 | `8000:8000` |

单 GPU 部署默认只热加载一个模型。WebUI 不再挂载 Docker socket，也不包含 LibreOffice；模型切换由带内部 token 的 `pandocr-controller` 执行，Office 文件由隔离的 `pandocr-office-converter` 转换。控制器仍持有等同宿主机管理权限的 Docker socket，因此不要映射其端口，并应为 `PANDOCR_MODEL_CONTROLLER_TOKEN` 设置随机长值。
解析历史会通过 `./data:/app/data` 挂载保存到宿主机，默认路径为 `data/tasks/`。

## 推荐配置

先按显卡型号选择环境文件：

| 显卡 | 推荐环境文件 | `API_IMAGE_TAG_SUFFIX` / `VLM_IMAGE_TAG_SUFFIX` |
| --- | --- | --- |
| RTX 30 系列 | `env.docker` | `latest-nvidia-gpu-offline` |
| RTX 40 系列 | `env.docker` | `latest-nvidia-gpu-offline` |
| RTX 50 系列 / Blackwell | `env.txt` | `latest-nvidia-gpu-sm120-offline` |

`env.txt` 是 RTX 50 / Blackwell 推荐配置：

```text
API_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
API_IMAGE_DIGEST=@sha256:0971c409d1cab2b12aa17b76855e36ac8eb9fb1adc97dbeea15e9b09432a4a3b
VLM_BACKEND=vllm
VLM_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
VLM_IMAGE_DIGEST=@sha256:bffd525308facf5dba2f8eca44ab476704a0ae3bfdcba25f77655973e4c0a7ca
PANDOCR_GPU_DEVICE_ID=0
PADDLEOCR_VL_MODEL_NAME=PaddleOCR-VL-1.6-0.9B
PPOCR_V6_MODEL_NAME=PP-OCRv6_medium
PANDOCR_MODEL_CONTROL=docker
PANDOCR_MODEL_CONTROLLER_TOKEN=请替换为随机长值
PANDOCR_ACTIVE_MODEL_ON_START=paddleocr-vl-1.6
PANDOCR_MODEL_SWITCH_TIMEOUT=1200
PADDLE_REQUEST_TIMEOUT=3600
PANDOCR_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
PANDOCR_MAX_UPLOAD_MB=512
PANDOCR_MAX_HTTP_BODY_MB=0
PANDOCR_MAX_CONCURRENT_OCR=1
PANDOCR_ENFORCE_ORIGIN_CHECK=1
PANDOCR_API_TOKEN=
PANDOCR_ENABLE_API_DOCS=0
UNLIMITED_OCR_MODEL_REVISION=07dea832e22aefee32ad281d4b80551282e1c168
UNLIMITED_OCR_MAX_RENDER_PIXELS=60000000
OVISOCR2_MODEL_REVISION=65c619d374b55d4152e85150fc1b003700bc1f0c
```

`PANDOCR_MAX_UPLOAD_MB` 限制解码后的原文件；JSON 中的 base64 会额外膨胀，`PANDOCR_MAX_HTTP_BODY_MB=0` 表示自动计算安全的请求体上限。Unlimited-OCR 会在渲染 PDF 前按 `UNLIMITED_OCR_MAX_RENDER_PIXELS` 拒绝像素预算过大的文件，OvisOCR2 则逐页渲染和推理。远程模型 revision 与上游 Paddle/CUDA/Python 镜像均锁定到已核验提交或 digest；升级时应同时更新标签和哈希。

RTX 30/40 系列等非 Blackwell NVIDIA GPU 使用 `env.docker`，或把两个镜像标签改为：

```text
latest-nvidia-gpu-offline
```

下文命令以 `env.txt` 为例；如果你使用 RTX 30/40 系列，请把命令中的 `env.txt` 换成 `env.docker`。

## 启动

Windows + NVIDIA 用户推荐直接运行一键部署脚本：

```powershell
.\windows-one-click.bat
```

脚本会自动选择环境文件，并让用户从五个模型中选择首次部署模型；只创建选中的模型容器和 WebUI，然后等待所选模型健康。选择多个模型时可通过 `-ActiveModel` 指定首次启动模型：

```powershell
.\windows-one-click.bat -Model ovisocr2
.\windows-one-click.bat -Models all-five -ActiveModel ovisocr2
```

HPD-Parsing 可直接一键部署：

```powershell
.\windows-one-click.bat -Model hpd-parsing
```

该模型使用官方 `hpd-parsing-vllm` 镜像，要求 NVIDIA GPU、Linux x86-64 容器和支持 CUDA 12.8+ 的驱动。在线镜像首次启动会下载 `PaddlePaddle/HPD-Parsing`；缓存保存在 `model_cache_hpd_parsing/`。如需官方离线镜像，可设置 `HPD_PARSING_IMAGE=ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/hpd-parsing-vllm:latest-nvidia-gpu-offline`。

HPD-Parsing 默认按约 6.5 GiB 的绝对预算自动计算 vLLM 显存比例，而不是预占整张显卡的 90%；16 GiB 显卡约使用 `0.408`，建议至少使用 8 GiB 显卡。可用 `HPD_PARSING_GPU_MEMORY_TARGET_MIB` 调整目标预算，或将 `HPD_PARSING_GPU_MEMORY_UTILIZATION` 设置为明确比例以覆盖自动计算。

## 启动前 GPU 显存预检

`pandocr-controller` 会在切换/启动模型前运行短生命周期的 GPU 探测容器。`/api/model-runtime` 的 `gpuPreflight` 字段和 WebUI 顶部提示会列出 GPU 型号、总/空闲显存、可运行模型以及低显存环境变量。PaddleOCR-VL 按官方当前最低成功运行配置 RTX 3060 12 GB 设置 `11264 MiB` 保护下限；RTX 4070 Laptop 8 GB 不会再尝试启动该模型，页面会直接推荐 PP-OCRv6 等兼容模型。依据见 [PaddleOCR-VL 推理部署高频问题](https://github.com/PaddlePaddle/PaddleOCR/discussions/16822)。

PaddleOCR-VL 推荐保留以下默认值：

```dotenv
PANDOCR_VLLM_MIN_TOTAL_MIB=11264
PANDOCR_VLLM_MIN_REQUIRED_MIB=6656
PANDOCR_VLLM_RESERVE_MIB=512
PANDOCR_MAX_CONCURRENT_OCR=1
```

HPD-Parsing 在 8 GB 卡上使用 `HPD_PARSING_GPU_MEMORY_UTILIZATION=auto`；若仍接近上限，可将 `HPD_PARSING_GPU_MEMORY_TARGET_MIB` 从 `6656` 降到 `6144`，并配合 `HPD_PARSING_MAX_MODEL_LEN=8192`、`HPD_PARSING_MAX_TOKENS=4096` 和 `HPD_PARSING_MAX_CONCURRENCY=1`。

手动部署命令如下：

```powershell
docker compose --env-file env.txt pull paddleocr-vlm-server paddleocr-vl-api
docker compose --env-file env.txt build paddleocr-ocr-api pandocr-web pandocr-office-converter
docker compose --env-file env.txt up -d --no-start
docker compose --env-file env.txt start pandocr-web
```

## 健康检查

```powershell
docker compose --env-file env.txt ps
curl http://localhost:8000/api/models
curl http://localhost:8000/api/model-runtime
curl http://localhost:8081/health
```

默认情况下只有 `PaddleOCR-VL 1.6` 会启动，`PP-OCRv6` 的健康检查不通是正常的。切到 `PP-OCRv6` 后，`8082/health` 会变为可用，`8081/health` 会进入 standby。解析正在运行时模型切换会返回 `409`，避免长任务中途被停容器打断。

如果要通过反向代理、局域网或公网暴露 WebUI，请设置 `PANDOCR_API_TOKEN`。`PANDOCR_ENFORCE_ORIGIN_CHECK=1` 会拒绝未加入来源白名单的跨站 API 写请求，但它不能替代 token；前端会在 API 返回 401 时提示输入 token。`PANDOCR_ENABLE_API_DOCS=1` 时才启用 `/docs` 和 `/redoc`。

`/api/models` 应返回：

```json
{"default":"paddleocr-vl-1.6","data":[{"id":"paddleocr-vl-1.6","name":"PaddleOCR-VL-1.6-0.9B"},{"id":"pp-ocrv6","name":"PP-OCRv6_medium"}],"originProtection":true,"maxConcurrentOcr":1}
```

## 重启 Web 服务

前端或 FastAPI 逻辑变更后，重建并重启 `pandocr-web`；控制或 Office 转换逻辑变更时也重建对应的隔离服务：

```powershell
docker compose --env-file env.txt build pandocr-web pandocr-office-converter
docker compose --env-file env.txt up -d --no-deps --force-recreate pandocr-controller pandocr-office-converter pandocr-web
```

代码以只读方式挂载；生产部署仍建议重建镜像，避免运行内容与镜像版本不一致。

## 本地任务数据

解析完成的任务会保存到 `data/tasks/`。这个目录已经加入 `.gitignore`，不会随代码提交。

如需清空历史，可以在 WebUI 侧边栏点击清空按钮，或删除本机目录后重启 Web 服务。

## 日志

```powershell
docker compose --env-file env.txt logs -f pandocr-web
docker compose --env-file env.txt logs -f pandocr-controller pandocr-office-converter
docker compose --env-file env.txt logs -f paddleocr-vl-api
docker compose --env-file env.txt logs -f paddleocr-ocr-api
docker compose --env-file env.txt logs -f paddleocr-vlm-server
docker compose --env-file env.txt logs --tail=200 hpd-parsing-server
docker compose --env-file env.txt logs --tail=200 hpd-parsing-api
```

WebUI 模型启动失败时会从 Docker API 读取对应容器最后 120 行日志并显示在显存提示栏，同时给出等价的 `docker logs --tail 200 <container>` 命令。PaddleOCR-VL 优先检查 `paddleocr-vlm-server`；HPD-Parsing 优先检查 `hpd-parsing-server`，再检查各自的 API/adapter 容器。

## 端口调整

修改 `docker-compose.yml`：

```yaml
pandocr-web:
  ports:
    - "18000:8000"

paddleocr-vl-api:
  ports:
    - "18081:8080"

paddleocr-ocr-api:
  ports:
    - "18082:8080"
```

## 数据和缓存

模型缓存通过目录挂载保留：

- `./model_cache:/home/paddleocr/.paddlex`：PaddleOCR-VL / PaddleX 缓存
- `./model_cache_ocr:/home/paddleocr/.paddleocr`：PaddleOCR-VL 相关缓存
- `./model_cache_ppocrv6:/home/paddleocr/.paddlex`：PP-OCRv6 / PaddleX 3.7 缓存
- `./model_cache_ppocrv6_ocr:/home/paddleocr/.paddleocr`：PP-OCRv6 相关缓存

这些缓存目录已加入 `.dockerignore`，不会被打进 `pandocr-web` 镜像构建上下文。

解析历史保存在 `./data/tasks/`。每个任务目录下 `task.json` 只保存轻量元数据，`summary.json` 用于快速列表，`result.json` 保存 Markdown、OCR JSON 和图片 base64。清空历史只删除合法 task id 子目录，不会递归删除整个 `data/`。

## 清理

```powershell
docker compose --env-file env.txt down
docker image prune
```

谨慎清理模型缓存目录；删除后下次启动会重新下载或加载模型资源。
