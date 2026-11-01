"""Single-file OvisOCR3 document parsing and cross-page table reconstruction.

Reading guide: public entry point -> document pipeline -> text/table helpers ->
model backend -> browser renderer. Import the text helpers without installing
vLLM or browser dependencies; those are loaded only when used.

Algorithm: parse pages independently, then visit adjacent table-bearing pages
from last to first. Write each merged table into the earlier page and render
that updated page before its next prediction.
 Keep non-table source untouched.

Support: the document pipeline accepts one merged table per adjacent pair and
binds the earlier page's last table to the later page's first table. Multiple
merges need explicit fragment bindings via apply_pair(); nested or incomplete
HTML tables are rejected on writeback. This is a conservative binding policy,
not a general table-fragment matching algorithm.

Page numbers are 1-based; table fragment indices are 0-based. Model predictions
use pair-local pages 1 and 2; saved Markdown and provenance use document pages.
Raw predictions, intermediate files and partial results are retained.
"""

from __future__ import annotations

import argparse
import base64
import copy
import html
import io
import json
import math
import re
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping, NamedTuple

NEGATIVE = "No cross-page table exists between these pages."
TABLE = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.I | re.S)
IMAGE_REF = re.compile(
    r"(?<![\w/])(?:page_(\d+)/)?images/bbox_(\d+)_(\d+)_(\d+)_(\d+)\.jpg"
)
REF = re.compile(r"page_(\d+)/images/")
CATEGORIES = set(
    "abstract algorithm aside_text chart content display_formula doc_title figure_title footer footer_image footnote formula_number header header_image image inline_formula number paragraph_title reference_content seal table text vertical_text vision_footnote".split()
)

FragmentId = tuple[int, int]
Backend = Callable[[list[str], str, Path, int], tuple[str, str]]


class GenerationResult(NamedTuple):
    """Tuple-compatible raw model output; cleaning belongs to the page adapter."""

    text: str
    finish_reason: str


class RenderResult(NamedTuple):
    """Tuple-compatible render result; aliases map rendered crops to originals."""

    image_path: str
    rewritten_source: str
    alias_to_original: dict[str, str]
    diagnostics: dict


@dataclass
class PageState:
    """One document page and the original fragments behind its current tables."""

    number: int
    image_path: str
    original_markdown: str
    markdown: str
    finish_reason: str
    parse_diagnostics: dict | None = None
    table_sources: list[set[FragmentId]] = field(init=False)

    def __post_init__(self):
        self.table_sources = [
            {(self.number, index)}
            for index, _ in enumerate(TABLE.finditer(self.markdown))
        ]


@dataclass
class PairInput:
    """Two model inputs with explicit document/pair-local coordinate mapping."""

    page_numbers: tuple[int, int]
    image_paths: list[str]
    markdowns: list[str]
    alias_to_original: dict[str, str]
    rendered_pages: set[int]


@dataclass
class DocumentState:
    pages: list[PageState] = field(default_factory=list)
    chunks: list[list[int]] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    merged_tables: list[dict] = field(default_factory=list)
    parse_warnings: list[dict] = field(default_factory=list)


# Public entry point and document pipeline.


def reconstruct_document(
    image_paths,
    output,
    *,
    model_path=".",
    stage1_prompt="document_structured",
    mathjax=None,
    backend: Backend | None = None,
    renderer=None,
):
    """Reconstruct ordered page images into a document, keeping all evidence.

    Use a fresh output directory. Stage 1 accepts document_markdown[.txt] or
    document_structured[.txt]; prompts are loaded from model_path/prompts.
    Returns the saved result.json dictionary. Truncated generations, recovered
    JSON or invalid structured fields return PARTIAL with issue-specific warnings;
    other processing failures save partial output and raise RuntimeError.

    For research, inject backend(paths, prompt, folder, stage) -> (raw, reason)
    and/or renderer.render(source, owner, paths, folder, name) -> RenderResult.
    Injected renderers remain caller-owned. Defaults load vLLM and Chromium lazily.
    Engine settings, prompt bytes and renderer resize policy are unchanged.
    """
    stage1_prompt = stage1_prompt.removesuffix(".txt")
    if stage1_prompt not in {"document_markdown", "document_structured"}:
        raise ValueError("Choose document_markdown or document_structured")
    images = _document_images(image_paths)
    if Path(output).exists():
        raise FileExistsError("Use a fresh output directory")

    prompt_dir = Path(model_path) / "prompts"
    page_prompt = (prompt_dir / f"{stage1_prompt}.txt").read_text(encoding="utf-8").removesuffix("\n")
    table_prompt = (prompt_dir / "crosspage_table.txt").read_text(encoding="utf-8").removesuffix("\n")
    if backend is None:
        backend = VllmBackend(model_path)
    owns_renderer = renderer is None
    if owns_renderer:
        renderer = _BrowserRenderer(
            mathjax or Path(__file__).resolve().parent / "assets/mathjax.js"
        )
    try:
        result = _run_document(
            images,
            output,
            backend,
            renderer,
            page_prompt=page_prompt,
            table_prompt=table_prompt,
            stage1_prompt=stage1_prompt,
        )
    finally:
        if owns_renderer:
            renderer.close()

    if result["error"] and not result["error"].get("recoverable", False):
        raise RuntimeError(f"Document incomplete; inspect {output}/failure.json")
    return result


def _run_document(
    image_paths, output, backend, renderer, *, page_prompt, table_prompt, stage1_prompt
):
    """Parse pages, reconstruct adjacent pairs backwards, then persist results.

    Recovery is confined to Stage 1 output parsing. A failed window stops further
    reconstruction while preserving earlier pages, merges and failure details.
    No benchmark labels are used. The output directory must not already exist.
    """
    images = _document_images(image_paths)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    save(
        output / "inputs.json",
        {
            "images": images,
            "stage1_prompt": stage1_prompt,
            "page_prompt": page_prompt,
            "table_prompt": table_prompt,
        },
    )
    state = DocumentState()
    error = None
    context = {"stage": 1, "pages": []}
    try:
        for number, image in enumerate(images, 1):
            context = {"stage": 1, "pages": [number]}
            page = _parse_page(
                number, image, output, backend, page_prompt, stage1_prompt
            )
            state.pages.append(page)
            warning = _page_warning(page)
            if warning:
                state.parse_warnings.append(warning)

        state.chunks = find_chunks([page.markdown for page in state.pages])
        save(
            output / "chunks.json",
            {
                "chunks": state.chunks,
                "basis": "Stage 1 predicted HTML tables only",
            },
        )
        for left, right in _adjacent_pairs(state.chunks):
            context = {"stage": 2, "pages": [left, right]}
            folder = output / "stage2" / f"pair_{left:04d}_{right:04d}"
            folder.mkdir(parents=True)
            pair = _prepare_pair(
                state.pages[left - 1 : right], images, folder, renderer
            )
            tables = _predict_pair(pair, folder, backend, table_prompt)
            _apply_merge(state, pair, tables)
            state.steps.append(
                {
                    "pages": list(pair.page_numbers),
                    "merged": bool(tables),
                    "rendered_pages": sorted(pair.rendered_pages),
                }
            )
            save(
                folder / "result.json",
                {
                    "tables": tables,
                    "pages": [
                        state.pages[number - 1].markdown for number in pair.page_numbers
                    ],
                    "groups": copy.deepcopy(state.merged_tables),
                },
            )
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "recoverable": False,
            **context,
        }
        save(output / "failure.json", {**error, "traceback": traceback.format_exc()})
    return _finalize_document(state, images, output, error)


def _parse_page(number, image, output, backend, page_prompt, stage1_prompt):
    """Retain raw output, apply the existing cleanup, then adapt the page format."""
    folder = output / "stage1" / f"page_{number:04d}"
    folder.mkdir(parents=True)
    generation = GenerationResult(*backend([image], page_prompt, folder, 1))
    raw, reason = generation
    (folder / "raw.txt").write_text(raw, encoding="utf-8")
    cleaned = _clean_truncated_repeats(raw.strip())
    save(
        folder / "generation.json",
        {
            "finish_reason": reason,
            "complete": reason == "stop",
            "tail_changed": cleaned != raw.strip(),
            "raw_chars": len(raw.strip()),
            "cleaned_chars": len(cleaned),
        },
    )
    (folder / "cleaned_raw.txt").write_text(cleaned, encoding="utf-8")
    if reason not in ("stop", "length"):
        raise ValueError(f"Stage 1 page {number}: finish_reason={reason}")

    diagnostics = None
    if stage1_prompt == "document_structured":
        projected, blocks, diagnostics = structured_to_markdown(cleaned)
        save(folder / "blocks.json", blocks)
        save(folder / "adapter.json", diagnostics)
        (folder / "projected.md").write_text(projected, encoding="utf-8")
        markdown = page_markdown(projected, number, unwrap_fence=False) if projected.strip() else ""
    else:
        markdown = page_markdown(cleaned, number)
    (folder / "page.md").write_text(markdown, encoding="utf-8")
    return PageState(number, image, markdown, markdown, reason, diagnostics)


def _page_warning(page):
    """Separate generation truncation, JSON recovery and structured schema issues."""
    codes = []
    if page.finish_reason == "length":
        codes.append("generation_truncated")
    diagnostics = page.parse_diagnostics
    if diagnostics is not None:
        if not diagnostics["json_complete"]:
            codes.append("json_incomplete")
        if diagnostics["schema_issues"]:
            codes.append("schema_invalid")
        if (
            diagnostics["invalid_content_objects"]
            or not diagnostics["emitted_contents"]
        ):
            codes.append("content_invalid")
    if not codes:
        return None
    warning = {"page": page.number, "finish_reason": page.finish_reason, "codes": codes}
    if diagnostics is not None:
        warning["diagnostics"] = diagnostics
    return warning


def _adjacent_pairs(chunks):
    """Yield (2, 3), then (1, 2) for a three-page table-bearing run."""
    for chunk in chunks:
        for left in range(chunk[-1] - 1, chunk[0] - 1, -1):
            yield left, left + 1


def _prepare_pair(pages, images, folder, renderer):
    page_numbers = tuple(page.number for page in pages)
    document_to_local = {number: local for local, number in enumerate(page_numbers, 1)}
    paths, markdowns, aliases, rendered_pages = [], [], {}, set()
    for page in pages:
        path, markdown = page.image_path, page.markdown
        if markdown != page.original_markdown:
            rendered = RenderResult(
                *renderer.render(
                    markdown, page.number, images, folder, f"page_{page.number:04d}"
                )
            )
            path, markdown = rendered.image_path, rendered.rewritten_source
            aliases.update(rendered.alias_to_original)
            rendered_pages.add(page.number)
        paths.append(path)
        markdowns.append(remap(markdown, document_to_local))
    return PairInput(page_numbers, paths, markdowns, aliases, rendered_pages)


def _predict_pair(pair, folder, backend, table_prompt):
    page_context = "\n\n".join(
        f'<INPUT_PAGE index="{index}">\n{markdown}\n</INPUT_PAGE>'
        for index, markdown in enumerate(pair.markdowns, 1)
    )
    prompt = table_prompt.removesuffix("\n") + "\n\nInput Markdown:\n\n" + page_context
    if all(TABLE.search(markdown) for markdown in pair.markdowns):
        generation = GenerationResult(*backend(pair.image_paths, prompt, folder, 2))
    else:
        generation = GenerationResult(
            json.dumps([{"source_pages": [], "html": NEGATIVE}]), "stop"
        )
        save(
            folder / "gate_skip.json",
            {"reason": "README table gate", "model_called": False},
        )
    (folder / "raw.txt").write_text(generation.text, encoding="utf-8")
    save(folder / "generation.json", {"finish_reason": generation.finish_reason})
    if generation.finish_reason != "stop":
        raise ValueError(
            f"Stage 2 pair {list(pair.page_numbers)}: finish_reason={generation.finish_reason}"
        )
    tables = parse_tables(generation.text)
    if len(tables) > 1:
        raise ValueError(
            "Multiple merges need explicit fragment bindings; output retained."
        )
    return [
        dict(table, html=_restore_image_refs(table["html"], pair)) for table in tables
    ]


def _restore_image_refs(table_html, pair):
    """Restore document page IDs, then resolve crops from re-rendered pages exactly."""
    local_to_document = {
        local: number for local, number in enumerate(pair.page_numbers, 1)
    }
    table_html = remap(table_html, local_to_document)

    def restore(match):
        reference = match[0]
        if match[1] is None:
            raise ValueError("Output crop lacks a page number.")
        if int(match[1]) in pair.rendered_pages:
            if reference not in pair.alias_to_original:
                raise ValueError("Unmappable crop from a rendered page: " + reference)
            return pair.alias_to_original[reference]
        return reference

    return IMAGE_REF.sub(restore, table_html)


def _apply_merge(state, pair, tables):
    """Validate writeback before updating page contents or fragment provenance."""
    earlier, later = (state.pages[number - 1] for number in pair.page_numbers)
    before = (earlier.markdown, later.markdown)
    after = apply_pair(before, tables)
    if any(TABLE.sub("", old) != TABLE.sub("", new) for old, new in zip(before, after)):
        raise ValueError("Writeback changed non-table content.")
    if tables:
        sources = earlier.table_sources[-1] | later.table_sources[0]
        state.merged_tables = [
            group
            for group in state.merged_tables
            if not sources.intersection(map(tuple, group["fragment_ids"]))
        ]
        state.merged_tables.append(
            {
                "source_pages": sorted({number for number, _ in sources}),
                "fragment_ids": sorted(sources),
                "html": tables[0]["html"],
            }
        )
        earlier.table_sources[-1] = sources
        del later.table_sources[0]
    earlier.markdown, later.markdown = after


def _save_document_crops(markdown, images, output):
    """Materialize page-qualified references from the original input images."""
    from PIL import Image

    errors = []
    references = {}
    for match in IMAGE_REF.finditer(markdown):
        references.setdefault(match[1], {})[match[0]] = match.groups()[1:]
    for page, crops in references.items():
        try:
            if page is None or not 1 <= int(page) <= len(images):
                raise ValueError("Image reference needs a valid source page number")
            with Image.open(images[int(page) - 1]) as source:
                for reference, coordinates in crops.items():
                    try:
                        left, top, right, bottom = map(int, coordinates)
                        width, height = source.size
                        box = (
                            max(0, min(width, round(left * width / 1000))),
                            max(0, min(height, round(top * height / 1000))),
                            max(0, min(width, round(right * width / 1000))),
                            max(0, min(height, round(bottom * height / 1000))),
                        )
                        if box[2] <= box[0] or box[3] <= box[1]:
                            raise ValueError("Image crop has zero or negative pixel area")
                        target = output / reference
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source.crop(box).convert("RGB").save(target)
                    except Exception as exc:
                        errors.append({"reference": reference, "message": str(exc)})
        except Exception as exc:
            errors.extend(
                {"reference": reference, "message": str(exc)} for reference in crops
            )
    return errors


def _finalize_document(state, images, output, error):
    document = "\n\n".join(page.markdown for page in state.pages) + "\n"
    crop_errors = _save_document_crops(document, images, output)
    for page in state.pages:
        target = output / "pages" / f"page_{page.number:04d}.md"
        target.parent.mkdir(exist_ok=True)
        # Per-page Markdown is one directory below the shared crop assets.
        target.write_text(IMAGE_REF.sub(lambda m: "../" + m[0], page.markdown), encoding="utf-8")
    (output / "document.md").write_text(document, encoding="utf-8")
    if crop_errors and error is None:
        error = {"type": "ImageCropExportFailed", "recoverable": False}
        save(output / "failure.json", {**error, "image_export_errors": crop_errors})
    if error is None and state.parse_warnings:
        error = {
            "type": "Stage1OutputIncomplete",
            "recoverable": True,
            "pages": [warning["page"] for warning in state.parse_warnings],
        }
    result = {
        "status": "COMPLETE" if error is None else "PARTIAL",
        "error": error,
        "input_pages": len(images),
        "parsed_pages": len(state.pages),
        "chunks": state.chunks,
        "steps": state.steps,
        "merged_tables": state.merged_tables,
        "stage1_parse_warnings": state.parse_warnings,
        "image_export_errors": crop_errors,
    }
    save(output / "result.json", result)
    return result


# Reusable text adapters and table operations (standard library only).


def _document_images(image_paths):
    images = [str(Path(path).resolve()) for path in image_paths]
    if not images or not all(Path(path).is_file() for path in images):
        raise ValueError("Provide existing page images in reading order.")
    return images


def save(path, value):
    """Write a JSON artifact, creating its parent directory when needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _is_valid_bbox(value):
    if not isinstance(value, list) or len(value) != 4:
        return False
    if not all(type(coordinate) is int for coordinate in value):
        return False
    left, top, right, bottom = value
    return 0 <= left <= right < 1000 and 0 <= top <= bottom < 1000


def _recover_json_prefix(text):
    """Return only complete prefix objects; never guess or repair JSON syntax."""
    blocks, unparsed_tail_chars = [], 0
    if not text.startswith("["):
        return blocks, unparsed_tail_chars
    position = 1
    decoder = json.JSONDecoder()
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        if position == len(text) or text[position] == "]":
            break
        try:
            block, end = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            unparsed_tail_chars = len(text) - position
            break
        blocks.append(block)
        position = end
        while position < len(text) and text[position].isspace():
            position += 1
        if position < len(text) and text[position] == ",":
            position += 1
        elif position < len(text) and text[position] != "]":
            unparsed_tail_chars = len(text) - position
            break
    return blocks, unparsed_tail_chars


def structured_to_markdown(text):
    """Project content in emitted order, retaining diagnostics for incomplete JSON.

    Preserve every category and keep only complete prefix objects on JSON failure.
    Invalid bbox/category fields are reported without discarding valid content.
    """
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*\n|\n```$", "", stripped, flags=re.I)
    diagnostics = {
        "json_complete": False,
        "schema_issues": [],
        "invalid_content_objects": [],
        "unparsed_tail_chars": 0,
    }
    try:
        blocks = json.loads(stripped)
        if not isinstance(blocks, list):
            raise ValueError("Expected one JSON array")
        diagnostics["json_complete"] = True
    except ValueError as exc:
        diagnostics["parse_error"] = str(exc)
        blocks, diagnostics["unparsed_tail_chars"] = _recover_json_prefix(stripped)

    contents = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or not isinstance(block.get("content"), str):
            diagnostics["invalid_content_objects"].append(index)
            continue
        contents.append(block["content"])
        if not _is_valid_bbox(block.get("bbox")):
            diagnostics["schema_issues"].append({"index": index, "field": "bbox"})
        if (
            not isinstance(block.get("category"), str)
            or block["category"] not in CATEGORIES
        ):
            diagnostics["schema_issues"].append({"index": index, "field": "category"})
    diagnostics.update(
        parsed_objects=len(blocks),
        emitted_contents=len(contents),
        policy="join content in emitted array order; preserve every category; retain complete prefix objects only",
    )
    diagnostics["valid"] = (
        diagnostics["json_complete"]
        and not diagnostics["schema_issues"]
        and not diagnostics["invalid_content_objects"]
        and bool(contents)
    )
    return "\n\n".join(contents), blocks, diagnostics


def _clean_truncated_repeats(
    text: str,
    min_text_len: int = 8000,
    max_period: int = 200,
    min_period: int = 1,
    min_repeat_chars: int = 100,
    min_repeat_times: int = 5,
) -> str:
    n = len(text)
    if n < min_text_len:
        return text

    max_period = min(max_period, n - 1)
    for unit_len in range(min_period, max_period + 1):
        if text[n - 1] != text[n - 1 - unit_len]:
            continue

        match_len = 1
        idx = n - 2
        while idx >= unit_len and text[idx] == text[idx - unit_len]:
            match_len += 1
            idx -= 1

        total_len = match_len + unit_len
        repeat_times = total_len // unit_len
        tail_len = total_len % unit_len

        if repeat_times >= min_repeat_times and total_len >= min_repeat_chars:
            return text[: n - total_len + unit_len] + text[n - tail_len :]

    return text


def parse_tables(raw: str) -> list[dict]:
    """Return all positive objects or [] for the exact negative sentinel."""
    items = json.loads(raw)
    if not isinstance(items, list) or not items:
        raise ValueError("Expected a nonempty JSON array.")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"source_pages", "html"}:
            raise ValueError("Each object needs exactly source_pages and html.")
        if not isinstance(item["source_pages"], list) or not isinstance(
            item["html"], str
        ):
            raise ValueError("Invalid field types.")
        if not item["source_pages"]:
            if len(items) == 1 and item["html"] == NEGATIVE:
                return []
            raise ValueError("Invalid or mixed negative sentinel.")
        if item["source_pages"] != [1, 2] or any(
            type(p) is not int for p in item["source_pages"]
        ):
            raise ValueError("A positive two-page result must use [1, 2].")
        if not TABLE.fullmatch(item["html"].strip()):
            raise ValueError("Expected complete HTML table output.")
    return items


def apply_pair(
    markdowns: tuple[str, str],
    tables: list[dict],
    bindings: list[tuple[int, int]] | None = None,
) -> tuple[str, str]:
    """Replace page 1's last table and remove page 2's first table by default.

    Multiple merged tables require explicit zero-based fragment bindings.
    Nested or incomplete HTML tables are rejected; non-table source is retained.
    """
    if len(markdowns) != 2:
        raise ValueError("Need exactly two pages.")
    spans = [list(TABLE.finditer(md)) for md in markdowns]
    if bindings is None:
        if not tables:
            return tuple(markdowns)
        if len(tables) != 1:
            raise ValueError("Multiple merged tables require explicit source bindings.")
        bindings = [(len(spans[0]) - 1, 0)]
    if len(tables) != len(bindings):
        raise ValueError("Need one explicit binding per merged table.")
    for md, matches in zip(markdowns, spans):
        if len(re.findall(r"<table\b", md, re.I)) != len(matches) or len(
            re.findall(r"</table\s*>", md, re.I)
        ) != len(matches):
            raise ValueError(
                "Nested or incomplete tables require a layout-aware editor."
            )
    edits = [{}, {}]
    for item, binding in zip(tables, bindings):
        parse_tables(json.dumps([item]))
        if len(re.findall(r"<table\b", item["html"], re.I)) != 1:
            raise ValueError(
                "Nested or multiple tables cannot be written into one fragment."
            )
        if item["source_pages"] != [1, 2] or len(binding) != 2:
            raise ValueError("Only positive two-page tables can be written back.")
        for page, index in enumerate(binding):
            if type(index) is not int or not 0 <= index < len(spans[page]):
                raise ValueError("Source table index out of range.")
            if index in edits[page]:
                raise ValueError("Two results claim the same source fragment.")
            edits[page][index] = item["html"] if page == 0 else ""
    output = []
    for page, md in enumerate(markdowns):
        for index in sorted(edits[page], reverse=True):
            span = spans[page][index]
            md = md[: span.start()] + edits[page][index] + md[span.end() :]
        output.append(md)
    return tuple(output)


def find_chunks(pages):
    """Maximal runs of >=2 pages with predicted HTML tables; 1-based IDs."""
    chunks, run = [], []
    for page, markdown in enumerate(pages, 1):
        if TABLE.search(markdown):
            run.append(page)
        else:
            if len(run) >= 2:
                chunks.append(run)
            run = []
    if len(run) >= 2:
        chunks.append(run)
    return chunks


def remap(markdown: str, mapping: Mapping[int, int]) -> str:
    def replace(match):
        page = int(match[1])
        if page not in mapping:
            raise ValueError(f"Image page {page} is outside the current pair.")
        return f"page_{mapping[page]}/images/"

    return REF.sub(replace, markdown)


def page_markdown(raw, page, *, unwrap_fence=True):
    """Accept Markdown, optionally fenced; qualify page-local crop references."""
    markdown = raw.strip()
    # Structured category content already is Markdown; its code fences belong
    # to algorithms/source examples rather than an enclosing response wrapper.
    if unwrap_fence:
        markdown = re.sub(
            r"^```(?:markdown|html)?\s*\n|\n```\s*$", "", markdown, flags=re.I
        )
    if not markdown:
        raise ValueError("Empty Stage 1 Markdown.")
    # Each Stage 1 call contains one image, so crop IDs refer to this page.
    return IMAGE_REF.sub(
        lambda m: f"page_{page}/images/bbox_" + "_".join(m.groups()[1:]) + ".jpg",
        markdown,
    )


# Model inference and lazy browser lifecycle.


class VllmBackend:
    """Keep engine, sampling and request settings aligned with README infer().

    Benchmark-specific scheduling overrides do not belong in this backend.
    """

    def __init__(self, model_path):
        from vllm import LLM, SamplingParams

        self.model = LLM(
            model=str(Path(model_path).resolve()),
            tensor_parallel_size=1,
            gpu_memory_utilization=0.8,
            gdn_prefill_backend="triton",
            limit_mm_per_prompt={"image": 2},
        )
        self.tokenizer = self.model.get_tokenizer()
        self.sampling = SamplingParams(max_tokens=16384, temperature=0.0)

    def __call__(self, paths, task_prompt, folder, stage):
        from PIL import Image

        images = []
        for path in paths:
            with Image.open(path) as image:
                images.append(image.convert("RGB"))
        content = [{"type": "image"} for _ in images]
        content.append({"type": "text", "text": task_prompt})
        prompt = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": content}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        request = {
            "prompt": prompt,
            "multi_modal_data": {"image": images},
            "mm_processor_kwargs": {
                "images_kwargs": {
                    "min_pixels": 448 * 448,
                    "max_pixels": 2880 * 2880,
                }
            },
        }
        save(
            folder / "input.json",
            {
                "stage": stage,
                "images": list(paths),
                "prompt": prompt,
                "mm_processor_kwargs": request["mm_processor_kwargs"],
            },
        )
        output = self.model.generate([request], self.sampling)[0].outputs[0]
        return GenerationResult(output.text, output.finish_reason)


class _BrowserRenderer:
    def __init__(self, mathjax):
        self.mathjax = mathjax
        self.playwright = self.browser = self.renderer = None

    def render(self, *args):
        if self.renderer is None:
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=True)
            self.renderer = Renderer(self.browser, self.mathjax)
        return self.renderer.render(*args)

    def close(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()


# Content-preserving Markdown/HTML rendering.


CROP = re.compile(r"page_(\d+)/images/bbox_(\d+)_(\d+)_(\d+)_(\d+)\.jpg")
_CROP_ASSET = re.compile(
    r"(?:page_(\d+)/)?images/(?:bbox_)?(\d+)_(\d+)_(\d+)_(\d+)\.(?:jpg|jpeg|png)", re.I
)
_TAG = re.compile(
    r"<!--.*?-->|</?[A-Za-z][\w:-]*(?:[^>\'\"]|\"[^\"]*\"|\'[^\']*\')*>", re.S
)
_SRC = re.compile(r"""(?<![\w:-])src\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_ALLOWED = set(
    "p div span br hr h1 h2 h3 h4 h5 h6 table thead tbody tfoot tr td th caption colgroup col ul ol li dl dt dd blockquote pre code b strong em i u s del sup sub a img figure figcaption section article main center font".split()
)
_DROP = set("script style iframe object embed link meta base template".split())
_CSS = """
html,body{margin:0;padding:0;background:#fff;color:#111}
#capture{padding:28px;box-sizing:border-box;font-family:Arial,'Noto Sans CJK SC',sans-serif;font-size:18px;line-height:1.4}
table{border-collapse:collapse;width:100%;margin:12px 0}td,th{border:1px solid #666;padding:5px;vertical-align:top;overflow-wrap:anywhere}
img{max-width:100%;height:auto}h1{font-size:28px}h2{font-size:24px}h3{font-size:21px}p{margin:10px 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}code{font-family:monospace;white-space:pre-wrap;overflow-wrap:anywhere}
.math-display{display:block;text-align:center;margin:10px 0}.math-inline{display:inline-block;vertical-align:middle}.math-fallback{white-space:pre-wrap;overflow-wrap:anywhere}
mjx-container{max-width:none}svg{overflow:visible}th{font-weight:600}
"""


@dataclass(frozen=True)
class RenderLimits:
    """Configurable engineering limits, not validated OCR accuracy thresholds."""

    max_side: int = 32768
    max_render_pixels: int = 64_000_000
    max_source_chars: int = 4_000_000
    min_effective_font_px: float = 12.0
    min_effective_cell_height_px: float = 8.0
    timeout_ms: int = 30000

    def __post_init__(self):
        for name, value in asdict(self).items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(name + " must be finite and positive")


class RenderError(ValueError):
    """Concise caller-facing error; details remain in the render.json sidecar."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _escaped(text, pos):
    start = pos
    while start and text[start - 1] == "\\":
        start -= 1
    return (pos - start) % 2 == 1


def _dollar_is_math(body, mode):
    if mode == "all":
        return True
    if mode == "off" or not body or body != body.strip():
        return False
    if re.search(r"\\[A-Za-z]+", body):
        return True
    # Pure numbers and prose are ambiguous; preserve them, rather than invent math.
    if re.fullmatch(r"[A-Za-z]", body):
        return True
    if re.search(r"[^\x00-\x7f]", body):
        return bool(
            re.fullmatch(r"[\w\s\d.,_^=+*/<>{}()−-]+", body)
            and re.search(r"[_^=+*/<>−-]", body)
        )
    if re.fullmatch(r"\d+(?:\.\d+)?\\[,;!: ](?:[A-Za-z]+)", body):
        return True
    words = re.findall(r"[A-Za-z]+", body)
    if any(
        len(w) > 1
        and w not in {"sin", "cos", "tan", "log", "ln", "exp", "max", "min", "lim"}
        for w in words
    ):
        return False
    return bool(re.search(r"[_^=+*/<>{}]", body) or re.search(r"\w\s*-\s*\w", body))


def _markdown_image(text, pos, parser, env):
    from markdown_it.common.utils import normalizeReference
    from markdown_it.helpers import parseLinkDestination, parseLinkLabel, parseLinkTitle
    from markdown_it.rules_inline.state_inline import StateInline

    state = StateInline(text, parser, env, [])
    end_label = parseLinkLabel(state, pos + 1, False)
    if end_label < 0:
        return None
    alt = text[pos + 2 : end_label]
    cursor = end_label + 1
    title = ""
    if cursor < len(text) and text[cursor] == "(":
        cursor += 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        dest = parseLinkDestination(text, cursor, len(text))
        if not dest.ok:
            return None
        ref, cursor = dest.str, dest.pos
        spaces = cursor
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor > spaces:
            parsed_title = parseLinkTitle(text, cursor, len(text))
            if parsed_title.ok:
                title, cursor = parsed_title.str, parsed_title.pos
                while cursor < len(text) and text[cursor].isspace():
                    cursor += 1
        if cursor >= len(text) or text[cursor] != ")":
            return None
        end = cursor + 1
    else:
        label, end = alt, cursor
        if cursor < len(text) and text[cursor] == "[":
            close = text.find("]", cursor + 1)
            if close < 0:
                return None
            label = text[cursor + 1 : close] or alt
            end = close + 1
        definition = env.get("references", {}).get(normalizeReference(label))
        if not definition:
            return None
        ref, title = definition["href"], definition.get("title", "")
    return end, ref, alt, title


def _image_markup(item, ref):
    if item["kind"] == "html":
        return html.escape(ref, quote=True)
    title = (
        (' "' + item["title"].replace("\\", "\\\\").replace('"', '\\"') + '"')
        if item["title"]
        else ""
    )
    return "![" + item["alt"] + "](<" + ref + ">" + title + ")"


def _edit_source(text, edits):
    out, previous = [], 0
    for start, end, value in sorted(edits):
        if start < previous:
            raise RenderError(
                "overlapping_source_spans",
                "Cannot safely map overlapping source elements.",
            )
        out.extend([text[previous:start], value])
        previous = end
    out.append(text[previous:])
    return "".join(out)


def prepare_source(source, parser, dollar_math="auto", html_document=False):
    """Protect math before CommonMark and identify each actual image occurrence.

    Code fences, indented code, inline code, HTML attributes and comments remain
    literal. Markdown image references are expanded per occurrence, not per URL.
    """
    from markdown_it.helpers import parseLinkDestination, parseLinkTitle

    prefix = "RDR" + uuid.uuid4().hex.upper()
    env = {}
    tokens = [] if html_document else parser.parse(source, env)
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    literal = {
        offsets[t.map[0]]: offsets[min(t.map[1], len(offsets) - 1)]
        for t in tokens
        if t.type in {"fence", "code_block"} and t.map
    }
    for definition in env.get("references", {}).values():
        if definition.get("map"):
            a, b = definition["map"]
            literal[offsets[a]] = offsets[min(b, len(offsets) - 1)]
    formulas, images, edits, notes = [], [], [], []
    i = 0
    while i < len(source):
        if i in literal:
            i = literal[i]
            continue
        if source[i] == "`" and not _escaped(source, i):
            run = re.match(r"`+", source[i:])[0]
            close = re.search(
                r"(?<!`)" + re.escape(run) + r"(?!`)", source[i + len(run) :]
            )
            if close:
                i += len(run) + close.end()
                continue
        if source.startswith("](", i):
            # Markdown link destinations are not mathematical prose.
            dest = parseLinkDestination(source, i + 2, len(source))
            if dest.ok:
                cursor = dest.pos
                while cursor < len(source) and source[cursor].isspace():
                    cursor += 1
                title = parseLinkTitle(source, cursor, len(source))
                if title.ok:
                    cursor = title.pos
                    while cursor < len(source) and source[cursor].isspace():
                        cursor += 1
                if cursor < len(source) and source[cursor] == ")":
                    i = cursor + 1
                    continue
        if source[i] == "<":
            tag = _TAG.match(source, i)
            if tag:
                raw = tag[0]
                kind = re.match(r"<([A-Za-z][\w:-]*)", raw)
                kind = kind[1].lower() if kind else ""
                if kind in _DROP | {"code"}:
                    close = re.search(
                        r"</" + re.escape(kind) + r"\s*>", source[tag.end() :], re.I
                    )
                    if close:
                        i = tag.end() + close.end()
                        continue
                if kind == "img":
                    src = _SRC.search(raw)
                    if src:
                        group = next(j for j in (1, 2, 3) if src[j] is not None)
                        start, end = i + src.start(group), i + src.end(group)
                        item = dict(
                            start=start,
                            end=end,
                            ref=html.unescape(src[group]),
                            kind="html",
                            token=prefix + "IMG" + str(len(images)),
                        )
                        images.append(item)
                        edits.append((start, end, item["token"]))
                i = tag.end()
                continue
        if source.startswith("![", i) and not _escaped(source, i):
            found = _markdown_image(source, i, parser, env)
            if found:
                end, ref, alt, title = found
                item = dict(
                    start=i,
                    end=end,
                    ref=ref,
                    alt=alt,
                    title=title,
                    kind="markdown",
                    token=prefix + "IMG" + str(len(images)),
                )
                images.append(item)
                edits.append((i, end, _image_markup(item, item["token"])))
                i = end
                continue
        opening = next(
            (s for s in ("\\(", "\\[", "$$", "$") if source.startswith(s, i)), None
        )
        if opening and not _escaped(source, i):
            closing = {"\\(": "\\)", "\\[": "\\]", "$$": "$$", "$": "$"}[opening]
            pos = i + len(opening)
            while True:
                end = source.find(closing, pos)
                if end < 0:
                    break
                if not _escaped(source, end):
                    break
                pos = end + len(closing)
            # Delimiters may not consume table cells or block boundaries.
            # An explicit unclosed formula remains visible in its own cell.
            boundary = re.search(
                r"</?(?:table|thead|tbody|tfoot|tr|td|th|div|p|h[1-6]|li|ul|ol|pre|blockquote)\b[^>]*>|<br\b[^>]*>",
                source[pos:],
                re.I,
            )
            if boundary and end >= 0 and pos + boundary.start() < end:
                notes.append({"code": "formula_block_boundary", "offset": i})
                end = -1
            if end >= 0:
                body = source[i + len(opening) : end]
                allowed = True
                if opening == "$":
                    allowed = (
                        "\n" not in body
                        and not source.startswith("$$", end)
                        and _dollar_is_math(body, dollar_math)
                    )
                    if not allowed:
                        notes.append(
                            {"code": "literal_or_ambiguous_dollar", "offset": i}
                        )
                elif "\n\n" in body and opening == "\\(":
                    allowed = False
                if allowed:
                    token = prefix + "MATH" + str(len(formulas)) + "X"
                    formulas.append(
                        dict(
                            token=token,
                            tex=html.unescape(body),
                            raw=source[i : end + len(closing)],
                            display=opening in {"\\[", "$$"},
                        )
                    )
                    edits.append((i, end + len(closing), token))
                    i = end + len(closing)
                    continue
            elif opening != "$":
                # Preserve malformed explicit notation visibly, even through Markdown.
                stop = source.find("\n\n", i)
                stop = len(source) if stop < 0 else stop
                tag_boundary = _TAG.search(source, i + len(opening))
                if tag_boundary:
                    stop = min(stop, tag_boundary.start())
                token = prefix + "MATH" + str(len(formulas)) + "X"
                formulas.append(
                    dict(
                        token=token,
                        tex="",
                        raw=source[i:stop],
                        display=False,
                        malformed=True,
                    )
                )
                edits.append((i, stop, token))
                i = stop
                continue
            # Consume an unrecognized delimiter as a unit (do not reinterpret $$ as $).
            i += len(opening)
            continue
        i += 1
    return _edit_source(source, edits), formulas, images, notes


def sanitize(soup):
    from bs4 import Comment

    removed = 0
    for node in list(soup.find_all(string=lambda t: isinstance(t, Comment))):
        node.extract()
    for tag in list(soup.find_all(True)):
        if tag.name is None:
            continue
        if tag.name in {"svg", "canvas", "math", "video", "audio"}:
            raise RenderError(
                "unsupported_embedded_media",
                "Embedded media needs an explicit image asset; source retained.",
            )
        if tag.name in _DROP:
            tag.decompose()
            removed += 1
            continue
        if tag.name not in _ALLOWED:
            tag.unwrap()
            removed += 1
            continue
        attrs = {}
        if tag.name in {"td", "th", "col", "colgroup"}:
            for key in ("rowspan", "colspan", "span"):
                if key in tag.attrs:
                    value = str(tag[key])
                    if not value.isdecimal() or not (
                        0 if key == "rowspan" else 1
                    ) <= int(value) <= (65534 if key == "rowspan" else 1000):
                        raise RenderError(
                            "invalid_table_span",
                            "Invalid table cell span; source retained.",
                        )
                    attrs[key] = value
        if tag.name == "img":
            attrs.update({k: tag[k] for k in ("src", "alt", "title") if k in tag.attrs})
        if tag.name == "ol" and str(tag.get("start", "")).lstrip("-").isdigit():
            attrs["start"] = tag["start"]
        # Retain safe alignment, but no CSS that can hide, move, clip or fetch.
        declarations = dict(
            (k.strip().lower(), v.strip().lower())
            for k, v in re.findall(r"([\w-]+)\s*:\s*([^;]+)", str(tag.get("style", "")))
        )
        align = declarations.get("text-align", str(tag.get("align", "")).lower())
        valign = declarations.get("vertical-align", str(tag.get("valign", "")).lower())
        style = []
        if align in {"left", "right", "center", "justify"}:
            style.append("text-align:" + align)
        if valign in {"top", "middle", "bottom", "baseline"}:
            style.append("vertical-align:" + valign)
        if style:
            attrs["style"] = ";".join(style)
        if dict(tag.attrs) != attrs:
            removed += 1
        tag.attrs = attrs
    return removed


def _png_data(image):
    from PIL import Image

    image = image.convert("RGBA")
    background = Image.new("RGBA", image.size, "white")
    background.alpha_composite(image)
    buf = io.BytesIO()
    background.convert("RGB").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def resolve_alias(ref, aliases, *, tolerance=2, min_iou=0.98, events=None):
    """Exact first; a small coordinate jitter must match one unique occurrence.

    This is a configurable rounding tolerance, not semantic figure localization.
    An ambiguous or newly predicted region is never assigned to the nearest image.
    """
    if ref in aliases:
        return aliases[ref]
    target = CROP.fullmatch(ref)
    if not target:
        raise RenderError("unmapped_crop", "Cannot map an image region to its source.")
    page, *box = map(int, target.groups())
    candidates = []
    for key in aliases:
        m = CROP.fullmatch(key)
        if not m or int(m[1]) != page:
            continue
        other = list(map(int, m.groups()[1:]))
        if max(abs(a - b) for a, b in zip(box, other)) > tolerance:
            continue
        intersection = max(0, min(box[2], other[2]) - max(box[0], other[0])) * max(
            0, min(box[3], other[3]) - max(box[1], other[1])
        )
        area = max(0, box[2] - box[0]) * max(0, box[3] - box[1])
        area2 = max(0, other[2] - other[0]) * max(0, other[3] - other[1])
        union = area + area2 - intersection
        if union and intersection / union >= min_iou:
            candidates.append(key)
    if len(candidates) != 1:
        raise RenderError(
            "ambiguous_crop" if candidates else "unmapped_crop",
            "Cannot uniquely map an image region to its source.",
        )
    if events is not None:
        events.append(
            {
                "code": "coordinate_rounding_match",
                "predicted": ref,
                "matched": candidates[0],
            }
        )
    return aliases[candidates[0]]


class Renderer:
    def __init__(
        self,
        browser,
        mathjax,
        *,
        limits=None,
        dollar_math="auto",
        assets=None,
        asset_root=None,
        resize=None,
        resize_name=None,
        max_input_pixels=None,
    ):
        from markdown_it import MarkdownIt

        if dollar_math not in {"auto", "all", "off"}:
            raise ValueError("dollar_math must be auto, all or off")
        self.mathjax = Path(mathjax).resolve()
        self.limits = limits or RenderLimits()
        self.dollar_math = dollar_math
        self.assets = dict(assets or {})
        self.asset_root = Path(asset_root).resolve() if asset_root else None
        self.resize = resize
        self.resize_name = resize_name or (
            "custom"
            if resize
            else "area_budget_estimate" if max_input_pixels else "unknown"
        )
        if max_input_pixels is not None and max_input_pixels <= 0:
            raise ValueError("max_input_pixels must be positive")
        self.max_input_pixels = max_input_pixels
        self.page = browser.new_page(
            viewport={"width": 1200, "height": 900}, device_scale_factor=1
        )
        self.page.set_default_timeout(self.limits.timeout_ms)
        # No external assets, CSS or network dependencies in model-supplied HTML.
        self.page.route("**/*", lambda route: route.abort())
        self.md = MarkdownIt("commonmark", {"html": True}).enable(
            ["table", "strikethrough"]
        )

    def _asset(self, ref, owner, paths):
        from PIL import Image

        crop = _CROP_ASSET.fullmatch(ref)
        if crop:
            page, left, top, right, bottom = crop.groups()
            page = int(page) if page else owner
            left, top, right, bottom = map(int, (left, top, right, bottom))
            if (
                not 1 <= page <= len(paths)
                or not 0 <= left < right <= 1000
                or not 0 <= top < bottom <= 1000
            ):
                raise RenderError(
                    "invalid_crop",
                    "An image crop has invalid coordinates; source retained.",
                )
            with Image.open(paths[page - 1]) as im:
                box = (
                    round(left * im.width / 1000),
                    round(top * im.height / 1000),
                    round(right * im.width / 1000),
                    round(bottom * im.height / 1000),
                )
                if box[0] >= box[2] or box[1] >= box[3]:
                    raise RenderError(
                        "empty_crop", "An image crop is smaller than one pixel."
                    )
                if (box[2] - box[0]) * (
                    box[3] - box[1]
                ) > self.limits.max_render_pixels:
                    raise RenderError(
                        "asset_limit",
                        "An image asset exceeds the rendering pixel budget.",
                    )
                return _png_data(im.crop(box))
        if ref.startswith("data:"):
            header, sep, payload = ref.partition(",")
            if not sep or not re.fullmatch(
                r"data:image/(?:png|jpeg|webp|gif);base64", header, re.I
            ):
                raise RenderError(
                    "unsupported_asset",
                    "An image format is unsupported; source retained.",
                )
            with Image.open(io.BytesIO(base64.b64decode(payload, validate=True))) as im:
                if im.width * im.height > self.limits.max_render_pixels:
                    raise RenderError(
                        "asset_limit",
                        "An image asset exceeds the rendering pixel budget.",
                    )
                return _png_data(im)
        path = self.assets.get(ref)
        if path is None and self.asset_root:
            from urllib.parse import unquote, urlsplit

            if not urlsplit(ref).scheme and not ref.startswith("//"):
                candidate = (self.asset_root / unquote(ref)).resolve()
                if candidate.is_relative_to(self.asset_root):
                    path = candidate
        if path is None:
            raise RenderError(
                "unresolved_asset",
                "An image needs a local asset mapping; source retained.",
            )
        with Image.open(path) as im:
            if im.width * im.height > self.limits.max_render_pixels:
                raise RenderError(
                    "asset_limit", "An image asset exceeds the rendering pixel budget."
                )
            return _png_data(im)

    def render(self, md, owner, paths, directory, name):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if Path(name).name != name or not name:
            raise ValueError("name must be a filename stem")
        started = time.monotonic()
        diag = {
            "version": 2,
            "status": "ERROR",
            "math": 0,
            "errors": 0,
            "broken": 0,
            "images": [],
            "notes": [],
            "limits": asdict(self.limits),
            "resize_policy": self.resize_name,
        }
        (directory / (name + ".source.md")).write_text(md, encoding="utf-8")
        try:
            result = self._render(md, owner, paths, directory, name, diag)
            diag["status"] = "DEGRADED" if diag.get("formula_fallbacks") else "COMPLETE"
            return result
        except Exception as exc:
            diag["error"] = {
                "code": getattr(exc, "code", "render_failed"),
                "type": type(exc).__name__,
                "message": str(exc)[:1000],
            }
            if isinstance(exc, RenderError):
                raise
            raise RenderError(
                "render_failed",
                "Page rendering failed; source and diagnostics were retained.",
            ) from exc
        finally:
            diag["seconds"] = time.monotonic() - started
            save(directory / (name + ".render.json"), diag)

    def _render(self, md, owner, paths, directory, name, diag):
        from bs4 import BeautifulSoup, Comment, NavigableString
        from PIL import Image

        if not 1 <= owner <= len(paths):
            raise RenderError("invalid_owner", "Page index is outside the document.")
        if len(md) > self.limits.max_source_chars:
            raise RenderError(
                "source_limit", "Page source exceeds the configured rendering limit."
            )
        probe = (
            BeautifulSoup(md, "html.parser") if md.lstrip().startswith("<") else None
        )
        html_document = bool(
            probe is not None
            and probe.find(True)
            and all(
                not isinstance(x, NavigableString)
                or isinstance(x, Comment)
                or not str(x).strip()
                for x in probe.contents
            )
        )
        prepared, formulas, occurrences, notes = prepare_source(
            md, self.md, self.dollar_math, html_document
        )
        diag["input_format"] = "html" if html_document else "markdown"
        diag["notes"].extend(notes)
        soup = BeautifulSoup(
            prepared if html_document else self.md.render(prepared), "html.parser"
        )
        diag["sanitized_elements"] = sanitize(soup)
        by_token = {x["token"]: x for x in occurrences}
        seen = set()
        for image in soup.find_all("img"):
            key = image.get("src", "")
            if key not in by_token or key in seen:
                raise RenderError(
                    "image_mapping", "Cannot safely map image occurrences."
                )
            seen.add(key)
            item = by_token[key]
            image["src"] = self._asset(item["ref"], owner, paths)
            image["data-image-id"] = key
        if len(seen) != len(occurrences):
            raise RenderError(
                "image_mapping", "An image occurrence was lost during parsing."
            )
        by_math = {x["token"]: x for x in formulas}
        if by_math:
            pattern = re.compile(
                "|".join(map(re.escape, sorted(by_math, key=len, reverse=True)))
            )
            found = []
            for node in list(soup.find_all(string=pattern)):
                if isinstance(node, Comment):
                    continue
                pieces = pattern.split(str(node))
                matches = list(pattern.finditer(str(node)))
                for k, part in enumerate(pieces):
                    if part:
                        node.insert_before(NavigableString(part))
                    if k < len(matches):
                        item = by_math[matches[k][0]]
                        found.append(item["token"])
                        tag = soup.new_tag("span")
                        tag["data-math-id"] = item["token"]
                        tag["class"] = (
                            "math-display" if item["display"] else "math-inline"
                        )
                        tag.string = item["raw"]
                        node.insert_before(tag)
                node.extract()
            if sorted(found) != sorted(by_math):
                raise RenderError(
                    "formula_mapping", "A formula was lost during parsing."
                )
        tables = soup.find_all("table")
        expected_cells = len(soup.find_all(["td", "th"]))
        columns = max(
            (
                sum(
                    int(c.get("colspan", 1))
                    for c in row.find_all(["td", "th"], recursive=False)
                )
                for row in soup.find_all("tr")
            ),
            default=1,
        )
        with Image.open(paths[owner - 1]) as original:
            width = max(original.width, columns * 120 + 56)
        document = (
            '<!doctype html><html><head><meta charset="utf-8"><style>'
            + _CSS
            + '</style></head><body><div id="capture" style="width:'
            + str(width)
            + 'px">'
            + str(soup)
            + "</div></body></html>"
        )
        # Persist complete render input before any resource/readability rejection.
        (directory / (name + ".html")).write_text(document, encoding="utf-8")
        if width > self.limits.max_side:
            raise RenderError(
                "size_limit",
                "Rendered page exceeds the configured size limit; complete source retained.",
            )
        page = self.page
        page.set_viewport_size({"width": width, "height": 900})
        page.set_content(document, wait_until="domcontentloaded")
        diag["expected_formulas"] = len(formulas)
        formula_results = []
        if formulas:
            if not self.mathjax.is_file():
                raise RenderError("mathjax_missing", "Local MathJax bundle is missing.")
            page.evaluate(
                "window.MathJax={startup:{typeset:false},svg:{fontCache:'none'},tex:{maxBuffer:100000,packages:['base','ams','newcommand']}}"
            )
            page.add_script_tag(path=str(self.mathjax))
            page.wait_for_function(
                "window.MathJax && MathJax.startup && MathJax.startup.promise"
            )
            formula_results = page.evaluate(
                """async ({items,timeout})=>{
              const work=async()=>{
                await MathJax.startup.promise;
                if(typeof MathJax.tex2svgPromise!=='function') throw Error('MathJax SVG API unavailable');
                const results=[];
                for(const item of items){
                  const target=document.querySelector('[data-math-id="'+item.token+'"]');
                  let error=null;
                  try{
                    if(item.malformed) throw Error('Unclosed formula delimiter');
                    if(!item.tex.trim()) throw Error('Empty formula');
                    const rendered=await MathJax.tex2svgPromise(item.tex,{display:item.display});
                    if(rendered.querySelector('[data-mml-node="merror"],mjx-merror,[data-mjx-error]')) throw Error('Invalid TeX');
                    // This screenshot renderer needs only SVG. Standalone tex2svg
                    // may emit assistive MathML without the CSS that hides it.
                    rendered.querySelectorAll('mjx-assistive-mml').forEach(node=>node.remove());
                    target.replaceChildren(rendered);
                  }catch(e){error=String(e.message||e);target.textContent=item.raw;target.classList.add('math-fallback');}
                  results.push({id:item.token,rendered:!error,error});
                }
                return results;
              };
              let timer;try{return await Promise.race([work(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Formula rendering timeout')),timeout)})]);}finally{clearTimeout(timer);}
            }""",
                {"items": formulas, "timeout": self.limits.timeout_ms},
            )
        diag["formulas"] = formula_results
        diag["formula_fallbacks"] = sum(not x["rendered"] for x in formula_results)
        page.evaluate(
            """async timeout=>{
          let timer;try{await Promise.race([(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode()));await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));})(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Image/font readiness timeout')),timeout)})]);}finally{clearTimeout(timer);}
        }""",
            self.limits.timeout_ms,
        )
        # Allow intrinsically wide math/tables to grow once, within the resource budget.
        needed = page.evaluate("document.documentElement.scrollWidth")
        if needed > width:
            width = needed + 28
            if width > self.limits.max_side:
                raise RenderError(
                    "size_limit",
                    "Rendered content is too wide; complete source retained.",
                )
            page.set_viewport_size({"width": width, "height": 900})
            page.locator("#capture").evaluate('(e,w)=>e.style.width=w+"px"', width)
        layout = page.evaluate("""()=>{
          const root=document.getElementById('capture');const area=root.getBoundingClientRect();
          const boxes=[...root.querySelectorAll('table,td,th,img,[data-math-id]')];
          const bad=boxes.filter(e=>{const b=e.getBoundingClientRect(),s=getComputedStyle(e);return !b.width||!b.height||s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0||b.left<0||b.right>area.right+.5;}).map(e=>({tag:e.tagName,id:e.dataset.imageId||e.dataset.mathId||null}));
          const fonts=[];const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n;
          while(n=walker.nextNode()){if(n.textContent.trim()&&!n.parentElement.closest('mjx-container')){const s=getComputedStyle(n.parentElement);fonts.push(parseFloat(s.fontSize));}}
          const cellHeights=[...root.querySelectorAll('td,th')].map(e=>e.getBoundingClientRect().height);
          return {math:root.querySelectorAll('mjx-container').length,broken:[...root.querySelectorAll('img')].filter(i=>!i.complete||!i.naturalWidth).length,
            tables:root.querySelectorAll('table').length,cells:cellHeights.length,invisible_or_clipped:bad,
            min_font_px:fonts.length?fonts.reduce((a,b)=>Math.min(a,b),Infinity):18,min_cell_height_px:cellHeights.length?cellHeights.reduce((a,b)=>Math.min(a,b),Infinity):null,
            width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,
            images:[...root.querySelectorAll('img')].map(i=>{const b=i.getBoundingClientRect();return {id:i.dataset.imageId,x:b.x,y:b.y,w:b.width,h:b.height}})};
        }""")
        diag.update(layout)
        diag["errors"] = diag["formula_fallbacks"]
        if layout["tables"] != len(tables) or layout["cells"] != expected_cells:
            raise RenderError(
                "table_structure",
                "Browser parsing changed the table structure; source retained.",
            )
        if layout["invisible_or_clipped"] or layout["broken"]:
            raise RenderError(
                "invisible_content",
                "Some page content is not visible; source retained.",
            )
        if layout["math"] + diag["formula_fallbacks"] != len(formulas):
            raise RenderError(
                "formula_coverage", "Formula rendering coverage is incomplete."
            )
        w, h = layout["width"], layout["height"]
        # Snapshot has no live external scripts or assets; it opens offline as rendered.
        (directory / (name + ".html")).write_text(
            page.evaluate(
                "() => { const root=document.documentElement.cloneNode(true); root.querySelectorAll('script').forEach(e=>e.remove()); return '<!doctype html>'+root.outerHTML; }"
            )
        , encoding="utf-8")
        if max(w, h) > self.limits.max_side or w * h > self.limits.max_render_pixels:
            raise RenderError(
                "size_limit",
                "Rendered page exceeds the configured size limit; complete HTML retained.",
            )
        target = (w, h)
        if not self.resize and not self.max_input_pixels:
            diag["notes"].append({"code": "model_resize_unspecified"})
        if self.resize:
            try:
                target = tuple(self.resize(w, h))
            except Exception as exc:
                raise RenderError(
                    "model_resize_limit",
                    "Page exceeds the model image-size limits; complete HTML retained.",
                ) from exc
        elif self.max_input_pixels:
            scale = min(1, math.sqrt(self.max_input_pixels / (w * h)))
            target = (max(1, math.floor(w * scale)), max(1, math.floor(h * scale)))
        if len(target) != 2 or min(target) <= 0:
            raise RenderError(
                "resize_policy", "Invalid target dimensions from resize policy."
            )
        scale = min(target[0] / w, target[1] / h)
        font = layout["min_font_px"] * scale
        cell = (
            layout["min_cell_height_px"] * scale
            if layout["min_cell_height_px"]
            else None
        )
        diag["readability"] = {
            "input_size": [w, h],
            "model_size": list(target),
            "scale": scale,
            "effective_font_px": font,
            "effective_cell_height_px": cell,
            "model_resize_known": bool(self.resize),
            "estimated_from_pixel_budget": bool(
                not self.resize and self.max_input_pixels
            ),
            "thresholds_are_heuristic": True,
        }
        if font < self.limits.min_effective_font_px or (
            cell is not None and cell < self.limits.min_effective_cell_height_px
        ):
            raise RenderError(
                "readability_limit",
                "Page text would be too small after resizing; complete HTML retained.",
            )
        aliases, edits = {}, []
        for image in layout["images"]:
            item = by_token[image["id"]]
            image["ref"] = item["ref"]
            box = [
                round(image["x"] / w * 1000),
                round(image["y"] / h * 1000),
                round((image["x"] + image["w"]) / w * 1000),
                round((image["y"] + image["h"]) / h * 1000),
            ]
            if not 0 <= box[0] < box[2] <= 1000 or not 0 <= box[1] < box[3] <= 1000:
                raise RenderError(
                    "crop_quantization",
                    "An image is too small for normalized coordinate mapping.",
                )
            alias = f"page_{owner}/images/bbox_" + "_".join(map(str, box)) + ".jpg"
            if alias in aliases:
                raise RenderError(
                    "crop_alias_collision",
                    "Image positions cannot be uniquely represented.",
                )
            aliases[alias] = item["ref"]
            edits.append((item["start"], item["end"], _image_markup(item, alias)))
        rewritten = _edit_source(md, edits)
        path = directory / (name + ".png")
        page.screenshot(path=str(path), full_page=True, timeout=self.limits.timeout_ms)
        with Image.open(path) as rendered:
            if rendered.size != (w, h):
                raise RenderError(
                    "screenshot_dimensions",
                    "Screenshot dimensions differ from image coordinate mapping.",
                )
        (directory / (name + ".rewritten.md")).write_text(rewritten, encoding="utf-8")
        save(directory / (name + ".aliases.json"), aliases)
        return RenderResult(str(path), rewritten, aliases, diag)


# Command-line interface.


def main():
    parser = argparse.ArgumentParser(
        description="OvisOCR3 document-level cross-page reconstruction"
    )
    parser.add_argument("images", nargs="+", help="Page images in reading order")
    parser.add_argument("--model", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--stage1-prompt",
        default="document_structured.txt",
        choices=[
            "document_markdown",
            "document_markdown.txt",
            "document_structured",
            "document_structured.txt",
        ],
    )
    parser.add_argument(
        "--mathjax",
        default=None,
        help="Local MathJax bundle; defaults to assets/mathjax.js beside this script",
    )
    args = parser.parse_args()
    result = reconstruct_document(
        args.images,
        args.output,
        model_path=args.model,
        stage1_prompt=args.stage1_prompt,
        mathjax=args.mathjax,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
