# 项目总结

## 定位

本项目是 PaddleOCR Local 的 WebUI 和代理层。Web 服务负责上传、预览、任务持久化和 API 代理；Office 转 PDF 与 Docker 模型控制分别由隔离的内部服务完成，OCR 推理由独立模型服务完成。

## 运行架构

```text
Browser
  -> pandocr-web
     -> paddleocr-vl-api -> paddleocr-vlm-server
     -> paddleocr-ocr-api
```

服务职责：

- `pandocr-web`：提供前端页面、FastAPI 代理、本地任务持久化和结果格式整理。
- `pandocr-controller`：内部 token 鉴权的模型生命周期控制器，唯一挂载 Docker socket。
- `pandocr-office-converter`：非 root、只读运行的 LibreOffice 转换器。
- `paddleocr-vl-api`：官方 PaddleX layout-parsing 服务。
- `paddleocr-vlm-server`：官方 VLLM 推理服务，模型为 `PaddleOCR-VL-1.6-0.9B`。
- `paddleocr-ocr-api`：PP-OCRv6 OCR 服务，默认模型为 `PP-OCRv6_medium`。

## 核心接口

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/` | GET | WebUI 首页 |
| `/api/models` | GET | 返回可用模型和代理入口 |
| `/api/model-runtime` | GET | 返回当前活跃模型、容器状态和切换状态 |
| `/api/model-runtime/switch` | POST | 切换活跃模型并按需启停模型容器 |
| `/api/parse` | POST | 通过 `modelId` 调用任一已启用模型的统一解析入口 |
| `/api/tasks` | GET | 返回本机持久化任务列表 |
| `/api/tasks/{task_id}` | PUT | 保存一个本地任务 |
| `/api/tasks/{task_id}` | DELETE | 删除一个本地任务 |
| `/api/tasks/{task_id}/clone-source/{target_task_id}` | POST | 在服务端本地复制源文件，供多模型对比任务复用 |
| `/api/tasks` | DELETE | 清空本地任务历史 |
| `/api/convert/to-pdf` | POST | Office 文件转 PDF |
| `/api/paddleocr-vl-1.6` | POST | PaddleOCR-VL 文档解析代理接口 |
| `/api/pp-ocrv6` | POST | PP-OCRv6 OCR 代理接口 |

## 前端处理流程

1. 上传图片、PDF 或 Office 文件。
2. Office 文件先请求 `/api/convert/to-pdf` 转成 PDF。
3. PDF 使用 PDF.js 生成预览，使用 PDF-lib 按页切分请求体。
4. 根据当前选择的模型请求 `/api/paddleocr-vl-1.6` 或 `/api/pp-ocrv6`。
5. 原始 JSON 会保留到任务数据中；PaddleOCR-VL 展示 Markdown，PP-OCRv6 展示可视化 OCR 层和 JSON。
6. 使用 KaTeX 渲染数学公式。
7. 将 OCR 结果中的字面量 `\n`、`\r\n` 规范化为真实换行。
8. 下载时合并 Markdown、OCR JSON 和提取图片。

## 关键配置

`env.txt` 用于 RTX 50 / Blackwell：

```text
API_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
VLM_BACKEND=vllm
VLM_IMAGE_TAG_SUFFIX=latest-nvidia-gpu-sm120-offline
PADDLEOCR_VL_MODEL_NAME=PaddleOCR-VL-1.6-0.9B
PPOCR_V6_MODEL_NAME=PP-OCRv6_medium
PANDOCR_MODEL_CONTROL=docker
PANDOCR_ACTIVE_MODEL_ON_START=paddleocr-vl-1.6
PADDLE_REQUEST_TIMEOUT=3600
PANDOCR_MAX_CONCURRENT_OCR=1
PANDOCR_ENFORCE_ORIGIN_CHECK=1
```

`PADDLE_REQUEST_TIMEOUT` 建议保持较大值，大 PDF 按页处理时整体耗时仍可能较长。

## 主要文件

- `server.py`：FastAPI 后端。
- `static/app.js`：上传、队列、批处理、模型切换、OCR 调用和下载逻辑。
- `static/index.html`：页面结构和前端依赖。
- `static/style.css`：界面样式、表格和公式预览样式。
- `static/vendor/katex/`：本地 KaTeX 运行时资源。
- `data/tasks/`：本地任务数据目录，已加入 `.gitignore`。
- `Dockerfile`：Web 容器镜像。
- `docker-compose.yml`：WebUI、PaddleOCR-VL、PP-OCRv6 服务编排。
- `pipeline_config_vllm.yaml`：PaddleOCR-VL API pipeline 配置。

## 验证建议

代码改动后至少执行：

```powershell
make check
node --check static/app.js
python -m py_compile server.py
docker compose --env-file env.txt config --quiet
docker compose --env-file env.txt build pandocr-web
docker compose --env-file env.txt up -d --no-deps --force-recreate pandocr-web
curl http://localhost:8000/api/models
curl http://localhost:8000/api/model-runtime
curl http://localhost:8081/health
```

涉及前端预览时，额外检查：

- 公式是否生成 `.katex` 节点。
- 表格是否可横向滚动。
- OCR 文本中的字面量 `\n` 是否变成真实换行。
