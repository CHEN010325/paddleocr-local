"""Recover captioned PDF figures from source geometry, without model inference.

Only figures with an explicit source-PDF figure caption and nearby graphic
geometry qualify. Crops include vector artwork and embedded images together.
Generated descriptions never enter ``sourceTextBlocks``.
"""
import base64
import os
import re

import fitz


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


def _source_graphics(pdf_source, page_indices):
    """Native geometry used only to reject crops with no graphic content."""
    doc = (fitz.open(stream=bytes(pdf_source), filetype='pdf')
           if isinstance(pdf_source, (bytes, bytearray, memoryview)) else fitz.open(os.fspath(pdf_source)))
    output = {}
    with doc:
        for index in page_indices:
            if not 0 <= index < len(doc):
                continue
            page = doc[index]
            if page.rotation:
                continue
            rects = list(page.cluster_drawings())
            rects.extend(fitz.Rect(info['bbox']) for info in page.get_image_info())
            output[index] = [[r.x0 / page.rect.width * 1000, r.y0 / page.rect.height * 1000,
                              r.x1 / page.rect.width * 1000, r.y1 / page.rect.height * 1000]
                             for r in rects if r.width >= 8 and r.height >= 8]
    return output


def _has_geometry(box, graphics):
    try:
        return any((fitz.Rect(box) & fitz.Rect(native)).get_area() > 1 for native in graphics)
    except (TypeError, ValueError):
        return True  # Unknown geometry is not evidence for deleting an image.


def _archive_images(result, keys, page_images=None):
    for key in keys:
        for source in (page_images or {}, result.get('images', {})):
            if key in source:
                result.setdefault('originalImages', {}).setdefault(key, source.pop(key))


def _insert_by_caption(text, figure, link):
    # Tolerate formatting differences in the source caption's identifier.
    figure_id = _figure_id(figure['caption'])
    for match in _FIGURE_ID.finditer(text):
        if (match.group(1) or match.group(2)).lower() == figure_id:
            line_start = text.rfind('\n', 0, match.start()) + 1
            return text[:line_start] + link + '\n\n' + text[line_start:]
    return text.rstrip() + ('\n\n' if text.strip() else '') + link + '\n\n' + figure['caption']


def enrich_ovis_pdf_result(result, pdf_source):
    """Mutate and return an OCR response or persisted task, preserving body text.

    Supports ``layoutParsingResults`` and persisted ``ocrResults``. Matching
    model crops are replaced in page and aggregate Markdown/images; unmatched
    figures are inserted by the source caption. Source text blocks are untouched.
    """
    pages = result.get('layoutParsingResults') or result.get('ocrResults') or []
    page_map = {(p['sourcePage'] - 1 if isinstance(p.get('sourcePage'), int) and p['sourcePage'] > 0
                 else p.get('pageIndex', i)): p for i, p in enumerate(pages)}
    if not page_map:
        return result
    figures = recover_pdf_figures(pdf_source, page_indices=page_map)
    native_graphics = _source_graphics(pdf_source, {f['pageIndex'] for f in figures}) if figures else {}
    # Reject text-only model crops on pages for which native figure recovery is
    # available. Keep all text following a rejected Markdown image link.
    for index, page in page_map.items():
        if index not in native_graphics:
            continue
        dead_keys = set()
        blocks = []
        for block in page.get('parsing_res_list', []):
            if (block.get('block_label') in _VISUALS and block.get('block_bbox') and
                    not _has_geometry(block['block_bbox'], native_graphics[index])):
                dead_keys.update(_image_key(m) for m in _IMAGE_LINK.finditer(block.get('block_content', '')))
            else:
                blocks.append(block)
        page['parsing_res_list'] = blocks
        if page.get('batchId'):
            dead_keys.update(f'ocr_images/{page["batchId"]}_{k.rsplit("/", 1)[-1]}' for k in list(dead_keys))
        # Full-document enhancement emits coordinate-bearing names independently
        # of the page response. Their source page and box let us validate them.
        for key in result.get('images', {}):
            match = re.search(r'enhanced_page_(\d+)_bbox_(\d+)_(\d+)_(\d+)_(\d+)', key)
            if match and int(match[1]) == index + 1:
                box = [int(match[i]) for i in range(2, 6)]
                if not _has_geometry(box, native_graphics[index]):
                    dead_keys.add(key)
        md = page.get('markdown', {})
        if isinstance(md, dict):
            md['text'] = _IMAGE_LINK.sub(lambda m: '' if _image_key(m) in dead_keys else m[0], md.get('text', ''))
        result['markdown'] = _IMAGE_LINK.sub(lambda m: '' if _image_key(m) in dead_keys else m[0], result.get('markdown', ''))
        _archive_images(result, dead_keys, md.get('images', {}) if isinstance(md, dict) else None)
    for figure in figures:
        page = page_map[figure['pageIndex']]
        markdown = page.setdefault('markdown', {'text': '', 'images': {}})
        if isinstance(markdown, str):
            markdown = page['markdown'] = {'text': markdown, 'images': {}}
        images = markdown.setdefault('images', {})
        all_images = result.setdefault('images', {})
        key = figure['key']
        link = f'![Figure {_figure_id(figure["caption"])}]({key})'
        blocks = page.setdefault('parsing_res_list', [])
        previous_text = markdown.get('text', '')
        text = previous_text
        removed_keys = set()
        kept_blocks = []
        for block in blocks:
            matched = block.get('block_label') in _VISUALS and (
                _overlaps(block.get('block_bbox', []), figure['bbox']) or
                (_figure_id(block.get('block_content', '')) == _figure_id(figure['caption'])))
            if matched:
                removed_keys.update(_image_key(m) for m in _IMAGE_LINK.finditer(block.get('block_content', '')))
            else:
                kept_blocks.append(block)
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
        kept_blocks.append({'block_label': 'image', 'block_bbox': figure['bbox'], 'block_content': link,
                            'figureSource': 'pdf', 'caption': figure['caption']})
        page['parsing_res_list'] = sorted(kept_blocks, key=lambda block: (block.get('block_bbox') or [0, 0])[1])
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
    return result
