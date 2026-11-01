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
    assert any(b.get('figureSource')=='pdf' for b in result['layoutParsingResults'][0]['parsing_res_list'])
