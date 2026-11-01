"""Conservative, source-PDF reading order for native, unrotated PDF pages.

Model blocks and sourceTextBlocks remain untouched.  The derived view records
its own geometry and keeps the original Markdown for inspection or rollback.
"""
import os
import re
from collections import Counter

import fitz


_FIG = re.compile(r'^\s*(?:Fig(?:ure)?\.?\s*(\d+[A-Za-z]?)|图\s*(\d+))\s*[.:：、]', re.I)
_TABLE = re.compile(r'^\s*TABLE\s+([IVXLCDM]+|\d+)\s*(?:\n|$)', re.I)
_EQUATION = re.compile(r'\((\d+)\)\s*$')
_FORMULAS = {'formula', 'display_formula', 'equation'}
_SECTION = re.compile(r'^[IVXLCDM]+\.\s+[A-Z][A-Z0-9 &,:\-]+$')
_SUBSECTION = re.compile(r'^[A-Z]\.\s+[A-Z][^.!?]{2,100}$')
_VISUALS = {'image', 'chart', 'figure', 'header_image', 'footer_image'}


def _text(block):
    return block.get('block_content', block.get('text', ''))


def _native_blocks(page):
    result = []
    for block in page.get_text('dict')['blocks']:
        if block.get('type') != 0:
            continue
        lines = []
        for line in block['lines']:
            text = ''.join(span['text'] for span in line['spans']).strip()
            if text:
                lines.append((fitz.Rect(line['bbox']), text, line.get('dir', (1, 0))))
        if lines:
            result.append({'rect': fitz.Rect(block['bbox']), 'lines': lines,
                           'text': '\n'.join(line[1] for line in lines),
                           'fonts': [(round(span['size'], 1), len(span['text']))
                                     for line in block['lines'] for span in line['spans']
                                     if span['text'].strip()]})
    return result


def _normalized(rect, page):
    return [round(rect.x0 / page.rect.width * 1000, 2),
            round(rect.y0 / page.rect.height * 1000, 2),
            round(rect.x1 / page.rect.width * 1000, 2),
            round(rect.y1 / page.rect.height * 1000, 2)]


def _rect(box, page):
    return fitz.Rect(box[0] * page.rect.width / 1000, box[1] * page.rect.height / 1000,
                     box[2] * page.rect.width / 1000, box[3] * page.rect.height / 1000)


def _overlap_x(a, b):
    return max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) / max(1, min(a.width, b.width))


def _clean(text):
    # Join native line wraps and discretionary end-of-line hyphens only.
    text = re.sub(r'(?<=[A-Za-z])-\n(?=[a-z])', '', text)
    text = re.sub(r'(?<=[A-Za-z])-\n(?=[A-Z])', '-', text)
    return ' '.join(text.split())


def _block(label, rect, text, page):
    return {'block_label': label, 'block_bbox': _normalized(rect, page),
            'block_content': text, 'textSource': 'pdf-native' if label != 'formula' else 'model-latex'}


def _unit(rect, *parts):
    return {'rect': rect, 'parts': list(parts)}


def _text_parts(lines, block, page, body_font, aside):
    box = fitz.Rect(lines[0][0])
    for rect, _, _ in lines[1:]:
        box |= rect
    text = _clean('\n'.join(line[1] for line in lines))
    if aside:
        return [_block('aside', box, text, page)]
    if block['fonts'] and min(size for size, _ in block['fonts']) >= max(body_font * 1.6, body_font + 4):
        return [_block('doc_title', box, '# ' + text, page)]
    parts = []
    remaining = list(lines)
    while remaining:
        rect, value, _ = remaining[0]
        if _SECTION.fullmatch(value):
            prefix = '## '
        elif _SUBSECTION.fullmatch(value):
            prefix = '### '
        else:
            break
        parts.append(_block('section_header', rect, prefix + value, page))
        remaining.pop(0)
    if remaining:
        body_box = fitz.Rect(remaining[0][0])
        for rect, _, _ in remaining[1:]:
            body_box |= rect
        parts.append(_block('text', body_box, _clean('\n'.join(line[1] for line in remaining)), page))
    return parts


def _merge_title_lines(blocks):
    merged = []
    for block in blocks:
        if (merged and block['block_label'] == merged[-1]['block_label'] == 'doc_title'
                and -10 <= block['block_bbox'][1] - merged[-1]['block_bbox'][3] <= 35):
            previous = merged[-1]
            previous['block_content'] += ' ' + block['block_content'].removeprefix('# ')
            previous['block_bbox'] = list(fitz.Rect(previous['block_bbox']) | fitz.Rect(block['block_bbox']))
        else:
            merged.append(block)
    return merged


def _reading_order(units, page):
    """Full-width items divide the page into bands; read columns within a band."""
    middle = page.rect.width / 2
    asides = [u for u in units if u.get('aside')]
    body = [u for u in units if not u.get('aside')]
    paragraphs = [u for u in body if u['parts'][0]['block_label'] == 'text'
                  and len(u['parts'][0]['block_content']) > 120 and u['rect'].height > 25]
    prelude = []
    if paragraphs:
        start = min(u['rect'].y0 for u in paragraphs)
        header = [u for u in body if u['rect'].y1 <= start]
        if any(u['rect'].x0 < middle < u['rect'].x1 for u in header):
            # Titles and multi-author affiliations precede the body columns.
            # Reading them by rows avoids treating a centered author as a new
            # full-width body section or postponing a title continuation.
            prelude = sorted(header, key=lambda u: (round(u['rect'].y0), u['rect'].x0))
            body = [u for u in body if u not in header]
    full = sorted([u for u in body
                   if u['rect'].x0 < middle - page.rect.width * .04
                   and u['rect'].x1 > middle + page.rect.width * .04],
                  key=lambda u: u['rect'].y0)
    narrow = [u for u in body if u not in full]

    def columns(items):
        left = [u for u in items if (u['rect'].x0 + u['rect'].x1) / 2 < middle]
        right = [u for u in items if u not in left]
        return sorted(left, key=lambda u: (u['rect'].y0, u['rect'].x0)) + sorted(
            right, key=lambda u: (u['rect'].y0, u['rect'].x0))

    ordered = list(prelude)
    for separator in full:
        before = [u for u in narrow if (u['rect'].y0 + u['rect'].y1) / 2 < separator['rect'].y0]
        ordered.extend(columns(before))
        narrow = [u for u in narrow if u not in before]
        ordered.append(separator)
    ordered.extend(columns(narrow))
    ordered.extend(sorted(asides, key=lambda u: (u['rect'].y0, u['rect'].x0)))
    return _merge_title_lines([part for unit in ordered for part in unit['parts']])


def _math_block(block):
    words = re.findall(r'[A-Za-z]{4,}', block['text'])
    return not words or (len(words) <= 1 and len(block['text']) < 120)


def _column(rect, page):
    middle = page.rect.width / 2
    if rect.x0 < middle - page.rect.width * .04 and rect.x1 > middle + page.rect.width * .04:
        return 'wide'
    return 'left' if (rect.x0 + rect.x1) / 2 < middle else 'right'


def _bind_columns(models, source_rects, page):
    """Match columns before vertical order; unknown multi-item geometry fails closed."""
    if len(models) != len(source_rects):
        return None
    groups, native_groups = {}, {}
    for model in models:
        box = model.get('block_bbox')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        try:
            rect = _rect(box, page)
        except (TypeError, ValueError):
            return None
        if rect.is_empty or rect.is_infinite:
            return None
        if len(models) == 1:
            model_column, native_column = _column(rect, page), _column(source_rects[0], page)
            # A full-width model box carries no column evidence. With a unique
            # native candidate it is safe; contradictory narrow columns are not.
            if model_column != native_column and model_column != 'wide':
                return None
            return list(models)
        groups.setdefault(_column(rect, page), []).append((rect.y0, model))
    for index, rect in enumerate(source_rects):
        native_groups.setdefault(_column(rect, page), []).append((rect.y0, index))
    if set(groups) != set(native_groups):
        return None
    matched = [None] * len(models)
    for column, candidates in groups.items():
        native_candidates = native_groups[column]
        if len(candidates) != len(native_candidates):
            return None
        for (_, model), (_, index) in zip(sorted(candidates, key=lambda x: x[0]), sorted(native_candidates)):
            matched[index] = model
    return matched


def _visuals_bound(model, figures, page):
    for block in model:
        if block.get('block_label') not in _VISUALS:
            continue
        if any(f['key'] in _text(block) for f in figures):
            continue
        box = block.get('block_bbox')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return False
        try:
            rect = _rect(box, page)
            matches = [f for f in figures if (rect & _rect(f['bbox'], page)).get_area()
                       / max(1, min(rect.get_area(), _rect(f['bbox'], page).get_area())) >= .5]
        except (TypeError, ValueError):
            return False
        if len(matches) != 1:
            return False
    return True


def _equation_units(native, model, page):
    formulas = [b for b in model if b.get('block_label') in _FORMULAS and _text(b).strip()]
    anchors = []
    for block in native:
        for rect, text, _ in block['lines']:
            match = _EQUATION.search(text)
            if match and _math_block({'text': text}):
                anchors.append((int(match[1]), rect))
    anchors.sort(key=lambda a: (a[1].x0 >= page.rect.width / 2, a[1].y0))
    if not formulas or len(formulas) != len(anchors):
        return [], set()
    tags = [re.search(r'\\tag\s*\{(\d+)\}', _text(formula)) for formula in formulas]
    if all(tags) and {int(tag[1]) for tag in tags} == {number for number, _ in anchors}:
        by_number = {int(tag[1]): formula for formula, tag in zip(formulas, tags)}
        formulas = [by_number[number] for number, _ in anchors]
    else:
        formulas = _bind_columns(formulas, [rect for _, rect in anchors], page)
        if formulas is None:
            return [], set()
    units, claimed = [], set()
    for formula, (number, anchor) in zip(formulas, anchors):
        column = anchor.x0 >= page.rect.width / 2
        selected = []
        for i, block in enumerate(native):
            rect = block['rect']
            if ((rect.x0 >= page.rect.width / 2) == column and _math_block(block)
                    and rect.y0 <= anchor.y1 + 22 and rect.y1 >= anchor.y0 - 22):
                selected.append((i, rect))
        if not selected:
            continue
        rect = fitz.Rect(anchor)
        for i, box in selected:
            claimed.add(i)
            rect |= box
        content = _text(formula)
        tag = re.search(r'\\tag\s*\{(\d+)\}', content)
        if tag and int(tag[1]) != number:
            return [], set()
        if not tag:
            closing = content.rstrip().rfind('$$')
            if closing > 0:
                content = content[:closing].rstrip() + f'\n\\tag{{{number}}}\n$$'
            else:
                content = content.rstrip() + f'\n\n({number})'
        part = _block('formula', rect, content, page)
        part['sourceEquationNumber'] = number
        units.append(_unit(rect, part))
    return units, claimed


def _project_page(page, output, figures):
    if page.rotation:
        return None
    native = _native_blocks(page)
    font_weights = Counter()
    for block in native:
        for size, count in block['fonts']:
            font_weights[size] += count
    body_font = font_weights.most_common(1)[0][0] if font_weights else 10
    text = '\n'.join(b['text'] for b in native)
    letters = sum(c.isalpha() for c in text)
    if letters < 80 or text.count('\ufffd') / max(1, len(text)) > .03:
        return None
    model = output.get('parsing_res_list', [])
    if not _visuals_bound(model, figures, page):
        return None
    units, claimed = _equation_units(native, model, page)
    if len(units) != sum(b.get('block_label') in _FORMULAS and bool(_text(b).strip()) for b in model):
        return None
    masks = []
    for figure in figures:
        box = _rect(figure['bbox'], page)
        match = _FIG.match(figure.get('caption', ''))
        if not match:
            return None
        identifier = (match[1] or match[2]).lower()
        candidates = []
        for i, block in enumerate(native):
            caption = _FIG.match(block['text'])
            if (caption and (caption[1] or caption[2]).lower() == identifier
                    and _overlap_x(box, block['rect']) > .3
                    and -6 <= block['rect'].y0 - box.y1 <= 100):
                candidates.append((i, block))
        if not candidates:
            return None
        i, caption = min(candidates, key=lambda b: abs(b[1]['rect'].y0 - box.y1))
        claimed.add(i)
        masks.append(box)
        image = _block('image', box, f'![Figure {identifier}]({figure["key"]})', page)
        image['figureSource'] = 'pdf'
        units.append(_unit(box | caption['rect'], image,
                           _block('figure_caption', caption['rect'], _clean(caption['text']), page)))

    # A grid alone is insufficient evidence: axes and diagram panels also form
    # grids. Only native TABLE labels can authorize a source table projection.
    captions = [(i, b) for i, b in enumerate(native) if _TABLE.match(b['text'])]
    found = page.find_tables().tables if captions else []
    tables = []
    for i, caption in captions:
        options = [t for t in found if t.row_count >= 2 and t.col_count >= 2
                   and -3 <= t.bbox[1] - caption['rect'].y1 <= 100
                   and _overlap_x(fitz.Rect(t.bbox), caption['rect']) > .25
                   and not any((fitz.Rect(t.bbox) & mask).get_area() > 1 for mask in masks)]
        if options:
            table = min(options, key=lambda t: t.bbox[1] - caption['rect'].y1)
            tables.append((i, caption, table))
    tables.sort(key=lambda t: (t[1]['rect'].y0, t[1]['rect'].x0))
    model_tables = [b for b in model if b.get('block_label') == 'table']
    if model_tables:
        model_tables = _bind_columns(model_tables, [fitz.Rect(t[2].bbox) for t in tables], page)
        if model_tables is None:
            return None
    for n, (i, caption, table) in enumerate(tables):
        box = fitz.Rect(table.bbox)
        caption_box = fitz.Rect(caption['rect'])
        caption_text = [caption['text']]
        claimed.add(i)
        # Some PDFs put the standalone TABLE identifier in a separate block.
        for j, block in enumerate(native):
            if (j not in claimed and block['rect'].y0 >= caption['rect'].y1 - 2
                    and block['rect'].y1 <= box.y0 + 2
                    and _overlap_x(block['rect'], box) > .4):
                caption_box |= block['rect']
                caption_text.append(block['text'])
                claimed.add(j)
        content = _text(model_tables[n]) if model_tables else table.to_markdown()
        if not content.strip():
            return None
        part = _block('table', box, content, page)
        part['textSource'] = 'model-table' if model_tables else 'pdf-native-table'
        units.append(_unit(box | caption_box,
                           _block('table_caption', caption_box, _clean('\n'.join(caption_text)), page), part))
        masks.append(box)

    for i, block in enumerate(native):
        if i in claimed:
            continue
        lines = [(rect, value, direction) for rect, value, direction in block['lines']
                 if not any(mask.contains(fitz.Point((rect.x0 + rect.x1) / 2,
                                                      (rect.y0 + rect.y1) / 2)) for mask in masks)]
        if not lines:
            continue
        box = fitz.Rect(lines[0][0])
        for rect, _, _ in lines[1:]:
            box |= rect
        aside = (box.y0 > page.rect.height * .94 or
                 any(abs(direction[1]) > .5 for _, _, direction in lines))
        unit = _unit(box, *_text_parts(lines, block, page, body_font, aside))
        unit['aside'] = aside
        units.append(unit)
    return _reading_order(units, page)


def project_native_pdf_pages(result, pdf_source, figures):
    """Derive Markdown from trustworthy source pages, preserving all raw fields.

    Figures have the same normalized geometry/key schema as
    ``recover_pdf_figures``. Unreliable pages and cross-page table merges retain
    their original output. No OCR, GPU, network or filesystem writes occur.
    """
    if (result.get('manualEditedAt') or result.get('enhancement', {}).get('crossPageTables') or
            result.get('ovisResult', {}).get('enhancement', {}).get('crossPageTables')):
        return result
    pages = result.get('layoutParsingResults') or result.get('ocrResults') or []
    if not pages:
        return result
    doc = (fitz.open(stream=bytes(pdf_source), filetype='pdf')
           if isinstance(pdf_source, (bytes, bytearray, memoryview)) else fitz.open(os.fspath(pdf_source)))
    changed = []
    with doc:
        for position, output in enumerate(pages):
            index = (output['sourcePage'] - 1 if isinstance(output.get('sourcePage'), int)
                     and output['sourcePage'] > 0 else output.get('pageIndex', position))
            if not isinstance(index, int) or not 0 <= index < len(doc):
                continue
            page_figures = [f for f in figures if f.get('pageIndex') == index]
            blocks = _project_page(doc[index], output, page_figures)
            if not blocks:
                continue
            md = output.get('markdown', {})
            old = md.get('text', '') if isinstance(md, dict) else md
            output.setdefault('originalMarkdown', old)
            if not isinstance(md, dict):
                md = output['markdown'] = {'text': old, 'images': {}}
            else:
                output['markdown'] = md
            md['text'] = '\n\n'.join(b['block_content'] for b in blocks)
            for figure in page_figures:
                md.setdefault('images', {})[figure['key']] = figure['imageBase64']
                result.setdefault('images', {})[figure['key']] = figure['imageBase64']
            output['sourceOrderBlocks'] = blocks
            output['sourceOrder'] = {'method': 'pdf-native', 'version': 1}
            changed.append(index)
    if changed:
        result.setdefault('originalMarkdown', result.get('markdown', ''))
        ordered = sorted(enumerate(pages), key=lambda pair: (
            pair[1].get('sourcePage', pair[1].get('pageIndex', pair[0]) + 1)))
        result['markdown'] = '\n\n'.join(
            p['markdown'].get('text', '') if isinstance(p.get('markdown'), dict)
            else p.get('markdown', '') for _, p in ordered)
        result['sourceOrder'] = {'method': 'pdf-native', 'version': 1, 'pageIndices': changed}
    return result
