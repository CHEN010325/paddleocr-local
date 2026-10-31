# Third-party notices

Browser assets under `static/vendor/` are generated from the exact npm versions
locked in `package-lock.json`. Run `npm run vendor:sync` after an upgrade and
`npm run vendor:check` in CI. Each vendor directory contains its upstream
license.

The project integrates with PaddleOCR, PaddleX, Unlimited-OCR-2, OvisOCR3,
HPD-Parsing, NaviDC-OCR, SGLang, vLLM, PyTorch, Hugging Face, LibreOffice and their
transitive dependencies. Their own licenses and model terms continue to apply;
this project's Apache-2.0 license does not relicense them or downloaded model
weights.
# OvisOCR3 task assets and document renderer

The eighteen prompts, `ovisocr3_reference.py` and local MathJax bundle are sourced
from https://huggingface.co/ATH-MaaS/OvisOCR3 at revision
`4833d5d2afa6e769ee933f024504d67ace80a24c` (Apache-2.0). The upstream document helper
has only an explicit UTF-8 file I/O adaptation for Windows; task projection and
workbench integration reside in separate project modules. Source hashes and the
adaptation are recorded in `ovisocr3_prompts/manifest.json`. The upstream license
is retained at `ovisocr3_assets/LICENSE`. MathJax is distributed under Apache-2.0.
