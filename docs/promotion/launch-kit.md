# Launch Kit

## GitHub About

```text
Local-first document parsing workbench for five OCR models. Parse PDF, images, Word and PowerPoint with one WebUI, model switching, comparison reports, CLI and Apple Silicon support.
```

建议 Topics：

```text
ocr document-ai local-ai self-hosted pdf-to-markdown paddleocr paddleocr-vl mlx fastapi docker webui
```

Social Preview：`docs/assets/social-preview.jpg`。在 Repository Settings → Social preview 手动上传。

## Show HN

标题：

```text
Show HN: PaddleOCR Local – a local workbench for comparing five OCR models
```

正文：

```text
I built PaddleOCR Local because running a document model is usually much harder than trying its demo.

It provides one local-first WebUI for PaddleOCR-VL 1.6, PP-OCRv6, Unlimited-OCR, OvisOCR2, and HPD-Parsing. It accepts PDFs, images, Word, and PowerPoint, keeps task history locally, switches models on a single GPU, performs VRAM preflight checks, and exports Markdown/JSON.

The latest work adds sequential multi-model comparison reports, a stable /api/parse endpoint, a CLI, and watch-folder automation. Apple Silicon is supported through native/MLX paths.

It is Apache-2.0 and does not enable document telemetry. Model quality varies by document and hardware, so the repository includes a reproducible benchmark/reporting format instead of claiming a universal winner.

GitHub: https://github.com/CHEN010325/paddleocr-local
```

## Reddit r/selfhosted

标题：

```text
PaddleOCR Local: self-hosted PDF/Office to Markdown with five switchable OCR models
```

正文：

```text
I have been building a local-first document parsing workbench for people who want OCR without uploading private files to a cloud service.

It supports five independent models behind one UI, PDF/image/Word/PowerPoint input, local task history, Markdown/JSON export, single-GPU model switching, and VRAM checks. Windows/Linux NVIDIA and Apple Silicon paths are documented.

I recently added sequential model comparison reports plus a CLI and watch-folder mode. There is no default telemetry. Public exposure still requires an API token, TLS, and the usual reverse-proxy controls.

Repo: https://github.com/CHEN010325/paddleocr-local

I would especially appreciate reproducible hardware reports and feedback on the install path.
```

## V2EX / Linux.do

标题：

```text
[开源] 本地多模型 OCR 工作台：支持 5 个模型、PDF/Office、显存预检和 Apple Silicon
```

正文：

```text
最近把自己的 PaddleOCR Local 做了一轮整理。

目前支持 PaddleOCR-VL 1.6、PP-OCRv6、Unlimited-OCR、OvisOCR2、HPD-Parsing，输入支持 PDF、图片、Word、PPT。单卡机器只启动当前模型，切换时自动释放显存；Windows NVIDIA 和 Apple Silicon 都有一键脚本。

新增加了同文档多模型顺序对比、Markdown 编辑、统一 /api/parse、CLI 和 Watch Folder。项目不会默认上传文档或收集遥测。

这次特别想征集不同显卡和 Apple 芯片的兼容报告。仓库里提供了 Hardware Report 表单和记录规范，敏感文档不用上传。

GitHub：https://github.com/CHEN010325/paddleocr-local
```

## 发布节奏

1. GitHub Release 与演示 GIF 同日发布。
2. 次日发中文长文和 B 站视频。
3. 第三天发 Reddit / Show HN，重点征集安装反馈。
4. 一周后发布第一份公开硬件兼容汇总。
5. 每接入一个新模型，只发布一篇真实测试，不重复发项目介绍。

## 上游收录请求

```text
Hi, I maintain PaddleOCR Local, an Apache-2.0 local-first WebUI and CLI that integrates <MODEL> with four other OCR/document models. It includes on-demand deployment, single-GPU switching, local task history, comparison reports, and a documented hardware-report format.

Would you consider listing it in your community tools/ecosystem section? I am happy to submit a small documentation PR and keep the integration current.

Repository: https://github.com/CHEN010325/paddleocr-local
```
