import json
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

import ovisocr2_adapter as adapter


def test_windows_port_resolution_matches_compose_empty_fallback(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path
    shell = shutil.which('powershell') or shutil.which('pwsh')
    if not shell:
        pytest.skip('PowerShell unavailable')
    source = str(Path(__file__).resolve().parents[1] / 'scripts/windows-one-click.ps1').replace("'", "''")
    probe = tmp_path / 'port.ps1'
    probe.write_text(r"""
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile('SOURCE',[ref]$null,[ref]$null)
$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @('Get-EnvLineValue','Resolve-OvisApiPort')},$true) | ForEach-Object { Invoke-Expression $_.Extent.Text }
$env:OVISOCR2_API_PORT=''; $env:OVISOCR3_API_PORT=''
$values=@()
$values+=Resolve-OvisApiPort @()
$values+=Resolve-OvisApiPort @('OVISOCR2_API_PORT=','OVISOCR3_API_PORT=')
$values+=Resolve-OvisApiPort @('OVISOCR2_API_PORT=18082','OVISOCR3_API_PORT=')
$values+=Resolve-OvisApiPort @('OVISOCR2_API_PORT=18082','OVISOCR3_API_PORT=18083')
$env:OVISOCR3_API_PORT='18084'
$values+=Resolve-OvisApiPort @('OVISOCR3_API_PORT=18083')
$env:OVISOCR3_API_PORT='70000'
try { Resolve-OvisApiPort @() | Out-Null; throw 'invalid accepted' } catch { if ($_.Exception.Message -eq 'invalid accepted') { throw } }
ConvertTo-Json -InputObject $values -Compress
""".replace('SOURCE', source), encoding='utf-8')
    result = subprocess.run([shell, '-NoProfile', '-File', str(probe)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip()) == ['8084', '8084', '18082', '18083', '18084']


def test_default_model_and_official_structured_prompt():
    assert adapter.MODEL_NAME == 'ATH-MaaS/OvisOCR3'
    assert adapter.MODEL_REVISION == '4833d5d2afa6e769ee933f024504d67ace80a24c'
    assert adapter.OUTPUT_FORMAT == 'structured'
    assert 'single JSON array in natural reading order' in adapter.PROMPT


def test_old_model_defaults_upgrade_but_custom_explicit_settings_survive():
    from ovisocr3_config import resolve_model_settings
    current=('ATH-MaaS/OvisOCR3','4833d5d2afa6e769ee933f024504d67ace80a24c')
    assert resolve_model_settings({}) == current
    assert resolve_model_settings({'OVISOCR2_MODEL_NAME':'ATH-MaaS/OvisOCR2','OVISOCR2_MODEL_REVISION':'old'}) == current
    assert resolve_model_settings({'OVISOCR3_MODEL_NAME':'custom/model','OVISOCR3_MODEL_REVISION':'fixed'}) == ('custom/model','fixed')


def test_structured_document_preserves_regions_and_builds_markdown_and_crops():
    text=json.dumps([
        {'bbox':[10,20,900,100],'category':'doc_title','content':'Heading'},
        {'bbox':[20,110,900,200],'category':'text','content':'正文 ALPHA'},
        {'bbox':[20,210,900,400],'category':'table','content':'<table><tr><td>12</td></tr></table>'},
        {'bbox':[20,410,900,500],'category':'display_formula','content':'E=mc^2'},
        {'bbox':[20,510,900,700],'category':'image','content':'<img src="images/bbox_20_510_900_700.jpg" />'},
    ])
    with patch.object(adapter,'OUTPUT_FORMAT','structured'):
        r=adapter.build_response([text],[Image.new('RGB',(100,200),'white')],1)
    page=r['layoutParsingResults'][0]
    assert len(page['parsing_res_list'])==5
    assert page['parsing_res_list'][0]['block_bbox']==[10,20,900,100]
    assert page['parsing_res_list'][1]['block_content']=='正文 ALPHA'
    assert 'Heading' in r['markdown'] and '$$' in r['markdown']
    assert len(r['images'])==1 and 'ovisocr3' in next(iter(r['images']))
    assert page['parser']=='ovisocr3'
    assert page['parsing_res_list'][0]['block_content'].startswith('# ')
    assert '$$' in page['parsing_res_list'][3]['block_content']
    assert next(iter(r['images'])) in page['parsing_res_list'][4]['block_content']


@pytest.mark.parametrize('value',[
    'not JSON','{}','[{}]','[1]',
    '[{"bbox":[0,0,1001,200],"category":"text","content":"bad"}]',
    '[{"bbox":[50,20,10,100],"category":"text","content":"bad"}]',
    '[{"bbox":[0,0,100,100],"category":"unknown","content":"bad"}]',
    '[{"bbox":[0,0,100,100],"category":"text","content":null}]',
])
def test_malformed_structured_output_is_not_a_success(value):
    with patch.object(adapter,'OUTPUT_FORMAT','structured'),pytest.raises(HTTPException) as exc:
        adapter.build_response([value],[Image.new('RGB',(10,10))],1)
    assert exc.value.status_code==502


def test_page_count_cannot_silently_drop_a_result():
    with pytest.raises(HTTPException):
        adapter.build_response([],[Image.new('RGB',(10,10))],1)


def test_new_model_id_is_an_alias_for_existing_task_identity():
    import server
    with patch.object(server,'ENGINE_SETTINGS',None):
        assert server.OCRRequest(file='AA==',modelId='ovisocr3').modelId=='ovisocr2'
        assert server.ModelSwitchRequest(modelId='ovisocr3').modelId=='ovisocr2'


def test_container_selects_physical_gpu_but_uses_internal_zero():
    import server
    with patch.object(server,'PANDOCR_GPU_DEVICE_ID','3'):
        config=server.container_payload_for('ovisocr2-api',host_root=str(server.PROJECT_ROOT),network_name='test')
    assert 'CUDA_VISIBLE_DEVICES=0' in config['Env']
    assert config['HostConfig']['DeviceRequests'][0]['DeviceIDs']==['3']


def test_existing_second_generation_container_is_recreated_without_volumes():
    import server
    calls=[]
    async def api(method,path,**kwargs):
        calls.append((method,path))
        return type('Response',(),{'status_code':204 if method=='DELETE' else 201,'text':''})()
    with patch.object(server,'inspect_container',AsyncMock(return_value={'exists':True,'running':True,'image':'pandocr-ovisocr2:latest'})), patch.object(server,'docker_container_action',AsyncMock()) as stop, patch.object(server,'assert_containers_stopped_twice',AsyncMock()), patch.object(server,'docker_api_request',side_effect=api):
        asyncio.run(server.docker_create_container('ovisocr2-api',{'Image':'pandocr-ovisocr3:latest'}))
    stop.assert_awaited_once_with('ovisocr2-api','stop')
    assert calls[0]==('DELETE','/containers/ovisocr2-api?v=0')


@pytest.mark.parametrize('image,expected',[('pandocr-ovisocr2:latest',True),('pandocr-ovisocr3:latest',False)])
def test_ovis_generation_probe(image,expected):
    import server
    with patch.object(server,'inspect_container',AsyncMock(return_value={'exists':True,'image':image})):
        assert asyncio.run(server.ovisocr_generation_upgrade_needed()) is expected


def test_old_weights_cannot_report_third_generation_ready():
    import server
    assert server.model_health_ready_state('ovisocr2',True,{'model':'ATH-MaaS/OvisOCR2'}) == (False,'generation-mismatch')


def test_cuda_transformers_selects_sdpa_in_auto_attention_mode():
    loader=MagicMock()
    torch=SimpleNamespace(backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda:False)),cuda=SimpleNamespace(is_available=lambda:True),bfloat16='bf16',device=lambda x:x)
    transformers=SimpleNamespace(AutoModelForMultimodalLM=SimpleNamespace(from_pretrained=loader),AutoProcessor=SimpleNamespace(from_pretrained=MagicMock()))
    with patch.dict(sys.modules,{'torch':torch,'transformers':transformers}),patch.object(adapter,'ATTENTION_IMPLEMENTATION','auto'):
        adapter.TransformersOvisOCR3Parser('model')
    assert loader.call_args.kwargs['attn_implementation']=='sdpa'


@pytest.mark.parametrize('outputs',[[],[SimpleNamespace(outputs=[SimpleNamespace(finish_reason='length')])]])
def test_missing_or_truncated_vllm_generation_is_an_error(outputs):
    parser=object.__new__(adapter.VllmOvisOCR3Parser)
    parser.prompt='prompt';parser.sampling_params='params'
    parser.model=SimpleNamespace(generate=lambda *_:outputs)
    with pytest.raises(HTTPException) as exc:
        parser.parse([Image.new('RGB',(10,10))])
    assert exc.value.status_code==502


def test_bad_output_mode_fails_at_startup():
    import os,runpy
    with patch.dict(os.environ,{'OVISOCR3_OUTPUT_FORMAT':'invalid'}),pytest.raises(ValueError):
        runpy.run_path(adapter.__file__)


def test_new_revision_key_survives_a_legacy_model_name():
    from ovisocr3_config import resolve_model_settings
    assert resolve_model_settings({'OVISOCR2_MODEL_NAME':'ATH-MaaS/OvisOCR2','OVISOCR3_MODEL_REVISION':'explicit'}) == ('ATH-MaaS/OvisOCR3','explicit')
