# OvisOCR3 upgrade validation

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
