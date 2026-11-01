import copy
import json

import fitz
import pytest

from ovisocr3_pdf import enrich_ovis_pdf_result


def legacy_task():
    raw = json.dumps([
        {'category': 'chart', 'bbox': [100, 100, 800, 500], 'content': '<caption>CHART DESCRIPTION</caption>'},
        {'category': 'reference_content', 'bbox': [100, 600, 800, 800], 'content': '[1] First reference.\n\n[2] Second reference.'},
    ])
    return {'markdown': 'Legacy flat', 'images': {}, 'ocrResults': [{
        'pageIndex': 0, 'rawOutput': raw, 'taskResult': json.loads(raw),
        'parsing_res_list': [{'block_label': 'image', 'block_bbox': [100, 100, 800, 500], 'block_content': 'legacy'}],
        'markdown': {'text': 'Legacy flat', 'images': {}},
    }]}


def source_pdf():
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.draw_rect(fitz.Rect(60, 80, 480, 400))
        return doc.tobytes()


def test_restore_legacy_categories_into_separate_view_without_changing_raw_fields():
    task = legacy_task()
    before = copy.deepcopy(task['ocrResults'][0])
    enrich_ovis_pdf_result(task, source_pdf())
    page = task['ocrResults'][0]
    assert [b['block_label'] for b in page['modelLayoutBlocks']] == ['chart', 'reference_content']
    assert page['sourceFigureBlocks'] == page['modelLayoutBlocks']
    assert page['rawOutput'] == before['rawOutput']
    assert page['taskResult'] == before['taskResult']
    assert page['parsing_res_list'] == before['parsing_res_list']
    assert 'CHART DESCRIPTION' in task['markdown']
    assert '[1] First reference.\n\n[2] Second reference.' in task['markdown']
    assert len(task['images']) == 1
    assert page['modelLayoutRestoration']['source'] == 'raw-output'
    restored = copy.deepcopy(task)
    enrich_ovis_pdf_result(task, source_pdf())
    assert task == restored


def test_restore_does_not_change_manual_edits():
    task = legacy_task()
    task['manualEditedAt'] = 123
    before = copy.deepcopy(task)
    enrich_ovis_pdf_result(task, source_pdf())
    assert task == before


@pytest.mark.parametrize('nested', [False, True])
def test_legacy_restore_keeps_merged_document_aggregate(nested):
    task = legacy_task()
    task['markdown'] = 'MERGED DOCUMENT AND TABLE'
    enhancement = {'crossPageTables': [{'source_pages': [1, 2]}]}
    task['ovisResult' if nested else 'enhancement'] = {'enhancement': enhancement} if nested else enhancement
    enrich_ovis_pdf_result(task, source_pdf())
    assert task['markdown'] == 'MERGED DOCUMENT AND TABLE'
    assert task['ocrResults'][0]['modelLayoutBlocks'][1]['block_label'] == 'reference_content'


def test_invalid_raw_cannot_authorize_legacy_reconstruction():
    task = legacy_task()
    task['ocrResults'][0]['rawOutput'] = '[{"category":"chart","bbox":[0,0,1001,100],"content":"bad"}]'
    task['ocrResults'][0].pop('taskResult')
    enrich_ovis_pdf_result(task, source_pdf())
    assert 'modelLayoutBlocks' not in task['ocrResults'][0]


def test_saved_task_result_can_restore_when_raw_output_is_unavailable():
    task = legacy_task()
    task['ocrResults'][0].pop('rawOutput')
    enrich_ovis_pdf_result(task, source_pdf())
    assert task['ocrResults'][0]['modelLayoutRestoration']['source'] == 'task-result'
    assert '[1] First reference.' in task['markdown']
