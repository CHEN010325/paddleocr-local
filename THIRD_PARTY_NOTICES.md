# Third-party notices

Browser assets under `static/vendor/` are generated from the exact npm versions
locked in `package-lock.json`. Run `npm run vendor:sync` after an upgrade and
`npm run vendor:check` in CI. Each vendor directory contains its upstream
license.

The project integrates with PaddleOCR, PaddleX, Unlimited-OCR, OvisOCR2,
HPD-Parsing, SGLang, vLLM, PyTorch, Hugging Face, LibreOffice and their
transitive dependencies. Their own licenses and model terms continue to apply;
this project's Apache-2.0 license does not relicense them or downloaded model
weights.
