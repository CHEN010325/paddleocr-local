# Unlimited-OCR-2 部署与使用

第二代已替换第一代，进入 Windows/Linux Docker 默认六模型目录，支持按需部署、
独占显存切换、图片/PDF/Office、流式结果、任务恢复和导出。内部 ID/API/profile
保留 `unlimited-ocr`，以兼容已有任务。前端目录来自 `/api/models`。

## Windows 一键部署

```powershell
.\windows-one-click.bat -Model unlimited-ocr -UnlimitedOcrBackend transformers
.\windows-one-click.bat -Model unlimited-ocr -UnlimitedOcrBackend sglang
```

也可以在 WebUI 选择模型后部署，并在后端下拉框切换。控制器先停止旧模型并确认显存释放。
SGLang 模式仅 SGLang 服务加载权重，适配器不重复加载 Transformers 权重。

## 官方来源与依赖

- [官方教程](https://huggingface.co/baidu/Unlimited-OCR-2) / [官方源码](https://github.com/baidu/Unlimited-OCR)
- 固定权重及代码 revision：`9cf077c4b206e0e2a31879df937b0b0bcba3661c`。
- Transformers 使用官方测试的 `4.52.1`、Hub `0.36.0`、tokenizers `0.21.4`。
- SGLang 使用官方专用 `0.5.20.dev10+g8d62f5fb9c` wheel，固定 revision 和 SHA-256。
- 两条 Docker 路线使用 CUDA 13.0.2 与 PyTorch 2.13。后端依赖环境保持隔离。
- 第二代使用独立的 `pandocr-unlimited-ocr2-*` 镜像名称，避免升级时复用第一代的旧依赖镜像。

单页默认动态切片，多页默认 base。单页和多页均使用官方第二代默认指令
`Multi page parsing.`，每页提示词包含 `<PAGE><image>`；输出的 `<PAGE>`
直接决定页码，在正文和导出中清理。SGLang 启用官方分页调度、共享内存和页面生成保护；
桌面显卡默认单并发、关闭 CUDA Graph，不能照搬官方 A800 的 64 并发参数。
关闭 CUDA Graph 时同时将 `SGLANG_UOCR_GRAPH_METADATA_REFRESH` 和
`SGLANG_UOCR_GRAPH_METADATA_INPUTS` 设为 `0`，避免固定版本 wheel 的 eager 解码
误用未初始化的 graph 坐标缓冲区、跳过 Page2D RoPE 而产生重复坐标。
适配器同时清理 det 和 BLOCK/FORK/CHILD 布局标记，并将坐标与显式页码写入统一解析结果。

`UNLIMITED_OCR_MAX_TOKENS` / `UNLIMITED_OCR_SGLANG_MAX_TOKENS` 控制输出预算，
WebUI 默认 8192；`UNLIMITED_OCR_CONTEXT_LENGTH` 默认 131072。PDF 默认最多 50 页，
渲染像素预算 60000000。模型的无限长能力不代表 WebUI 取消这些资源保护。

官方当前代码依赖 CUDA；原生 macOS MPS 不能据此视为已验证支持。Mac 用户可连接
NVIDIA 主机部署的统一 `/api/parse`，参见下述外部引擎配置。

## 已有本地或远程 Unlimited-OCR-2 服务

建议通过[外部引擎配置](external-engines.md)接入。
若为另一个 WebUI 的 `/api/parse`，使用 `unified` 并设置 `model: unlimited-ocr`。
原始 `unlimited_ocr_adapter.py` 的 `/ocr` 不是 Paddle layout-parsing 协议，
应先通过本项目 WebUI 的统一接口接入，不能直接选择 `paddle` 协议。

管理式部署必须让 WebUI 和控制器使用相同的
`PANDOCR_MODEL_CATALOG`（包含 `unlimited-ocr`），并启用对应 Compose profile。
仅设置 `PANDOCR_ENABLE_UNLIMITED_OCR=1` 不会覆盖一个显式的模型目录。
不要同时启动多个 GPU 模型；通过原有控制器执行部署和切换。

协议回归测试见 `tests/test_unlimited_ocr2.py`，覆盖提示词、生成预算、分页保护、多页返回值与页码清理。
真实 GPU 验收与单元测试分别记录；本机通过不代表所有 GPU、系统或文档均通过。
