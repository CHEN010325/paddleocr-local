# 快速开始

完整部署说明统一维护在 `README.md` 的“部署方式”章节；那里分为 NVIDIA Docker 版本和 macOS Apple Silicon 版本。

## macOS Apple Silicon

Apple M1/M2/M3/M4 一键部署：

```bash
./macos-one-click.command
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

## 1. 检查环境

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

## 2. 拉取并构建

```powershell
docker compose --env-file env.txt pull
docker compose --env-file env.txt build pandocr-web
```

`pandocr-web` 只构建 Web 服务，不包含 Paddle/PaddleX；PaddleOCR-VL 由官方 `paddleocr-vl-api` 和 `paddleocr-vlm-server` 镜像提供。

## 3. 启动服务

```powershell
docker compose --env-file env.txt up -d
```

首次启动 VLM 服务会加载模型，可能需要几分钟。

## 4. 验证

```powershell
docker compose --env-file env.txt ps
curl http://localhost:8000/api/models
curl http://localhost:8081/health
```

期望看到 3 个容器：

- `paddleocr-vlm-server`
- `paddleocr-vl-api`
- `pandocr-web`

`/api/models` 应返回 `PaddleOCR-VL-1.6-0.9B`。

## 5. 使用

打开 http://localhost:8000。

- 图片会直接作为图片请求提交。
- PDF 会按页提交，任务完成后会保留每页原始 JSON，方便和官方在线结果核对。
- PPT/PPTX/DOC/DOCX 会先由 `pandocr-web` 调 LibreOffice 转 PDF，再进入 PDF 流程。
- 结果区会渲染 Markdown、表格和 KaTeX 公式，并修正 OCR 结果里字面量 `\n` 导致的不换行问题。
- 历史任务会保存到本机 `data/tasks/`，侧边栏删除按钮会同时删除对应本地记录。

## 常见问题

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
