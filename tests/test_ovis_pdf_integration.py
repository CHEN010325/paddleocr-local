import copy
import fitz
from unittest.mock import patch


def source_pdf():
    with fitz.open() as doc:
        page=doc.new_page(width=600,height=800)
        page.draw_rect(fitz.Rect(310,180,560,300),color=(0,0,1))
        page.insert_text((310,322),'Fig. 1. Source diagram.')
        return doc.tobytes()


def test_cached_ovis_task_recovers_figures_without_rewriting_saved_results(tmp_path):
    import server
    task={'id':'cached-ovis','modelId':'ovisocr2','sourceKind':'pdf','markdown':'Body text','images':{},
          'ocrResults':[{'sourcePage':1,'pageIndex':0,'markdown':{'text':'Body text','images':{}},'parsing_res_list':[]}]}
    with patch.object(server,'TASK_DATA_DIR',tmp_path):
        source=server.task_source_path(task['id']);source.parent.mkdir(parents=True);source.write_bytes(source_pdf())
        result=server.hydrate_task_detail(task['id'],copy.deepcopy(task))
        assert '![Figure 1]' in result['markdown'] and result['images']
        assert not server.task_result_path(task['id']).exists()
        ordinary=copy.deepcopy(task);ordinary['modelId']='paddleocr-vl-1.6'
        assert server.hydrate_task_detail(task['id'],ordinary)==ordinary


def test_auto_adapter_recovers_pdf_figure_without_requiring_model_visual_detection():
    import base64
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock
    import ovisocr2_adapter as adapter
    raw='[{"bbox":[0,0,999,100],"category":"text","content":"Body text"}]'
    with patch.object(adapter,'get_parser',AsyncMock(return_value=object())),patch.object(adapter,'infer_task',return_value=raw):
        r=TestClient(adapter.app).post('/ocr',json={'file':base64.b64encode(source_pdf()).decode(),'task':'document_auto'})
    assert r.status_code==200,r.text
    result=r.json();assert '![Figure 1]' in result['markdown']
    page = result['layoutParsingResults'][0]
    assert page['parsing_res_list'] == [{'block_bbox': [0, 0, 999, 100], 'block_label': 'text', 'block_content': 'Body text'}]
    assert any(b.get('figureSource')=='pdf' for b in page['sourceFigureBlocks'])


def test_cached_enhancement_preserves_latex_delimiters_when_projecting_native_order():
    import base64,json
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock
    import ovisocr2_adapter as adapter
    with fitz.open() as doc:
        page=doc.new_page(width=600,height=800)
        page.insert_text((50,60),'Before the equation is a long complete paragraph of source text.')
        page.insert_text((80,120),'x = a / b');page.insert_text((260,120),'(1)')
        page.insert_text((50,180),'After the equation is another complete source paragraph with enough readable text.')
        pdf=doc.tobytes()
    raw=json.dumps([{'category':'display_formula','bbox':[100,130,500,170],'content':r'x=\frac{a}{b}'}])
    cached=[{'pageIndex':0,'task':'figure_captioning','sourceHash':'h','rawOutput':raw}]
    with patch.object(adapter,'get_parser',AsyncMock(return_value=object())),patch('ovisocr3_crosspage.PDFPages',return_value=[]),patch('ovisocr3_crosspage.enhance_document',return_value={'markdown':'Original model body','images':{},'status':'complete','crossPageTables':[]}):
        r=TestClient(adapter.app).post('/ocr',json={'file':base64.b64encode(pdf).decode(),'task':'document_auto','enhanceOnly':True,'pageResults':cached})
    assert r.status_code==200,r.text
    md=r.json()['markdown'];assert '$$' in md
    assert md.index('Before the equation') < md.index('$$') < md.index('After the equation')
