"""Recover captioned PDF figures from source geometry, without model inference.

Only figures with an explicit source-PDF figure caption and nearby graphic
geometry qualify. Crops include vector artwork and embedded images together.
Generated descriptions never enter ``sourceTextBlocks``.
"""
import base64
import copy
import json
import math
import os
import re

import fitz
from ovisocr3_tasks import CATEGORIES, VISUAL_CATEGORIES, valid_bbox, document_content, caption_markdown


_CAPTION = re.compile(r'^\s*(?:Fig(?:ure)?\.?\s*\d+[A-Za-z]?|图\s*\d+)\s*(?:[.:：、]|\s|$)', re.I)
_FIGURE_ID = re.compile(r'(?:Fig(?:ure)?\.?\s*(\d+[A-Za-z]?)|图\s*(\d+))', re.I)
_IMAGE_LINK = re.compile(r'''!\[[^\]]*\]\(([^)]+)\)|<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["'][^>]*>''', re.I)
_VISUALS = {'image', 'chart', 'figure', 'header_image', 'footer_image'}


def _image_key(match):
    return match.group(1) or match.group(2)


def _figure_id(text):
    match = _FIGURE_ID.search(text)
    return (match.group(1) or match.group(2)).lower() if match else None


def _horizontal_overlap(a, b):
    return max(0, min(a.x1, b.x1) - max(a.x0, b.x0)) / max(1, min(a.width, b.width))


def recover_pdf_figures(pdf_source, *, page_indices=None):
    """Return source figures: pageIndex, normalized bbox, caption, key, imageBase64.

    ``pdf_source`` accepts PDF bytes or a filesystem path. ``page_indices`` may
    restrict work to zero-based pages in a partial OCR response. No files are
    written. Invalid/encrypted PDFs raise PyMuPDF's normal exceptions.
    """
    if isinstance(pdf_source, (bytes, bytearray, memoryview)):
        doc = fitz.open(stream=bytes(pdf_source), filetype='pdf')
    else:
        doc = fitz.open(os.fspath(pdf_source))
    recovered = []
    with doc:
        indices = range(len(doc)) if page_indices is None else sorted(set(page_indices))
        for page_index in indices:
            if not 0 <= page_index < len(doc):
                continue
            page = doc[page_index]
            if page.rotation:
                # Source drawing coordinates and raster clip coordinates differ
                # on rotated pages. Keep existing model crops rather than replace
                # them with a potentially empty or unrelated native crop.
                continue
            text_blocks = [b for b in page.get_text('blocks') if len(b) > 6 and b[6] == 0]
            captions = [(fitz.Rect(b[:4]), ' '.join(b[4].split())) for b in text_blocks if _CAPTION.match(b[4])]
            if not captions:
                continue
            graphics = list(page.cluster_drawings())
            graphics.extend(fitz.Rect(info['bbox']) for info in page.get_image_info())
            graphics = [r for r in graphics if r.width >= 8 and r.height >= 8]
            claimed = set()
            for caption_rect, caption in sorted(captions, key=lambda item: item[0].y0):
                candidates = []
                # Caption text width is not the column width: a one-line short
                # caption can label an entire right-column diagram. Its left
                # anchor identifies that column, whose right edge is the page.
                right_limit = (page.rect.x1 if caption_rect.x0 >= page.rect.width * .4
                               else caption_rect.x1 + 96)
                for i, rect in enumerate(graphics):
                    gap = caption_rect.y0 - rect.y1
                    if (i not in claimed and -3 <= gap <= 72 and
                            _horizontal_overlap(rect, caption_rect) >= .25 and
                            rect.x0 >= caption_rect.x0 - 96 and rect.x1 <= right_limit):
                        candidates.append((i, rect))
                if not candidates:
                    continue
                # The nearest large component anchors the figure; isolated tiny
                # icons cannot create a figure on their own.
                anchors = [(i, r) for i, r in candidates if r.width >= 45 and r.height >= 30]
                if not anchors:
                    continue
                anchor_i, anchor = min(anchors, key=lambda item: caption_rect.y0 - item[1].y1)
                selected = [(anchor_i, anchor)]
                # Adjacent panels at the same height belong to one caption.
                for i, rect in candidates:
                    if i != anchor_i and min(rect.y1, anchor.y1) > max(rect.y0, anchor.y0):
                        selected.append((i, rect))
                whole = fitz.Rect(selected[0][1])
                for _, rect in selected[1:]:
                    whole |= rect
                # Reject body/table text between the drawing and the caption.
                intervening = [b for b in text_blocks if b[1] >= whole.y1 + 2 and b[3] < caption_rect.y0 - 1
                               and _horizontal_overlap(fitz.Rect(b[:4]), whole) > .5]
                if intervening:
                    continue
                claimed.update(i for i, _ in selected)
                clip = fitz.Rect(whole.x0 - 2, whole.y0 - 2, whole.x1 + 2, whole.y1 + 2) & page.rect
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, colorspace=fitz.csRGB, alpha=False)
                bbox = [round(clip.x0 / page.rect.width * 1000), round(clip.y0 / page.rect.height * 1000),
                        round(clip.x1 / page.rect.width * 1000), round(clip.y1 / page.rect.height * 1000)]
                key = f'ocr_images/ovisocr3_pdf_p{page_index + 1}_figure_{_figure_id(caption)}.png'
                recovered.append({'pageIndex': page_index, 'bbox': bbox, 'caption': caption,
                                  'key': key, 'imageBase64': base64.b64encode(pixmap.tobytes('png')).decode('ascii')})
    return recovered


def _overlaps(a, b):
    try:
        ra, rb = fitz.Rect(a), fitz.Rect(b)
        return (ra & rb).get_area() / max(1, min(ra.get_area(), rb.get_area())) >= .2
    except (TypeError, ValueError):
        return False


def _archive_images(result, keys, page_images=None):
    for key in keys:
        for source in (page_images or {}, result.get('images', {})):
            if key in source:
                result.setdefault('originalImages', {}).setdefault(key, source.pop(key))


def _insert_by_caption(text, figure, link):
    # A prose reference to a figure does not identify the figure's placement.
    figure_id = _figure_id(figure['caption'])
    for match in re.finditer(r'(?m)^.*$', text):
        if _CAPTION.match(match[0]) and _figure_id(match[0]) == figure_id:
            return text[:match.start()] + link + '\n\n' + text[match.start():]
    return text.rstrip() + ('\n\n' if text.strip() else '') + link + '\n\n' + figure['caption']


def _restore_model_layout(result, page_map, pdf_source, merged_tables):
    """Recover legacy derived labels from intact validated model evidence."""
    pending = []
    for index, page in page_map.items():
        try:
            value = json.loads(page.get('rawOutput', ''))
            source = 'raw-output'
        except (ValueError, TypeError):
            value, source = page.get('taskResult'), 'task-result'
        if not isinstance(value, list) or not all(
                isinstance(b, dict) and isinstance(b.get('category'), str) and b['category'] in CATEGORIES
                and valid_bbox(b.get('bbox')) and isinstance(b.get('content'), str) for b in value):
            continue
        if [b['category'] for b in value] == [b.get('block_label') for b in page.get('parsing_res_list', [])]:
            continue
        pending.append((index, page, value, source))
    if not pending:
        return False
    doc = (fitz.open(stream=bytes(pdf_source), filetype='pdf')
           if isinstance(pdf_source, (bytes, bytearray, memoryview)) else fitz.open(os.fspath(pdf_source)))
    restored = False
    with doc:
        for index, page, value, source in pending:
            if not isinstance(index, int) or not 0 <= index < len(doc):
                continue
            native = doc[index]
            visual_boxes = [b['bbox'] for b in value if b['category'] in VISUAL_CATEGORIES]
            area = sum((b[2]-b[0])*(b[3]-b[1])/1e6*native.rect.get_area() for b in visual_boxes)
            scale = min(2., math.sqrt(4_000_000 / max(1., area)))
            blocks, images = [], {}
            for block in value:
                label, box, content = block['category'], block['bbox'], block['content']
                if label in VISUAL_CATEGORIES:
                    key = f'ocr_images/ovisocr3_restored_p{index+1}_bbox_' + '_'.join(map(str, box)) + '.png'
                    clip = fitz.Rect(*(box[n] * (native.rect.width if n % 2 == 0 else native.rect.height) / 1000 for n in range(4)))
                    pix = native.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, colorspace=fitz.csRGB, alpha=False)
                    images[key] = base64.b64encode(pix.tobytes('png')).decode('ascii')
                    link = f'![{label}]({key})'
                    content = caption_markdown(content)
                    content = _IMAGE_LINK.sub(lambda _: link, content) if _IMAGE_LINK.search(content) else link + '\n\n' + content
                blocks.append({'block_label': label, 'block_bbox': list(box), 'block_content': document_content(label, content)})
            page['modelLayoutBlocks'] = blocks
            page['sourceFigureBlocks'] = copy.deepcopy(blocks)
            page['modelLayoutRestoration'] = {'source': source, 'reason': 'legacy-category-mismatch'}
            markdown = page.get('markdown')
            if not isinstance(markdown, dict):
                markdown = page['markdown'] = {'text': markdown or '', 'images': {}}
            page.setdefault('legacyMarkdown', markdown.get('text', ''))
            markdown['text'] = '\n\n'.join(b['block_content'] for b in blocks)
            markdown.setdefault('images', {}).update(images)
            result.setdefault('images', {}).update(images)
            page.pop('sourceOrder', None)
            page.pop('sourceOrderBlocks', None)
            restored = True
    return restored and not merged_tables


def _resolve_page_image_aliases(result, page):
    """Page caches use raw names while the task stores batch-qualified assets."""
    markdown = page.get('markdown')
    if not isinstance(markdown, dict):
        return
    available = dict(result.get('ovisResult', {}).get('originalImages', {}))
    available.update(result.get('originalImages', {}))
    available.update(result.get('images', {}))
    aliases = markdown.get('images', {})

    def resolve(match):
        key = _image_key(match)
        mapped = aliases.get(key)
        target = mapped if isinstance(mapped, str) and mapped in available else key
        if target in available:
            result.setdefault('images', {})[target] = available[target]
        return match[0].replace(key, target) if target != key else match[0]

    markdown['text'] = _IMAGE_LINK.sub(resolve, markdown.get('text', ''))


def enrich_ovis_pdf_result(result, pdf_source):
    """Mutate and return an OCR response or persisted task, preserving body text.

    Supports ``layoutParsingResults`` and persisted ``ocrResults``. Matching
    model crops are replaced in page and aggregate Markdown/images; unmatched
    figures are inserted by the source caption. Source text blocks are untouched.
    """
    if result.get('manualEditedAt'):
        return result
    pages = result.get('layoutParsingResults') or result.get('ocrResults') or []
    page_map = {(p['sourcePage'] - 1 if isinstance(p.get('sourcePage'), int) and p['sourcePage'] > 0
                 else p.get('pageIndex', i)): p for i, p in enumerate(pages)}
    if not page_map:
        return result
    merged_tables = (result.get('enhancement', {}).get('crossPageTables') or
                     result.get('ovisResult', {}).get('enhancement', {}).get('crossPageTables'))
    restored = _restore_model_layout(result, page_map, pdf_source, merged_tables)
    for page in page_map.values():
        if not merged_tables and page.get('sourceOrder', {}).get('method') == 'pdf-native' and 'originalMarkdown' in page:
            md = page.get('markdown')
            if isinstance(md, dict):
                md['text'] = page['originalMarkdown']
            else:
                page['markdown'] = {'text': page['originalMarkdown'], 'images': {}}
            page.pop('sourceOrder', None)
            page.pop('sourceOrderBlocks', None)
            restored = True
        _resolve_page_image_aliases(result, page)
    if restored:
        result['markdown'] = '\n\n'.join(
            page['markdown'].get('text', '') if isinstance(page.get('markdown'), dict)
            else page.get('markdown', '') for _, page in sorted(page_map.items()))
        result.pop('sourceOrder', None)
    figures = recover_pdf_figures(pdf_source, page_indices=page_map)
    # Raw model layout is immutable. Source crops are a separate derived view;
    # missing native graphic evidence does not prove that a model region is empty.
    for page in page_map.values():
        if figures:
            page['sourceFigureBlocks'] = copy.deepcopy(page.get('modelLayoutBlocks', page.get('parsing_res_list', [])))
    for figure in figures:
        page = page_map[figure['pageIndex']]
        markdown = page.setdefault('markdown', {'text': '', 'images': {}})
        if isinstance(markdown, str):
            markdown = page['markdown'] = {'text': markdown, 'images': {}}
        images = markdown.setdefault('images', {})
        all_images = result.setdefault('images', {})
        key = figure['key']
        link = f'![Figure {_figure_id(figure["caption"])}]({key})'
        blocks = page['sourceFigureBlocks']
        previous_text = markdown.get('text', '')
        text = previous_text
        removed_keys = set()
        matches = [i for i, block in enumerate(blocks)
                   if block.get('block_label') in _VISUALS and (
                       _overlaps(block.get('block_bbox', []), figure['bbox']) or
                       _figure_id(block.get('block_content', '')) == _figure_id(figure['caption']))]
        if len(matches) > 1:
            page.setdefault('sourceFigureRecovery', {})[key] = 'ambiguous_model_regions'
            continue
        for i in matches:
            removed_keys.update(_image_key(m) for m in _IMAGE_LINK.finditer(blocks[i].get('block_content', '')))
        # A source key is deterministic for a given selected page set.
        removed_keys.add(key)
        if page.get('batchId'):
            removed_keys.update(f'ocr_images/{page["batchId"]}_{old_key.rsplit("/", 1)[-1]}'
                                for old_key in list(removed_keys) if old_key != key)
        for old_key in all_images:
            match = re.search(r'enhanced_page_(\d+)_bbox_(\d+)_(\d+)_(\d+)_(\d+)', old_key)
            if (match and int(match[1]) == figure['pageIndex'] + 1 and
                    _overlaps([int(match[i]) for i in range(2, 6)], figure['bbox'])):
                removed_keys.add(old_key)
        inserted = False
        def replace_image(match):
            nonlocal inserted
            if _image_key(match) not in removed_keys:
                return match.group(0)
            if inserted:
                return ''
            inserted = True
            return link
        text = _IMAGE_LINK.sub(replace_image, text)
        if not inserted:
            text = _insert_by_caption(text, figure, link)
        _archive_images(result, removed_keys - {key}, images)
        images[key] = all_images[key] = figure['imageBase64']
        if matches:
            i = matches[0]
            original = blocks[i]
            content = _IMAGE_LINK.sub(link, original.get('block_content', ''))
            if not _IMAGE_LINK.search(content):
                content = link + ('\n\n' + content if content else '')
            blocks[i] = dict(original, block_bbox=figure['bbox'], block_content=content,
                             figureSource='pdf', caption=figure['caption'], sourceModelIndex=i)
        else:
            blocks.append({'block_label': 'image', 'block_bbox': figure['bbox'], 'block_content': link,
                           'figureSource': 'pdf', 'caption': figure['caption']})
        markdown['text'] = text
        aggregate = result.get('markdown', '')
        if previous_text and previous_text in aggregate:
            result['markdown'] = aggregate.replace(previous_text, text, 1)
        else:
            # Cross-page enhancement may rewrite prose; retain it and update its
            # figure links directly, inserting at the source caption if absent.
            inserted = False
            aggregate = _IMAGE_LINK.sub(replace_image, aggregate)
            result['markdown'] = aggregate if inserted else _insert_by_caption(aggregate, figure, link)
    from ovisocr3_pdf_order import project_native_pdf_pages
    return project_native_pdf_pages(result, pdf_source, figures)
