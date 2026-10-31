from unittest.mock import MagicMock, patch
from types import SimpleNamespace as NS
import sys
import pytest


def test_volta_auto_dtype_uses_fp16_instead_of_unsupported_bf16():
    import ovisocr2_adapter as adapter
    loader=MagicMock()
    torch=NS(backends=NS(mps=NS(is_available=lambda:False)),cuda=NS(is_available=lambda:True,is_bf16_supported=lambda:False),float16='fp16',bfloat16='bf16',device=lambda x:x)
    transformers=NS(AutoModelForMultimodalLM=NS(from_pretrained=loader),AutoProcessor=NS(from_pretrained=MagicMock()))
    with patch.dict(sys.modules,{'torch':torch,'transformers':transformers}),patch.object(adapter,'TRANSFORMERS_DTYPE','auto'):
        adapter.TransformersOvisOCR3Parser('model')
    assert loader.call_args.kwargs['dtype']=='fp16'


def test_controller_rebuilds_selected_compatible_dockerfile_and_passes_dtype():
    import server
    with patch.dict('os.environ',{'OVISOCR3_DOCKERFILE':'Dockerfile.ovisocr3-transformers','OVISOCR3_TRANSFORMERS_DTYPE':'float16','OVISOCR3_DOCUMENT_TOOL_MAX_TOKENS':'1024'}):
        assert server.dockerfile_path_for('ovisocr2-api').name=='Dockerfile.ovisocr3-transformers'
        assert server.docker_image_name_for('ovisocr2-api')=='pandocr-ovisocr3-transformers-cu126:latest'
        payload=server.container_payload_for('ovisocr2-api',host_root=str(server.PROJECT_ROOT),network_name='test')
        assert 'OVISOCR3_TRANSFORMERS_DTYPE=float16' in payload['Env']
        assert 'OVISOCR3_DOCUMENT_TOOL_MAX_TOKENS=1024' in payload['Env']


def test_volta_bf16_emulation_is_not_mistaken_for_native_bf16_hardware():
    import ovisocr2_adapter as adapter
    loader=MagicMock()
    torch=NS(backends=NS(mps=NS(is_available=lambda:False)),cuda=NS(is_available=lambda:True,is_bf16_supported=lambda:True,get_device_capability=lambda:(7,0)),float16='fp16',bfloat16='bf16',device=lambda x:x)
    transformers=NS(AutoModelForMultimodalLM=NS(from_pretrained=loader),AutoProcessor=NS(from_pretrained=MagicMock()))
    with patch.dict(sys.modules,{'torch':torch,'transformers':transformers}),patch.object(adapter,'TRANSFORMERS_DTYPE','auto'):
        adapter.TransformersOvisOCR3Parser('model')
    assert loader.call_args.kwargs['dtype']=='fp16'


def test_low_vram_profile_releases_inactive_cuda_cache_after_task_inference():
    import ovisocr2_adapter as adapter
    cuda=NS(is_available=lambda:True,empty_cache=MagicMock())
    with patch.dict(sys.modules,{'torch':NS(cuda=cuda)}),patch.dict('os.environ',{'OVISOCR3_RELEASE_CUDA_CACHE':'1'}),patch.object(adapter,'BACKEND','transformers'),patch.object(adapter,'dynamic_infer',return_value='result'):
        assert adapter.infer_task(object(),[], 'prompt')=='result'
        cuda.empty_cache.assert_called_once()
