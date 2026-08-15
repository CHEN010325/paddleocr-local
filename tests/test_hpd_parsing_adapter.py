import asyncio
import base64
import io
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import fitz
import httpx
import pytest
from fastapi import HTTPException
from PIL import Image

import hpd_parsing_adapter as adapter


def test_parse_blocks_and_build_response():
    raw = (
        "<BLOCK>title [10, 20, 900, 80]<CHILD># Heading"
        "<BLOCK>text [10, 100, 900, 200]<CHILD>Body"
        "<BLOCK>image [20, 220, 600, 700]"
    )
    blocks = adapter.parse_blocks(raw)
    assert blocks == [
        {"type": "title", "bbox": [10, 20, 900, 80], "text": "# Heading"},
        {"type": "text", "bbox": [10, 100, 900, 200], "text": "Body"},
        {"type": "image", "bbox": [20, 220, 600, 700], "text": ""},
    ]
    result = adapter.build_response([Image.new("RGB", (100, 200), "white")], [("# Heading\n\nBody", blocks)])
    image_path = "images/page_1_image_1.jpg"
    assert result["markdown"] == f"# Heading\n\nBody\n\n![image 1]({image_path})"
    assert list(result["images"]) == [image_path]
    crop = Image.open(io.BytesIO(base64.b64decode(result["images"][image_path])))
    assert crop.size == (58, 96)
    assert result["layoutParsingResults"][0]["markdown"]["images"] == result["images"]
    assert result["layoutParsingResults"][0]["parsing_res_list"][2]["block_content"] == (
        f"![image 1]({image_path})"
    )
    assert result["layoutParsingResults"][0]["parser"] == "hpd-parsing"


def test_build_response_promotes_only_the_first_page_title_to_document_title():
    pages = [Image.new("RGB", (20, 20), "white") for _ in range(2)]
    parsed = [
        ("Paper Title", [{"type": "title", "bbox": [0, 0, 1000, 100], "text": "Paper Title"}]),
        ("Methods", [{"type": "title", "bbox": [0, 0, 1000, 100], "text": "Methods"}]),
    ]
    result = adapter.build_response(pages, parsed)
    assert result["layoutParsingResults"][0]["markdown"]["text"] == "# Paper Title"
    assert result["layoutParsingResults"][1]["markdown"]["text"] == "## Methods"


def test_normalized_bbox_to_pixels_clamps_and_rejects_invalid_boxes():
    assert adapter.normalized_bbox_to_pixels([100, 200, 500, 600], 100, 200) == (10, 40, 50, 120)
    assert adapter.normalized_bbox_to_pixels([-10, -20, 1100, 1200], 100, 200) == (0, 0, 100, 200)
    assert adapter.normalized_bbox_to_pixels([500, 500, 400, 600], 100, 200) is None
    assert adapter.normalized_bbox_to_pixels([0, 0, "bad", 10], 100, 200) is None


def test_rag_markdown_block_formatting():
    assert adapter.format_rag_text_block("title", "II. RELATED WORK", [100, 100, 400, 120]) == (
        "## II. RELATED WORK"
    )
    assert adapter.format_rag_text_block("title", "A. Methods", [100, 100, 400, 120]) == "### A. Methods"
    assert adapter.format_rag_text_block("title", "C. Analysis", [100, 100, 400, 120]) == "### C. Analysis"
    assert adapter.format_rag_text_block("doc_title", "Paper Title", [100, 50, 900, 120]) == "# Paper Title"
    assert adapter.format_rag_text_block("text", "Abstract\u2014First line\nsecond line.", None) == (
        "## Abstract\n\nFirst line second line."
    )
    assert adapter.format_rag_text_block("text", "- First item", None) == "- First item"
    assert adapter.format_rag_text_block("equation", r"\(x = y\)", None) == "$$\nx = y\n$$"
    assert adapter.format_rag_text_block("ref_text", "[12] A reference.\nSecond line.", None) == (
        "12. A reference. Second line."
    )
    assert adapter.format_rag_text_block("footer", "Conference footer", None) == ""


def test_html_table_to_gfm_expands_spans_for_rag():
    table = (
        '<table><tr><td rowspan="2">Model</td><td colspan="2">Score</td></tr>'
        '<tr><td>A</td><td>B</td></tr><tr><td>Ours</td><td>1</td><td>2</td></tr></table>'
    )
    assert adapter.html_table_to_gfm(table) == (
        "| Model | Score / A | Score / B |\n"
        "| --- | --- | --- |\n"
        "| Ours | 1 | 2 |"
    )


def test_html_table_repairs_real_world_hpd_colspan_drift():
    table = (
        '<table><tr><td rowspan="3">Model</td><td rowspan="3">Methods</td>'
        '<td colspan="5">In-Domain</td><td colspan="3">Out-of-Domain</td>'
        '<td colspan="2">Overall Performance</td><td></td></tr>'
        '<tr><td colspan="2">HotpotQA</td><td colspan="3">NQ</td>'
        '<td colspan="3">MuSiQue</td><td colspan="2">Average</td><td></td></tr>'
        '<tr><td>EM</td><td>ACC</td><td>Total Time (s)</td>'
        '<td>EM</td><td>ACC</td><td>Total Time (s)</td>'
        '<td>EM</td><td>ACC</td><td>Total Time (s)</td><td>EM</td><td>ACC</td></tr>'
        '<tr><td>LLM</td><td>Baseline</td><td>1</td><td>2</td><td>3</td>'
        '<td>4</td><td>5</td><td>6</td><td>7</td><td>8</td><td>9</td><td>10</td><td>11</td></tr></table>'
    )
    markdown = adapter.html_table_to_gfm(table)
    header = markdown.splitlines()[0]
    assert header == (
        "| Model | Methods | In-Domain / HotpotQA / EM | In-Domain / HotpotQA / ACC | "
        "In-Domain / HotpotQA / Total Time (s) | In-Domain / NQ / EM | In-Domain / NQ / ACC | "
        "In-Domain / NQ / Total Time (s) | Out-of-Domain / MuSiQue / EM | "
        "Out-of-Domain / MuSiQue / ACC | Out-of-Domain / MuSiQue / Total Time (s) | "
        "Overall Performance / Average / EM | Overall Performance / Average / ACC |"
    )
    assert markdown.splitlines()[-1].count("|") == 14


def test_html_table_infers_leaf_metrics_omitted_from_hpd_header():
    table = (
        '<table><tr><td rowspan="2">Model</td><td rowspan="2">Methods</td>'
        '<td colspan="4">In-Domain</td><td colspan="3">Out-of-Domain</td>'
        '<td colspan="3">Overall Performance</td></tr>'
        '<tr><td colspan="2">HotpotQA</td><td colspan="2">NQ</td>'
        '<td colspan="2">MuSiQue</td><td colspan="2">Average</td>'
        '<td>EM</td><td>ACC</td><td>Total Time (s)</td></tr>'
        '<tr><td>Gemma</td><td>LLM</td>'
        + ''.join(f'<td>{value}</td>' for value in range(1, 13))
        + '</tr></table>'
    )
    header = adapter.html_table_to_gfm(table).splitlines()[0]
    assert header == (
        "| Model | Methods | In-Domain / HotpotQA / EM | In-Domain / HotpotQA / ACC | "
        "In-Domain / HotpotQA / Total Time (s) | In-Domain / NQ / EM | In-Domain / NQ / ACC | "
        "In-Domain / NQ / Total Time (s) | Out-of-Domain / MuSiQue / EM | "
        "Out-of-Domain / MuSiQue / ACC | Out-of-Domain / MuSiQue / Total Time (s) | "
        "Overall Performance / Average / EM | Overall Performance / Average / ACC | "
        "Overall Performance / Average / Total Time (s) |"
    )


def test_official_markdown_postprocess_defaults():
    raw = (
        r"<BLOCK>title [10,20,900,80]<CHILD>Heading"
        r"<BLOCK>formula [10,100,900,200]<CHILD>\[" "\n" r"a÷b" "\n" r"\]"
        r"<BLOCK>chart [10,220,900,500]<CHILD>ignored"
        r"<BLOCK>figure [10,520,900,800]<CHILD>Figure text"
    )
    assert adapter.remove_block_fork_tags(raw) == (
        "Heading\n\n"
        r"\(a\div b\)"
        "\n\nFigure text"
    )
    blocks = adapter.parse_blocks(raw)
    assert [block["text"] for block in blocks] == [
        "Heading",
        r"\(a\div b\)",
        "",
        "Figure text",
    ]


def test_load_image_and_pdf_pages():
    image_buffer = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(image_buffer, "PNG")
    assert [page.size for page in adapter.load_pages(image_buffer.getvalue(), 1)] == [(12, 8)]
    document = fitz.open()
    document.new_page(width=72, height=144)
    pdf_pages = adapter.load_pages(document.tobytes(), 0)
    assert len(pdf_pages) == 1
    assert pdf_pages[0].height > pdf_pages[0].width


def test_decode_input_and_data_url():
    raw = b"image bytes"
    encoded = base64.b64encode(raw).decode("ascii")
    assert adapter.decode_input({"file": encoded, "fileType": 1}) == (raw, 1)
    assert adapter.image_data_url(Image.new("RGB", (1, 1))).startswith("data:image/png;base64,")


def test_input_validation_and_pdf_chunk_loading():
    encoded = base64.b64encode(b"%PDF-broken").decode("ascii")
    assert adapter.decode_input({"file": encoded})[1] == 0
    with pytest.raises(HTTPException, match="Missing base64"):
        adapter.decode_input({})
    with pytest.raises(HTTPException, match="Invalid base64"):
        adapter.decode_input({"file": "%%%"})

    document = fitz.open()
    for _ in range(3):
        document.new_page(width=72, height=72)
    raw = document.tobytes()
    assert adapter.input_page_count(raw, 0) == 3
    assert len(adapter.load_pages(raw, 0, start_page=1, page_limit=1)) == 1
    assert adapter.load_pages(b"unused", 1, start_page=1, page_limit=1) == []
    with pytest.raises(HTTPException, match="Unsupported PDF"):
        adapter.input_page_count(b"broken", 0)


def test_markdown_helpers_cover_structural_variants():
    assert adapter.normalize_prose("hyphen-\nated text") == "hyphenated text"
    assert adapter.heading_level("title", "1.2 Details", None) == 3
    assert adapter.heading_level("subtitle", "Details", None) == 3
    assert adapter.display_formula(r"\[x+y\]") == "$$\nx+y\n$$"
    assert adapter.format_reference("plain reference") == "- plain reference"
    assert adapter.format_rag_text_block("image_caption", "Fig. 1 Caption", None) == "*Fig. 1 Caption*"
    assert adapter.format_rag_text_block("table_caption", "Table I", None) == "**Table I**"
    assert adapter.format_rag_text_block("text", "Keywords—one, two", None) == "## Keywords\n\none, two"
    assert adapter.blocks_to_official_markdown([
        {"type": "text", "text": "Body"},
        {"type": "chart", "text": "ignored"},
    ]) == "Body"


class FakeClient:
    def __init__(self, *, post_response=None, get_response=None):
        self.post_response = post_response
        self.get_response = get_response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, *_args, **_kwargs):
        return self.post_response

    async def get(self, *_args, **_kwargs):
        if isinstance(self.get_response, Exception):
            raise self.get_response
        return self.get_response


def http_response(status: int, payload=None, text: str = ""):
    request = httpx.Request("POST", "http://hpd/test")
    if payload is not None:
        return httpx.Response(status, json=payload, request=request)
    return httpx.Response(status, text=text, request=request)


def test_parse_page_and_health_paths():
    image = Image.new("RGB", (4, 4), "white")
    success = http_response(200, {"choices": [{"message": {"content": "<BLOCK>text [0,0,1,1]<CHILD>Body"}}]})
    markdown, blocks = asyncio.run(adapter.parse_page(FakeClient(post_response=success), image, asyncio.Semaphore(1)))
    assert markdown == "Body"
    assert blocks[0]["text"] == "Body"

    with pytest.raises(HTTPException, match="server error"):
        asyncio.run(adapter.parse_page(
            FakeClient(post_response=http_response(500, text="failed")), image, asyncio.Semaphore(1),
        ))
    with pytest.raises(HTTPException, match="Unexpected response"):
        asyncio.run(adapter.parse_page(
            FakeClient(post_response=http_response(200, {})), image, asyncio.Semaphore(1),
        ))

    with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient(
        get_response=http_response(200, {"status": "ok"}),
    )):
        assert asyncio.run(adapter.health())["status"] == "ok"
    with patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient(get_response=RuntimeError("down"))):
        with pytest.raises(HTTPException, match="not ready"):
            asyncio.run(adapter.health())


def test_ocr_processes_pdf_in_bounded_chunks():
    class Request:
        async def json(self):
            return {"file": base64.b64encode(b"pdf").decode("ascii"), "fileType": 0}

    load_calls = []

    def load_chunk(_raw, _file_type, start_page=0, page_limit=None):
        load_calls.append((start_page, page_limit))
        count = min(page_limit, 5 - start_page)
        return [Image.new("RGB", (10, 10), "white") for _ in range(count)]

    async def parse_stub(_client, _page, _semaphore):
        return "Body", [{"type": "text", "bbox": [0, 0, 1000, 1000], "text": "Body"}]

    with (
        patch.object(adapter, "MAX_CONCURRENCY", 2),
        patch.object(adapter, "input_page_count", return_value=5),
        patch.object(adapter, "load_pages", side_effect=load_chunk),
        patch.object(adapter, "parse_page", new=AsyncMock(side_effect=parse_stub)),
        patch.object(adapter.httpx, "AsyncClient", return_value=FakeClient()),
    ):
        result = asyncio.run(adapter.ocr(Request()))

    assert load_calls == [(0, 2), (2, 2), (4, 2)]
    assert [page["pageIndex"] for page in result["layoutParsingResults"]] == [0, 1, 2, 3, 4]
    assert result["markdown"].count("Body") == 5


def test_one_click_and_runtime_include_hpd_parsing():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "windows-one-click.ps1").read_text(encoding="utf-8")
    server = (root / "server.py").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    launcher = (root / "start-hpd-parsing.sh").read_text(encoding="utf-8")
    assert '"hpd-parsing"' in script
    assert 'MODEL_RUNTIME_CONFIG["hpd-parsing"]' in server
    assert "hpd-parsing-server:" in compose
    assert "hpd-parsing-api:" in compose
    assert "HPD_PARSING_GPU_MEMORY_UTILIZATION:-auto" in compose
    assert "HPD_PARSING_GPU_MEMORY_TARGET_MIB:-6656" in compose
    assert 'GPU_MEMORY_UTILIZATION="${HPD_PARSING_GPU_MEMORY_UTILIZATION:-auto}"' in launcher
    assert 'target="$TARGET_GPU_MEMORY_MIB"' in launcher
    legacy_all = re.search(r'\{ \$_ -in @\("8".*?\n\s*\}', script, re.DOTALL)
    legacy_sglang = re.search(r'\{ \$_ -in @\("9".*?\n\s*\}', script, re.DOTALL)
    assert legacy_all and 'Add-DeploymentModel -Models $selected -ModelId "hpd-parsing"' not in legacy_all.group(0)
    assert legacy_sglang and 'Add-DeploymentModel -Models $selected -ModelId "hpd-parsing"' not in legacy_sglang.group(0)
    assert '"11", "all-five", "full-five"' in script

    local_check = (root / "scripts" / "check-local.sh").read_text(encoding="utf-8")
    coverage_check = (root / "scripts" / "check-coverage.sh").read_text(encoding="utf-8")
    assert "hpd_parsing_adapter.py" in local_check
    assert "hpd_parsing_adapter.py" in coverage_check
