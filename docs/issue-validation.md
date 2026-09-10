# Issue 排查与发布候选验收

验收日期：2026-09-11。范围：仓库全部 7 个 issue（包含已关闭的 #7），读取正文和讨论后逐项核对当前代码。此记录描述本地工作区发布候选；不代表已发布版本，也不代表所有硬件组合已认证。

## 逐项结论

| Issue | 核实结果与处理 | 证据及边界 |
| --- | --- | --- |
| [#1 CPU 模式](https://github.com/CHEN010325/paddleocr-local/issues/1) | 原 GPU Compose 确实要求 NVIDIA；新增独立 PP-OCRv6 CPU 部署，完全移除 GPU 和控制器依赖。真实运行又发现 oneDNN/PIR 错误，CPU 专用配置改用 Paddle 原生执行模式解决。 | Windows 原生 CPU、全新 Linux Docker 镜像及空模型卷均完成图片和双页 PDF 识别。CPU 提供文字识别，不承诺 VL 的复杂版面能力；见 [CPU 部署](cpu.md)。 |
| [#2 macOS Python 安装](https://github.com/CHEN010325/paddleocr-local/issues/2) | 报告中的 Python 3.14 与 Paddle 版本不匹配；现有脚本已包含兼容 Python 选择和环境修复，保留并验证这些修复。 | 9 组 Python 选择脚本用例通过。没有 Apple Silicon 实机，不能据此声称 MLX 模型实际推理通过。 |
| [#3 MLX 超时](https://github.com/CHEN010325/paddleocr-local/issues/3) | 日志根因为依赖导入异常。保留锁定依赖；预检扩展到实际 `mlx_vlm.server` 入口，直接启动也执行预检，等待时检测服务提前退出并限制单次 HTTP 探测时间。 | 入口导入失败回归、安装与启动脚本检查通过。Apple Silicon 实际安装与推理仍需实机验收。 |
| [#6 Unlimited-OCR 选项](https://github.com/CHEN010325/paddleocr-local/issues/6) | 当前默认目录有意排除实验模型，并非 UI 丢失。补全按需启用说明，验证显式目录能正确显示及选择，保留适配器能力。 | 显式目录回归通过；见 [启用说明](unlimited-ocr.md)。没有将未经本次硬件验收的实验模型提升为默认稳定模型。 |
| [#7 8 GB 显卡启动失败](https://github.com/CHEN010325/paddleocr-local/issues/7) | 报告配置低于当前 VL 11,264 MiB 预检要求。保留资源检查，并补测总显存不足与显存暂被占用两种情况；提供 PP-OCRv6/CPU 替代入口。 | 8 GB 与 16 GB 但剩余空间不足的回归通过。不通过降低校验假装模型能够运行。 |
| [#11 脚注丢失](https://github.com/CHEN010325/paddleocr-local/issues/11) | 确认前端请求、显示过滤和 VL YAML 都默认忽略脚注/侧栏。三处统一改为默认保留，新增独立忽略开关并使缓存与设置同步。 | 请求、DOM、配置、DOCX/搜索 PDF 回归通过；真实 CPU/GPU 样例保留底部文字。合成样例不代表所有论文的 footnote 分类准确率；模型误识别仍可能发生。历史识别已丢失的内容需重新解析。 |
| [#12 UI 与模型解耦](https://github.com/CHEN010325/paddleocr-local/issues/12) | 保留并完善工作区已有外部引擎实现；配置驱动目录支持 OpenAI vision、Paddle layout-parsing 与统一 API，独立服务无需 Docker 控制权。补全非法端口、总请求超时和取消时资源释放。 | 外部引擎模块覆盖率 99%；真实独立 GPU Paddle API 经 WebUI API 完成图片/双页 PDF。OpenAI/unified 的协议与故障测试使用模拟服务，未对所有供应商实测。见 [配置说明](external-engines.md)。 |

## 真实运行记录

本次主机：Windows x86-64，Python 3.12.7，Docker Engine 28.0.4 / Linux containers，RTX 5070 Ti 16 GB。以下耗时仅为合成小样例的单次记录，不是性能基准或 SLA。

| 路径 | 图片 | 双页 PDF | 检查 |
| --- | ---: | ---: | --- |
| Windows 原生 PP-OCRv6 CPU（可移植测试样例） | 7.97 s | 14.73 s | 金额、底部文字、页数 |
| Linux Docker PP-OCRv6 CPU（全新构建和模型卷） | 7.66 s | 7.83 s | 金额、底部文字、页数；自定义映射端口 18081 |
| 独立 PaddleOCR-VL 1.6 GPU API → 外部引擎 WebUI | 77.80 s（首次请求） | 1.11 s（热服务） | 金额、底部文字、页数；不同 API/Web 端口 |

浏览器实际上传双页 PDF 并识别，下载 DOCX 与可搜索 PDF 后读取验证底部文字及 PDF 页数。修复浏览器发现的自定义端口来源检查 403，以及结果栏按钮挤压问题；1280 px 截图复核，390 px 页面无横向溢出。

可复跑 CPU 验收：`python scripts/smoke-cpu.py --url http://127.0.0.1:8000`。脚本生成非私密样例，不依赖 Windows 字体或本机文件。

## 自动检查

- `scripts/check-local.sh`：336 个 Python 测试及 32 个子测试通过，1 个可选本地样本测试跳过；54 个前端测试通过。Python/JavaScript/Shell 语法、前端 vendor 文件、macOS Python/模型选择脚本、OpenAPI 快照均通过。
- `scripts/check-coverage.sh` 全部通过：Python 总覆盖率 96%，主服务 97%、外部引擎 99%、导出 96%；前端行覆盖率 98.91%、分支覆盖率 96.75%。HPD 适配器沿用 84% 的既有门槛，不以总覆盖率掩盖该限制。
- `npm audit` 和全部 Web/macOS requirements 的严格 `pip_audit` 无已知漏洞。pypdf 更新为 6.16.1；OvisOCR2 与实验 HPD macOS 单设备加载去除无需使用的 Accelerate 依赖及已废弃参数。检查了缓存中的 HPD 自定义模型源码未引用 Accelerate。
- CI 配置新增 Windows/macOS/Linux 检查矩阵、HPD requirements 审计及干净 CPU Docker 实际推理作业。远端执行结果以对应提交的 GitHub Actions 记录为准，不能把添加工作流当作验证已通过。

## 发布边界

上述问题处理与本地验收纳入本次源码提交，保留了开始时工作区已有的改动。已按维护者要求逐条公开回复全部 7 个 issue，并关闭原先开放的 6 个；#7 补充回复后保持关闭。提交推送后，各 issue 的最新回复提供实际提交链接。源码修复与版本发布分别记录，本次不创建 Release/tag，也不将尚未执行的实机验证描述为通过。

正式稳定发布仍须目标提交的三平台 CI 全绿，以及 Apple Silicon 实机安装/MLX 推理记录。Linux ARM64、其他 NVIDIA 架构、不同内存容量、代理/离线环境及长文档压力不在本次实测范围；实验模型继续标为实验。硬件能力不足应明确失败或选择较轻模型，不能保证任意电脑运行任意模型。

本地原始日志位于忽略目录 `tmp/issue-audit-*`，浏览器截图在 `output/playwright/issue-audit-cpu-fixed.png`；正式发布时应将可公开的日志随对应 CI/验收记录留档，不包含用户文档或密钥。

## 默认部署回归复核

后续试用发现：使用单引擎配置启动的外部 API 模式替换了五模型目录。原因是试用入口选择错误，并非默认 Docker 目录被删。已停止临时 API 试用服务，使用正式 Windows 一键脚本恢复完整部署，重建最新 WebUI；真实浏览器确认五个默认模型，控制器可用，PaddleOCR-VL 1.6 是唯一运行且就绪的 GPU 模型。临时引擎配置仅在忽略目录 `tmp/`，不随代码提交。

复核工作区改动涉及的服务路由、前端选择/解析选项、脚注过滤、启动脚本、依赖、CPU 配置、CI、测试和文档；清理状态提示中的重复返回分支及 Windows 脚本重复赋值。未发现需撤销的 CPU/脚注修复，但此前单引擎实测不足以覆盖默认产品流程，已补上正式部署验证及五模型契约测试。README 明确默认部署和可选 API 模式的区别。

新增测试通过正式 Compose 配置分别导入 WebUI/控制器环境，验证双方都返回五模型且未启用外部 API 目录。最新完整检查：337 个 Python 测试、32 个子测试及 54 个前端测试通过，1 个可选样本测试跳过。原始日志：`tmp/review-regression-quality.log`、`tmp/full-ui-start.log`。本轮没有逐个启动其余四种模型，已有切换/部署自动测试不等于这四种模型的本次实机推理验收。
