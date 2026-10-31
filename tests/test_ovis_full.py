import json
import pytest
from fastapi import HTTPException


def test_all_official_tasks_and_literal_prompt_replacements():
    from ovisocr3_tasks import TASKS, task_prompt
    assert len(TASKS) == 18
    prompt = task_prompt('kie_schema', {'schema': {'seller': {'name': ''}, 'items': [{'amount': ''}]}})
    assert '"seller"' in prompt and '"items"' in prompt
    assert '{schema_json}' not in prompt
    assert 'invoice' in task_prompt('reference_grounding', {'references': ['invoice']})
    assert '999' in task_prompt('region_ocr', {})


@pytest.mark.parametrize('task,options', [
    ('unknown', {}), ('kie_fields', {}), ('kie_schema', {'schema': 'invalid'}),
    ('region_ocr', {'region': [900, 0, 10, 500]}),
    ('region_ocr', {'region': [0, False, 999, 999]}),
    ('image_classification', {'candidateLabels': []}),
    ('text_translation', {}), ('visual_question_answering', {}),
])
def test_invalid_task_parameters(task, options):
    from ovisocr3_tasks import task_prompt
    with pytest.raises(HTTPException) as exc:
        task_prompt(task, options)
    assert exc.value.status_code == 422


def test_outputs_have_real_types_and_layout_has_no_invented_text():
    from ovisocr3_tasks import decode_result
    layout = decode_result('document_layout', '[{"bbox":[0,0,99,99],"category":"text"}]', {})
    assert layout[0]['category'] == 'text' and 'content' not in layout[0]
    assert decode_result('kie_open', '{"total":"10"}', {}) == {'total': '10'}
    with pytest.raises(HTTPException):
        decode_result('word_spotting', '[{"bbox":[0,0,999,999],"text":3}]', {})
    with pytest.raises(HTTPException):
        decode_result('image_classification', 'unlisted', {'targetDescription':'subject','candidateLabels':['cat','dog']})
    with pytest.raises(HTTPException):
        decode_result('kie_schema', '{"wrong":""}', {'schema': {'total': ''}})


def test_proxy_forwards_valid_task_options():
    import server
    req = server.OCRRequest(task='kie_fields', taskOptions={'fields':['total']})
    payload = server.build_ovisocr2_payload(req, 'AA==', 1)
    assert payload['task'] == 'kie_fields'
    assert payload['taskOptions'] == {'fields':['total']}


def test_task_endpoint_and_enhancement_cache_validation():
    import base64, io
    from PIL import Image
    from fastapi.testclient import TestClient
    from unittest.mock import patch, AsyncMock
    import ovisocr2_adapter as adapter
    buffer = io.BytesIO()
    Image.new('RGB', (100, 80), 'white').save(buffer, format='PNG')
    client = TestClient(adapter.app)
    with patch.object(adapter, 'get_parser', AsyncMock(return_value=object())), patch.object(adapter, 'infer_task', return_value='{"total":"10"}') as infer:
        response = client.post('/ocr', json={'file':base64.b64encode(buffer.getvalue()).decode(), 'task':'kie_fields','taskOptions':{'fields':['total']}})
        assert response.status_code == 200, response.text
        page = response.json()['layoutParsingResults'][0]
        assert page['taskResult'] == {'total':'10'} and page['rawOutput']
        assert page['sourceHash'] and page['searchableText'] is False
        assert 'total' in infer.call_args.args[2]
    bad = client.post('/ocr', files={'file':('page.png',buffer.getvalue())}, data={'task':'kie_fields','taskOptions':'invalid'})
    assert bad.status_code == 422
    bad = client.post('/ocr', json={'file':base64.b64encode(buffer.getvalue()).decode(), 'task':'document_structured','enhanceOnly':True,'pageResults':[]})
    assert bad.status_code == 422
    assert len(client.get('/capabilities').json()['tasks']) == 18


def test_dynamic_vllm_uses_one_request_with_two_images_and_rejects_length():
    from types import SimpleNamespace as NS
    from unittest.mock import MagicMock
    from PIL import Image
    from ovisocr3_inference import infer
    model = MagicMock()
    model.generate.return_value = [NS(outputs=[NS(text='answer',finish_reason='stop')])]
    parser = NS(model=model, sampling_params=NS(max_tokens=16384))
    images = [Image.new('RGB',(10,10)) for _ in range(2)]
    assert infer(parser,'vllm',images,'PAIR',max_tokens=100,min_pixels=10,max_pixels=100) == 'answer'
    request = model.generate.call_args.args[0]
    assert len(request) == 1 and len(request[0]['multi_modal_data']['image']) == 2
    content = model.get_tokenizer.return_value.apply_chat_template.call_args.args[0][0]['content']
    assert len(content) == 3 and content[-1]['text'] == 'PAIR'
    model.generate.return_value[0].outputs[0].finish_reason = 'length'
    with pytest.raises(HTTPException):
        infer(parser,'vllm',images,'PAIR',max_tokens=100,min_pixels=10,max_pixels=100)


def test_crosspage_three_pages_reuses_stage_one_preserves_non_table_and_rerenders():
    from ovisocr3_crosspage import enhance_document
    from ovisocr3_reference import RenderResult
    from PIL import Image
    from pathlib import Path
    images = [Image.new('RGB',(100,80),'white') for _ in range(3)]
    raws = ['lead%d\n<table><tr><td>%d</td></tr></table>\ntail%d' % (n,n,n) for n in range(1,4)]
    calls = []
    def infer(pages,prompt):
        calls.append(prompt)
        assert len(pages) == 2
        merged = '23' if len(calls)==1 else '123'
        return json.dumps([{'source_pages':[1,2],'html':f'<table><tr><td>{merged}</td></tr></table>'}])
    class Renderer:
        calls = 0
        def render(self, source, owner, paths, folder, name):
            self.calls += 1
            assert '23' in source and owner == 2
            return RenderResult(paths[owner-1], source, {}, {})
    renderer=Renderer()
    result=enhance_document(images,raws,'document_markdown',infer,renderer)
    assert result['status']=='complete' and renderer.calls == 1 and len(calls)==2
    assert result['crossPageTables'][0]['source_pages']==[1,2,3]
    assert result['markdown'].count('<table>') == 1
    for n in range(1,4):
        assert f'lead{n}' in result['markdown'] and f'tail{n}' in result['markdown']


def test_crosspage_negative_and_failure_preserve_pages():
    from PIL import Image
    from ovisocr3_crosspage import enhance_document
    images=[Image.new('RGB',(100,80)) for _ in range(2)]
    raws=['<table><tr><td>original</td></tr></table>'] * 2
    result=enhance_document(images,raws,'document_markdown',lambda *a: '[{"source_pages":[],"html":"No cross-page table exists between these pages."}]',object())
    assert result['status']=='complete' and not result['crossPageTables']
    result=enhance_document(images,raws,'document_markdown',lambda *a: 'invalid JSON',object())
    assert result['status']=='partial' and result['warnings'] and result['markdown'].count('original')==2


TASK_SAMPLES = {
    'document_structured': ({}, [{'bbox':[1,2,900,800],'category':'text','content':'正文'}]),
    'document_markdown': ({}, '# 正文'),
    'document_layout': ({}, [{'bbox':[1,2,900,800],'category':'text'}]),
    'plain_ocr': ({}, '正文'),
    'word_spotting': ({}, [{'bbox':[1,2,900,800],'text':'word'}]),
    'reference_grounding': ({'references':['word']}, [{'bbox':[1,2,900,800],'reference':'word'}]),
    'region_ocr': ({'region':[0,0,999,999]}, '正文'),
    'formula_recognition': ({}, '$$E=mc^2$$'),
    'chart_parsing': ({}, {'title':'chart','source':'None','x_title':'x','y_title':'y','values':{'s':{'x':'10'}}}),
    'text_translation': ({'targetLanguage':'English'}, 'text'),
    'image_classification': ({'targetDescription':'subject','candidateLabels':['cat','dog']}, 'cat'),
    'visual_question_answering': ({'question':'What?'}, 'Reasoning \\boxed{answer}'),
    'position_aware_vqa': ({'question':'What?'}, {'answer':'answer','bbox':'(1,2,900,800)'}),
    'kie_open': ({}, {'total':['1','2']}),
    'kie_fields': ({'fields':['total']}, {'total':'10'}),
    'kie_schema': ({'schema':{'items':[{'amount':''}]}}, {'items':[{'amount':'10'}]}),
    'figure_captioning': ({}, [{'bbox':[1,2,900,800],'category':'chart','content':'<caption>A chart</caption><mermaid>graph TD; A-->B</mermaid>'}]),
    'crosspage_table': ({}, [{'source_pages':[1,2],'html':'<table><tr><td>10</td></tr></table>'}]),
}


@pytest.mark.parametrize('task', TASK_SAMPLES)
def test_every_official_task_prompt_and_result_contract(task):
    from ovisocr3_tasks import task_prompt, decode_result, TASKS
    options, result = TASK_SAMPLES[task]
    prompt = task_prompt(task, options)
    assert prompt and TASKS[task]['label']
    raw = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
    assert decode_result(task, raw, options) == result
    if task != 'crosspage_table':
        from PIL import Image
        from ovisocr2_adapter import build_task_response
        response = build_task_response(task, options, [raw], [Image.new('RGB',(100,100))], 1)
        page = response['layoutParsingResults'][0]
        assert page['taskResult'] == result and page['rawOutput'] == raw and page['sourceHash']
        if task == 'figure_captioning':
            assert '```mermaid' in response['markdown'] and len(response['images']) == 1


@pytest.mark.parametrize('task,options', [
    ('kie_open', []), ('kie_open', {'unexpected':1}), ('kie_fields', {'fields':['a','a']}),
    ('kie_fields', {'fields':['']}), ('kie_schema', {'schema':{}}),
    ('visual_question_answering', {'question':'x'*32001}),
    ('image_classification',{'targetDescription':False,'candidateLabels':['a']}),
])
def test_invalid_option_types_and_limits(task,options):
    from ovisocr3_tasks import task_prompt
    with pytest.raises(HTTPException): task_prompt(task,options)


@pytest.mark.parametrize('task,raw,options', [
    ('document_layout','{}',{}), ('document_layout','[{}]',{}),
    ('document_structured','[{"bbox":[1,2,3,4],"category":"bogus","content":""}]',{}),
    ('word_spotting','[{"bbox":[1,2,3,4],"text":null}]',{}),
    ('reference_grounding','[{"bbox":[1,2,3,4],"reference":"wrong"}]',{'references':['expected']}),
    ('reference_grounding','[]',{'references':['expected']}),
    ('kie_open','{"nested":{}}',{}), ('kie_open','{"total":[1]}',{}),
    ('kie_fields','{"other":""}',{'fields':['total']}),
    ('kie_schema','{"items":[{"wrong":""}]}',{'schema':{'items':[{'amount':''}]}}),
    ('chart_parsing','{}',{}),
    ('chart_parsing','{"title":"c","source":"None","x_title":"x","y_title":"y","values":{"x":1}}',{}),
    ('position_aware_vqa','{"answer":1,"bbox":"(1,2,3,4)"}',{'question':'x'}),
    ('position_aware_vqa','{"answer":"a","bbox":"(900,2,1,4)"}',{'question':'x'}),
    ('crosspage_table','[{"source_pages":[1,3],"html":"<table></table>"}]',{}),
])
def test_malformed_outputs_are_not_reported_as_success(task,raw,options):
    from ovisocr3_tasks import decode_result
    with pytest.raises(HTTPException) as exc: decode_result(task,raw,options)
    assert exc.value.status_code == 502


def test_task_literal_placeholders_are_not_recursively_expanded():
    from ovisocr3_tasks import task_prompt
    prompt=task_prompt('image_classification',{'targetDescription':'{candidate_labels}','candidateLabels':['A']})
    assert '{candidate_labels}' in prompt and '["A"]' in prompt


def test_dynamic_transformers_and_mlx_pair_inputs_and_limits():
    from types import SimpleNamespace as NS
    from unittest.mock import MagicMock, patch
    import sys
    from contextlib import nullcontext
    from PIL import Image
    from ovisocr3_inference import infer
    class Tensor:
        def __init__(self,n): self.shape=(1,n)
        def to(self,*a): return self
        def __getitem__(self,*a): return self
    processor=NS(apply_chat_template=MagicMock(return_value={'input_ids':Tensor(4),'plain':'value'}),decode=MagicMock(return_value='answer'))
    parser=NS(device='cpu',processor=processor,model=NS(generate=MagicMock(return_value=Tensor(8)),config='config'))
    images=[Image.new('RGB',(20,10)) for _ in range(2)]
    kwargs={'max_tokens':20,'min_pixels':1,'max_pixels':100}
    with patch.dict(sys.modules,{'torch':NS(inference_mode=nullcontext)}):
        assert infer(parser,'transformers',images,'PAIR',**kwargs)=='answer'
        content=processor.apply_chat_template.call_args.args[0][0]['content']
        assert len(content)==3 and content[-1]['text']=='PAIR'
        parser.model.generate.return_value=Tensor(24)
        with pytest.raises(HTTPException): infer(parser,'transformers',images,'PAIR',**kwargs)
    output=NS(text='answer',finish_reason='stop')
    generate=MagicMock(return_value=output);template=MagicMock(return_value='rendered')
    with patch.dict(sys.modules,{'mlx_vlm':NS(generate=generate),'mlx_vlm.prompt_utils':NS(apply_chat_template=template)}):
        assert infer(parser,'mlx',images,'PAIR',**kwargs)=='answer'
        assert template.call_args.kwargs['num_images']==2 and generate.call_args.kwargs['image']==images
        output.finish_reason='length'
        with pytest.raises(HTTPException): infer(parser,'mlx',images,'PAIR',**kwargs)
    with pytest.raises(HTTPException): infer(parser,'unknown',images,'PAIR',**kwargs)
    with pytest.raises(HTTPException): infer(parser,'vllm',[],'PAIR',**kwargs)


def test_new_task_and_legacy_multipart_contracts_and_cache_enhancement():
    import base64, io
    from PIL import Image
    import fitz
    from fastapi.testclient import TestClient
    from unittest.mock import patch, AsyncMock
    import ovisocr2_adapter as adapter
    client=TestClient(adapter.app)
    doc=fitz.open();doc.new_page(width=100,height=100);doc.new_page(width=100,height=100)
    pdf=doc.tobytes();doc.close()
    raw='[{"bbox":[1,2,900,800],"category":"text","content":"中文 😀"}]'
    with patch.object(adapter,'get_parser',AsyncMock(return_value=object())), patch.object(adapter,'infer_task',return_value=raw) as infer:
        response=client.post('/ocr',files={'file':('doc.pdf',pdf)},data={'task':'document_structured','taskOptions':'{}'})
        assert response.status_code==200,response.text
        cached=[{k:p[k] for k in ('pageIndex','task','sourceHash','rawOutput')} for p in response.json()['layoutParsingResults']]
        enhanced=client.post('/ocr',json={'file':base64.b64encode(pdf).decode(),'task':'document_structured','enhanceOnly':True,'pageResults':cached})
        assert enhanced.status_code==200,enhanced.text
        assert enhanced.json()['enhancement']['status']=='complete'
        assert infer.call_count==2 # no repeated stage-one inference
        cached[0]['sourceHash']='wrong'
        bad=client.post('/ocr',json={'file':base64.b64encode(pdf).decode(),'task':'document_structured','enhanceOnly':True,'pageResults':cached})
        assert bad.status_code==422
    with patch.object(adapter,'get_parser',AsyncMock(return_value=object())), patch.object(adapter,'infer_task',return_value='{"total":"10"}'):
        invalid=client.post('/ocr',files={'file':('doc.pdf',pdf)},data={'task':'kie_open','crossPageTables':'true'})
        assert invalid.status_code==422


def test_crosspage_input_validation_and_caption_projection():
    from PIL import Image
    from ovisocr3_crosspage import enhance_document
    image=Image.new('RGB',(50,50))
    with pytest.raises(HTTPException): enhance_document([image],[],'document_structured',None)
    with pytest.raises(HTTPException): enhance_document([image],['text'],'plain_ocr',None)
    raw=json.dumps([{'bbox':[1,2,40,45],'category':'image','content':'<caption>图像 😀</caption><mermaid>graph TD; A-->B</mermaid>'}])
    result=enhance_document([image],[raw],'figure_captioning',None,object())
    assert result['status']=='complete' and '```mermaid' in result['markdown']


def test_structured_enhancement_preserves_headings_and_formula_delimiters_without_merges():
    from PIL import Image
    from ovisocr3_crosspage import enhance_document
    raw=json.dumps([
        {'bbox':[1,2,400,80],'category':'doc_title','content':'Title'},
        {'bbox':[1,90,400,180],'category':'display_formula','content':'x^2'},
        {'bbox':[1,190,400,280],'category':'inline_formula','content':'y+1'},
    ])
    result=enhance_document([Image.new('RGB',(50,50))]*2,[raw,raw],'document_structured',None,object())
    assert result['status']=='complete'
    assert result['markdown'].count('# Title')==2
    assert result['markdown'].count('$$\nx^2\n$$')==2
    assert result['markdown'].count('$y+1$')==2


def test_complete_blank_pages_do_not_become_permanent_partial_results():
    from PIL import Image
    from ovisocr3_crosspage import enhance_document
    result=enhance_document([Image.new('RGB',(10,10))]*2,['[]','[]'],'document_structured',None,object())
    assert result['status']=='complete' and not result['warnings']


def test_enhancement_can_stream_more_than_one_inference_batch_of_original_pdf_pages():
    import fitz
    from ovisocr3_crosspage import PDFPages, enhance_document
    from ovisocr2_adapter import page_hash
    doc=fitz.open()
    for _ in range(51): doc.new_page(width=10,height=10)
    pdf=doc.tobytes();doc.close()
    records=[]
    # A complete empty source page is a valid cached parse. Digests still must match.
    with fitz.open(stream=pdf,filetype='pdf') as source:
        from PIL import Image
        for n in range(len(source)):
            pix=source[n].get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
            image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
            records.append({'pageIndex':n,'task':'document_structured','rawOutput':'[]','sourceHash':page_hash(image)})
    pages=PDFPages(pdf,records,'document_structured',dpi=72,max_pages=400)
    result=enhance_document(pages,[r['rawOutput'] for r in records],'document_structured',None,object())
    assert result['status']=='complete' and result['sourcePageCount']==51
    records[2]['sourceHash']='invalid'
    with pytest.raises(HTTPException): list(pages)


def test_asset_manifest_and_build_context_include_full_integration():
    from pathlib import Path
    import hashlib,tarfile,io
    import server
    manifest=json.loads(Path('ovisocr3_prompts/manifest.json').read_text(encoding='utf8'))
    for filename,digest in manifest['sha256'].items():
        assert hashlib.sha256(Path(filename).read_bytes()).hexdigest()==digest
    with tarfile.open(fileobj=io.BytesIO(server.make_docker_build_context('ovisocr2-api'))) as tar:
        names=tar.getnames()
    for name in ['ovisocr3_tasks.py','ovisocr3_inference.py','ovisocr3_crosspage.py','ovisocr3_reference.py','requirements-ovisocr3-render.txt','ovisocr3_assets/mathjax.js','ovisocr3_prompts/kie_schema.txt']:
        assert name in names


def test_browser_module_scripts_have_javascript_mime_type():
    from fastapi.testclient import TestClient
    import server
    client=TestClient(server.app)
    for url in ['/static/bootstrap.mjs','/static/vendor/pdfjs/pdf.min.mjs']:
        response=client.get(url)
        assert response.status_code==200
        assert response.headers['content-type'].split(';')[0] in {'application/javascript','text/javascript'}


def test_pair_adapter_and_document_reconstruction_api():
    import io,base64,fitz
    from PIL import Image
    from fastapi.testclient import TestClient
    from unittest.mock import patch,AsyncMock
    import ovisocr2_adapter as adapter
    client=TestClient(adapter.app)
    stream=io.BytesIO();Image.new('RGB',(80,80),'red').save(stream,format='PNG')
    encoded=base64.b64encode(stream.getvalue()).decode()
    negative='[{"source_pages":[],"html":"No cross-page table exists between these pages."}]'
    with patch.object(adapter,'get_parser',AsyncMock(return_value=object())),patch.object(adapter,'infer_task',return_value=negative) as infer:
        response=client.post('/crosspage',json={'images':[encoded,encoded],'markdowns':['<table><tr><td>A</td></tr></table>']*2})
        assert response.status_code==200,response.text
        assert response.json()['taskResult'][0]['source_pages']==[]
        assert len(infer.call_args.args[1])==2
        assert '<INPUT_PAGE index="2">' in infer.call_args.args[2]
        assert client.post('/crosspage',json={'images':[encoded,encoded],'markdowns':['','a']}).status_code==422
    document=fitz.open();document.new_page(width=50,height=50);document.new_page(width=50,height=50)
    pdf=document.tobytes();document.close()
    table='[{"bbox":[1,2,900,800],"category":"table","content":"<table><tr><td>A</td></tr></table>"}]'
    with patch.object(adapter,'get_parser',AsyncMock(return_value=object())),patch.object(adapter,'infer_task',side_effect=[table,table,negative]):
        response=client.post('/ocr',files={'file':('doc.pdf',pdf)},data={'task':'crosspage_table'})
        assert response.status_code==200,response.text
        assert response.json()['enhancement']['status']=='complete'
    with patch.object(adapter,'prepare_images',return_value=([],1)):
        assert client.post('/ocr',json={'file':encoded,'task':'plain_ocr'}).status_code==400
    assert client.post('/ocr',json={'file':base64.b64encode(pdf).decode(),'task':'document_structured','enhanceOnly':True,'pageResults':'invalid'}).status_code==422


def test_server_task_capabilities_multipart_pair_proxy_and_legacy_service_detection():
    import asyncio
    from types import SimpleNamespace as NS
    from unittest.mock import patch, AsyncMock
    from fastapi.testclient import TestClient
    import httpx,server
    client=TestClient(server.app)
    assert len(client.get('/api/ovisocr3/capabilities').json()['tasks'])==18
    with pytest.raises(HTTPException): server.parse_ovis_json_field('invalid',dict)
    with pytest.raises(HTTPException): server.parse_ovis_json_field('[]',dict)
    assert server.parse_ovis_json_field('',dict)=={}
    assert server.parse_ovis_json_field({'a':1},dict)=={'a':1}
    req=NS(headers={'content-type':'multipart/form-data'},form=AsyncMock(return_value={'file':NS(read=AsyncMock(side_effect=[b'x',b''])),'task':'kie_fields','taskOptions':'{"fields":["total"]}'}))
    parsed,raw=asyncio.run(server.parse_ocr_input(req))
    assert raw==b'x' and parsed.taskOptions=={'fields':['total']}
    class Client:
        def __init__(self,*a,**kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*a): pass
        async def post(self,*a,**kw): return response
    response=NS(status_code=200,json=lambda:{'task':'crosspage_table','taskResult':[]},text='fixture')
    body=json.dumps({'images':['a','b'],'markdowns':['one','two']}).encode()
    request=NS(body=AsyncMock(return_value=body))
    with patch.object(server,'ENABLE_OVISOCR2',False),pytest.raises(HTTPException): asyncio.run(server.proxy_ovisocr3_pair(request))
    with patch.object(server,'ENABLE_OVISOCR2',True),patch.object(server,'acquire_ocr_slot',AsyncMock(return_value=None)),patch.object(server,'release_ocr_slot',AsyncMock()) as release,patch.object(server.httpx,'AsyncClient',Client):
        assert asyncio.run(server.proxy_ovisocr3_pair(request))['task']=='crosspage_table'
        release.assert_awaited_once()
        response.status_code=502
        with pytest.raises(HTTPException): asyncio.run(server.proxy_ovisocr3_pair(request))
        request.body=AsyncMock(return_value=b'invalid')
        with pytest.raises(HTTPException): asyncio.run(server.proxy_ovisocr3_pair(request))
        with patch.object(server,'MAX_REQUEST_BYTES',1),pytest.raises(HTTPException): asyncio.run(server.proxy_ovisocr3_pair(request))
        response.status_code=200;response.json=lambda:{'layoutParsingResults':[]}
        with patch.object(server,'prepare_service_input',return_value=('a',1)),pytest.raises(HTTPException) as exc:
            asyncio.run(server.run_ovisocr2_request(server.OCRRequest(task='plain_ocr'),b'x'))
        assert exc.value.status_code==503


def test_pdf_page_protocol_rejects_incomplete_order_task_and_document_limits():
    import fitz
    from ovisocr3_crosspage import PDFPages
    doc=fitz.open();doc.new_page();pdf=doc.tobytes();doc.close()
    for records in [[],[{}],[{'pageIndex':0,'task':'wrong','rawOutput':'[]'}],[{'pageIndex':False,'task':'document_structured','rawOutput':'[]'}]]:
        with pytest.raises(HTTPException): PDFPages(pdf,records,'document_structured',dpi=72,max_pages=400)
    with pytest.raises(HTTPException): PDFPages(pdf,[],'document_structured',dpi=72,max_pages=0)


def test_nontranscription_task_cannot_be_exported_as_searchable_source_pdf(tmp_path):
    from exporters import build_searchable_pdf,ExportError
    with pytest.raises(ExportError):
        build_searchable_pdf({'ocrResults':[{'searchableText':False}]},tmp_path/'missing.pdf')


def test_enhanced_result_heavy_storage_is_hydrated_and_survives_metadata_saves(tmp_path):
    from unittest.mock import patch
    import server
    task={'id':'enhance-store','status':'completed','markdown':'merged','images':{},'ocrResults':[],
          'ovisOptions':{'task':'document_structured','crossPageTables':True},'ovisEnhancementStatus':'complete',
          'ovisResult':{'originalMarkdown':'original','enhancement':{'status':'complete','crossPageTables':[]}},'batches':[]}
    with patch.object(server,'TASK_DATA_DIR',tmp_path):
        server.ensure_task_data_dir()
        stored,payload=server.split_task_for_storage(task)
        server.write_json_file(server.task_file_path(task['id']),stored)
        server.write_json_file(server.task_result_path(task['id']),payload)
        metadata=server.read_task_file(server.task_file_path(task['id']))
        assert 'ovisResult' not in metadata
        detail=server.hydrate_task_detail(task['id'],metadata)
        assert detail['ovisResult']['originalMarkdown']=='original'
        summary=server.task_summary(detail)
        assert summary['ovisOptions']==task['ovisOptions'] and summary['ovisEnhancementStatus']=='complete'


def test_invalid_empty_task_option_array_is_rejected_before_inference():
    import io,base64
    from PIL import Image
    from fastapi.testclient import TestClient
    from unittest.mock import patch,AsyncMock
    import ovisocr2_adapter as adapter
    stream=io.BytesIO();Image.new('RGB',(10,10)).save(stream,format='PNG')
    with patch.object(adapter,'get_parser',AsyncMock(return_value=object())),patch.object(adapter,'infer_task',return_value='text'):
        response=TestClient(adapter.app).post('/ocr',json={'file':base64.b64encode(stream.getvalue()).decode(),'task':'plain_ocr','taskOptions':[]})
        assert response.status_code==422


def test_json_proxy_rejects_invalid_typed_task_parameters_with_422():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import server
    for payload in [{'image':'AA==','taskOptions':[]}, ['invalid-root']]:
        request=SimpleNamespace(headers={'content-type':'application/json'},body=AsyncMock(return_value=json.dumps(payload).encode()))
        with pytest.raises(HTTPException) as exc: asyncio.run(server.parse_ocr_input(request))
        assert exc.value.status_code==422
