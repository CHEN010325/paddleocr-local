import base64
import json
from pathlib import Path

import httpx
import pytest

import pandocr_cli


def test_model_ready_requires_target_to_be_the_only_running_logical_model():
    clean = {
        "activeModelId": "pp-ocrv6",
        "runningModelIds": ["pp-ocrv6"],
        "readyModelIds": ["pp-ocrv6"],
        "exclusivityViolation": False,
        "models": {"pp-ocrv6": {"ready": True}},
    }
    overlap = {
        **clean,
        "runningModelIds": ["pp-ocrv6", "ovisocr2"],
        "readyModelIds": ["pp-ocrv6", "ovisocr2"],
        "exclusivityViolation": True,
    }

    assert pandocr_cli.PandOCRClient.model_is_exclusively_ready(clean, "pp-ocrv6") is True
    assert pandocr_cli.PandOCRClient.model_is_exclusively_ready(overlap, "pp-ocrv6") is False


def test_model_ready_is_backward_compatible_but_rejects_wrong_active_model():
    legacy = {"models": {"pp-ocrv6": {"ready": True}}}
    wrong_active = {**legacy, "activeModelId": "ovisocr2"}

    assert pandocr_cli.PandOCRClient.model_is_exclusively_ready(legacy, "pp-ocrv6") is True
    assert pandocr_cli.PandOCRClient.model_is_exclusively_ready(wrong_active, "pp-ocrv6") is False


def test_result_markdown_prefers_complete_top_level_text_without_page_duplication():
    result = {
        "markdown": "page one\n\npage two",
        "layoutParsingResults": [
            {"markdown": {"text": "page one"}},
            {"markdown": {"text": "page two"}},
        ],
    }

    assert pandocr_cli.result_markdown(result) == "page one\n\npage two"


def test_result_markdown_falls_back_to_unique_nested_fragments():
    result = {
        "layoutParsingResults": [
            {"markdown": {"text": "first"}},
            {"markdown": {"text": "first"}},
            {"nested": {"markdown": "second"}},
        ]
    }

    assert pandocr_cli.result_markdown(result) == "first\n\nsecond"


def test_write_result_safely_extracts_images_and_rewrites_markdown(tmp_path: Path):
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"pdf")
    png = b"\x89PNG\r\n\x1a\n"
    result = {
        "markdown": "![crop](images/crop.png)",
        "images": {"images/crop.png": base64.b64encode(png).decode("ascii")},
    }

    summary = pandocr_cli.write_result(tmp_path / "out", source, "pp-ocrv6", result, 1.25)

    assert summary["status"] == "success"
    assert summary["resources"] == ["sample.pp-ocrv6.assets/images/crop.png"]
    assert (tmp_path / "out" / summary["resources"][0]).read_bytes() == png
    markdown = (tmp_path / "out" / summary["markdown"]).read_text(encoding="utf-8")
    assert "sample.pp-ocrv6.assets/images/crop.png" in markdown
    assert json.loads((tmp_path / "out" / summary["json"]).read_text(encoding="utf-8")) == result


@pytest.mark.parametrize("name", ["../escape.png", "/absolute.png", "C:/escape.png", "images//crop.png"])
def test_write_result_rejects_unsafe_image_paths(tmp_path: Path, name: str):
    with pytest.raises(ValueError, match="Unsafe image path"):
        pandocr_cli.write_result_images(
            tmp_path,
            "sample.pp-ocrv6",
            {"images": {name: base64.b64encode(b"image").decode("ascii")}},
            "",
        )


def test_parse_files_keeps_same_named_inputs_separate_and_stable(tmp_path: Path):
    first = tmp_path / "one" / "report.pdf"
    second = tmp_path / "two" / "report.pdf"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    class Client:
        def parse(self, path, model_id, *, deploy=False):
            return {"markdown": path.read_text(encoding="utf-8")}

    used_stems: set[str] = set()
    stems_by_path: dict[str, str] = {}
    summaries = pandocr_cli.parse_files(
        Client(),
        [first, second],
        "pp-ocrv6",
        tmp_path / "out",
        False,
        used_stems=used_stems,
        stems_by_path=stems_by_path,
    )
    repeated = pandocr_cli.parse_files(
        Client(),
        [second],
        "pp-ocrv6",
        tmp_path / "out",
        False,
        used_stems=used_stems,
        stems_by_path=stems_by_path,
    )

    assert summaries[0]["markdown"] != summaries[1]["markdown"]
    assert summaries[1]["markdown"] == repeated[0]["markdown"]
    assert (tmp_path / "out" / summaries[0]["markdown"]).read_text(encoding="utf-8") == "one"
    assert (tmp_path / "out" / summaries[1]["markdown"]).read_text(encoding="utf-8") == "two"


def test_compare_continues_after_one_model_error_and_writes_report(tmp_path: Path):
    source = tmp_path / "sample.png"
    source.write_bytes(b"image")

    class Client:
        def parse(self, path, model_id, *, deploy=False):
            if model_id == "broken":
                raise httpx.ConnectError("offline")
            return {"markdown": f"result from {model_id}"}

    summaries = pandocr_cli.compare_file(
        Client(), source, ["first", "broken", "last"], tmp_path / "out", False
    )

    assert [item["status"] for item in summaries] == ["success", "error", "success"]
    report = (tmp_path / "out" / "sample.comparison.md").read_text(encoding="utf-8")
    assert "| broken | error |" in report
    assert "offline" in report
    assert (tmp_path / "out" / "sample.last.md").is_file()


def test_compare_rejects_duplicate_model_ids_before_any_request(tmp_path: Path, monkeypatch):
    source = tmp_path / "sample.png"
    source.write_bytes(b"image")

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(pandocr_cli, "PandOCRClient", Client)

    assert pandocr_cli.main(["compare", str(source), "--models", "same,same"]) == 1
