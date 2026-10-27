"""Protocol regressions against the pinned Unlimited-OCR-2 model contract."""
import io
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

import unlimited_ocr_adapter as adapter


def page():
    out = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(out, "PNG")
    return out.getvalue()


def test_sglang_page_markers_and_page_guards():
    with patch.object(adapter, "get_no_repeat_processor_str", return_value="processor"):
        payload = adapter.build_sglang_payload([page(), page()], 0)
    content = payload["messages"][0]["content"]
    assert content[-1] == {"type": "text", "text": "<PAGE><image><PAGE><image>Multi page parsing."}
    assert all(item["type"] == "image_url" for item in content[:-1])
    assert payload["images_config"]["crop_mode"] is False
    assert payload["custom_params"]["page_token_id"] == 128827
    assert payload["custom_params"]["eos_token_id"] == 1
    assert payload["custom_params"]["max_page_tokens"] == 20000


def test_single_page_prompt_includes_page_boundary():
    payload = adapter.build_sglang_payload([page()], 1)
    assert payload["messages"][0]["content"][-1]["text"] == "<PAGE><image>Multi page parsing."
    assert payload["images_config"]["crop_mode"] is True


def test_transformers_single_page_uses_trained_prompt_and_output_budget():
    model = SimpleNamespace(infer=MagicMock(return_value="<PAGE>Hello"))
    with patch.object(adapter, "cleanup_torch_accelerator_cache"):
        text, _ = adapter.run_transformers_inference_sync(None, model, [page()], 1)
    assert text == "<PAGE>Hello"
    args = model.infer.call_args.kwargs
    assert args["prompt"] == "Multi page parsing."
    assert args["max_new_tokens"] == adapter.MAX_TOKENS
    assert args["max_length"] > args["max_new_tokens"]


def test_transformers_multi_page_retains_input_page_order():
    model = SimpleNamespace(infer_multi=MagicMock(return_value=("<PAGE>A<PAGE>B", 6)))
    with patch.object(adapter, "cleanup_torch_accelerator_cache"):
        text, _ = adapter.run_transformers_inference_sync(None, model, [page(), page()], 0)
    assert text == "<PAGE>A<PAGE>B"
    args = model.infer_multi.call_args.kwargs
    assert args["prompt"] == "Multi page parsing."
    assert args["max_new_tokens"] == adapter.MAX_TOKENS
    assert len(args["image_files"]) == 2


def test_page_markers_override_coordinate_heuristics_and_stay_out_of_markdown():
    raw = "<PAGE><|det|>text [0, 0, 100, 100]<|/det|>First<PAGE><|det|>text [0, 200, 100, 300]<|/det|>Second"
    response = adapter.build_adapter_response(raw, 2, 0, {}, [page(), page()])
    assert "<PAGE>" not in response["markdown"]
    pages = response["layoutParsingResults"]
    assert pages[0]["parsing_res_list"][0]["block_content"] == "First"
    assert pages[1]["parsing_res_list"][0]["block_content"] == "Second"


def test_plain_output_is_distributed_by_explicit_page_markers():
    response = adapter.build_adapter_response("<PAGE>First<PAGE>Second", 2, 0, {}, [page(), page()])
    assert [p["markdown"]["text"] for p in response["layoutParsingResults"]] == ["First", "Second"]


def test_stream_stdout_preserves_first_page_marker_on_its_own_line():
    raw = "INFO: preparing\n<PAGE>\n<|det|>text [0,0,100,100]<|/det|>First\n<PAGE>\n<|det|>image [0,200,100,300]<|/det|>Second"
    captured = adapter.extract_layout_text_from_transformers_stdout(raw)
    blocks = adapter.parse_layout_blocks(captured)
    adapter.assign_block_pages(blocks, 2)
    assert [b["page_index"] for b in blocks] == [0, 1]


def test_compose_and_controller_use_the_same_ocr2_runtime_contract():
    import pytest
    if not shutil.which("docker"):
        pytest.skip("Docker CLI unavailable")
    root = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PANDOCR_", "UNLIMITED_OCR_"))}
    config = json.loads(subprocess.check_output(
        ["docker", "compose", "--env-file", str(root / "env.docker"), "--profile", "unlimited-ocr", "config", "--format", "json"],
        cwd=root, env=env, text=True,
    ))["services"]
    controller_env = {**env, **config["pandocr-controller"]["environment"]}
    code = (
        "import json,server; print(json.dumps({name:server.container_payload_for(name,host_root='/workspace',network_name='test') "
        "for name in ['unlimited-ocr-api','unlimited-ocr-sglang']}))"
    )
    dynamic = json.loads(subprocess.check_output([sys.executable, "-c", code], cwd=root, env=controller_env, text=True))
    assert dynamic["unlimited-ocr-sglang"]["Cmd"] == config["unlimited-ocr-sglang"]["command"]
    sg_env = dict(v.split("=", 1) for v in dynamic["unlimited-ocr-sglang"]["Env"])
    for key in ['SGLANG_UOCR_GRAPH_METADATA_REFRESH', 'SGLANG_UOCR_GRAPH_METADATA_INPUTS']:
        assert sg_env[key] == config['unlimited-ocr-sglang']['environment'][key] == '0'
    api_env = dict(v.split("=", 1) for v in dynamic["unlimited-ocr-api"]["Env"])
    for key in ["UNLIMITED_OCR_MODEL_NAME", "UNLIMITED_OCR_MODEL_REVISION", "UNLIMITED_OCR_SERVED_MODEL_NAME", "UNLIMITED_OCR_CONTEXT_LENGTH", "UNLIMITED_OCR_MAX_TOKENS", "UNLIMITED_OCR_SGLANG_MAX_TOKENS"]:
        assert api_env[key] == config["unlimited-ocr-api"]["environment"][key]


def test_request_failure_does_not_poison_loaded_model_health():
    with patch.object(adapter, "TRANSFORMERS_MODEL_ERROR", None), patch.object(adapter, "TRANSFORMERS_MODEL", object()):
        assert adapter.transformers_error_detail(ValueError("invalid image")) == "invalid image"
        assert adapter.TRANSFORMERS_MODEL_ERROR is None


def test_ocr2_does_not_reuse_first_generation_dependency_images():
    import server
    assert server.docker_image_name_for('unlimited-ocr-api') == 'pandocr-unlimited-ocr2-transformers:latest'
    assert server.docker_image_name_for('unlimited-ocr-sglang') == 'pandocr-unlimited-ocr2-sglang:latest'


def test_first_generation_container_is_stopped_and_recreated_without_removing_volumes():
    import server
    calls = []
    async def stop(name, action):
        calls.append(('stop', name, action))
    async def request(method, path, **kwargs):
        calls.append((method, path))
        return SimpleNamespace(status_code=204, text='')
    existing = {'exists': True, 'running': True, 'image': 'pandocr-unlimited-ocr-transformers:latest'}
    with patch.object(server, 'inspect_container', AsyncMock(return_value=existing)), patch.object(
        server, 'docker_container_action', side_effect=stop
    ), patch.object(server, 'assert_containers_stopped_twice', AsyncMock()) as verified, patch.object(
        server, 'docker_api_request', side_effect=request
    ):
        asyncio.run(server.docker_create_container('unlimited-ocr-api', {'Image': 'pandocr-unlimited-ocr2-transformers:latest'}))
    verified.assert_awaited_once()
    assert calls == [
        ('stop', 'unlimited-ocr-api', 'stop'),
        ('DELETE', '/containers/unlimited-ocr-api?v=0'),
        ('POST', '/containers/create?name=unlimited-ocr-api'),
    ]


def test_switching_backend_on_running_ocr1_uses_full_migration_and_release_gate():
    import server
    with patch.object(server, 'unlimited_ocr_generation_upgrade_needed', AsyncMock(return_value=True)), patch.object(
        server, 'model_runtime_status', AsyncMock(return_value={'running': True})
    ), patch.object(server, 'deploy_and_activate_model_runtime', AsyncMock()) as deploy:
        asyncio.run(server.activate_unlimited_ocr_backend('sglang'))
    deploy.assert_awaited_once_with('unlimited-ocr', 'sglang')


def test_generation_probe_recognizes_old_and_current_container_images():
    import server
    for image, expected in [('pandocr-unlimited-ocr-transformers:latest', True), ('pandocr-unlimited-ocr2-transformers:latest', False)]:
        with patch.object(server, 'unlimited_ocr_runtime_backend', 'transformers'), patch.object(
            server, 'inspect_container', AsyncMock(return_value={'exists': True, 'image': image})
        ):
            assert asyncio.run(server.unlimited_ocr_generation_upgrade_needed()) is expected


def test_backend_generation_probe_failure_finishes_operation_with_error():
    import server
    with patch.object(server, 'unlimited_ocr_generation_upgrade_needed', AsyncMock(side_effect=RuntimeError('Docker inspect unavailable'))), patch.object(
        server, 'set_model_runtime_operation'
    ) as operation:
        asyncio.run(server.activate_unlimited_ocr_backend('sglang'))
    operation.assert_called_once_with('error', 'Docker inspect unavailable', 'unlimited-ocr')


def test_second_generation_block_tokens_render_and_keep_page_coordinates():
    raw = '<PAGE><BLOCK>title<LEVEL1>[10, 20, 900, 80]<FORK><CHILD>ALPHA 中文<PAGE><BLOCK>text[20, 30, 800, 90]<FORK><CHILD>BETA 第二页'
    rendered, images = adapter.render_unlimited_ocr_document(raw)
    assert 'ALPHA 中文' in rendered and 'BETA 第二页' in rendered
    assert all(token not in rendered for token in ['<BLOCK>', '<FORK>', '<CHILD>', '<LEVEL1>', '[10,'])
    pages = adapter.build_layout_parsing_results(raw, rendered, images, 2, 0, {})
    assert pages[0]['parsing_res_list'][0]['block_bbox'] == [10, 20, 900, 80]
    assert pages[1]['parsing_res_list'][0]['block_content'] == 'BETA 第二页'


def test_second_generation_image_block_without_body_keeps_crop():
    raw = '<PAGE><BLOCK>text[10, 10, 900, 50]<CHILD>Hello<BLOCK>image[20, 100, 600, 700]'
    blocks = adapter.parse_layout_blocks(raw)
    assert [block['label'] for block in blocks] == ['text', 'image']
    rendered, images = adapter.render_unlimited_ocr_document(raw, [page()])
    assert len(images) == 1 and '![' in rendered
    assert 'image[' not in rendered


def test_controller_recovers_windows_host_root_when_running_on_linux():
    import server
    mounts = {'Mounts': [{'Destination': '/app/static', 'Source': r'D:\paddleocr-vl-webui\static'}]}
    with patch.object(server, 'docker_inspect_self', AsyncMock(return_value=mounts)):
        assert asyncio.run(server.docker_host_repo_root()) == 'D:/paddleocr-vl-webui'


def test_incomplete_block_coordinates_are_not_rendered_as_document_text():
    raw = '<PAGE><BLOCK>text[1,2,900,100]<FORK>Hello<BLOCK>title<LEVEL1>[68, 67, 67,'
    rendered, _ = adapter.render_streaming_markdown(raw)
    assert rendered == 'Hello'


def test_runaway_block_coordinate_loop_triggers_guard_before_rendering():
    raw = '<PAGE><BLOCK>title<LEVEL1>[68,' + '100,' * 24
    assert adapter.detect_unlimited_structural_artifacts(raw) == 'layout coordinate loop'
