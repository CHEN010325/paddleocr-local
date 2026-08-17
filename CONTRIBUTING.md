# 参与贡献

感谢你帮助 PaddleOCR Local 变得更稳定、更易用。代码、文档、硬件报告、测试样本说明和模型适配器都欢迎提交。

## 开始之前

1. 先搜索现有 Issue，避免重复工作。
2. 较大的功能先开 Feature Request，说明使用场景和设计范围。
3. 不要提交模型权重、真实敏感文档、Token、任务历史或生成缓存。
4. 新依赖必须说明必要性、许可证和安全影响。

## 本地开发

```bash
python -m pip install -r requirements-dev.txt
npm ci
npm test
python -m pytest -q
```

完整质量门禁：

```bash
make check
```

前端修改还应执行：

```bash
node --check static/app.js
npm run coverage:frontend
```

## Pull Request 要求

- 一个 PR 尽量只解决一个主题。
- 描述用户影响、实现方式和验证方法。
- UI 改动附前后截图或短视频。
- 新功能包含正常路径和失败路径测试。
- 更新相关 README、QUICKSTART、CHANGELOG 或 API 文档。
- 不降低既有覆盖率和安全限制。

## 新增模型适配器

模型适配器应把模型差异限制在独立模块内，向 Web 层返回可归一化的 Markdown / JSON：

1. 在独立适配器文件中实现健康检查和推理入口。
2. 明确模型来源、许可证、固定 revision 和硬件要求。
3. 设置请求体、页数、像素、Token 和超时上限。
4. 增加 Docker / macOS 启动路径和运行时状态。
5. 增加单元测试，不要求 CI 下载真实权重。
6. 在兼容矩阵中记录至少一个验证环境。

开 Feature Request 时请包含：模型仓库、许可证、官方推理示例、最小显存、输出格式和你愿意维护的平台。

## 硬件兼容报告

兼容报告不要求改代码。请使用 Hardware Report Issue 表单，至少提供：

- 操作系统和架构
- GPU / Apple 芯片及显存或统一内存
- Docker、驱动和 CUDA 版本
- 模型、后端和提交版本
- 是否启动成功、首次结果耗时和脱敏日志

禁止上传含个人信息或商业机密的原始文档。

## 提交风格

使用简短、可读的提交信息，例如：

```text
Add OvisOCR2 hardware diagnostics
Fix PDF retry state recovery
Document RTX 3060 compatibility
```
