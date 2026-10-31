# Official OvisOCR3 document prompts

Source: https://huggingface.co/ATH-MaaS/OvisOCR3/tree/4833d5d2afa6e769ee933f024504d67ace80a24c/prompts

All eighteen text files are copied unchanged from the pinned Apache-2.0 model repository.
The adapter exposes document parsing, the ten general OCR tasks, three KIE tasks,
figure captioning and document-wide cross-page table reconstruction. See manifest.json
for SHA256 hashes. The upstream document_crosspage.py is vendored as
ovisocr3_reference.py; its text file I/O explicitly uses UTF-8 for Windows compatibility.
The inference algorithm is unchanged. The local MathJax bundle is from the same revision.
