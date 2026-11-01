"""Official 24-category contracts across literal and readable projections."""
import json

import pytest
from PIL import Image

from ovisocr3_tasks import CATEGORIES, document_content


@pytest.mark.parametrize('label', ['display_formula', 'inline_formula'])
@pytest.mark.parametrize('content', [r'\(x+1\)', r'\[x+1\]', '$x+1$', '$$x+1$$'])
def test_formula_delimiters_are_not_nested_or_removed(label, content):
    assert document_content(label, content) == content


def test_formula_projection_preserves_literal_escaped_dollar():
    assert document_content('display_formula', r'\text{price}=5\$') == '$$\n\\text{price}=5\\$\n$$'


def category_blocks():
    content = {
        'algorithm': 'Algorithm 1\n\n```python\nfor x in xs:\n    emit(x)\n```',
        'reference_content': '[1] Author. Paper.\n\n[2] Other. Paper.',
        'table': '<table><tr><th>A</th><th>B</th></tr><tr><td rowspan="2">1</td><td>$x$</td></tr></table>',
        'display_formula': r'\sum_i x_i', 'inline_formula': r'x_i',
    }
    for index, label in enumerate(sorted(CATEGORIES)):
        yield {'category': label, 'bbox': [0, index * 30, 999, index * 30 + 20],
               'content': content.get(label, 'SENTINEL_' + label + '\n\nsecond paragraph')}


def test_all24_retain_order_content_and_raw_evidence_after_real_markdown_normalization():
    from ovisocr2_adapter import build_task_response
    from exporters import normalize_markdown
    blocks = list(category_blocks())
    raw = json.dumps(blocks)
    with Image.new('RGB', (100, 100)) as image:
        result = build_task_response('document_structured', {}, [raw], [image], 1)
    page = result['layoutParsingResults'][0]
    assert result['rawOutputs'] == [raw] and page['taskResult'] == blocks
    assert [b['block_label'] for b in page['parsing_res_list']] == [b['category'] for b in blocks]
    markdown = normalize_markdown(result['markdown'])
    assert markdown == normalize_markdown('\n\n'.join(document_content(b['category'], b['content']) for b in blocks))
    assert 'Algorithm 1\n\n```python\nfor x in xs:\n    emit(x)\n```' in markdown
    assert '[1] Author. Paper.\n\n[2] Other. Paper.' in markdown
    assert blocks[[b['category'] for b in blocks].index('table')]['content'] in markdown


def test_caption_markup_only_projects_visual_categories():
    from ovisocr2_adapter import build_task_response
    blocks = [
        {'category': 'table', 'bbox': [0, 0, 999, 100], 'content': '<table><caption>Table title</caption><tr><td>DATA</td></tr></table>'},
        {'category': 'algorithm', 'bbox': [0, 110, 999, 200], 'content': '```html\n<caption>literal source</caption>\n```'},
        {'category': 'image', 'bbox': [0, 210, 999, 900], 'content': '<caption>Visual caption</caption><mermaid>graph LR; A-->B</mermaid>'},
    ]
    raw = json.dumps(blocks)
    with Image.new('RGB', (100, 100)) as image:
        result = build_task_response('figure_captioning', {}, [raw], [image], 1)
    projected = result['layoutParsingResults'][0]['parsing_res_list']
    assert projected[0]['block_content'] == blocks[0]['content']
    assert projected[1]['block_content'] == blocks[1]['content']
    assert '```mermaid\ngraph LR; A-->B\n```' in projected[2]['block_content']
    assert len(result['images']) == 1
    assert result['rawOutputs'] == [raw]


def test_auto_document_source_projection_keeps_every_nonvisual_category_literal():
    from ovisocr3_document import parse_document_page, VISUAL_LABELS
    blocks = list(category_blocks())
    raw = json.dumps(blocks)
    def infer(images, prompt):
        return raw
    with Image.new('RGB', (100, 100)) as image:
        actual_raw, extras, source = parse_document_page(
            image, infer, refine=lambda images, prompt: '' if 'plain text' in prompt.lower() else '[]')
    assert actual_raw == raw
    expected = [b for b in blocks if b['category'] not in VISUAL_LABELS]
    assert [(b['block_label'], b['block_content']) for b in source] == [(b['category'], b['content']) for b in expected]
    assert len(expected) == 20


def test_crosspage_projection_keeps_table_caption_code_math_and_raw_inputs():
    from ovisocr3_crosspage import enhance_document
    blocks = [
        {'category': 'table', 'bbox': [0, 0, 999, 100], 'content': '<table><caption>Table title</caption><tr><td>DATA</td></tr></table>'},
        {'category': 'algorithm', 'bbox': [0, 110, 999, 200], 'content': '```html\n<caption>literal source</caption>\n```'},
        {'category': 'display_formula', 'bbox': [0, 210, 999, 300], 'content': r'\text{price}=5\$'},
        {'category': 'doc_title', 'bbox': [0, 310, 999, 400], 'content': 'Document title'},
    ]
    raw = json.dumps(blocks)
    raws = [raw]
    def unexpected_infer(*args):
        raise AssertionError('One page must reuse cached stage-one output')
    with Image.new('RGB', (100, 100)) as image:
        result = enhance_document([image], raws, 'figure_captioning', unexpected_infer, object())
    assert result['status'] == 'complete'
    assert blocks[0]['content'] in result['markdown']
    assert blocks[1]['content'] in result['markdown']
    assert '$$\n\\text{price}=5\\$\n$$' in result['markdown']
    assert '# Document title' in result['markdown']
    assert raws == [raw]


@pytest.mark.parametrize('language', ['html', 'markdown', ''])
def test_crosspage_structured_algorithm_at_page_start_keeps_code_fences(language):
    from ovisocr3_crosspage import enhance_document
    code = '```' + language + '\n<caption>literal algorithm</caption>\n```'
    blocks = [
        {'category': 'algorithm', 'bbox': [0, 0, 999, 100], 'content': code},
        {'category': 'text', 'bbox': [0, 110, 999, 200], 'content': 'Following paragraph'},
    ]
    with Image.new('RGB', (100, 100)) as image:
        result = enhance_document([image], [json.dumps(blocks)], 'figure_captioning', None, object())
    assert result['status'] == 'complete'
    assert result['markdown'].strip() == code + '\n\nFollowing paragraph'
