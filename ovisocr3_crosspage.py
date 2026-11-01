"""Document-wide stage two using the pinned official algorithm and cached page parses."""
import base64
import json
import tempfile
from pathlib import Path

from PIL import Image
from fastapi import HTTPException

import ovisocr3_reference as reference
from ovisocr3_tasks import task_prompt, decode_result, caption_markdown, document_content, page_hash, VISUAL_CATEGORIES


class PDFPages:
    """Bounded full-document protocol: render/validate one original page at a time."""
    def __init__(self, data, records, task, *, dpi, max_pages):
        import fitz
        self.data, self.records, self.dpi = data, records, dpi
        with fitz.open(stream=data, filetype='pdf') as doc:
            self.count = len(doc)
        if not 1 <= self.count <= max_pages:
            raise HTTPException(422, f'Document enhancement supports 1–{max_pages} pages')
        if not isinstance(records, list) or len(records) != self.count:
            raise HTTPException(422, 'pageResults must cover the complete original document')
        for index, record in enumerate(records):
            if (not isinstance(record, dict) or type(record.get('pageIndex')) is not int or record['pageIndex'] != index
                    or record.get('task') != task or not isinstance(record.get('rawOutput'), str)):
                raise HTTPException(422, 'Cached page results do not match the original document/task/order')

    def __len__(self):
        return self.count

    def __iter__(self):
        import fitz
        with fitz.open(stream=self.data, filetype='pdf') as doc:
            for index in range(len(doc)):
                pix = doc[index].get_pixmap(matrix=fitz.Matrix(self.dpi / 72, self.dpi / 72), alpha=False)
                image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
                try:
                    if self.records[index].get('sourceHash') != page_hash(image):
                        raise HTTPException(422, 'Cached page results do not match original source pixels')
                    yield image
                finally:
                    image.close()


def enhance_document(images, raw_outputs, stage1_task, infer, renderer=None):
    if stage1_task not in {'document_structured', 'document_markdown', 'figure_captioning'}:
        raise HTTPException(422, 'Cross-page reconstruction requires document parsing results')
    if len(images) != len(raw_outputs) or not images:
        raise HTTPException(422, 'One original parse result is required per source page')
    # Validate everything before stage two; do not "recover" malformed stage-one JSON.
    for raw in raw_outputs:
        decode_result(stage1_task, raw, {})
    with tempfile.TemporaryDirectory(prefix='ovisocr3-crosspage-') as scratch:
        root = Path(scratch)
        paths = []
        for n, image in enumerate(images, 1):
            path = root / f'page_{n}.png'
            image.save(path)
            paths.append(str(path))
        raw_predictions = []
        cursor = 0

        def backend(input_paths, prompt, folder, stage):
            nonlocal cursor
            if stage == 1:
                raw = raw_outputs[cursor]
                cursor += 1
                if stage1_task == 'figure_captioning':
                    # Preserve official raw output separately. Caption markup is projected
                    # as readable text/code for page rendering without executable HTML.
                    blocks = json.loads(raw)
                    for block in blocks:
                        if block['category'] in VISUAL_CATEGORIES:
                            block['content'] = caption_markdown(block['content'])
                            box = '_'.join(str(n) for n in block['bbox'])
                            block['content'] = f'<img src="images/bbox_{box}.jpg" />\n\n' + block['content']
                    raw = json.dumps(blocks, ensure_ascii=False)
                if stage1_task != 'document_markdown':
                    blocks = json.loads(raw)
                    for block in blocks:
                        block['content'] = document_content(block['category'], block['content'])
                    raw = json.dumps(blocks, ensure_ascii=False)
                return raw, 'stop'
            opened = []
            try:
                for path in input_paths:
                    with Image.open(path) as image:
                        opened.append(image.convert('RGB'))
                raw = infer(opened, prompt)
                raw_predictions.append({'pair': folder.name, 'rawOutput': raw})
                return raw, 'stop'
            finally:
                for image in opened:
                    image.close()

        owned_renderer = renderer is None
        if owned_renderer:
            renderer = reference._BrowserRenderer(Path(__file__).with_name('ovisocr3_assets') / 'mathjax.js')
        output = root / 'output'
        try:
            result = reference._run_document(
                paths, output, backend, renderer,
                page_prompt=task_prompt(stage1_task), table_prompt=task_prompt('crosspage_table'),
                stage1_prompt='document_markdown' if stage1_task == 'document_markdown' else 'document_structured')
            if result['error'] and result['error'].get('type') == 'Stage1OutputIncomplete':
                # Raw [] is valid complete structured output for a blank page. The
                # upstream research helper treats no emitted content as a warning.
                warnings = [warning for warning in result['stage1_parse_warnings']
                            if stage1_task == 'document_markdown' or json.loads(raw_outputs[warning['page'] - 1]) != []]
                if not warnings:
                    result['status'], result['error'] = 'COMPLETE', None
                else:
                    result['error']['pages'] = [warning['page'] for warning in warnings]
            markdown = (output / 'document.md').read_text(encoding='utf-8')
            assets = {str(p.relative_to(output)).replace('\\', '/'): base64.b64encode(p.read_bytes()).decode('ascii')
                      for p in output.glob('page_*/images/*') if p.is_file()}
            # Every source page and raw output remains available even when enhancement fails.
            return {'markdown': markdown, 'images': assets, 'status': result['status'].lower(),
                    'crossPageTables': result['merged_tables'], 'steps': result['steps'],
                    'warnings': ([result['error']] if result['error'] else []),
                    'rawPredictions': raw_predictions, 'sourcePageCount': len(images)}
        finally:
            if owned_renderer:
                renderer.close()
