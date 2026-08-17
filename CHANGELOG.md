# 更新日志

## Unreleased

### 多模型工作台

- 新增同一源文件的多模型顺序解析、独立任务、耗时记录、并排查看和 Markdown 对比报告下载。
- 新增 Markdown 人工编辑并持久化修订结果。
- 新增服务端本地源文件克隆，创建对比任务时不再通过浏览器重复上传大文件。

### 自动化

- 新增稳定的模型无关接口 `POST /api/parse`，通过 `modelId` 选择已启用模型。
- 新增 `pandocr_cli.py`，支持环境诊断、批量解析、多模型对比和 Watch Folder。

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
