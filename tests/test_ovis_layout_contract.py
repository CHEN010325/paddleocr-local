"""End-to-end retention oracle for the official 24-category document contract.

Model generation is substituted; HTTP parsing, cropping, PDF recovery and export
run normally. This checks application transformations, not model accuracy.
"""
import base64
import io
import json
import re
from unittest.mock import AsyncMock, patch

import fitz
import pytest
from docx import Document
from fastapi.testclient import TestClient
from PIL import Image

import ovisocr2_adapter as adapter
from exporters import build_docx
from ovisocr3_tasks import task_prompt


# Independent of the implementation's category set: omissions must fail the oracle.
CONTENT = {
    'abstract': 'ABSTRACT_SENTINEL with **emphasis**.',
    'algorithm': '```python\ndef ALGORITHM_SENTINEL():\n    return 42\n```',
    'aside_text': '> ASIDE_SENTINEL',
    'chart': '<caption>CHART_SENTINEL</caption><mermaid>graph TD; A-->B;</mermaid>',
    'content': '- CONTENT_SENTINEL\n- Second section',
    'display_formula': r'\[x_{DISPLAY_SENTINEL}=\frac{1}{2}\]',
    'doc_title': 'TITLE_SENTINEL',
    'figure_title': 'Fig. 1. FIGURE_TITLE_SENTINEL',
    'footer': 'FOOTER_SENTINEL',
    'footer_image': '<caption>FOOTER_IMAGE_SENTINEL</caption>',
    'footnote': 'FOOTNOTE_SENTINEL',
    'formula_number': '(FORMULA_NUMBER_SENTINEL)',
    'header': 'HEADER_SENTINEL',
    'header_image': '<caption>HEADER_IMAGE_SENTINEL</caption>',
    'image': '<caption>IMAGE_SENTINEL</caption>',
    'inline_formula': r'\(x_{INLINE_SENTINEL}=2\)',
    'number': 'NUMBER_SENTINEL',
    'paragraph_title': 'PARAGRAPH_TITLE_SENTINEL',
    'reference_content': '[1] REFERENCE_ONE_SENTINEL.\n\n[2] REFERENCE_TWO_SENTINEL.',
    'seal': '**SEAL_SENTINEL**',
    'table': '<table><caption>TABLE_CAPTION_SENTINEL</caption><tr><th>TABLE_SENTINEL</th></tr><tr><td>CELL_SENTINEL</td></tr></table>',
    'text': 'TEXT_FIRST_SENTINEL\n\nTEXT_SECOND_SENTINEL',
    'vertical_text': 'VERTICAL_SENTINEL',
    'vision_footnote': 'VISION_FOOTNOTE_SENTINEL',
}


def source_and_raw(pdf):
    raw = json.dumps([{'category': label, 'content': content,
                       'bbox': [30, 10 + n * 39, 950, 43 + n * 39]}
                      for n, (label, content) in enumerate(CONTENT.items())])
    if pdf:
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 60), 'Native layer only. The raster region has additional model-recognized content.')
            page.insert_text((40, 85), 'A source text layer must never silently replace unmatched recognized structures.')
            return doc.tobytes(), raw
    with Image.new('RGB', (800, 1200), 'white') as image:
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        return stream.getvalue(), raw


@pytest.mark.parametrize('pdf,enhance_only', [(False, False), (True, False), (True, True)],
                         ids=['image', 'native-pdf', 'cached-enhancement'])
def test_all24_survive_auto_http_pipeline_with_raw_originals(pdf, enhance_only):
    source, raw = source_and_raw(pdf)

    def infer(_parser, _images, prompt, **_options):
        if prompt == task_prompt('figure_captioning'):
            return raw
        if prompt == task_prompt('plain_ocr'):
            return ''
        if prompt == task_prompt('chart_parsing'):
            return json.dumps({'title': '', 'source': '', 'x_title': '', 'y_title': '', 'values': {'A': '1'}})
        return '[]'

    with patch.object(adapter, 'get_parser', AsyncMock(return_value=object())), patch.object(adapter, 'infer_task', side_effect=infer):
        response = TestClient(adapter.app).post('/ocr', json={
            'file': base64.b64encode(source).decode(), 'task': 'document_auto'})
    assert response.status_code == 200, response.text
    data = response.json()
    page = data['layoutParsingResults'][0]
    assert [b['block_label'] for b in page['parsing_res_list']] == list(CONTENT)
    assert page['rawOutput'] == raw
    assert page['taskResult'] == json.loads(raw)
    if enhance_only:
        cached = [{key: page[key] for key in ('pageIndex', 'task', 'sourceHash', 'rawOutput', 'documentExtras')}]
        with patch.object(adapter, 'get_parser', AsyncMock(return_value=object())):
            response = TestClient(adapter.app).post('/ocr', json={
                'file': base64.b64encode(source).decode(), 'task': 'document_auto',
                'enhanceOnly': True, 'pageResults': cached})
        assert response.status_code == 200, response.text
        data = response.json()
        assert cached[0]['rawOutput'] == raw
    md = data['markdown']
    for label, value in CONTENT.items():
        if label in {'chart', 'image', 'header_image', 'footer_image'}:
            assert value.split('<caption>')[1].split('</caption>')[0] in md
        elif label == 'doc_title':
            assert '# ' + value in md
        elif label == 'paragraph_title':
            assert '## ' + value in md
        else:
            assert value in md, label
    assert len(re.findall(r'!\[|<img\b', md)) == 4
    assert len(data['images']) == 4
    if pdf and not enhance_only:
        assert page.get('sourceOrder', {}).get('method') != 'pdf-native'
        assert page['sourceOrderDiagnostic']['method'] == 'model-preserved'

    exported = Document(io.BytesIO(build_docx(dict(data, name='All24.pdf'))))
    texts = '\n'.join(p.text for p in exported.paragraphs)
    assert 'REFERENCE_ONE_SENTINEL' in texts and 'REFERENCE_TWO_SENTINEL' in texts
    assert 'TEXT_FIRST_SENTINEL' in texts and 'TEXT_SECOND_SENTINEL' in texts
    assert 'ALGORITHM_SENTINEL' in texts
    assert 'TABLE_CAPTION_SENTINEL' in texts
    assert len(exported.tables) == 1
    assert len(exported.inline_shapes) >= 4
