# 快速开始

默认中文 `README.md` 只保留最常用的一键部署入口；本页提供 Windows/NVIDIA、macOS Apple Silicon 和手动 Docker 的补充步骤。

macOS 可先运行统一诊断：

```bash
make doctor
```

Windows 可直接使用一键脚本的预检模式：

```powershell
.\windows-one-click.bat -DryRun
```

## macOS Apple Silicon

Apple M1/M2/M3/M4 一键部署：

```bash
./macos-one-click.command
```

脚本会像 Windows 版本一样提示选择一个模型，只安装并启动所选模型：

```bash
./macos-one-click.command --model paddleocr-vl-1.6
./macos-one-click.command --model pp-ocrv6
./macos-one-click.command --model unlimited-ocr
./macos-one-click.command --model ovisocr2
```

只检查选择和参数，不安装或启动：

```bash
./macos-one-click.command --model ovisocr2 --dry-run
```

或：

```bash
make mac-one-click
```

手动 native 模式：

```bash
make mac-setup
make mac-up
```

```bash
make mac-test
make mac-down
```

MLX-VLM 提速模式：

```bash
make mac-setup-mlx
make mac-down
make mac-up-mlx
make mac-test-mlx
```

NVIDIA 用户继续使用下面的 Docker 流程。

## Windows NVIDIA 一键部署（推荐）

在 Windows + NVIDIA Docker 环境下，推荐直接运行：

```powershell
.\windows-one-click.bat
```

脚本会让用户从 `PaddleOCR-VL 1.6`、`PP-OCRv6`、`Unlimited-OCR`、`OvisOCR2`、`HPD-Parsing` 中选择首次部署模型，只拉取或构建对应服务和 `pandocr-web`。随后由 WebUI 运行时控制器只启动选择的模型，并通过 `/api/model-runtime` 等待它进入 ready，避免单 GPU 同时加载多个模型。

只做预检、不启动服务：

```powershell
.\windows-one-click.bat -DryRun
```

多卡机器指定 GPU：

```powershell
.\windows-one-click.bat -GpuId 1
```

直接指定 HPD-Parsing，或部署全部模型但首次启动 HPD-Parsing：

```powershell
.\windows-one-click.bat -Model hpd-parsing
.\windows-one-click.bat -Models all-five -ActiveModel hpd-parsing
```

HPD-Parsing 官方运行时要求 NVIDIA GPU、Linux x86-64 容器和支持 CUDA 12.8+ 的驱动；Apple Silicon 一键脚本暂不提供该模型。

## 手动 Docker 流程

### 1. 检查环境

```powershell
docker --version
nvidia-smi
```

根据 `nvidia-smi` 看到的显卡型号选择环境文件：

| 显卡 | 使用的环境文件 | 说明 |
| --- | --- | --- |
| RTX 30 系列 | `env.docker` | 使用普通 NVIDIA GPU 离线镜像 |
| RTX 40 系列 | `env.docker` | 使用普通 NVIDIA GPU 离线镜像 |
| RTX 50 系列 / Blackwell | `env.txt` | 使用 SM120 / Blackwell 专用离线镜像 |

下面命令以 RTX 50 系列的 `env.txt` 为例。RTX 30/40 系列用户请把命令里的 `env.txt` 换成 `env.docker`。

### 2. 拉取并构建

```powershell
docker compose --env-file env.txt pull paddleocr-vlm-server paddleocr-vl-api
docker compose --env-file env.txt build paddleocr-ocr-api pandocr-web
```

`pandocr-web` 提供 WebUI、FastAPI 代理和 Office 转 PDF 能力；PaddleOCR-VL 由官方 `paddleocr-vl-api` 和 `paddleocr-vlm-server` 镜像提供，PP-OCRv6 由本地 `paddleocr-ocr-api` 镜像提供。

### 3. 启动服务

```powershell
docker compose --env-file env.txt up -d --no-start
docker compose --env-file env.txt start pandocr-web
```

首次启动默认模型会加载权重，可能需要几分钟。单 GPU 部署默认只热加载 `PaddleOCR-VL 1.6`；切到 `PP-OCRv6` 时，WebUI 会先停止 VL 相关容器，再启动 PP-OCRv6 容器。

### 4. 验证

```powershell
docker compose --env-file env.txt ps
curl http://localhost:8000/api/models
curl http://localhost:8000/api/model-runtime
curl http://localhost:8081/health
./test-connection.sh env.txt
```

实际容器数量取决于已部署模型和启用的 Compose profile。单 GPU 环境中，`pandocr-web` 与当前活跃模型处于 running/healthy，其他已创建模型处于 created/exited/standby 均属正常。可能出现的模型服务包括：

- `paddleocr-vlm-server`
- `paddleocr-vl-api`
- `paddleocr-ocr-api`
- `unlimited-ocr-api`
- `unlimited-ocr-sglang`
- `ovisocr2-api`
- `hpd-parsing-server`
- `hpd-parsing-api`
- `pandocr-web`

`/api/models` 应返回 `paddleocr-vl-1.6`、`pp-ocrv6`、`unlimited-ocr`、`ovisocr2` 和 `hpd-parsing`；未部署模型会显示为待部署。`/api/model-runtime` 应返回当前活跃模型和每个模型的真实运行状态。

模型健康检查端口：

- PaddleOCR-VL: http://localhost:8081/health
- PP-OCRv6: http://localhost:8082/health
- Unlimited-OCR: http://localhost:8083/health
- OvisOCR2: http://localhost:8084/health
- HPD-Parsing: http://localhost:8085/health

### 5. 使用

打开 http://localhost:8000。

- 图片会直接作为图片请求提交。
- PDF 会按页提交，任务完成后会保留每页原始 JSON，方便和官方在线结果核对。
- PPT/PPTX/DOC/DOCX 会先由 `pandocr-web` 调 LibreOffice 转 PDF，再进入 PDF 流程。
- 结果区会渲染 Markdown、表格和 KaTeX 公式，并修正 OCR 结果里字面量 `\n` 导致的不换行问题。
- 历史任务会保存到本机 `data/tasks/`，侧边栏删除按钮会同时删除对应本地记录。

## 常见问题

### 拉取镜像时出现 `pandocr-web:latest` 403

如果日志里出现类似：

```text
failed to resolve reference "docker.io/library/pandocr-web:latest"
unexpected status ... docker.m.daocloud.io ... 403 Forbidden
```

说明 Docker 正在尝试从远端仓库拉取 `pandocr-web:latest`。这个镜像应该在本机从项目源码构建，不需要从 Docker Hub 拉取。请先更新到最新代码，然后使用：

```powershell
docker compose --env-file env.txt pull paddleocr-vlm-server paddleocr-vl-api
docker compose --env-file env.txt build paddleocr-ocr-api pandocr-web
docker compose --env-file env.txt up -d --no-start
docker compose --env-file env.txt start pandocr-web
```

不要单独执行旧版本文档里的 `docker compose --env-file env.txt pull`。如果 403 出现在其他 Docker Hub 镜像上，再检查 Docker Desktop 的 registry mirror 配置，移除或更换返回 403 的 `docker.m.daocloud.io` 镜像源。

### `paddleocr-vlm-server is unhealthy`

`paddleocr-vlm-server` 是最底层的 VLM 推理服务。它没有健康起来时，后面的 `paddleocr-vl-api` 和 `pandocr-web` 都会被依赖关系卡住。先看它自己的日志：

```powershell
docker compose --env-file env.txt logs --tail=200 paddleocr-vlm-server
```

如果你使用 RTX 30/40 系列，命令里的 `env.txt` 要换成 `env.docker`：

```powershell
docker compose --env-file env.docker pull paddleocr-vlm-server paddleocr-vl-api
docker compose --env-file env.docker build paddleocr-ocr-api pandocr-web
docker compose --env-file env.docker up -d --no-start
docker compose --env-file env.docker start pandocr-web
```

如果之前已经启动失败过，先清掉旧的 unhealthy 容器再重启：

```powershell
docker compose --env-file env.txt down
docker compose --env-file env.txt up -d --no-start --force-recreate
docker compose --env-file env.txt start pandocr-web
```

首次启动 VLM 会加载模型，可能需要 10-15 分钟。若日志提示显存不足，请关闭占用 GPU 的程序，或在 `env.txt` / `env.docker` 中把 `PANDOCR_GPU_DEVICE_ID` 改成另一张空闲显卡的编号。

### 端口占用

修改 `docker-compose.yml` 中的端口映射，例如：

```yaml
ports:
  - "18000:8000"
```

### OCR 请求超时

大 PDF 批处理可能很慢，可以调大：

```text
PADDLE_REQUEST_TIMEOUT=7200
```

修改后重建或重启 `pandocr-web`：

```powershell
docker compose --env-file env.txt up -d --no-deps --force-recreate pandocr-web
```

### 前端改动没有生效

浏览器可能缓存了 `/static/app.js`。确认 `static/index.html` 中脚本版本号变化，或强制刷新页面。
