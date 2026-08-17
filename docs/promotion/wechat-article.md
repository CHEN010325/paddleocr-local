# 我把 5 个开源 OCR 模型装进一个本地工作台：PDF 转 Markdown，不用再来回折腾了

> 备选标题：
>
> 1. 一个界面跑 5 个 OCR 模型：这个开源项目把本地文档解析做成了工作台
> 2. 8GB 显卡也别急着放弃：本地 OCR 模型怎么选，我做了个自动推荐工具
> 3. PDF、Word、PPT 本地转 Markdown：数据不上传，模型还能随时切换
> 4. 别再手搓 Docker 命令了，5 款文档解析模型现在可以一键部署
> 5. 从 PaddleOCR-VL 到 OvisOCR2：一个本地 WebUI 解决安装、切换和导出

大家好，我是晴天。

如果你折腾过开源 OCR，应该很熟悉这种场面：

模型仓库找到了，Demo 看起来也不错，但真正放到自己电脑上，马上就会遇到 CUDA、Docker、显存、模型权重和接口格式等一连串问题。

更麻烦的是，每个模型都有自己的启动方式。想比较两款模型，往往要重新搭两套环境。

所以我做了一个开源项目：**PaddleOCR Local**。

它把 PaddleOCR-VL 1.6、PP-OCRv6、Unlimited-OCR、OvisOCR2 和 HPD-Parsing 放进同一个本地文档解析工作台。

上传 PDF、图片、Word 或 PowerPoint，选择模型，就能在浏览器中查看原文件与解析结果，并导出 Markdown 和 JSON。

## 先说结论：它解决的不是“有没有 OCR”，而是“怎么真正用起来”

PaddleOCR Local 目前最值得关注的有五点：

- 文档和结果保存在本机，不需要上传第三方云服务。
- 五款模型共用一套界面和任务历史。
- 单 GPU 只加载当前模型，切换时自动释放其他模型显存。
- 启动前检查显存，并告诉你当前硬件更适合哪些模型。
- Windows、Linux NVIDIA 和 Apple Silicon 都有对应部署路径。

这点很关键。

很多开源项目展示的是“模型能跑”，但普通用户真正需要的是：文件怎么传、失败怎么恢复、结果怎么查看、怎么导出，以及显存不够时该怎么办。

## 同一份文档，现在可以顺序跑多个模型

项目增加了一个“多模型对比”入口。

选择两款或更多模型后，系统会依次切换模型并解析同一份源文件。它会记录：

- 模型名称
- 成功或失败状态
- 页数
- 解析耗时
- 输出字符数
- 每款模型的完整 Markdown

最后可以复制或下载对比报告。

这里必须说明：解析耗时只适用于当次硬件和参数，输出字符数也不等于准确率。项目不会把少量体验包装成权威 Benchmark。

真正的模型质量对比，还需要公开测试文件、固定模型版本和人工评分规则。仓库里已经给出一套 Benchmark 记录规范，欢迎社区一起补充。

## 低显存机器，也会得到明确建议

项目不会假装所有模型都能在所有显卡上运行。

目前的参考策略是：

| 场景 | 建议 |
| --- | --- |
| 没有 NVIDIA 显卡 | Apple Silicon 可尝试 OvisOCR2 MLX；Windows/Linux CPU Lite 尚在路线图中 |
| 4～8 GB 显存 | 优先 PP-OCRv6，其他模型使用每批 1 页和低显存参数 |
| 12 GB 以上 | 可以尝试 PaddleOCR-VL 和更多文档解析模型 |
| 复杂论文、表格、公式 | 建议用多模型对比选择结果 |

这些数字是启动兼容参考，不是性能保证。长页面、高分辨率和高 Token 上限都会继续增加显存。

## Windows 和 Mac 怎么开始

Windows + NVIDIA 用户，提前安装支持 GPU 的 Docker Desktop，然后运行：

```powershell
.\windows-one-click.bat
```

Apple Silicon 用户可以运行：

```bash
./macos-one-click.command
```

脚本会询问首次部署哪个模型，只下载和启动你选择的模型。

如果暂时不想下载几个 GB 的镜像，可以先做 Dry Run：

```powershell
.\windows-one-click.bat -DryRun
```

部署完成后，打开：

```text
http://localhost:8000
```

## 不想打开网页，也可以直接用 CLI

查看本机模型和运行状态：

```bash
python pandocr_cli.py doctor
```

解析一份 PDF：

```bash
python pandocr_cli.py parse invoice.pdf --model pp-ocrv6
```

让同一份论文依次运行三款模型：

```bash
python pandocr_cli.py compare paper.pdf \
  --models paddleocr-vl-1.6,ovisocr2,hpd-parsing
```

还可以监听一个目录。扫描仪、NAS 或其他程序只要把新文件放进去，CLI 就会自动解析：

```bash
python pandocr_cli.py watch incoming --model pp-ocrv6 --output parsed
```

## 适合谁，不适合谁

它比较适合：

- 希望文档留在本机的个人和团队
- 经常把论文、财报或扫描件转成 Markdown 的用户
- 想比较不同开源 OCR 模型的开发者
- 需要在内网部署文档解析能力的人

它暂时不适合：

- 追求云服务级高并发、但不愿配置推理服务的团队
- 完全不接受模型下载和本地存储占用的用户
- 希望所有 VLM 都在纯 CPU 上快速运行的用户

项目不会默认收集文档或遥测数据。公网部署仍然需要设置 API Token、TLS 和额外访问控制。

## 最后

我希望这个项目最终不只是一个 PaddleOCR Demo，而是一套真正能用的本地文档解析工作台，也是一座开源 OCR 模型的本地竞技场。

如果你有 RTX 30/40/50、Apple M 系列或其他硬件，欢迎提交一份脱敏的兼容性报告。

项目地址：<https://github.com/CHEN010325/paddleocr-local>

如果它帮你少踩了一个 CUDA 或 Docker 的坑，也欢迎点一个 Star。
