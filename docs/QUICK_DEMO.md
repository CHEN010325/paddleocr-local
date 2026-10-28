# 60 秒体验指南

这份指南用于第一次了解项目，不要求先下载模型权重。请按所在平台先执行下面对应的诊断或 Dry Run 命令。

## 你会得到什么

```text
上传 PDF / 图片 / Word / PowerPoint
        ↓
采用显存预检推荐的模型，或手动选择
        ↓
按页显示进度并保留任务历史
        ↓
查看原文与结构化结果
        ↓
下载 DOCX、可搜索 PDF、HTML、Markdown、JSON 或表格
```

## 推荐第一次运行

### Apple Silicon

```bash
make doctor
./macos-one-click.command --model ovisocr2 --dry-run
./macos-one-click.command --model ovisocr2
```

### Windows + NVIDIA

```powershell
.\windows-one-click.bat -DryRun
.\windows-one-click.bat
```

### Linux / Docker

```bash
make doctor
cp env.docker env.txt
./build.sh
./deploy.sh
```

服务启动后打开 <http://localhost:8000>，上传一份不含敏感信息的 PDF，先使用默认模型完成一次单页解析。

## 如何选择模型

| 文档类型 | 优先尝试 |
| --- | --- |
| 普通文字、低显存 | PP-OCRv6 |
| 论文、复杂版面、表格和公式 | PaddleOCR-VL 1.6 |
| Apple Silicon 或文档理解 | OvisOCR3 |
| 高质量文档解析 | HPD-Parsing |
| 复杂文档、版面、表格和公式 | NaviDC-OCR |

模型切换会先停止旧模型并等待显存释放，因此单卡机器可能出现几秒空窗，这是安全门禁的一部分。
在 NVIDIA Docker 路径中，顶部显存预检会综合可运行状态、推荐显存和模型能力给出一个推荐模型；如果它不是当前模型，可以直接点击“使用推荐模型”。

## 导出结果

解析完成后打开结果区的“导出”菜单：

- 可编辑 DOCX：保留标题、正文、列表、表格和提取图片，适合继续在 Word 中修改。
- 可搜索 PDF：保留原 PDF / 图片视觉内容，并添加不可见 OCR 文本层。
- 表格 CSV / XLSX：支持单表或多表导出，并阻止电子表格公式注入；XLSX 会为每个表格建立独立工作表并冻结表头。
- 单文件 HTML：图片内嵌，复制到离线机器也能直接打开。

## 反馈一份可复现结果

如果解析成功或失败，建议记录：

- 项目版本或 Commit
- 操作系统、GPU / 芯片、显存或统一内存
- 模型名称
- 文档类型和页数
- 首次启动时间、解析耗时和失败批次数
- macOS / Linux 的 `make doctor` 输出，或 Windows 的 `.\windows-one-click.bat -DryRun` 输出，以及必要的脱敏日志

可以通过 [Hardware Report Issue](../.github/ISSUE_TEMPLATE/hardware.yml) 提交硬件结果，敏感文档不需要上传。
