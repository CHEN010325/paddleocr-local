# Unlimited-OCR-2 部署与使用

第二代已替换第一代，进入 Windows/Linux Docker 默认六模型目录，支持按需部署、
独占显存切换、图片/PDF/Office、流式结果、任务恢复和导出。内部 ID/API/profile
保留 `unlimited-ocr`，以兼容已有任务。前端目录来自 `/api/models`。

## Windows 一键部署

安装 NVIDIA 驱动与支持 GPU 的 Docker Desktop 后，在仓库根目录运行独立入口：

```powershell
.\unlimited-ocr2-one-click.bat
```

自动检查硬件并选择第二代模型的后端及资源配置，首次构建依赖环境并下载固定版本权重，完成后打开 WebUI。
该入口复用原有部署器、持久凭据、六模型目录和独占显存检查。其他模型仍使用原来的
`windows-one-click.bat`，模型缓存与任务目录保留。

选择原生 Transformers，或仅预检查：

```powershell
.\unlimited-ocr2-one-click.bat -Backend transformers
.\unlimited-ocr2-one-click.bat -DryRun -NoOpen
```

已有本机镜像可加 `-SkipBuild -SkipPull`；首次安装应使用默认构建流程。
自动入口要求 NVIDIA Ampere 或更新架构（sm80+）、12GB 档及以上显存、Windows NVIDIA
驱动 580.88 或更新，以及 Linux 容器模式的 Docker Desktop（至少分配 8GiB 内存）。
启动前还会实际执行 CUDA BF16 运算；多显卡默认从符合条件的设备中选择，也可用 `-GpuId` 指定。

| 显存档位 | 自动后端 | 上下文 / 输出预算 | 默认每批页数 |
| --- | --- | --- | --- |
| 12GB | Transformers | 16384 / 8192 | 2 |
| 16GB | SGLang | 32768 / 28672 | 5 |
| 24GB 及以上 | SGLang | 65536 / 57344 | 10 |

这些是保守的自动配置策略，当前实机验证范围为 RTX 5070 Ti 16GB，其他档位已有策略测试，
尚未完成对应实机验收。12GB 档不开放 SGLang 自动部署；资源不足会中止并说明原因。
CPU、AMD、Intel 或旧 NVIDIA 设备可使用原来的 PP-OCRv6 CPU 入口，或连接外部 OCR 服务。
不会自动切换成另一种模型并声称已部署第二代。原有 Linux 和 macOS 入口保持原来的支持范围。

原有带参数入口继续有效：

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
16GB 桌面显卡默认一个公共 OCR 作业，启用完整预填充和解码 CUDA Graph：解码捕获 batch=1/2，
预填充桶为 1024/2048，`full_prefill_max_req=1`，静态显存比例为 0.65。
graph 元数据刷新与输入均设为 `1`，不能照搬官方 64 并发的图捕获范围。
内部保留两个请求槽，分别容纳跨窗口保留的 KV 和当前活动窗口；只留一个槽会导致
第十页后的下一窗口无法继续。这不代表开放两个并发文档作业。
如自行关闭 CUDA Graph，必须同时把 `SGLANG_UOCR_GRAPH_METADATA_REFRESH` 和
`SGLANG_UOCR_GRAPH_METADATA_INPUTS` 设为 `0`，避免 eager 解码缺失页坐标。
健康轮询使用 `/ready`；官方 wheel 的 `/health` 会生成单 token，
此类日志中的低吞吐不能用于衡量文档解析速度。
适配器同时清理 det 和 BLOCK/FORK/CHILD 布局标记，并将坐标与显式页码写入统一解析结果。

`UNLIMITED_OCR_MAX_TOKENS` / `UNLIMITED_OCR_SGLANG_MAX_TOKENS` 控制输出预算，
默认输出预算 28672；`UNLIMITED_OCR_CONTEXT_LENGTH` 默认 32768，保留输入空间。
WebUI 为首次使用或旧版强制单页配置默认选择每批 5 页；显式选择的页数保留，可在设置中调整。
同一批次的页面一次交给模型，具有跨页上下文；不同批次之间不共享模型上下文。
每个 API 请求最多 50 页，渲染像素预算 60000000，栅格页最长边默认 2048，
可用 `UNLIMITED_OCR_PDF_MAX_DIMENSION` 调整。SGLang PDF 请求仍使用 base 模式、最长边 1120。
输出达到 token 上限或显式页标记数量不匹配时返回错误，不把不完整文档标成成功。
模型的无限长能力不代表 WebUI 取消这些资源保护。

Transformers 加载采用共享任务；请求取消不会启动第二份权重拷贝。
取模型引用、推理及卸载遵循同一锁顺序，流式请求关闭后等待工作线程退出再允许卸载。
Windows 宿主机绑定目录上的权重加载与首次内核编译可能耗时数分钟，应与热启动识别计时分开。

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
速度、跨窗口与内容对比见[性能实测记录](unlimited-ocr2-performance.md)。
