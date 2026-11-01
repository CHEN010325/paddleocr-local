import base64
import copy

import fitz


def figure_pdf(caption=True, split=False):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(310, 180, 560 if not split else 420, 300), color=(0, 0, 1))
    if split:
        page.draw_rect(fitz.Rect(435, 180, 560, 300), color=(1, 0, 0))
    page.insert_text((320, 230), 'ACTUAL FIGURE LABEL')
    if caption:
        page.insert_text((310, 322), 'Fig. 1. A complete vector diagram.')
    else:
        page.insert_text((310, 322), 'Table 1. Comparison of methods.')
    data = doc.tobytes()
    doc.close()
    return data


def test_recovers_whole_vector_figure_and_caption():
    from ovisocr3_pdf import recover_pdf_figures
    figures = recover_pdf_figures(figure_pdf(split=True))
    assert len(figures) == 1
    figure = figures[0]
    assert figure['pageIndex'] == 0
    assert figure['bbox'][0] <= 517 and figure['bbox'][2] >= 933
    assert figure['caption'].startswith('Fig. 1.')
    pix = fitz.Pixmap(base64.b64decode(figure['imageBase64']))
    assert pix.width > 450 and pix.height > 200


def test_does_not_promote_table_or_uncaptioned_drawing():
    from ovisocr3_pdf import recover_pdf_figures
    assert recover_pdf_figures(figure_pdf(caption=False)) == []


def test_enrichment_replaces_bad_model_crop_without_duplicates_or_source_pollution():
    from ovisocr3_pdf import enrich_ovis_pdf_result
    old_link = '![image](ocr_images/bad.jpg)'
    page = {'pageIndex': 0, 'width': 1000, 'height': 1000,
            'parsing_res_list': [{'block_label': 'image', 'block_bbox': [520, 225, 680, 380], 'block_content': old_link}],
            'sourceTextBlocks': [{'block_label': 'text', 'block_content': 'ACTUAL SOURCE'}],
            'markdown': {'text': old_link + '\n\nFig. 1. A complete vector diagram.', 'images': {'ocr_images/bad.jpg': 'BAD'}}}
    result = {'markdown': page['markdown']['text'], 'images': {'ocr_images/bad.jpg': 'BAD'}, 'ocrResults': [page]}
    original_source = copy.deepcopy(page['sourceTextBlocks'])
    enrich_ovis_pdf_result(result, figure_pdf())
    assert 'bad.jpg' not in result['markdown']
    assert len(result['images']) == 1
    assert page['markdown']['text'].count('![') == 1
    assert page['sourceTextBlocks'] == original_source
    snapshot = copy.deepcopy(result)
    enrich_ovis_pdf_result(result, figure_pdf())
    assert result == snapshot


def test_missing_figure_is_inserted_before_caption_on_its_page():
    from ovisocr3_pdf import enrich_ovis_pdf_result
    text = 'Body\n\nFig. 1. A complete vector diagram.\n\nAfter caption'
    page = {'pageIndex': 0, 'parsing_res_list': [], 'markdown': {'text': text, 'images': {}}}
    result = {'markdown': text, 'images': {}, 'layoutParsingResults': [page]}
    enrich_ovis_pdf_result(result, figure_pdf())
    assert result['markdown'].index('![') < result['markdown'].index('Fig. 1.')
    assert 'After caption' in result['markdown']
    assert len(page['parsing_res_list']) == 1


def test_persisted_batch_local_indices_use_one_based_source_page():
    from ovisocr3_pdf import enrich_ovis_pdf_result
    doc = fitz.open(stream=figure_pdf(), filetype='pdf')
    doc.new_page(width=600, height=800)
    pages = [{'pageIndex': 0, 'sourcePage': i + 1, 'markdown': {'text': text, 'images': {}},
              'parsing_res_list': []} for i, text in enumerate(['Fig. 1. A complete vector diagram.', 'Second page'])]
    result = {'ocrResults': pages, 'markdown': 'Fig. 1. A complete vector diagram.\n\nSecond page', 'images': {}}
    enrich_ovis_pdf_result(result, doc.tobytes())
    assert '![' in pages[0]['markdown']['text']
    assert pages[1]['markdown']['text'] == 'Second page'
    doc.close()


def test_persisted_top_level_batch_image_alias_is_replaced():
    from ovisocr3_pdf import enrich_ovis_pdf_result
    raw_key = 'ocr_images/ovisocr3_p1_image_1.jpg'
    global_key = 'ocr_images/batch_123_ovisocr3_p1_image_1.jpg'
    raw_link = f'![image]({raw_key})'
    page = {'pageIndex': 0, 'batchId': 'batch_123', 'parsing_res_list': [
        {'block_label': 'image', 'block_bbox': [510, 225, 940, 380], 'block_content': raw_link}],
        'markdown': {'text': raw_link, 'images': {raw_key: 'BAD'}}}
    result = {'markdown': f'![image]({global_key})', 'images': {global_key: 'BAD'}, 'ocrResults': [page]}
    enrich_ovis_pdf_result(result, figure_pdf())
    assert global_key not in result['images']
    assert result['markdown'].count('![') == 1
    assert 'batch_123' not in result['markdown']


def test_wrong_head_crop_and_enhanced_aliases_do_not_survive_in_display_markdown():
    from ovisocr3_pdf import enrich_ovis_pdf_result
    raw_key = 'ocr_images/ovisocr3_p1_image_1.jpg'
    head_alias = 'ocr_images/b_enhanced_page_1_bbox_0_0_999_100.jpg'
    figure_alias = 'ocr_images/b_enhanced_page_1_bbox_510_225_940_380.jpg'
    page = {'sourcePage': 1, 'pageIndex': 0, 'parsing_res_list': [
        {'block_label': 'image', 'block_bbox': [0, 0, 999, 100], 'block_content': f'![image]({raw_key})'}],
        'markdown': {'text': f'![image]({raw_key})\n\nKEEP BODY\n\nFig. 1. The diagram.', 'images': {raw_key: 'HEAD'}}}
    result = {'ocrResults': [page], 'markdown': f'![image]({head_alias})\n\nKEEP BODY\n\n![image]({figure_alias})\n\nFig. 1. The diagram.',
              'images': {head_alias: 'HEAD', figure_alias: 'PARTIAL'},
              'ovisResult': {'originalMarkdown': f'![image]({figure_alias})'}}
    original = copy.deepcopy(result['ovisResult'])
    enrich_ovis_pdf_result(result, figure_pdf())
    assert result['markdown'].count('![') == 1
    assert 'enhanced_page_' not in result['markdown']
    assert 'KEEP BODY' in result['markdown'] and 'KEEP BODY' in page['markdown']['text']
    assert result['ovisResult'] == original
    assert result['originalImages'][figure_alias] == 'PARTIAL'
    assert len(result['images']) == 1


def test_image_alt_is_safe_when_source_caption_contains_square_brackets():
    from ovisocr3_pdf import enrich_ovis_pdf_result
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(310, 180, 560, 300))
    page.insert_text((310, 322), 'Fig. 1. A complete diagram [baseline].')
    result = {'markdown': '', 'layoutParsingResults': [{'pageIndex': 0, 'markdown': {'text': '', 'images': {}}}]}
    enrich_ovis_pdf_result(result, doc.tobytes())
    assert '![Figure 1](' in result['markdown']
    doc.close()


def test_other_real_uncaptioned_graphic_is_preserved():
    from ovisocr3_pdf import enrich_ovis_pdf_result
    doc = fitz.open(stream=figure_pdf(), filetype='pdf')
    doc[0].draw_rect(fitz.Rect(30, 550, 180, 650))
    key = 'ocr_images/real_other.png'
    link = f'![image]({key})'
    page = {'pageIndex': 0, 'parsing_res_list': [
        {'block_label': 'image', 'block_bbox': [50, 687, 300, 813], 'block_content': link}],
        'markdown': {'text': 'Fig. 1. A complete vector diagram.\n\n' + link, 'images': {key: 'REAL'}}}
    result = {'markdown': page['markdown']['text'], 'images': {key: 'REAL'}, 'layoutParsingResults': [page]}
    enrich_ovis_pdf_result(result, doc.tobytes())
    assert result['images'][key] == 'REAL'
    assert link in result['markdown'] and result['markdown'].count('![') == 2
    doc.close()


def test_short_caption_anchors_wide_figure_in_its_right_column():
    from ovisocr3_pdf import recover_pdf_figures
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.draw_rect(fitz.Rect(310, 180, 560, 300))
    page.insert_text((310, 322), 'Fig. 1. Source diagram.')
    figures = recover_pdf_figures(doc.tobytes())
    assert len(figures) == 1
    assert figures[0]['bbox'][2] >= 933
    doc.close()


def test_html_image_aliases_are_replaced_and_deduplicated_with_markdown():
    from ovisocr3_pdf import enrich_ovis_pdf_result
    alias = 'ocr_images/b_enhanced_page_1_bbox_510_225_940_380.jpg'
    old_html = f'<img alt="legacy" src="{alias}" width="100" />'
    page = {'pageIndex': 0, 'parsing_res_list': [
        {'block_label': 'image', 'block_bbox': [510, 225, 940, 380], 'block_content': old_html}],
        'markdown': {'text': old_html + '\n\nFig. 1. Source diagram.', 'images': {alias: 'BAD'}}}
    result = {'markdown': old_html + f'\n\n![old]({alias})\n\nKEEP BODY', 'images': {alias: 'BAD'},
              'layoutParsingResults': [page], 'ovisResult': {'originalMarkdown': old_html}}
    original = copy.deepcopy(result['ovisResult'])
    enrich_ovis_pdf_result(result, figure_pdf())
    assert '<img' not in result['markdown'] and '<img' not in page['markdown']['text']
    assert result['markdown'].count('![') == 1
    assert 'enhanced_page_' not in result['markdown']
    assert 'KEEP BODY' in result['markdown']
    assert result['ovisResult'] == original
    assert len(page['parsing_res_list']) == 1
    assert 'enhanced_page_' not in page['parsing_res_list'][0]['block_content']


def test_rotated_pages_keep_existing_images_until_coordinate_recovery_is_supported():
    from ovisocr3_pdf import recover_pdf_figures,enrich_ovis_pdf_result
    for rotation in (90,180,270):
        doc=fitz.open(stream=figure_pdf(),filetype='pdf');doc[0].set_rotation(rotation);pdf=doc.tobytes();doc.close()
        task={'markdown':'![existing](existing.png)','images':{'existing.png':'ORIGINAL'},
              'ocrResults':[{'pageIndex':0,'parsing_res_list':[],'markdown':{'text':'![existing](existing.png)','images':{'existing.png':'ORIGINAL'}}}]}
        before=copy.deepcopy(task)
        assert recover_pdf_figures(pdf)==[]
        assert enrich_ovis_pdf_result(task,pdf)==before


def test_figure_reference_in_body_is_not_a_caption_anchor():
    from ovisocr3_pdf import _insert_by_caption
    text='Earlier prose discusses Fig. 1 as an example.\n\nActual later paragraph.\n\nFig. 1. Source diagram.\n\nAfter diagram.'
    result=_insert_by_caption(text,{'caption':'Fig. 1. Source diagram.'},'![Figure 1](figure.png)')
    assert result.index('Actual later paragraph.') < result.index('![') < result.index('Fig. 1. Source diagram.')


def test_manual_markdown_corrections_are_not_overwritten_by_pdf_recovery():
    from ovisocr3_pdf import enrich_ovis_pdf_result
    result={'manualEditedAt':123,'markdown':'User corrected wording and intentionally removed a figure.',
            'images':{},'ocrResults':[{'pageIndex':0,'markdown':{'text':'Old model output','images':{}},'parsing_res_list':[]}]}
    before=copy.deepcopy(result)
    assert enrich_ovis_pdf_result(result,figure_pdf())==before
