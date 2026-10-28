# Windows Docker socket 自动恢复：真实重启验收

日期：2026-10-28。Windows、Docker Desktop 4.40.0、Docker CLI 28.0.4、
Windows PowerShell 5.1、RTX 5070 Ti 16GB。

## 实际流程与结果

重启前确认没有 OCR 作业和控制器租约，原有两个任务均已完成。
记录任务、源文件和 Unlimited-OCR-2 缓存的 SHA-256，以及全部模型缓存的文件清单。
停止本项目的五个运行容器，再结束当前会话的 Docker Desktop 进程。
Docker 自身留下的 `userAnalyticsOtlpHttp.sock` 是实际 Windows socket 重解析点。

通过现有镜像与权重运行完整一键流程：

```powershell
.\unlimited-ocr2-one-click.bat -SkipBuild -SkipPull -NoOpen
```

| 检查 | 实测结果 |
| --- | --- |
| 实际故障识别 | Docker 启动产生 OTel stale socket 错误 |
| 自动恢复 | 日志记录 `repairing this socket once`，仅执行一次 |
| Docker 恢复 | 引擎正常提供服务，BF16 CUDA 探测通过 |
| 一键部署 | 完成容器创建、模型加载和就绪检查，退出码 0 |
| 模型状态 | Unlimited-OCR-2 SGLang 就绪，独占模型运行 |
| 页面上传 | 新建双页 PDF 测试任务，两页完成，无解析错误 |
| 页面内容 | 中英文、表格字段、公式、分页均核验通过 |
| 刷新恢复 | 完成结果从任务存储恢复，浏览器无 error 日志 |
| DOCX 导出 | 导出 38332 字节，压缩包正文中的两页标记和表格字段核验通过 |
| 原有数据 | 93 个文件的 SHA-256 全部一致，两个原有任务记录完全相同 |
| 模型缓存 | 6557 个既有文件大小和修改时间一致，无删除、无新增 |

SHA-256 范围包含 Unlimited-OCR-2 缓存全部文件、原有任务文件、源文件和运行设置。
其他模型缓存按文件清单、大小和修改时间核验。控制器的临时租约状态不按字节比对。
双页网页测试采用当前页面配置分为两批，耗时 18.062 秒，包含本次冷启动后的首次生成预热。
模型权重读取和 CUDA Graph 捕获另计，不属于该解析耗时。
本次只清理精确 ID 对应的自建网页验收任务，原有 17 页和 24 页任务保留。

## 本次实机验证补修

最初重启尝试暴露两个此前隔离测试未覆盖的行为，已修复后重新执行上述完整流程：

1. Docker CLI 在引擎管道不存在时，`docker info --format` 可能退出 0，仅输出换行。
   就绪检测同时要求合法服务端版本，避免把未启动的引擎判断为可用。
2. Windows PowerShell 5.1 调用原生命令时会剥掉 Go 模板的内嵌引号。
   Context 查询改为 `{{.Endpoints.docker.Host}}`，保持远程 context 保护有效。

补修完成后，411 个 Python 测试、32 个子测试、56 个前端测试通过，1 项按平台跳过；
整体覆盖率 96%，文件覆盖率门槛通过。恢复专项共 31 项测试，并经独立只读代码复核。
本次验收使用已有镜像和缓存，不替代全新机器首次下载、构建的验收。

本地证据位于忽略提交的 `tmp/socket-restart-acceptance/`：完整启动日志、退出码、
重启前清单、保全核验、网页结果摘要、测试与覆盖率日志以及导出样本。
