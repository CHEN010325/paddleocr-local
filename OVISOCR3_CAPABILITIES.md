# OvisOCR3 自动文档解析

工作台只有一个文档解析入口。上传图片、PDF 或 Office 文件后自动处理内容，不需要选择 OCR、图表、抽取或跨页表格任务。

权重、官方提示词、跨页参考实现和本地 MathJax 固定到 revision `4833d5d2afa6e769ee933f024504d67ace80a24c`。
官方来源：https://huggingface.co/ATH-MaaS/OvisOCR3

## 自动流程

1. 识别正文、阅读顺序、标题、公式、表格和图像区域，保留原始输出与坐标。
2. 根据页面内容补充信息：数值图表提取数据，图像区域识别真实文字标签，票据/表单提取键值。流程图保留模型生成的 Mermaid 源码。
3. 全部页面完成后重建跨页表格。结果可以查看原始逐页版本与合并版本，并定位回源页。

补充信息保存在每页 JSON 的 `documentExtras` 中，包括 `charts`、`keyValues`、`visualText`、`warnings` 和原始推理记录。可搜索 PDF 使用原文识别区块与真实图内文字；生成的图像描述、抽取结果不会冒充原文。辅助步骤失败会保留基础解析并显示警告。辅助推理默认最多输出 2048 token（`OVISOCR3_DOCUMENT_TOOL_MAX_TOKENS`），不降低主体文档预算。

通用翻译、用户自定义问答、分类等工具保留给接口诊断；常规文档解析不自动翻译或改写原文，也不需要用户配置这些工具。

旧版断点任务会保留已完成页面及原有逐页输出格式；完整重新解析会使用自动流程。缺少旧缓存的原始输出/图像摘要时，不混用新旧结果进行跨页重建。

## 跨页表格

文档解析自动执行跨页重建，不提供独立能力开关。

浏览器仍按批展示逐页解析进度。全部页面完成后，增强请求携带完整原始 PDF 以及各页缓存的原始输出和图像摘要；服务逐页渲染并验证来源，避免重复推理和跨批次边界遗漏。默认单次逐页解析批次最多 50 页，文档增强独立支持最多 400 页；界面在解析开始前检查文档上限。

相邻表格页面从末尾向前处理。合并表格后重新渲染更新页，供下一次双图推理使用，支持超过两页的连续表格。原始逐页 JSON、坐标和模型原始输出保留；合并视图只保留完整表格的一份，并记录来源页和表格片段。图片裁剪名称保留原始页号，相同坐标的不同页面图片不会覆盖。

“没有跨页表格”是正常结果。多表匹配有歧义、非法 JSON、缺失来源或模型截断时，不猜测删除内容：保留原始页面/已完成结果，显示部分完成和原因，使用重试按钮单独重跑增强阶段。页面原本为空的合法 `[]` 不会被误判为失败。

`OVISOCR3_MAX_DOCUMENT_PAGES` 可配置文档增强上限（默认 400），`OVISOCR3_MAX_PAGES_PER_REQUEST` / 旧 `OVISOCR2_MAX_PAGES_PER_REQUEST` 控制单批上限（默认 50）。渲染时只保留当前输入页和必要的双图推理，不同时将整份长文档的原图常驻内存；临时渲染文件在请求结束后清理。

## API

默认入口 `POST /api/parse`（`modelId=ovisocr3`）或 `POST /api/ovisocr3`，不需要传任务参数：

```bash
curl http://127.0.0.1:8000/api/parse -F 'modelId=ovisocr3' -F 'file=@document.pdf'
```

默认使用内部编排 `document_auto`。启用令牌认证时发送 `Authorization: Bearer <token>`。JSON 请求使用 `image` 传 Base64/data URL；同步文件请求仍受单批页数限制，长文档使用工作台分阶段协议。

每页返回 `markdown`、`images`、`layoutParsingResults`、`taskResult`、`rawOutput`、`sourceHash`、`sourceTextBlocks` 和 `documentExtras`。增强另返回 `enhancement.status`、`crossPageTables`、`steps`、`warnings` 和 `rawPredictions`。HTTP 200 后还应检查警告与增强状态。

以下18个官方工具保留在底层诊断 API 中，`GET /api/ovisocr3/capabilities` 可发现；显式 `task` / `taskOptions` 适用于开发集成，不是工作台操作步骤。

| 类别 | 任务 ID | 附加条件 |
| --- | --- | --- |
| 文档解析 | document_structured | 结构化区块、文本、表格、公式、裁剪图片 |
| 文档解析 | document_markdown | 连续 Markdown |
| 文档解析 | document_layout | 只有类别与坐标，不识别正文 |
| 通用 OCR | plain_ocr | 纯文本 |
| 通用 OCR | word_spotting | 每个词/文字的文本与坐标 |
| 通用 OCR | reference_grounding | references：指定文字 JSON 数组 |
| 通用 OCR | region_ocr | region：区域坐标 |
| 通用 OCR | formula_recognition | LaTeX |
| 通用 OCR | chart_parsing | 图表标题、轴、系列与数值 |
| 通用 OCR | text_translation | region、targetLanguage |
| 通用 OCR | image_classification | targetDescription、candidateLabels |
| 通用 OCR | visual_question_answering | question |
| 通用 OCR | position_aware_vqa | question；回答带定位 |
| 信息抽取 | kie_open | 全部可见键值 |
| 信息抽取 | kie_fields | fields：字段名称 JSON 数组 |
| 信息抽取 | kie_schema | schema：期望输出的 JSON 示例 |
| 图像描述 | figure_captioning | 页面正文、定位图像描述与 Mermaid 源码 |
| 跨页表格 | crosspage_table | 先结构化解析，再执行文档级续表重建 |


独立双图接口 `POST /api/ovisocr3/crosspage`：JSON 为 `{"images":["<BASE64_PAGE_1>","<BASE64_PAGE_2>"],"markdowns":["<PAGE_1_MARKDOWN>","<PAGE_2_MARKDOWN>"]}`。每次必须恰好两张相邻图片及各自 Markdown；结果页号为请求内的 1、2。完整多页文档应使用工作台自动管理的递归重建，避免手工合并时丢失来源。

分阶段增强使用原文件请求加 `enhanceOnly=true`、`task=document_auto`（兼容显式文档任务）、`pageResults`。pageResults 按原页序提供 `{pageIndex,task,sourceHash,rawOutput}`；pageIndex 从 0 开始。只有与原文档图像摘要及任务匹配的缓存才会被使用；不能拿别的文档或其他 DPI 的结果继续增强。

## 部署及验证范围

升级代码后需要重建 Ovis 模型镜像，不要对旧镜像使用 `-SkipBuild`。运行 `.\ovisocr3-one-click.bat`；macOS 运行 `./macos-ovisocr3-one-click.command`。安装流程包含 Chromium、beautifulsoup4、markdown-it-py 和本地 MathJax，渲染期间禁止外部资源请求及模型脚本执行。三个后端共享同一模型实例，通过按请求提示词和双图模板选择任务。

历史第二代模型、缓存和任务保留。新服务会拒绝把旧模型适配器忽略任务参数后的结果当成任务执行成功。

本机 TITAN V 12GB / 驱动 560.94 已启动 Docker、重建模型与工作台镜像，使用 PyTorch 2.7.0 CUDA 12.6、Transformers 5.14.1、FP16/SDPA。兼容镜像独立命名为 `pandocr-ovisocr3-transformers-cu126:latest`，避免误用 CUDA 12.9 的现代显卡镜像。默认图像预算 1,048,576 像素，每次推理后释放空闲 CUDA 缓存。

18个底层工具均通过真实权重 HTTP 样本测试，包含中文、公式、图表数值、票据抽取、Mermaid 和跨页表格。这些是已知样本验收，不代表任意文档准确率；Apple Silicon 未进行真机验收。自动文档流程的最新浏览器、导出和重启验证见[验收记录](docs/ovisocr3-validation.md)。
