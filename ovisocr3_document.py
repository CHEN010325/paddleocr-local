"""Document-first orchestration. Task prompts are internal tools, not user modes."""
import json
import re
import logging

from fastapi import HTTPException

from ovisocr3_tasks import task_prompt, decode_result

logger = logging.getLogger(__name__)

VISUAL_LABELS = {'image', 'chart', 'header_image', 'footer_image'}


def parse_document_page(image, infer, *, refine=None):
    """One page: read layout/content/figures, then refine only relevant regions."""
    refine = refine or infer
    warnings, traces = [], []
    logger.info("Document page primary figure_captioning started")
    raw = infer([image], task_prompt('figure_captioning'))
    logger.info('Document page primary completed: %d characters', len(raw))
    try:
        blocks = decode_result('figure_captioning', raw)
    except HTTPException:
        traces.append({'task':'figure_captioning','rawOutput':raw})
        raw = infer([image], task_prompt('document_structured'))
        blocks = decode_result('document_structured', raw)
        warnings.append({'message':'图像描述输出无效，已使用结构化文档解析保留正文和版面。'})
    extras = {'charts':[], 'keyValues':{}, 'visualText':[], 'warnings':warnings, 'rawOutputs':traces}
    source_blocks = [
        {'block_bbox':b['bbox'], 'block_label':b['category'], 'block_content':b['content']}
        for b in blocks if b['category'] not in VISUAL_LABELS
    ]

    def tool(task, pages):
        try:
            logger.info('Document refinement %s started', task)
            output = refine(pages, task_prompt(task))
            logger.info('Document refinement %s completed: %d characters', task, len(output))
            traces.append({'task':task,'rawOutput':output})
            return decode_result(task, output)
        except (HTTPException, RuntimeError, ValueError) as exc:
            warnings.append({'task':task,'message':str(exc.detail) if isinstance(exc, HTTPException) else str(exc)})
            return None

    for index, block in enumerate(blocks):
        if block['category'] not in VISUAL_LABELS:
            continue
        left, top, right, bottom = block['bbox']
        crop = image.crop((round(left*image.width/1000),round(top*image.height/1000),
                           round(right*image.width/1000),round(bottom*image.height/1000)))
        try:
            if crop.width < 2 or crop.height < 2:
                continue
            caption = block['content']
            numerical_chart = (block['category']=='chart' or re.search(
                r'\b(?:bar|line|pie|scatter|area|radar)\s+(?:chart|plot|graph)\b|柱状图|折线图|饼图|散点图', caption, re.I))
            # Dense chart geometry can make word-box generation repeat. A chart
            # needs literal labels, while its numeric structure is read separately.
            if numerical_chart and '<mermaid>' not in caption:
                text = tool('plain_ocr', [crop])
                words = [{'bbox':[0,0,999,999], 'text':text}] if text else []
            else:
                words = tool('word_spotting', [crop])
            if words is not None:
                for word in words:
                    x1,y1,x2,y2 = word['bbox']
                    box=[left+round(x1*(right-left)/1000),top+round(y1*(bottom-top)/1000),
                         left+round(x2*(right-left)/1000),top+round(y2*(bottom-top)/1000)]
                    if box[0] < box[2] and box[1] < box[3]:
                        source_blocks.append({'block_bbox':box,'block_label':'text','block_content':word['text']})
                extras['visualText'].append({'blockIndex':index,'words':words})
            if numerical_chart and '<mermaid>' not in caption:
                data = tool('chart_parsing', [crop])
                if data is not None:
                    extras['charts'].append({'blockIndex':index,'bbox':block['bbox'],'data':data})
        finally:
            crop.close()
    source = '\n'.join(b['block_content'] for b in source_blocks)
    fields = re.findall(r'(?m)^\s*[^\n:：]{1,40}[:：]\s*[^\n]{1,120}$', source)
    if len(fields) >= 2 or re.search(r'\b(?:invoice|receipt|application form)\b|发票|收据|申请表', source, re.I):
        extracted = tool('kie_open', [image])
        if extracted is not None:
            extras['keyValues'] = extracted
    return raw, extras, source_blocks


def document_supplement(extras):
    """Readable refinements; kept separate from literal source-PDF text."""
    if not isinstance(extras, dict):
        return ''
    sections=[]
    labels=[word.get('text','') for region in extras.get('visualText',[]) if isinstance(region,dict)
            for word in region.get('words',[]) if isinstance(word,dict) and isinstance(word.get('text'),str)]
    if labels:
        sections.append('### 图内文字\n\n'+' '.join(labels))
    for chart in extras.get('charts',[]):
        if isinstance(chart,dict) and chart.get('data'):
            sections.append('### 图表数据\n\n````json\n'+json.dumps(chart['data'],ensure_ascii=False,indent=2)+'\n````')
    if extras.get('keyValues'):
        sections.append('### 结构化字段\n\n````json\n'+json.dumps(extras['keyValues'],ensure_ascii=False,indent=2)+'\n````')
    return '\n\n'.join(sections)


def append_document_supplements(markdown, records):
    supplements=[]
    for index,record in enumerate(records):
        text=document_supplement(record.get('documentExtras',{}))
        if text:
            supplements.append(('## 第 '+str(index+1)+' 页补充信息\n\n' if len(records)>1 else '')+text)
    return markdown+('\n\n'+'\n\n'.join(supplements) if supplements else '')
