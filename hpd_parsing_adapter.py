"""PandOCR adapter for the official HPD-Parsing OpenAI-compatible server."""

import asyncio
import base64
import io
import logging
import math
import os
import re
from html.parser import HTMLParser
from typing import Any

import fitz
import httpx
from fastapi import FastAPI, HTTPException, Request
from PIL import Image


logging.basicConfig(level=os.getenv("HPD_PARSING_LOG_LEVEL", "INFO"))
logger = logging.getLogger("hpd-parsing-adapter")

SERVER_URL = os.getenv("HPD_PARSING_SERVER_URL", "http://hpd-parsing-server:8118").rstrip("/")
MODEL_NAME = os.getenv("HPD_PARSING_SERVED_MODEL_NAME", "HPD-Parsing")
MAX_TOKENS = int(os.getenv("HPD_PARSING_MAX_TOKENS", "8000"))
PDF_DPI = int(os.getenv("HPD_PARSING_PDF_DPI", "200"))
MAX_PAGES = int(os.getenv("HPD_PARSING_MAX_PAGES_PER_REQUEST", "50"))
MAX_CONCURRENCY = max(1, int(os.getenv("HPD_PARSING_MAX_CONCURRENCY", "1")))
REQUEST_TIMEOUT = float(os.getenv("HPD_PARSING_REQUEST_TIMEOUT", "1200"))
PROMPT = "document parsing with fork."

BLOCK_HEADER_RE = re.compile(
    r"([a-zA-Z_]+)\s*\[\s*([-\d.,\s]+)\]\s*(?:<(?:FORK|CHILD|BLOCK)>)?"
)
EMPTY_CONTENT_TYPES = {"chart", "seal"}
CROPPED_VISUAL_TYPES = {"image", "figure", "chart"}
HPD_COORDINATE_SIZE = 1000.0
SKIPPED_RAG_TYPES = {
    "header", "footer", "header_image", "footer_image", "page_number", "page_num", "number", "seal",
}
IMAGE_CAPTION_TYPES = {"image_caption", "figure_caption", "figure_title"}
TABLE_CAPTION_TYPES = {"table_caption"}
TITLE_TYPES = {"doc_title", "title", "section_title", "paragraph_title", "subtitle", "reference_title", "ref_title"}
FORMULA_TYPES = {"formula", "display_formula", "equation"}
REFERENCE_TYPES = {"reference", "reference_content", "reference_text", "ref_text"}

# Kept in sync with PaddlePaddle/HPD-Parsing's official
# eval/hpd_to_markdown.py post-processing defaults.
_TALL = re.compile(
    r"\\d?frac|\\tfrac|\\cfrac|\\binom|\\sqrt"
    r"|\\sum|\\prod|\\coprod|\\int|\\iint|\\iiint|\\oint"
    r"|\\bigcup|\\bigcap|\\bigoplus|\\bigotimes|\\bigsqcup"
    r"|\\begin\{"
    r"|\\overbrace|\\underbrace|\\overset|\\underset|\\stackrel"
    r"|\\substack|\\atop|\\\\"
)
_ELLIPSIS = r"(?:\\dots|\\cdots|\\ldots|\\dotsb|\\dotsc)"
_CLOSER = r"(?:\\right\s*[.\}\]\)]|\\end\s*\{(?:array|matrix|cases|bmatrix|pmatrix|vmatrix|smallmatrix)\})"
_TAIL_WRAP = re.compile(r"^(?P<core>.*?)(?P<wrap>\s*(?:\\\]|\\\)|\$\$))?\s*$", re.DOTALL)
_OP_MAP = {
    "≈": r"\approx", "≠": r"\neq", "≤": r"\leq", "≥": r"\geq", "×": r"\times",
    "÷": r"\div", "±": r"\pm", "∓": r"\mp", "·": r"\cdot", "∙": r"\cdot",
    "⋅": r"\cdot", "∗": "*", "−": "-", "≡": r"\equiv", "∝": r"\propto",
    "∞": r"\infty", "√": r"\sqrt", "→": r"\to", "≪": r"\ll", "≫": r"\gg",
}
_ARITH_ALLOWED = re.compile(
    r"^[0-9A-Za-z\s=+\-*/^_().,:;<>|%!\u4e00-\u9fff" + "".join(_OP_MAP) + r"]+$"
)
_ARITH_HASOP = re.compile(r"[=+\-*/" + "".join(_OP_MAP) + r"]")
_KNOWN_FUNCS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp", "lim", "max", "min",
    "det", "mod", "arcsin", "arccos", "arctan", "sqrt",
}
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_MATH_SPAN = re.compile(r"(\\\[.*?\\\]|\$\$.*?\$\$|\\\(.*?\\\)|\$.*?\$)", re.DOTALL)
_SEGMENT_CONTENT_RE = re.compile(r"[^<]*<CHILD>(.*)", re.DOTALL)

app = FastAPI(title="HPD-Parsing Adapter", docs_url=None, redoc_url=None)


def decode_input(payload: dict[str, Any]) -> tuple[bytes, int]:
    encoded = payload.get("file") or payload.get("image")
    if not isinstance(encoded, str) or not encoded.strip():
        raise HTTPException(status_code=400, detail="Missing base64 file input")
    if "base64," in encoded:
        encoded = encoded.split("base64,", 1)[1]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as error:
        raise HTTPException(status_code=400, detail="Invalid base64 file input") from error
    file_type = payload.get("fileType")
    if file_type is None:
        file_type = 0 if raw.startswith(b"%PDF-") else 1
    return raw, int(file_type)


def input_page_count(raw: bytes, file_type: int) -> int:
    if file_type == 0:
        try:
            with fitz.open(stream=raw, filetype="pdf") as document:
                page_count = len(document)
        except Exception as error:
            raise HTTPException(status_code=400, detail="Unsupported PDF input") from error
        if page_count > MAX_PAGES:
            raise HTTPException(status_code=400, detail=f"PDF has more than {MAX_PAGES} pages")
        return page_count
    return 1


def load_pages(
    raw: bytes,
    file_type: int,
    start_page: int = 0,
    page_limit: int | None = None,
) -> list[Image.Image]:
    if file_type == 0:
        scale = PDF_DPI / 72.0
        try:
            document = fitz.open(stream=raw, filetype="pdf")
        except Exception as error:
            raise HTTPException(status_code=400, detail="Unsupported PDF input") from error
        with document:
            if len(document) > MAX_PAGES:
                raise HTTPException(status_code=400, detail=f"PDF has more than {MAX_PAGES} pages")
            stop_page = len(document) if page_limit is None else min(len(document), start_page + page_limit)
            return [
                Image.open(io.BytesIO(document[index].get_pixmap(
                    matrix=fitz.Matrix(scale, scale), alpha=False,
                ).tobytes("png"))).convert("RGB")
                for index in range(max(0, start_page), stop_page)
            ]
    if start_page > 0:
        return []
    try:
        image = Image.open(io.BytesIO(raw))
        image.seek(0)
        return [image.convert("RGB")]
    except Exception as error:
        raise HTTPException(status_code=400, detail="Unsupported image input") from error


def image_data_url(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def normalized_bbox_to_pixels(bbox: Any, width: int, height: int) -> tuple[int, int, int, int] | None:
    if not isinstance(bbox, list) or len(bbox) < 4 or width <= 0 or height <= 0:
        return None
    try:
        x1, y1, x2, y2 = (float(value) for value in bbox[:4])
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
        return None
    left = max(0, min(width, int(x1 / HPD_COORDINATE_SIZE * width)))
    top = max(0, min(height, int(y1 / HPD_COORDINATE_SIZE * height)))
    right = max(0, min(width, math.ceil(x2 / HPD_COORDINATE_SIZE * width)))
    bottom = max(0, min(height, math.ceil(y2 / HPD_COORDINATE_SIZE * height)))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def crop_visual_region(image: Image.Image, bbox: Any) -> str | None:
    pixel_bbox = normalized_bbox_to_pixels(bbox, image.width, image.height)
    if not pixel_bbox:
        return None
    crop = image.crop(pixel_bbox).convert("RGB")
    buffer = io.BytesIO()
    crop.save(buffer, format="JPEG", quality=90, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class MarkdownTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict[str, Any]]] = []
        self.current_row: list[dict[str, Any]] | None = None
        self.current_cell: dict[str, Any] | None = None
        self.cell_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self.current_row = []
        elif tag in {"td", "th"} and self.current_row is not None:
            values = dict(attrs)
            self.current_cell = {
                "header": tag == "th",
                "rowspan": max(1, int(values.get("rowspan") or 1)),
                "colspan": max(1, int(values.get("colspan") or 1)),
            }
            self.cell_parts = []
        elif tag == "br" and self.current_cell is not None:
            self.cell_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self.current_cell is not None and self.current_row is not None:
            text = re.sub(r"[ \t]+", " ", "".join(self.cell_parts))
            text = re.sub(r"\s*\n\s*", "<br>", text).strip()
            self.current_cell["text"] = text
            self.current_row.append(self.current_cell)
            self.current_cell = None
            self.cell_parts = []
        elif tag == "tr" and self.current_row is not None:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = None


def assign_parent_groups(parent_cells: list[dict[str, Any]], subgroup_widths: list[int]) -> list[str]:
    """Map consecutive subgroups to parents while tolerating inaccurate OCR colspans."""
    if not parent_cells or not subgroup_widths:
        return [""] * len(subgroup_widths)

    best: tuple[int, list[str]] | None = None

    def search(parent_index: int, subgroup_index: int, cost: int, names: list[str]) -> None:
        nonlocal best
        if parent_index == len(parent_cells):
            if subgroup_index == len(subgroup_widths) and (best is None or cost < best[0]):
                best = (cost, names)
            return
        parents_left = len(parent_cells) - parent_index - 1
        groups_left = len(subgroup_widths) - subgroup_index
        minimum = 1 if groups_left >= parents_left + 1 else 0
        maximum = groups_left - min(parents_left, groups_left)
        for count in range(minimum, maximum + 1):
            width = sum(subgroup_widths[subgroup_index:subgroup_index + count])
            target = int(parent_cells[parent_index]["colspan"])
            parent_name = str(parent_cells[parent_index].get("text") or "").strip()
            search(
                parent_index + 1,
                subgroup_index + count,
                cost + abs(target - width),
                names + [parent_name] * count,
            )

    search(0, 0, 0, [])
    return best[1] if best else [""] * len(subgroup_widths)


def html_table_to_gfm(table_html: str) -> str:
    parser = MarkdownTableParser()
    try:
        parser.feed(table_html)
    except (ValueError, TypeError):
        return table_html.strip()
    if not parser.rows:
        return table_html.strip()

    cells_by_position: dict[tuple[int, int], dict[str, Any]] = {}
    max_column = 0
    max_row = len(parser.rows)
    for row_index, cells in enumerate(parser.rows):
        column = 0
        for cell in cells:
            while (row_index, column) in cells_by_position:
                column += 1
            rowspan = int(cell["rowspan"])
            colspan = int(cell["colspan"])
            text = str(cell.get("text") or "")
            for target_row in range(row_index, row_index + rowspan):
                for target_column in range(column, column + colspan):
                    cells_by_position[(target_row, target_column)] = {
                        "text": text,
                        "header": bool(cell["header"]),
                    }
            max_row = max(max_row, row_index + rowspan)
            max_column = max(max_column, column + colspan)
            column += colspan

    if max_column == 0:
        return table_html.strip()

    def markdown_cell(value: str) -> str:
        return value.replace("|", r"\|").replace("\n", "<br>").strip()

    rows = [
        [markdown_cell(str(cells_by_position.get((row, column), {}).get("text") or "")) for column in range(max_column)]
        for row in range(max_row)
    ]
    explicit_header_rows = {
        row for (row, _), cell in cells_by_position.items() if cell.get("header")
    }
    header_depth = max(explicit_header_rows, default=0) + 1
    numeric_cell = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:%|[a-zA-Z]+)?$")
    for row_index, row in enumerate(rows[1:], start=1):
        nonempty = [value for value in row if value]
        numeric_count = sum(bool(numeric_cell.match(value.replace(",", ""))) for value in nonempty)
        if nonempty and numeric_count >= max(1, len(nonempty) // 4):
            header_depth = max(header_depth, row_index)
            break
    header_depth = min(max(1, header_depth), len(rows))

    # Some OCR table outputs compensate for an undersized colspan with an
    # empty trailing header cell. Drop columns that contain no body data.
    while max_column > 1 and rows[header_depth:] and all(not row[max_column - 1] for row in rows[header_depth:]):
        max_column -= 1
        rows = [row[:max_column] for row in rows]

    header = []
    for column in range(max_column):
        hierarchy: list[str] = []
        for row in range(header_depth):
            value = rows[row][column]
            if value and (not hierarchy or hierarchy[-1] != value):
                hierarchy.append(value)
        header.append(" / ".join(hierarchy))

    # The official model sometimes omits the repeated leaf metric row and
    # places one copy at the end of the subgroup row. Infer the rectangular
    # hierarchy from the body width (for example EM/ACC/Time for each dataset).
    if header_depth == 2:
        identifier_count = sum(
            int(cell["colspan"])
            for cell in parser.rows[0]
            if int(cell["rowspan"]) >= header_depth and str(cell.get("text") or "").strip()
        )
        parent_cells = [
            cell for cell in parser.rows[0]
            if int(cell["rowspan"]) < header_depth and str(cell.get("text") or "").strip()
        ]
        second_level_names = [
            str(cell.get("text") or "").strip()
            for cell in parser.rows[1]
            if str(cell.get("text") or "").strip()
        ]
        metric_count = max_column - identifier_count
        inferred: tuple[list[str], list[str]] | None = None
        for leaf_count in range(1, min(6, len(second_level_names))):
            if metric_count % leaf_count:
                continue
            subgroup_count = metric_count // leaf_count
            if subgroup_count + leaf_count == len(second_level_names):
                inferred = (second_level_names[:subgroup_count], second_level_names[subgroup_count:])
                break
        if identifier_count and parent_cells and inferred:
            subgroup_names, leaf_names = inferred
            subgroup_widths = [len(leaf_names)] * len(subgroup_names)
            parent_names = assign_parent_groups(parent_cells, subgroup_widths)
            repaired = header[:identifier_count]
            for parent, subgroup in zip(parent_names, subgroup_names):
                repaired.extend(f"{parent} / {subgroup} / {leaf}" for leaf in leaf_names)
            if len(repaired) == max_column:
                header = repaired

    # Repair common multi-row OCR tables whose declared colspans are off by
    # one. The deepest header row still carries a reliable repeating metric
    # pattern, so rebuild the hierarchy from that pattern and the group names.
    if header_depth >= 3:
        identifier_count = sum(
            int(cell["colspan"])
            for cell in parser.rows[0]
            if int(cell["rowspan"]) >= header_depth and str(cell.get("text") or "").strip()
        )
        subgroup_names = [
            str(cell.get("text") or "").strip()
            for cell in parser.rows[header_depth - 2]
            if str(cell.get("text") or "").strip()
        ]
        leaf_names = [
            str(cell.get("text") or "").strip()
            for cell in parser.rows[header_depth - 1]
            if str(cell.get("text") or "").strip()
        ]
        metric_count = max_column - identifier_count
        metric_period = next((
            period
            for period in range(1, min(6, len(leaf_names)) + 1)
            if len(leaf_names) > period
            and all(leaf_names[index] == leaf_names[index % period] for index in range(len(leaf_names)))
        ), 0)
        if metric_period and subgroup_names and (len(subgroup_names) - 1) * metric_period < metric_count:
            subgroup_widths = [metric_period] * (len(subgroup_names) - 1)
            subgroup_widths.append(metric_count - sum(subgroup_widths))
            parent_cells = [
                cell for cell in parser.rows[0]
                if int(cell["rowspan"]) < header_depth and str(cell.get("text") or "").strip()
            ]
            parent_names = assign_parent_groups(parent_cells, subgroup_widths)

            repaired = header[:identifier_count]
            for subgroup_index, (subgroup, width) in enumerate(zip(subgroup_names, subgroup_widths)):
                parent = parent_names[subgroup_index] if subgroup_index < len(parent_names) else ""
                for metric_index in range(width):
                    leaf = leaf_names[metric_index % metric_period]
                    repaired.append(" / ".join(value for value in (parent, subgroup, leaf) if value))
            if len(repaired) == max_column:
                header = repaired
    body = rows[header_depth:]
    rendered = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    rendered.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(rendered)


def convert_html_tables(text: str) -> str:
    return re.sub(
        r"<table\b[^>]*>.*?</table>",
        lambda match: html_table_to_gfm(match.group(0)),
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )


def normalize_prose(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""
    if all(re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", line) for line in lines):
        return "\n".join(lines)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def heading_level(block_type: str, text: str, bbox: Any) -> int:
    if block_type == "doc_title":
        return 1
    roman = re.match(r"^([IVXLCDM]+)\.\s+", text, re.IGNORECASE)
    if roman and (len(roman.group(1)) > 1 or roman.group(1).upper() in {"I", "V", "X"}):
        return 2
    numeric = re.match(r"^(\d+(?:\.\d+)*)[.)]?\s+", text)
    if numeric:
        return min(2 + numeric.group(1).count("."), 6)
    if re.match(r"^[A-Z][.)]\s+", text):
        return 3
    if re.match(r"^(?:ABSTRACT|ACKNOWLEDGMENTS?|REFERENCES|APPENDIX\b)", text, re.IGNORECASE):
        return 2
    if block_type in {"paragraph_title", "subtitle"}:
        return 3
    return 2


def display_formula(text: str) -> str:
    text = text.strip()
    if text.startswith(r"\(") and text.endswith(r"\)"):
        text = text[2:-2].strip()
    elif text.startswith(r"\[") and text.endswith(r"\]"):
        text = text[2:-2].strip()
    elif text.startswith("$$") and text.endswith("$$"):
        text = text[2:-2].strip()
    return f"$$\n{text}\n$$" if text else ""


def format_reference(text: str) -> str:
    text = normalize_prose(text)
    match = re.match(r"^\[(\d+)\]\s*(.*)", text, re.DOTALL)
    return f"{match.group(1)}. {match.group(2).strip()}" if match else f"- {text}"


def format_rag_text_block(block_type: str, text: str, bbox: Any) -> str:
    if not text or block_type in SKIPPED_RAG_TYPES:
        return ""
    if block_type in TITLE_TYPES:
        explicit_heading = re.match(r"^(#{1,6})\s+", text.strip())
        title = normalize_prose(re.sub(r"^#{1,6}\s*", "", text))
        level = len(explicit_heading.group(1)) if explicit_heading else heading_level(block_type, title, bbox)
        return f"{'#' * level} {title}" if title else ""
    if block_type in FORMULA_TYPES:
        return display_formula(text)
    if block_type == "table" or "<table" in text.lower():
        return convert_html_tables(text)
    if block_type in IMAGE_CAPTION_TYPES:
        caption = normalize_prose(text)
        return f"*{caption}*" if caption else ""
    if block_type in TABLE_CAPTION_TYPES:
        caption = normalize_prose(text)
        return f"**{caption}**" if caption else ""
    if block_type in REFERENCE_TYPES:
        return format_reference(text)
    prose = normalize_prose(text)
    abstract = re.match(r"^(Abstract|摘要)\s*[—–:-]\s*(.+)$", prose, re.IGNORECASE | re.DOTALL)
    if abstract:
        return f"## {abstract.group(1).title()}\n\n{abstract.group(2).strip()}"
    keywords = re.match(r"^(Index Terms|Keywords|关键词)\s*[—–:-]\s*(.+)$", prose, re.IGNORECASE | re.DOTALL)
    if keywords:
        return f"## {keywords.group(1).title()}\n\n{keywords.group(2).strip()}"
    if re.match(r"^[-*+]\s+", prose):
        return re.sub(r"^[-*+]\s+", "- ", prose)
    return prose


def image_alt_text(blocks: list[dict[str, Any]], block_index: int, block_type: str, visual_index: int) -> str:
    for following in blocks[block_index + 1:block_index + 3]:
        following_type = str(following.get("type") or "").lower()
        if following_type in IMAGE_CAPTION_TYPES and following.get("text"):
            caption = normalize_prose(str(following["text"]))
            caption = re.sub(r"^(?:Fig(?:ure)?\.?\s*\d+[A-Za-z]?\.?\s*)", "", caption, flags=re.IGNORECASE)
            first_sentence = re.split(r"(?<=[.!?])\s+", caption, maxsplit=1)[0]
            return first_sentence[:140].replace("[", "").replace("]", "")
        if following_type not in {"image", "figure", "chart"}:
            break
    return f"{block_type} {visual_index}"


def _scan_delims(text: str) -> list[dict[str, Any]]:
    delimiters: list[dict[str, Any]] = []
    for match in re.finditer(r"\\(left|right)\s*", text):
        delimiter = re.match(r"\\[a-zA-Z]+|\\.|.", text[match.end():])
        if delimiter:
            delimiters.append({
                "kind": match.group(1),
                "delim": delimiter.group(0),
                "start": match.start(),
                "end": match.end() + delimiter.end(),
            })
    return delimiters


def simplify_left_right(text: str) -> str:
    if r"\left" not in text:
        return text
    stack: list[dict[str, Any]] = []
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for delimiter in _scan_delims(text):
        if delimiter["kind"] == "left":
            stack.append(delimiter)
        elif stack:
            pairs.append((stack.pop(), delimiter))
    edits: list[tuple[int, int, str]] = []
    for left, right in pairs:
        if left["delim"] == "(" and right["delim"] == ")" and not _TALL.search(text[left["end"]:right["start"]]):
            edits.extend(((left["start"], left["end"], "("), (right["start"], right["end"], ")")))
    for start, end, replacement in sorted(edits, key=lambda item: item[0], reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def clean_formula_tail(text: str) -> str:
    if not text:
        return text
    match = _TAIL_WRAP.match(text)
    if not match:
        return text
    core, wrap = match.group("core"), match.group("wrap") or ""
    previous = None
    while previous != core:
        previous = core
        core = re.sub(r"(" + _ELLIPSIS + r")(?:\s*" + _ELLIPSIS + r")+", r"\1", core)
        core = re.sub(
            r"(?P<keep>" + _CLOSER + r")\s*(?:\\q?quad\s*)*" + _ELLIPSIS + r"\s*$",
            lambda item: item.group("keep"),
            core,
        )
        core = re.sub(r"(?:\s*\\q?quad)+\s*" + _ELLIPSIS + r"\s*$", "", core)
        core = re.sub(r"(?:\s*\\q?quad)+\s*$", "", core).rstrip()
    return core + wrap


def _convert_unicode_ops(text: str) -> str:
    for operator, replacement in _OP_MAP.items():
        text = text.replace(operator, replacement + " " if replacement.startswith("\\") else replacement)
    text = _CJK_RUN.sub(lambda match: r"\text{" + match.group(0) + "}", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _is_pure_arith_line(line: str) -> bool:
    text = line.strip()
    if not text or r"\(" in text or r"\[" in text or "$" in text or "<" in text:
        return False
    if not _ARITH_ALLOWED.match(text) or not _ARITH_HASOP.search(text):
        return False
    return all(word.lower() in _KNOWN_FUNCS for word in re.findall(r"[A-Za-z]{2,}", text))


def normalize_arith(text: str) -> str:
    if not text:
        return text
    text = _MATH_SPAN.sub(lambda match: _convert_unicode_ops(match.group(0)), text)
    return "\n".join(
        r"\( " + _convert_unicode_ops(line.strip()) + r" \)" if _is_pure_arith_line(line) else line
        for line in text.split("\n")
    )


def clean_block_text(text: str) -> str:
    text = text.strip()
    text = text.replace("The image is too blurry to recognize any text content.", "").strip()
    text = text.replace(
        "The image contains no text or characters. It is a graphical element (a horizontal line with a vertical line) "
        "and does not contain any chart, graph, or data points that can be extracted. Therefore, the correct OCR "
        "output is an empty string.",
        "",
    ).strip()
    if not text or text == "[Non-Text]":
        return ""
    if text.startswith("<table>") and not text.endswith("</table>"):
        text += "</table>"
    if text.startswith(r"\[") and not text.endswith("\n" + r"\]"):
        text += "\n\\]"
    if "\\[\n" in text and "\\\\" not in text:
        text = text.replace("\\[\n", r"\(").replace("\n\\]", r"\)")
    text = text.replace(r"\) \(", "\\)\n\n\\(")
    if "÷" in text and r"\(" not in text:
        text = r"\( " + text + r" \)"
    text = re.sub(r"\\tag\s*\{[^{}]*\}", "", text)
    text = text.replace(r"\supset", r"\sqsupset")
    text = simplify_left_right(text)
    text = clean_formula_tail(text)
    return normalize_arith(text)


def remove_block_fork_tags(raw_text: str) -> str:
    """Official HPD-Parsing Markdown export, with all documented defaults enabled."""
    lines: list[str] = []
    for segment in raw_text.split("<BLOCK>")[1:]:
        category = re.match(r"\s*([a-zA-Z_]+)", segment)
        if category and category.group(1).lower() in EMPTY_CONTENT_TYPES:
            continue
        content = _SEGMENT_CONTENT_RE.match(segment)
        if not content:
            continue
        text = content.group(1).strip()
        text = re.sub(r"\b\w+\s*\[\s*[-\d.,\s]+\]\s*<(?:FORK|CHILD|BLOCK)>", "", text)
        text = re.sub(r"<(?:FORK|CHILD|BLOCK)>", "", text).strip()
        text = clean_block_text(text)
        if text:
            lines.append(text)
    return "\n\n".join(lines).strip()


def parse_blocks(raw_text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for segment in raw_text.split("<BLOCK>")[1:]:
        stripped = segment.strip()
        header = BLOCK_HEADER_RE.match(stripped)
        fallback = re.match(r"([a-zA-Z_]+)", stripped)
        block_type = header.group(1) if header else (fallback.group(1) if fallback else "unknown")
        bbox = None
        if header:
            values = [float(value) for value in re.split(r"[,\s]+", header.group(2).strip()) if value]
            if len(values) == 4:
                bbox = [int(value) if value.is_integer() else value for value in values]
        text = ""
        if block_type.lower() not in EMPTY_CONTENT_TYPES:
            content = re.search(r"<CHILD>(.*)", segment, re.DOTALL)
            if content:
                text = clean_block_text(re.split(r"<(?:FORK|CHILD|BLOCK)>", content.group(1))[0])
        blocks.append({"type": block_type, "bbox": bbox, "text": text})
    return blocks


def blocks_to_official_markdown(blocks: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        str(block.get("text") or "").strip()
        for block in blocks
        if str(block.get("type") or "").lower() not in EMPTY_CONTENT_TYPES and block.get("text")
    ).strip()


async def parse_page(client: httpx.AsyncClient, image: Image.Image, semaphore: asyncio.Semaphore) -> tuple[str, list[dict[str, Any]]]:
    body = {
        "model": MODEL_NAME,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": image_data_url(image)}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
    }
    async with semaphore:
        response = await client.post(f"{SERVER_URL}/v1/chat/completions", json=body)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=f"HPD-Parsing server error: {response.text}")
    try:
        raw_text = response.json()["choices"][0]["message"]["content"]
    except Exception as error:
        raise HTTPException(status_code=502, detail="Unexpected response from HPD-Parsing server") from error
    blocks = parse_blocks(str(raw_text))
    markdown = blocks_to_official_markdown(blocks) or str(raw_text).strip()
    return markdown, blocks


def build_response(
    pages: list[Image.Image],
    parsed: list[tuple[str, list[dict[str, Any]]]],
    page_offset: int = 0,
) -> dict[str, Any]:
    markdown_pages: list[str] = []
    results: list[dict[str, Any]] = []
    all_images: dict[str, str] = {}
    for local_index, (image, (markdown, blocks)) in enumerate(zip(pages, parsed)):
        index = page_offset + local_index
        document_title_pending = index == 0
        page_images: dict[str, str] = {}
        page_markdown_parts: list[str] = []
        parsing_blocks: list[dict[str, Any]] = []
        visual_index = 0
        for block_index, block in enumerate(blocks):
            content_parts: list[str] = []
            block_type = str(block["type"] or "unknown").lower()
            formatting_type = block_type
            if document_title_pending and block_type in TITLE_TYPES and str(block["text"] or "").strip():
                formatting_type = "doc_title"
                document_title_pending = False
            if block_type in CROPPED_VISUAL_TYPES:
                visual_index += 1
                encoded_crop = crop_visual_region(image, block["bbox"])
                if encoded_crop:
                    image_path = f"images/page_{index + 1}_{block_type}_{visual_index}.jpg"
                    alt_text = image_alt_text(blocks, block_index, block_type, visual_index)
                    image_markdown = f"![{alt_text}]({image_path})"
                    page_images[image_path] = encoded_crop
                    all_images[image_path] = encoded_crop
                    content_parts.append(image_markdown)
            formatted_text = format_rag_text_block(formatting_type, str(block["text"] or ""), block["bbox"])
            if formatted_text:
                content_parts.append(formatted_text)
            block_content = "\n\n".join(content_parts).strip()
            if block_content:
                page_markdown_parts.append(block_content)
            parsing_blocks.append({
                "block_label": block["type"],
                "block_content": block_content,
                "block_bbox": block["bbox"],
            })
        page_markdown = "\n\n".join(page_markdown_parts).strip() or markdown
        markdown_pages.append(page_markdown)
        results.append({
            "parser": "hpd-parsing",
            "pageIndex": index,
            "width": image.width,
            "height": image.height,
            "markdown": {"text": page_markdown, "images": page_images},
            "parsing_res_list": parsing_blocks,
        })
    return {
        "markdown": "\n\n".join(page for page in markdown_pages if page),
        "images": all_images,
        "layoutParsingResults": results,
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{SERVER_URL}/health")
        ready = 200 <= response.status_code < 300
    except Exception:
        ready = False
    if not ready:
        raise HTTPException(status_code=503, detail="HPD-Parsing server is not ready")
    return {"status": "ok", "model": MODEL_NAME, "backend": "vllm"}


@app.post("/ocr")
async def ocr(request: Request) -> dict[str, Any]:
    payload = await request.json()
    raw, file_type = decode_input(payload)
    page_count = await asyncio.to_thread(input_page_count, raw, file_type)
    if page_count < 1:
        raise HTTPException(status_code=400, detail="Input contains no pages")
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    timeout = None if REQUEST_TIMEOUT <= 0 else REQUEST_TIMEOUT
    markdown_chunks: list[str] = []
    all_images: dict[str, str] = {}
    layout_results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for page_offset in range(0, page_count, MAX_CONCURRENCY):
            pages = await asyncio.to_thread(
                load_pages, raw, file_type, page_offset, MAX_CONCURRENCY,
            )
            parsed = await asyncio.gather(*(parse_page(client, page, semaphore) for page in pages))
            chunk = build_response(pages, parsed, page_offset=page_offset)
            if chunk["markdown"]:
                markdown_chunks.append(chunk["markdown"])
            all_images.update(chunk["images"])
            layout_results.extend(chunk["layoutParsingResults"])
    logger.info("Parsed %d page(s) with HPD-Parsing", page_count)
    return {
        "markdown": "\n\n".join(markdown_chunks),
        "images": all_images,
        "layoutParsingResults": layout_results,
    }
