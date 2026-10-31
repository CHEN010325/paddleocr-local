"""Pinned OvisOCR3 task contracts, shared by the adapter and the workbench."""
import json
import hashlib
import re
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

PROMPT_DIR = Path(__file__).with_name('ovisocr3_prompts')
CATEGORIES = set('abstract algorithm aside_text chart content display_formula doc_title figure_title footer footer_image footnote formula_number header header_image image inline_formula number paragraph_title reference_content seal table text vertical_text vision_footnote'.split())

# Public parameter names deliberately match the API and saved-task snapshot.
_DEFINITIONS = [
    ('document_structured', '文档解析（结构化）', '文档解析', []),
    ('document_markdown', '文档解析（Markdown）', '文档解析', []),
    ('document_layout', '版面检测（不识别正文）', '文档解析', []),
    ('plain_ocr', '纯文本识别', '通用 OCR', []),
    ('word_spotting', '逐词识别与定位', '通用 OCR', []),
    ('reference_grounding', '指定文字定位', '通用 OCR', ['references']),
    ('region_ocr', '区域文字识别', '通用 OCR', ['region']),
    ('formula_recognition', '公式识别', '通用 OCR', []),
    ('chart_parsing', '图表数据解析', '通用 OCR', []),
    ('text_translation', '区域文字翻译', '通用 OCR', ['region', 'targetLanguage']),
    ('image_classification', '图像分类', '通用 OCR', ['targetDescription', 'candidateLabels']),
    ('visual_question_answering', '图像问答', '通用 OCR', ['question']),
    ('position_aware_vqa', '带定位的图像问答', '通用 OCR', ['question']),
    ('kie_open', '开放键值抽取', '信息抽取', []),
    ('kie_fields', '指定字段抽取', '信息抽取', ['fields']),
    ('kie_schema', '按 JSON 示例抽取', '信息抽取', ['schema']),
    ('figure_captioning', '文档解析与图像描述', '图像描述', []),
    ('crosspage_table', '文档解析与跨页表格重建', '跨页表格', []),
]
TASKS = {name: {'id': name, 'label': label, 'group': group, 'parameters': fields}
         for name, label, group, fields in _DEFINITIONS}
DOCUMENT_TASKS = {'document_structured', 'document_markdown', 'figure_captioning', 'crosspage_table', 'document_auto'}
JSON_TASKS = {'document_structured', 'document_layout', 'word_spotting', 'reference_grounding',
              'chart_parsing', 'position_aware_vqa', 'kie_open', 'kie_fields', 'kie_schema',
              'figure_captioning', 'crosspage_table'}


def page_hash(page):
    return hashlib.sha256(f'{page.width}x{page.height}:'.encode() + page.tobytes()).hexdigest()


class PairRequest(BaseModel):
    images: list[str] = Field(min_length=2, max_length=2)
    markdowns: list[str] = Field(min_length=2, max_length=2)


def valid_bbox(box):
    return (isinstance(box, list) and len(box) == 4
            and all(type(n) is int and 0 <= n < 1000 for n in box)
            and box[0] < box[2] and box[1] < box[3])


def validate_options(task, options=None):
    if task == 'document_auto':
        if options is not None and (not isinstance(options, dict) or options):
            raise HTTPException(422, 'Automatic document parsing does not require task options')
        return {}
    if task not in TASKS:
        raise HTTPException(422, 'Unknown OvisOCR3 task')
    if options is None:
        options = {}
    if not isinstance(options, dict) or set(options) - set(TASKS[task]['parameters']):
        raise HTTPException(422, 'Invalid taskOptions for ' + task)
    if len(json.dumps(options, ensure_ascii=False)) > 32000:
        raise HTTPException(422, 'taskOptions exceeds 32000 characters')
    options = dict(options)
    for name in TASKS[task]['parameters']:
        value = options.get(name)
        if name == 'region':
            value = options.setdefault(name, [0, 0, 999, 999])
            if not valid_bbox(value):
                raise HTTPException(422, 'region must be [left,top,right,bottom] integers in [0,999]')
        elif name in {'references', 'fields', 'candidateLabels'}:
            if (not isinstance(value, list) or not 1 <= len(value) <= 200
                    or any(not isinstance(x, str) or not x.strip() or len(x) > 1000 for x in value)
                    or len(set(value)) != len(value)):
                raise HTTPException(422, name + ' must be a nonempty array of unique strings')
        elif name == 'schema':
            if not isinstance(value, (dict, list)) or not value:
                raise HTTPException(422, 'schema must be a nonempty JSON output example')
        elif not isinstance(value, str) or not value.strip() or len(value) > 8000:
            raise HTTPException(422, name + ' is required (nonempty string, max 8000 characters)')
    return options


def task_prompt(task, options=None):
    options = validate_options(task, options)
    prompt = (PROMPT_DIR / (('figure_captioning' if task == 'document_auto' else task) + '.txt')).read_text(encoding='utf-8')
    placeholders = {'references': 'references', 'region': 'target_region', 'targetLanguage': 'target_language',
                    'targetDescription': 'target description', 'candidateLabels': 'candidate_labels',
                    'question': 'question', 'fields': 'keys_json', 'schema': 'schema_json'}
    # A single substitution pass prevents injected text from becoming another placeholder.
    replacements = {'{' + placeholders[k] + '}': (v if isinstance(v, str) else
                    json.dumps(v, ensure_ascii=False, separators=(',', ':'))) for k, v in options.items()}
    if replacements:
        prompt = re.sub('|'.join(re.escape(k) for k in replacements), lambda m: replacements[m[0]], prompt)
    return prompt if task == 'position_aware_vqa' else prompt.strip()


def _shape_matches(value, template):
    if isinstance(template, dict):
        return isinstance(value, dict) and set(value) == set(template) and all(_shape_matches(value[k], v) for k, v in template.items())
    if isinstance(template, list):
        return isinstance(value, list) and (not template or all(_shape_matches(x, template[0]) for x in value))
    return isinstance(value, type(template)) and not (isinstance(value, bool) and not isinstance(template, bool))


def decode_result(task, text, options=None):
    options = validate_options(task, options)
    try:
        value = json.loads(text) if task in JSON_TASKS else text
        if task in {'document_structured', 'document_layout', 'figure_captioning', 'word_spotting', 'reference_grounding'}:
            if not isinstance(value, list):
                raise ValueError('Expected a JSON array')
            for item in value:
                if not isinstance(item, dict) or not valid_bbox(item.get('bbox')):
                    raise ValueError('Invalid normalized bounding box')
                if task in {'word_spotting', 'reference_grounding'}:
                    key = 'text' if task == 'word_spotting' else 'reference'
                    if not isinstance(item.get(key), str):
                        raise ValueError('Missing text/reference')
                    if key == 'reference' and item[key] not in options['references']:
                        raise ValueError('Unrequested reference')
                elif item.get('category') not in CATEGORIES or (task != 'document_layout' and not isinstance(item.get('content'), str)):
                    raise ValueError('Invalid document category/content')
            if task == 'reference_grounding' and set(options['references']) - {x['reference'] for x in value}:
                raise ValueError('Missing requested references')
        elif task in {'kie_open', 'kie_fields', 'kie_schema'}:
            if task == 'kie_schema':
                if not _shape_matches(value, options['schema']):
                    raise ValueError('Output does not follow the requested JSON example')
            else:
                if not isinstance(value, dict) or any(not isinstance(v, (str, list)) or (isinstance(v, list) and any(not isinstance(x, str) for x in v)) for v in value.values()):
                    raise ValueError('Expected key/value object with string or repeated string values')
                if task == 'kie_fields' and set(value) != set(options['fields']):
                    raise ValueError('Output fields do not match requested fields')
        elif task == 'chart_parsing':
            if (not isinstance(value, dict) or set(value) != {'title','source','x_title','y_title','values'}
                    or any(not isinstance(value.get(k), str) for k in ('title','source','x_title'))
                    or not (isinstance(value['y_title'], str) or isinstance(value['y_title'], list) and len(value['y_title']) == 2 and all(isinstance(x, str) for x in value['y_title']))
                    or not isinstance(value['values'], dict) or not value['values']):
                raise ValueError('Invalid chart JSON')
            def strings_only(node):
                return all(strings_only(v) for v in node.values()) if isinstance(node, dict) else all(strings_only(v) for v in node) if isinstance(node, list) else isinstance(node, str)
            if not strings_only(value['values']):
                raise ValueError('Chart values must preserve scalar data as strings')
        elif task == 'position_aware_vqa':
            if not isinstance(value, dict) or not isinstance(value.get('answer'), str) or not isinstance(value.get('bbox'), str):
                raise ValueError('Invalid answer/bbox JSON')
            match = re.fullmatch(r'\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', value['bbox'])
            if not match or not valid_bbox([int(n) for n in match.groups()]):
                raise ValueError('Invalid answer bounding box')
        elif task == 'image_classification' and value.strip() not in options['candidateLabels']:
            raise ValueError('Output is not a candidate label')
        elif task == 'crosspage_table':
            from ovisocr3_reference import parse_tables
            parse_tables(text)
        return value
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise HTTPException(502, 'Invalid OvisOCR3 ' + task + ' result: ' + str(exc)) from exc


def caption_markdown(content):
    """Keep descriptions and Mermaid source visible without executing generated markup."""
    content = re.sub(r'<caption>(.*?)</caption>', lambda m: '\n\n' + m[1].strip() + '\n\n', content, flags=re.S)
    return re.sub(r'<mermaid>(.*?)</mermaid>', lambda m: '\n\n```mermaid\n' + m[1].strip() + '\n```\n', content, flags=re.S)


def document_content(label, content):
    """Project category-specific content consistently in single-page and stage-two views."""
    if label == 'display_formula':
        return '$$\n' + content.strip('$ \n') + '\n$$'
    if label == 'inline_formula':
        return '$' + content.strip('$ \n') + '$'
    if label in {'doc_title', 'paragraph_title'} and not content.lstrip().startswith('#'):
        return ('# ' if label == 'doc_title' else '## ') + content
    return content
