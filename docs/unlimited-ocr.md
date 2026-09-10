# Unlimited-OCR 为什么没有出现在下拉框

Issue #6 的现象与当前默认目录一致：Unlimited-OCR 保留为按需实验适配器，
不属于五模型默认产品线。前端从 `/api/models` 获取目录，不在浏览器写死模型列表。
只启动容器而不更新 WebUI/控制器目录，不会增加下拉选项。

## macOS 按需启用

```sh
./macos-one-click.command --model unlimited-ocr
```

安装器使用独立依赖环境，不与 MLX-VLM 的 Transformers 版本混装。

## 已有本地或远程 Unlimited-OCR 服务

建议通过[外部引擎配置](external-engines.md)接入。
若为另一个 WebUI 的 `/api/parse`，使用 `unified` 并设置 `model: unlimited-ocr`。
原始 `unlimited_ocr_adapter.py` 的 `/ocr` 不是 Paddle layout-parsing 协议，
应先通过本项目 WebUI 的统一接口接入，不能直接选择 `paddle` 协议。

管理式部署必须让 WebUI 和控制器使用相同的
`PANDOCR_MODEL_CATALOG`（包含 `unlimited-ocr`），并启用对应 Compose profile。
仅设置 `PANDOCR_ENABLE_UNLIMITED_OCR=1` 不会覆盖一个显式的模型目录。
不要同时启动多个 GPU 模型；通过原有控制器执行部署和切换。

适配器的可见性和契约有回归测试；实验后端不代表在所有 GPU、macOS 或文档上完成了稳定性认证。
