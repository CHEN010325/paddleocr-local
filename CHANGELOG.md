# 更新日志

## Unreleased

### Unlimited-OCR-2

- 用第二代替换第一代，并加入 Windows/Linux 默认六模型目录；保留内部模型 ID 兼容已保存任务。
- 更新 Transformers 与官方专用 SGLang 部署环境、权重 revision、分页提示词和页面生成保护。
- 处理第二代多页返回值与显式页边界，支持统一识别、流式预览、页码定位、任务恢复及导出。

### Issue 根因修复与部署验收

- 默认保留脚注和侧栏文字；请求、版面显示和两份 VL 配置保持一致，并提供独立忽略选项（#11）。历史任务若原识别结果已省略该内容，需要重新解析。
- 增加独立 PP-OCRv6 CPU Compose、CPU 兼容配置和可移植真实识别 smoke 脚本，避免 NVIDIA 设备要求及 oneDNN/PIR 算子错误（#1）。
- MLX 预检覆盖实际服务入口，直接启动也检查依赖；服务提前退出即报告原因，不再只等待超时（#3）。保留 Python 3.12/3.13 选择与环境修复（#2）。
- 加入独立外部 OCR 引擎协议层，支持 OpenAI vision、Paddle layout-parsing、统一 API，UI 根据服务目录选择，模型生命周期由部署者管理（#12）。
- 记录 Unlimited-OCR 实验目录的按需启用方式（#6）和低显存预检限制（#7）。
- 修复自定义 WebUI 端口导致本机上传被来源检查拒绝的问题。
- pypdf 升级至 6.16.1，修复依赖审计报告的已知漏洞。
- OvisOCR2 和实验性 HPD macOS 单设备加载移除不需要的 Accelerate 显式依赖，避免引入未修复的 checkpoint 路径漏洞；Transformers 5 已不使用旧 `low_cpu_mem_usage` 参数。
- CI 增加 Windows/macOS/Linux 检查矩阵及无 GPU 的真实 CPU 图片/PDF smoke 作业。

### 使用体验与导出

- 新增失败批次单独重试，只重新处理错误批次，并保留已完成批次的 Markdown、图片和 JSON。
- 新增离线单文件 HTML 导出，自动内嵌提取图片，不依赖网络即可打开。
- 新增 Markdown 表格 CSV 导出；多个表格自动打包为 ZIP，并阻止电子表格公式注入。
- 新增可编辑 DOCX、可搜索 PDF 和 XLSX 导出；DOCX 保留标题、列表与表格结构，PDF 在原页面上增加不可见检索文本层，XLSX 自动按表格拆分工作表并防止公式注入。
- 新增基于 GPU 显存与模型能力的推荐模型提示，可从预检栏一键切换。
- 重写中英文 README 首屏，新增 60 秒体验指南、Showcase 模板和清晰的版本路线。

### 多模型工作台

- 新增同一源文件的多模型顺序解析、独立任务、耗时记录、并排查看和 Markdown 对比报告下载。
- 新增 Markdown 人工编辑并持久化修订结果。
- 新增服务端本地源文件克隆，创建对比任务时不再通过浏览器重复上传大文件。

### 自动化

- 新增稳定的模型无关接口 `POST /api/parse`，通过 `modelId` 选择已启用模型。
- 新增 `pandocr_cli.py`，支持环境诊断、批量解析、多模型对比和 Watch Folder。
- CLI 会保存 Markdown 引用的图片资源、避免多页正文重复与同名文件覆盖，并在单模型失败后继续完成其余对比。

### 单模型显存互斥与可靠性

- 将五个默认逻辑模型（PaddleOCR-VL 1.6、PP-OCRv6、OvisOCR2、HPD-Parsing 和 NaviDC-OCR）放入独立 Compose profile；所有部署入口只预创建模型容器，由 controller 启动用户当前选择的唯一模型。Unlimited-OCR 保留为非默认实验适配器。
- 新增 controller 原子 OCR lease，消除 Web OCR 与远程模型切换之间的并发竞态；有在途 OCR 时切换、部署和后端变更均会失败闭锁。
- 模型切换严格执行“停止并复检全部非目标模型 → GPU 预检 → 启动目标 → 唯一 running/ready 复检”，失败时清理半启动目标。
- 运行状态新增 `runningModelIds`、`readyModelIds` 与 `exclusivityViolation`，不再隐藏异常的多模型残留。
- 修复 macOS 默认双模型启动、Windows 完成条件未检查非目标模型、OvisOCR2 在 Ubuntu 24.04 的 Python PEP 668 构建失败等问题。

### 开源发布

- 重写中英文 README，补充模型/硬件选择、质量与隐私边界。
- 新增硬件兼容表、Benchmark 规范、Roadmap、贡献指南、支持指南与社区模板。
- 新增 Tag 驱动的 GitHub Release / GHCR 支持镜像工作流。
- 新增 GitHub Social Preview、中文长文、视频脚本和中英文发布文案。

## 2026-08-15

### 安全与架构

- 将 Docker 模型控制从公开 Web 容器拆分到内部 token 鉴权的 `pandocr-controller`，Docker socket 不再暴露给 WebUI。
- 将 LibreOffice 拆分到非 root、只读运行的 `pandocr-office-converter`，并为 Web、控制器和转换器启用能力裁剪与内部网络隔离。
- 默认服务仅监听本机，收紧来源校验；API token 改为会话级存储。
- 更新 PDF.js、DOMPurify 等浏览器依赖，关闭 PDF.js 动态代码执行，Markdown 消毒器不可用时采用纯文本安全降级。

### 稳定性与资源保护

- 区分 HTTP 请求体上限与解码后文件上限，修复 base64 膨胀导致的误拒绝。
- OvisOCR2 改为逐页渲染和推理 PDF；Unlimited-OCR 增加 PDF 总渲染像素预算。
- Office 转换增加上传限制、超时和错误响应处理。
- 修复测试收集顺序导致的环境变量和任务目录不确定问题。

### 供应链与质量

- 锁定 Python、CUDA、Paddle 镜像 digest，以及 Unlimited-OCR、OvisOCR2 模型 revision。
- 移除 `curl | sh` 安装流程，锁定 GitHub Actions 提交哈希。
- 升级存在安全公告的 Python 和 npm 依赖；CI 覆盖所有依赖清单的漏洞扫描。
- 浏览器 vendor 文件改为由 `package-lock.json` 自动同步和校验，并收录第三方许可证。
- 新增 Apache-2.0 许可证、安全策略、第三方声明和逐文件覆盖率门禁。

### 验证

- Python：168 项测试通过，总覆盖率 97%。
- 前端：38 项测试通过，语句覆盖率 99.91%，分支覆盖率 98.41%。
- npm 与全部 Python 依赖清单扫描结果均为 0 个已知漏洞。
- Docker Compose 配置、OpenAPI 快照、脚本语法及 Web/Office 镜像构建验证通过。
