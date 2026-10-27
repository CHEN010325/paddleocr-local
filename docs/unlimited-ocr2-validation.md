# Unlimited-OCR-2 验证记录

日期：2026-10-28。Windows / Docker Desktop，RTX 5070 Ti 16GB。

## 结果

Transformers 和官方专用 SGLang 均完成真实 GPU 端到端验收。
每条后端分别测试图片、两页扫描 PDF、两页 Office 文档的同步和流式识别，
共 12 组；36 份 DOCX、XLSX、可搜索 PDF 导出均通过内容核验。
另外通过浏览器实际上传图片、自动流式识别、刷新恢复和 DOCX 下载。

| 用例 | Transformers | SGLang |
| --- | ---: | ---: |
| 中英文图片，流式 | 18.83 秒 | 8.97 秒 |
| 中英文图片，同步 | 18.56 秒 | 7.80 秒 |
| 两页扫描 PDF，流式 | 37.39 秒 | 16.16 秒 |
| 两页扫描 PDF，同步 | 32.81 秒 | 14.42 秒 |
| 两页 Office，流式 | 21.39 秒 | 8.31 秒 |
| 两页 Office，同步 | 20.23 秒 | 8.34 秒 |

计时包含识别、保存/读取任务及三种导出，不包含模型启动与 Office 转 PDF。
这些是本机小样本用例的耗时，不是通用性能基准。

核验内容：

- 中英文、ALPHA/BETA/GAMMA/DELTA 编号、第二页内容和页序保留。
- 统一解析结果包含页面坐标，正文没有 PAGE/BLOCK/FORK 等布局标记。
- 源文件上传、任务保存和恢复成功。
- DOCX 正文及表格文字完整；XLSX 包含 Apples 和数量 12 等实际单元格内容。
- 可搜索 PDF 页数正确，能够提取相应编号及年份。
- 识别期间请求切换后端返回 409，当前任务继续完成。
- 最终仅 Unlimited-OCR-2 运行，`exclusivityViolation=false`；SGLang 就绪，
  Transformers `modelLoaded=false`，无活动 OCR 或遗留验收 lease。
- 浏览器上传 `ui-uocr2.png` 后实际识别完成；刷新恢复成功；网页下载的 DOCX 内容通过检查。

## 接入与修复

- 修改前执行 `git fetch origin --prune`：本地 `main` 与 `origin/main` 都是
  `8268eabf60f52ffe63bc7d36eb19c226f17e96d1`，领先/落后均为 0，工作区干净。
  本次接入在该基线之上完成。
- 第二代已替换第一代的模型入口、部署默认值和下载 revision。
  内部 `unlimited-ocr` ID、API/profile 名称保留，以兼容已有任务。
- 权重和远程代码固定为 `9cf077c4b206e0e2a31879df937b0b0bcba3661c`；
  两条 GPU 镜像实际构建完成，使用隔离的依赖环境和新的 `pandocr-unlimited-ocr2-*` 名称。
- 第一代容器按旧镜像名称检测，停止并两次确认后重建；删除容器使用 `v=0` 保留挂载数据。
- 单页与多页采用官方第二代默认 `Multi page parsing.` 指令；
  兼容第二代真实输出的 PAGE + BLOCK/FORK 布局、分页坐标及多页返回值。
- 修复 Windows Docker Desktop 动态部署路径：Linux 控制器使用 `PureWindowsPath`
  解析宿主机的反斜杠路径。实际新容器挂载到项目缓存，日志确认命中本地快照、跳过下载。
- 修复固定 SGLang wheel 的 eager 解码配置问题：关闭 CUDA Graph 时，还必须关闭
  `SGLANG_UOCR_GRAPH_METADATA_REFRESH` 和 `SGLANG_UOCR_GRAPH_METADATA_INPUTS`。
  默认值会误走 graph 元数据刷新分支，缺失坐标并跳过 Page2D RoPE，造成坐标循环。
  关闭后，同一 gundam/base 短输出探针恢复正常，随后六组完整 SGLang 用例全部通过。
- 未闭合坐标头不再作为流式正文显示；异常坐标循环提前触发保护。
- Windows `SkipBuild` 路径不再因 Compose 的 `pull_policy: build` 重复构建。

官方来源：[模型及教程](https://huggingface.co/baidu/Unlimited-OCR-2)、
[官方仓库](https://github.com/baidu/Unlimited-OCR)。

## 启动和准确性边界

- Windows 挂载目录读取权重较慢。本轮 Transformers 从激活到权重就绪约 668 秒；
  SGLang 修正配置后权重加载约 218 秒，另有 tokenizer、缓存分配和首次内核编译开销。
  表中识别耗时没有混入这部分启动等待。
- 初始无边框 Office 两列表格被 Transformers 识别成普通文字，XLSX 按现有规则返回
  `Task has no Markdown tables`（422）。补验的有边框表格两后端均通过；不能据此声称所有无框表格都能恢复。
- 原生 macOS MPS、其他显卡、长文档上限和安装包升级尚未做实机验收。
- Docker 曾在首次真实推理期间退出，恢复时报告 OTel 残留 socket；不能据此断言由模型造成。
  用户退出 Docker 后，可逆改名运行目录恢复引擎，备份保留在
  `C:\Users\chenshulin\AppData\Local\Docker\run.uocr2-backup-20261028-0345`。
  恢复后旧镜像/容器未出现在列表，本次服务重新构建；项目、原有任务与权重缓存保留。
- 用户再次明确限定清理范围后，已按核对过的绝对路径逐项删除第一代模型缓存、对应锁目录
  和 `Unlimited_hyphen_OCR` 代码缓存，共约 6.68 GB；三处旧目录均不存在。
  第二代的 51 个文件大小及修改时间保持一致，项目 `data` 的 11 个文件 SHA-256 保持一致，
  SGLang 仍正常就绪。清理核验记录见 `tmp/uocr1-cleanup-receipt.json`。

## 自动检查和证据

最终 `scripts/check-local.sh` 通过：Python 356 项、1 项跳过、32 个 subtest；
前端 54 项；脚本语法、macOS 脚本选择逻辑和 OpenAPI 快照检查通过。
Apple Silicon 专属 dry run 在 Windows 跳过，不作为原生 Mac 验收。
代码另经独立审查，根因修复均有回归测试。六份真实 Transformers 响应用最终解析器重放，结果一致。

本机证据（`tmp/` 被 Git 忽略，不进入发布包）：

- `tmp/uocr2-acceptance/{transformers,sglang}/summary.json`
- 两个目录的识别 JSON、流式事件、三种导出、`export-content-verification.json` 及运行状态快照。
- `tmp/uocr2-acceptance/transformers/office-borderless/` 保留无框表格识别结果。
- `tmp/uocr2-acceptance/switch-during-ocr.json`、`ui-verified.jpg`、`ui-download.docx`。
- `tmp/uocr2-sg-probes.log`、`tmp/uocr2-sg-probes-fixed.log`：相同短输出探针修复前后结果。
- `tmp/uocr2-final-quality-verified.log` 和两条后端构建/部署日志。
- `tmp/uocr2_live_acceptance.py`、`tmp/uocr2_verify_exports.py`：实际接口及导出内容验证脚本。

12 个临时 API 验收任务已按精确 ID 清理；原有任务及一份网页验收样本保留。
浏览器下载文件已从 Downloads 移入本机验收证据目录。
