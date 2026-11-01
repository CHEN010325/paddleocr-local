"""Each model keeps its own layout vocabulary and emitted block structure."""
import base64
import copy
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import navidc_ocr_adapter as navidc
import unlimited_ocr_adapter as unlimited


NAVIDC_TYPES = ('text', 'title', 'table', 'image', 'code', 'algorithm', 'header', 'footer',
                'page_number', 'page_footnote', 'aside_text', 'equation', 'equation_block',
                'ref_text', 'list', 'phonetic', 'table_caption', 'image_caption', 'code_caption',
                'table_footnote', 'image_footnote', 'unknown', 'seal', 'char')


def png():
    out = io.BytesIO()
    Image.new('RGB', (40, 40), 'white').save(out, format='PNG')
    return out.getvalue()


def test_navidc_own_24_types_keep_all_content_raw_geometry_and_default_marginals():
    blocks = [{'type': label, 'bbox': [0.1, 0.1, 0.9, 0.9],
               'content': f'{label.upper()}_SENTINEL', 'angle': 90} for label in NAVIDC_TYPES]
    before = copy.deepcopy(blocks)
    parser = SimpleNamespace(parse_batch_async=AsyncMock(return_value=[blocks]))
    with patch.object(navidc, 'get_parser', AsyncMock(return_value=parser)):
        response = TestClient(navidc.app).post('/ocr', json={
            'file': base64.b64encode(png()).decode(), 'fileType': 1})
    assert response.status_code == 200, response.text
    data = response.json()
    page = data['layoutParsingResults'][0]
    assert [b['block_label'] for b in page['parsing_res_list']] == list(NAVIDC_TYPES)
    for label in NAVIDC_TYPES:
        assert f'{label.upper()}_SENTINEL' in data['markdown'], label
    assert data['markdown'].count('![') == 3  # image, seal, char (scientific figure)
    assert page['rawBlocks'] == before
    assert blocks == before


def test_navidc_explicit_markdown_ignore_keeps_model_layout_and_raw_blocks():
    blocks = [{'type': 'header', 'bbox': [0, 0, 1, .1], 'content': 'HEADER_SENTINEL', 'angle': 0}]
    md, _, page = navidc.build_page_response(Image.new('RGB', (40, 40)), blocks, 0, {'header'})
    assert 'HEADER_SENTINEL' not in md
    assert page['parsing_res_list'][0]['block_label'] == 'header'
    assert page['rawBlocks'] == blocks


@pytest.mark.parametrize('label', ['header', 'footer', 'page_number', 'number', 'page_num'])
def test_unlimited_main_pipeline_keeps_marginal_content(label):
    raw = f'<PAGE><BLOCK>{label}[1,2,900,100]<FORK>{label.upper()}_SENTINEL'
    result = unlimited.build_adapter_response(raw, 1, 1, {}, [png()])
    assert f'{label.upper()}_SENTINEL' in result['markdown']
    assert result['layoutParsingResults'][0]['parsing_res_list'][0]['block_label'] == label


def test_unlimited_code_indent_survives_adapter_and_server_cleaning():
    import server
    content = '```python\nif True:\n    print(1)\n```'
    raw = '<PAGE><BLOCK>algorithm[1,2,900,100]<FORK>' + content
    result = unlimited.build_adapter_response(raw, 1, 1, {}, [png()])
    assert content in result['markdown']
    assert result['layoutParsingResults'][0]['parsing_res_list'][0]['block_content'] == content
    assert content in server.parse_unlimited_ocr_response(result)['markdown']


@pytest.mark.parametrize('label', ['image', 'figure', 'picture', 'chart', 'img', 'seal'])
def test_unlimited_own_visual_aliases_crop_without_dropping_description(label):
    raw = f'<PAGE><BLOCK>{label}[10,10,900,900]<FORK>RECOGNIZED_DESCRIPTION'
    result = unlimited.build_adapter_response(raw, 1, 1, {}, [png()])
    assert len(result['images']) == 1
    assert 'RECOGNIZED_DESCRIPTION' in result['markdown']
    assert result['layoutParsingResults'][0]['parsing_res_list'][0]['block_label'] == label


@pytest.mark.parametrize('header', ['title<LEVEL3>[1,2,900,100]', 'title[1,2,900,100]<LEVEL3>'])
def test_unlimited_explicit_title_level_survives_protocol_normalization(header):
    raw = '<PAGE><BLOCK>' + header + '<FORK>Nested heading'
    result = unlimited.build_adapter_response(raw, 1, 1, {}, [png()])
    assert result['markdown'] == '### Nested heading'
    block = result['layoutParsingResults'][0]['parsing_res_list'][0]
    assert block['block_label'] == 'title'
    assert block['heading_level'] == 3


def test_server_legacy_det_cleaner_keeps_marginals_and_explicit_title_level():
    import server
    raw = '<|det|>header [0,0,10,10]<|/det|>HEADER_SENTINEL\n<|det|>title [0,20,10,30]<|/det|><LEVEL3>Nested'
    rendered = server.parse_unlimited_ocr_response({'markdown': raw})['markdown']
    assert 'HEADER_SENTINEL' in rendered
    assert '### Nested' in rendered


def test_unlimited_cross_append_semantics_remain_separate_and_open_labels_survive():
    raw = ('<PAGE><BLOCK>cross_text[1,2,900,100]<FORK>FIRST_SENTINEL'
           '<BLOCK>append_text[1,200,900,300]<FORK>SECOND_SENTINEL'
           '<BLOCK>future_model_type[1,400,900,500]<FORK>FUTURE_SENTINEL')
    result = unlimited.build_adapter_response(raw, 1, 1, {}, [png()])
    labels = [b['block_label'] for b in result['layoutParsingResults'][0]['parsing_res_list']]
    assert labels == ['cross_text', 'append_text', 'future_model_type']
    assert 'FIRST_SENTINEL\n\nSECOND_SENTINEL\n\nFUTURE_SENTINEL' in result['markdown']


def test_unlimited_caption_and_visual_body_keep_emitted_markdown_structure():
    import server
    caption = '*Already emphasized caption.*'
    raw = '<PAGE><BLOCK>image_caption[1,2,900,100]<FORK>' + caption
    assert unlimited.build_adapter_response(raw, 1, 1, {}, [png()])['markdown'] == caption
    assert server.clean_unlimited_ocr_markdown('<|det|>image_caption[1,2,900,100]<|/det|>' + caption) == caption
    body = '```mermaid\ngraph LR\n    A-->B\n```'
    raw = '<PAGE><BLOCK>image[1,2,900,100]<FORK>' + body
    rendered = unlimited.build_adapter_response(raw, 1, 1, {}, [png()])['markdown']
    assert '\n\n' + body in rendered
