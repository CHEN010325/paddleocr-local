# 硬件兼容表

这张表区分“项目预检下限”和“社区真实报告”。预检下限只表示允许尝试启动，不是速度或长文档稳定性保证。

## 项目预检参考

| 模型 | 参考下限 | 推荐配置 | CPU / Apple Silicon |
| --- | ---: | ---: | --- |
| PaddleOCR-VL 1.6 | NVIDIA 11,264 MiB | 12～16 GB+ | CPU 路径不作为默认推荐 |
| PP-OCRv6 | GPU 路径 NVIDIA 4,096 MiB | GPU 4 GB+；CPU 按文档测量内存 | 独立 CPU Compose 与 Python 路径，见 [部署说明](cpu.md) |
| OvisOCR3 | NVIDIA 7,680 MiB | 8～16 GB+ | Apple Silicon 默认 MLX |
| HPD-Parsing | NVIDIA 7,680 MiB | 12 GB+ | 当前不支持 Apple Silicon 一键部署 |
| NaviDC-OCR | NVIDIA 7,680 MiB | 12 GB+ | CUDA / vLLM 异步（默认；可选 Transformers）；模型权重约 1.2B BF16 |

长页面、高 DPI 图片、大批页数和较高 `max_tokens` 会增加显存。8 GB 设备优先使用每批 1 页和低显存参数。

## 已知验证信息

| 平台 | 模型 | 状态 | 说明 | 来源 |
| --- | --- | --- | --- | --- |
| RTX 4070 Laptop 8 GB / Ubuntu 24 | PaddleOCR-VL 1.6 | 不满足当前预检 | 推荐 PP-OCRv6 或 8 GB 适配模型 | Issue #7 |
| Apple M1/M2/M3/M4 | OvisOCR3 | 提供 MLX 路径 | 仍需更多芯片/内存/速度报告 | 项目安装脚本 |
| RTX 30/40 | 五个默认模型 | 提供 Docker 路径 | 按模型显存要求选择 | `env.docker` |
| RTX 50 / Blackwell | 五个默认模型 | 提供 SM120 路径 | 使用 `env.txt` | `env.txt` |
| Windows x86-64 / Linux Docker | PP-OCRv6 CPU | 本次实测通过 | 图片、双页 PDF；空模型卷首次部署 | [验收记录](issue-validation.md) |
| RTX 5070 Ti 16 GB / Linux Docker | PaddleOCR-VL 1.6 | 本次实测通过 | 独立 API 经 WebUI 图片与双页 PDF | [验收记录](issue-validation.md) |

没有社区报告的组合不会被标记为“已实测”。

## 提交兼容性结果

请新建 Hardware Report Issue，并提供以下内容：

```text
项目版本/Commit:
系统与架构:
GPU/芯片:
显存/统一内存:
驱动、CUDA、Docker 版本:
模型与后端:
文档类型和页数（不要上传敏感文件）:
是否启动成功:
首次结果耗时:
峰值显存（如可获得）:
必要的脱敏日志:
```

维护者确认信息完整后会把结果加入表格。
