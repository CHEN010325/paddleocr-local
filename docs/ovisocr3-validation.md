# OvisOCR3 upgrade validation

## Automatic document workflow and TITAN V acceptance, 2026-11-01

The workbench now offers one automatic document parsing workflow. Task selectors and
capability-specific parameter forms have been removed. Page reading uses the official
figure-aware document prompt, followed by content-driven chart data, figure text and
invoice/form field extraction. Cross-page table reconstruction runs automatically.
Readable supplements appear in Markdown and editable exports; source-searchable PDF
text uses literal OCR blocks and figure labels, excluding generated descriptions.
Optional inference failures preserve the base page and surface a warning. Auxiliary
generation has a separate 2048-token budget so figure OCR cannot consume the full
16384-token document allowance. Numerical charts use region transcription plus chart
parsing; diagram labels use word spotting. This avoids observed repetitive word-box
generation on chart geometry.

### Local runtime

- Windows, NVIDIA TITAN V 12GB, driver 560.94, compute capability 7.0.
- Docker Desktop/WSL engine started after repairing two verified stale Docker-owned
  socket endpoints. The shared startup recovery handles these endpoints automatically.
- Model and web images rebuilt. Volta profile uses the distinct
  `pandocr-ovisocr3-transformers-cu126:latest` image: PyTorch 2.7.0 CUDA 12.6,
  Transformers 5.14.1, FP16/SDPA, 1,048,576-pixel input budget, cache release after inference.
- Official weights and prompts pinned to `4833d5d2afa6e769ee933f024504d67ace80a24c`.
  Modern NVIDIA vLLM deployment remains a separate image/profile.
- Workbench at http://localhost:8000/, model at port 8084, controller and Office
  conversion healthy. Only the selected Ovis GPU model runs.

### Actual model evidence

`scripts/smoke-ovisocr3.py` sent real HTTP requests through the workbench to the actual
GPU weights. All 18 official underlying tools passed known-marker checks. This includes
Chinese transcription, formula LaTeX, numeric chart values, invoice KIE, grounded answers,
Mermaid pipeline text, and positive two-page table merging. A second full run under the
final low-VRAM profile also passed 18/18. Raw outputs and generated source fixtures:
`tmp/ovis-real-acceptance/final-tasks/`, with `final-tasks.log`.

The default `/api/parse` request was tested without task parameters against five real
inputs. All passed content checks with no auxiliary warnings:

| Unified document sample | Single observed request |
| --- | ---: |
| Numerical chart (labels and A=10, B=20 data) | 12.515 s |
| Invoice (body and automatic key values) | 18.875 s |
| Flowchart (Mermaid and literal source labels) | 13.172 s |
| Converted Office PDF | 10.032 s |
| Image-only scanned invoice PDF | 19.312 s |

These are individual local observations, not benchmark averages. Full responses and
checks: `tmp/ovis-real-acceptance/auto-api/`, `final-auto-api.log`.

The unified workflow was then exercised in real Chromium without network mocks:
invoice upload, result restoration after refresh, three one-page PDF batches, full-source
recursive cross-page reconstruction, JSON download, DOCX and searchable PDF downloads.
The three-page result contained one table with all six known rows; reconstruction
processed pages 2+3 before 1+updated2 and actually re-rendered the middle page.
Office DOCX upload/conversion and image-only scanned PDF parsing also passed through
the real browser. Both produced searchable source PDFs containing the expected original
invoice identifiers. No browser console errors occurred. Evidence:
`tmp/ovis-real-acceptance/auto-browser-results.json`; screenshots and exports in the
operating-system temporary directory `ovisocr3-real-qa/`.

These are known-sample functional acceptance results, not an accuracy benchmark,
throughput study, or guarantee for arbitrary documents. Apple Silicon hardware was
not tested. The RTX 5070 Ti timings below are historical and do not describe this machine.

After restarting the web, controller and model services, a new real default-document
request returned HELLO WORLD. The saved scanned-invoice task survived reload and browser
refresh. Export inspection found one DOCX table with all six source rows and a three-page
searchable PDF containing every expected row. The original image-only invoice PDF had
no text layer; its exported PDF contained ALPHA-2026. The original task-store marker
SHA256 remained unchanged, the controller lease store was empty, and only Ovis was
running and ready. Evidence: `tmp/ovis-real-acceptance/final-verification.json`, `restart.log`.

### Regression checks and preservation

The coverage gate passed with 521 Python tests, 1 skipped and 32 subtests, plus all
63 frontend tests. Aggregate Python coverage was 96%; frontend statements 98.61% and
branches 95.56%. Additional document regression tests cover optional GPU failure,
readable refinements, source-PDF exclusion and refinement retention after enhancement.
Final `scripts/check-local.sh` passed with 521 Python tests (1 skipped, 32 subtests)
and 63 frontend tests. An independent review reran all 84 focused document/runtime tests.
Logs: `tmp/ovis-real-acceptance/final-local.log`, `bounded-final-coverage.log`.

The 15 pre-existing vendor modifications were verified as line-ending-only, backed
up with SHA256 manifest under `tmp/ovis-vendor-eol-backup-20261101-060725/`, then
normalized. Existing task markers, model caches and the local TITAN V configuration
were preserved; no Docker images, volumes or model caches were pruned.

## 提交前代码审查，2026-11-01

本次审查覆盖全部66个待提交文件，包括共享服务、前端、导出、各平台部署、新增
解析模块、固定版本第三方代码、提示词、依赖和回归测试，并安排了独立只读复核。

已复现并修复三项回归风险：

- Ovis文档改用其他模型重解析时清除旧增强状态，避免旧正文覆盖新结果或混入JSON导出。
- 页数上限在每次重试前都校验，避免第一次被阻止的超长文档在重试时开始大量推理。
- 无新设置的旧失败任务正确迁移：无完成页面时使用自动流程；已有旧页面时保留原格式
  和完成批次，完整重新解析再采用自动流程，避免丢失旧页或混用不可验证的跨页缓存。

补齐辅助工具输出预算在Compose与控制器中的转发，清除旧选择器残留CSS/禁用逻辑、
重复导入，合并重复的图像摘要和类别定义。过时规划文档归档到忽略的临时目录；
本机专用配置、权重、任务数据和临时验收产物不进入提交。固定版本官方参考实现保留
完整来源与许可证，不为减少行数而裁剪其独立入口。

依赖审计发现原有pypdf 6.21.3的三项已公开问题，现将共享/各Mac入口锁定为6.21.4，
并提高NaviDC镜像允许的最低版本。[官方补丁说明](https://github.com/py-pdf/pypdf/releases/tag/6.21.4)。
升级后严格审计未发现已知漏洞，扫描PDF可搜索导出与DOCX导出均保留验收标记。

本轮新增4项前端回归用例均经历失败复现和修复验证；旧任务格式兼容覆盖structured与markdown。
独立复核重跑84项Ovis针对性测试通过。本机完整质量门禁通过521项Python测试
（1项跳过、32项子测试）和67项前端测试，包含其他模型适配器、任务存储、导出、
外部API模式、模型切换和启动脚本检查。OpenAPI快照、供应商文件一致性、Compose配置
及差异空白检查通过。最终覆盖率日志为`tmp/ovis-real-acceptance/prepush-final-coverage.log`；
质量门禁日志为`prepush-final-local.log`，依赖审计为`prepush-pip-audit-fixed.json`。
最终Python总体覆盖率96%，前端语句覆盖率98.70%、分支覆盖率95.66%；严格审计覆盖71个依赖包，
未发现已知漏洞。

本轮未中断本机已在执行的GPU解析任务。新模型与工作台镜像已构建，运行中容器继续
使用原实例；本轮PDF安全补丁另在隔离导出中验证。此前实际GPU/浏览器验收记录见上文。
Apple Silicon真机和其他模型的GPU推理未在本轮重跑；不能将隔离回归等同于全硬件验收。

## Historical document-only upgrade

Validated on 2026-10-28, Windows, Docker Desktop / WSL, RTX 5070 Ti 16 GB.
Official weights: `ATH-MaaS/OvisOCR3`, revision
`4833d5d2afa6e769ee933f024504d67ace80a24c`.
Source: https://huggingface.co/ATH-MaaS/OvisOCR3

## Scope

The existing document workbench now uses OvisOCR3 in place of OvisOCR2.
Structured document parsing is the default, using the vendored official prompt.
Markdown mode remains available. Separate general-OCR, KIE, figure-captioning,
and cross-page-table task interfaces are not included in this change.
The internal `ovisocr2` identifier remains for saved-task and deployment compatibility.
Unlimited-OCR-2 remains a separate supported model.

## Actual GPU tests

Both backends used the same third-generation weights and structured prompt.
The container uses vLLM 0.22.1, Transformers 5.14.1, and BF16 weights.
Transformers uses CUDA SDPA. Each timing is one local request, not a statistical
benchmark or a comparison against an official leaderboard. Office conversion is
performed before the timed parse request.

| Input | vLLM, seconds | Transformers, seconds | Pages / layout blocks |
| --- | ---: | ---: | --- |
| Image, after warmup | 1.593 | 19.188 | 1 / 5 |
| Two-page PDF | 4.032 | 40.750 | 2 / 5, 6 |
| Converted Office document | 2.953 | 26.563 | 2 / 4, 3 |
| Dense scanned PDF | 11.329 | 86.609 | 2 / 15, 6 |

The first warmup request took 56.516 seconds with vLLM and 40.609 seconds with
Transformers; model startup is separate. vLLM logs show first-use Triton kernel
compilation. These cold timings must not be represented as steady-state speed.
Transformers currently uses the native Torch fallback for the gated-delta kernels;
vLLM remains the recommended default on this tested NVIDIA host.

Image, PDF, and Office Markdown matched exactly between backends. The dense-scan
text matched apart from whether the copyright line was classified as a heading.
Page order, expected Chinese/English markers, table text, and nonempty normalized
layout boxes passed. These checks do not establish general OCR accuracy.

## Web acceptance

- The model selector displays OvisOCR3 and reports ready after the dedicated
  Windows one-click launch. Switching between both tested backends completed.
- The supplied phone screenshot parsed successfully: 40 blocks, 20 cropped assets,
  recognized OvisOCR3 and Chinese text, and displayed image crops in the result.
- A two-page PDF completed through the browser with both pages, table and formula.
  JSON view and refresh restored the saved result.
- A real browser DOCX download contained both ALPHA-2026 and BETA-2026 markers.
- Only the two assistant-created browser test tasks were removed afterward.

## Preservation and limits

After testing, both original task records matched the baseline. SHA256 hashes of
127 original task/model files matched. Size and modification time of all 6,592
original cache entries matched; 1,549 additional runtime cache entries were created.
Existing second-generation weights, Unlimited-OCR-2 weights, and project data were
not cleared. The active-model runtime setting intentionally selects OvisOCR3.

Unit tests cover legacy model/revision upgrade, custom settings, new request aliases,
structured JSON validation, cropped image references, output truncation failures,
old-container replacement without deleting volumes, and Windows port fallback.
macOS install/routing and shell checks are automated; no OvisOCR3 inference test
was performed on Apple hardware. Other untested computers are not hardware-accepted
by these results.

Local raw results and logs are retained under ignored `tmp/ovisocr3-acceptance/`.

Final `scripts/check-local.sh` and `scripts/check-coverage.sh` both passed:
435 Python tests passed, 1 skipped, 32 subtests; all 56 frontend tests passed.
Python aggregate coverage was 96%, Ovis adapter 96%, configuration resolver 100%.
Frontend statement coverage was 98.87% and branch coverage 96.69%.
