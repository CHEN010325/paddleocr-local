import json
from PIL import Image


def test_document_parser_automatically_reads_chart_labels_data_and_invoice_fields():
    from ovisocr3_document import parse_document_page
    responses=[
        json.dumps([{'bbox':[0,0,999,100],'category':'text','content':'Invoice No: ALPHA\nTotal: 35.00'},
                    {'bbox':[0,110,999,999],'category':'chart','content':'<caption>A bar chart</caption>'}]),
        'SALES',
        '{"title":"SALES","source":"None","x_title":"None","y_title":"None","values":{"A":"10"}}',
        '{"invoice_number":"ALPHA","total":"35.00"}',
    ]
    prompts=[]
    def infer(images,prompt):
        prompts.append(prompt);return responses.pop(0)
    raw,extras,source=parse_document_page(Image.new('RGB',(100,100)),infer)
    assert len(prompts)==4
    assert extras['charts'][0]['data']['values']=={'A':'10'}
    assert extras['keyValues']['total']=='35.00'
    assert source[-1]['block_content']=='SALES'
    assert all('caption' not in b['block_content'] for b in source)
    assert not extras['warnings']


def test_document_caption_failure_falls_back_to_document_content_without_losing_raw_evidence():
    from ovisocr3_document import parse_document_page
    outputs=iter(['not valid JSON','[{"bbox":[0,0,999,999],"category":"text","content":"Original"}]'])
    raw,extras,source=parse_document_page(Image.new('RGB',(100,100)),lambda *a:next(outputs))
    assert source[0]['block_content']=='Original'
    assert extras['warnings'] and extras['rawOutputs'][0]['rawOutput']=='not valid JSON'


def test_auto_document_api_invokes_relevant_tools_and_keeps_captions_out_of_source_pdf_text():
    import base64,io
    from fastapi.testclient import TestClient
    from unittest.mock import patch,AsyncMock
    import ovisocr2_adapter as adapter
    from exporters import _task_page_text
    buffer=io.BytesIO();Image.new('RGB',(200,200),'white').save(buffer,format='PNG')
    outputs=[
        '[{"bbox":[0,0,999,200],"category":"text","content":"INVOICE ALPHA Total: 35.00"}, {"bbox":[0,210,999,999],"category":"image","content":"<caption>A generated description</caption>"}]',
        '[{"bbox":[10,10,900,300],"text":"ACTUAL LABEL"}]',
        '{"total":"35.00"}',
    ]
    with patch.object(adapter,'get_parser',AsyncMock(return_value=object())),patch.object(adapter,'infer_task',side_effect=outputs):
        response=TestClient(adapter.app).post('/ocr',json={'file':base64.b64encode(buffer.getvalue()).decode(),'task':'document_auto'})
    assert response.status_code==200,response.text
    result=response.json();assert result['task']=='document_auto'
    page=result['layoutParsingResults'][0]
    assert page['documentExtras']['keyValues']['total']=='35.00'
    assert page['searchableText'] is True
    assert 'ACTUAL LABEL' in result['markdown'] and '35.00' in page['markdown']['text']
    text=' '.join(_task_page_text({'ocrResults':[page]},1)[0])
    assert 'ACTUAL LABEL' in text and 'generated description' not in text


def test_photograph_without_source_words_is_not_exported_as_caption_search_text():
    from exporters import _task_page_text
    task={'markdown':'A generated caption','ocrResults':[{'sourceTextBlocks':[],'markdown':{'text':'A generated caption'}}]}
    assert _task_page_text(task,1)==[[]]


def test_optional_gpu_failure_keeps_body_and_reports_warning():
    from ovisocr3_document import parse_document_page
    outputs=iter(['[{"bbox":[0,0,999,100],"category":"text","content":"IMPORTANT BODY"},{"bbox":[0,110,999,999],"category":"image","content":"A picture"}]',RuntimeError('CUDA out of memory')])
    def infer(*args):
        value=next(outputs)
        if isinstance(value,Exception): raise value
        return value
    raw,extras,blocks=parse_document_page(Image.new('RGB',(100,100)),infer)
    assert 'IMPORTANT BODY' in raw and blocks[0]['block_content']=='IMPORTANT BODY'
    assert extras['warnings'][0]['task']=='word_spotting'


def test_document_supplement_keeps_labels_chart_data_and_fields_in_readable_output():
    from ovisocr3_document import document_supplement
    extras={'visualText':[{'words':[{'text':'ACTUAL LABEL'}]}], 'charts':[{'data':{'A':'10'}}], 'keyValues':{'total':'35.00'}}
    text=document_supplement(extras)
    assert all(x in text for x in ['ACTUAL LABEL','10','35.00'])
    assert document_supplement({})==''


def test_full_document_enhancement_preserves_readable_refinements():
    import base64
    from fastapi.testclient import TestClient
    from unittest.mock import patch,AsyncMock
    import ovisocr2_adapter as adapter
    cached=[{'rawOutput':'[]','documentExtras':{'keyValues':{'total':'35.00'},'visualText':[{'words':[{'text':'ACTUAL LABEL'}]}]}}]
    with patch('ovisocr3_crosspage.PDFPages',return_value=[]),patch('ovisocr3_crosspage.enhance_document',return_value={'markdown':'Merged original body','images':{},'status':'complete'}),patch.object(adapter,'get_parser',AsyncMock(return_value=object())):
        r=TestClient(adapter.app).post('/ocr',json={'file':base64.b64encode(b'%PDF- test').decode(),'task':'document_auto','enhanceOnly':True,'pageResults':cached})
    assert r.status_code==200,r.text
    assert all(x in r.json()['markdown'] for x in ['Merged original body','35.00','ACTUAL LABEL'])


def test_document_optional_tools_use_separate_bounded_inference():
    from ovisocr3_document import parse_document_page
    from unittest.mock import Mock
    primary=Mock(return_value='[{"bbox":[0,0,999,999],"category":"image","content":"A picture"}]')
    refine=Mock(return_value='[]')
    parse_document_page(Image.new('RGB',(100,100)),primary,refine=refine)
    assert primary.call_count==1 and refine.call_count==1


def test_inference_can_limit_optional_generation_without_lowering_main_document_budget():
    from unittest.mock import patch
    import ovisocr2_adapter as adapter
    with patch.object(adapter,'dynamic_infer',return_value='[]') as infer:
        adapter.infer_task(object(),[],'prompt',max_tokens=2048)
    assert infer.call_args.kwargs['max_tokens']==2048
