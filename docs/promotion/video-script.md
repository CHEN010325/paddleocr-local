# B 站 / YouTube 演示脚本（4～6 分钟）

## 标题

一个界面跑 5 个 OCR 模型：PDF、Word、PPT 本地转 Markdown

## 封面文字

```text
5 个 OCR 模型
一个本地工作台
```

## 分镜

### 0:00～0:20 先展示结果

画面：拖入一份包含表格和公式的公开 PDF，左右显示原文与 Markdown。

旁白：

> 这是同一份 PDF。左边是原文，右边是本地模型解析出的 Markdown。整个过程不需要把文档上传到第三方云服务。

### 0:20～0:55 痛点

画面：快速切换 Docker、CUDA、不同模型仓库页面。

旁白：

> 开源 OCR 模型很多，但每款模型的安装方式、显存要求和输出格式都不一样。真正麻烦的往往不是模型能力，而是怎么把它稳定跑起来。

### 0:55～1:35 项目定位

画面：WebUI 模型选择器依次展开五个模型。

旁白：

> PaddleOCR Local 把 PaddleOCR-VL、PP-OCRv6、Unlimited-OCR、OvisOCR2 和 HPD-Parsing 放进同一套本地工作台。支持 PDF、图片、Word 和 PowerPoint。

### 1:35～2:15 一键部署

画面：Windows Dry Run，再展示 macOS 命令。

```powershell
.\windows-one-click.bat -DryRun
```

旁白：

> 安装脚本只部署你选择的模型。单 GPU 切换模型时会先释放其他模型显存。启动前还会检查显卡和空闲显存。

### 2:15～3:10 单模型结果

画面：选择当前模型，上传文件，展示源文件、Markdown、JSON 和下载结果。

旁白：

> 每次只运行用户当前选择的一个逻辑模型。解析完成后可以查看 Markdown 或 JSON，并将结果和图片资源下载到本地。

### 3:10～3:50 CLI 与目录监听

画面：终端运行 doctor、parse、watch。

```bash
python pandocr_cli.py doctor
python pandocr_cli.py parse paper.pdf --model pp-ocrv6
python pandocr_cli.py watch incoming --model pp-ocrv6
```

旁白：

> 如果你想接扫描仪、NAS 或自动化流程，也可以不用打开网页，直接调用统一接口或 Watch Folder。

### 3:50～4:30 硬件选择

画面：README 硬件表和 WebUI 显存提示。

旁白：

> 没有独显时，Apple Silicon 可以使用 OvisOCR2 MLX；Windows/Linux CPU Lite 仍在路线图中。复杂文档模型通常需要 8 到 16GB 显存，项目会明确提示，而不是等容器崩溃后只留下一句启动失败。

### 4:30～结束

画面：GitHub 首页、Roadmap、Hardware Report。

旁白：

> 项目采用 Apache 2.0 开源。如果你愿意分享自己的显卡和模型运行结果，可以提交硬件兼容报告。项目地址在简介里，觉得有用欢迎 Star。

## 录制检查

- 只使用可公开分发的测试 PDF。
- 镜头中隐藏用户名、Token、内网地址和 Docker 私有镜像信息。
- 等待模型下载和加载的过程使用明确的时间跳切，不伪装成瞬间完成。
- 没有跑完的模型不要写“实测通过”。
