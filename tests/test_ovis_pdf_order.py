import copy
import unittest

import fitz

from ovisocr3_pdf_order import project_native_pdf_pages, _text_parts, _visuals_bound
from ovisocr3_tasks import CATEGORIES


def document(lines, rotation=0, table=False):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    for entry in lines:
        x, y, text = entry[:3]
        page.insert_text((x, y), text, fontsize=entry[3] if len(entry) > 3 else 10)
    if table:
        for y in (110, 130, 150):
            page.draw_line((50, y), (550, y))
        for x in (50, 300, 550):
            page.draw_line((x, 110), (x, 150))
    page.set_rotation(rotation)
    data = doc.tobytes()
    doc.close()
    return data


def result(blocks=None):
    return {'markdown': 'OCR duplicated prose', 'ocrResults': [{
        'pageIndex': 0, 'markdown': {'text': 'OCR duplicated prose', 'images': {}},
        'parsing_res_list': blocks or [], 'sourceTextBlocks': [{'text': 'raw retained'}]}]}


def figure(box, caption='Fig. 1. Source diagram.'):
    return {'pageIndex': 0, 'bbox': box, 'caption': caption,
            'key': 'figure.png', 'imageBase64': 'abc'}


class SourceOrderTests(unittest.TestCase):
    def test_two_model_visuals_cannot_share_one_native_figure_identity(self):
        with fitz.open(stream=document([]), filetype='pdf') as doc:
            blocks = [{'block_label': 'image', 'block_bbox': [80, 180, 900, 375],
                       'block_content': '![First](figure.png)'},
                      {'block_label': 'chart', 'block_bbox': [80, 180, 900, 375],
                       'block_content': '![Second](figure.png)'}]
            self.assertFalse(_visuals_bound(blocks, [figure([80, 180, 900, 375])], doc[0]))

    def test_all_24_unbound_categories_preserve_model_markdown(self):
        pdf = document([(50, 60, 'Native prose has enough source text for a source reading order projection.'),
                        (50, 100, 'A second source paragraph supplies more reliable native evidence.')])
        for label in sorted(CATEGORIES):
            with self.subTest(label=label):
                original = '**Recognized semantic content**\n\nsecond recognized paragraph'
                data = result([{'block_label': label, 'block_content': original}])
                data['ocrResults'][0]['markdown']['text'] = original
                data['markdown'] = original
                before = copy.deepcopy(data['ocrResults'][0]['parsing_res_list'])
                project_native_pdf_pages(data, pdf, [])
                self.assertEqual(data['markdown'], original)
                self.assertEqual(data['ocrResults'][0]['parsing_res_list'], before)
                self.assertEqual(data['ocrResults'][0]['sourceOrderDiagnostic']['method'], 'model-preserved')

    def test_text_categories_keep_their_semantic_label_with_wrong_model_box(self):
        pdf = document([(50, 60, 'Recognized semantic content.'),
                        (50, 160, 'Native body prose has sufficient reliable source text for a source '
                         'reading order projection and preserves the rest of this paragraph.')])
        labels = ('abstract', 'algorithm', 'aside_text', 'content', 'doc_title', 'figure_title',
                  'footer', 'footnote', 'formula_number', 'header', 'inline_formula', 'number',
                  'paragraph_title', 'reference_content', 'seal', 'text', 'vertical_text', 'vision_footnote')
        for label in labels:
            with self.subTest(label=label):
                data = result([{'block_label': label, 'block_content': 'Recognized semantic content.',
                                'block_bbox': [900, 900, 999, 999], 'customMetadata': {'retained': True}}])
                project_native_pdf_pages(data, pdf, [])
                block = next(b for b in data['ocrResults'][0]['sourceOrderBlocks']
                             if b.get('sourceModelIndex') == 0)
                self.assertEqual(block['block_label'], label)
                self.assertEqual(block['customMetadata'], {'retained': True})
                self.assertLess(block['block_bbox'][1], 100)

    def test_number_inside_body_cannot_authorize_formula_number_semantics(self):
        pdf = document([(50, 60, 'This source paragraph evaluates 2026 independent source documents.'),
                        (50, 160, 'More native text supplies sufficient evidence for source projection.')])
        data = result([{'block_label': 'formula_number', 'block_content': '0',
                        'block_bbox': [900, 900, 999, 999]}])
        project_native_pdf_pages(data, pdf, [])
        self.assertNotIn('sourceOrderBlocks', data['ocrResults'][0])

    def test_hybrid_pdf_plain_model_text_absent_from_native_layer_is_preserved(self):
        pdf = document([(50, 60, 'This native source paragraph explains the main printed document body.'),
                        (50, 160, 'A second native paragraph provides enough source text for ordering.')])
        data = result([{'block_label': 'text', 'block_content': 'Recognized text in a raster inset.'}])
        data['markdown'] = data['ocrResults'][0]['markdown']['text'] = 'Recognized text in a raster inset.'
        project_native_pdf_pages(data, pdf, [])
        self.assertEqual(data['markdown'], 'Recognized text in a raster inset.')
        self.assertNotIn('sourceOrderBlocks', data['ocrResults'][0])

    def test_recovered_visual_keeps_all_four_model_visual_categories(self):
        pdf = document([(50, 60, 'A readable native source paragraph introduces a figure.'),
                        (50, 320, 'Fig. 1. Source diagram.'),
                        (50, 360, 'An additional source paragraph follows the original figure.')])
        for label in ('image', 'chart', 'header_image', 'footer_image'):
            with self.subTest(label=label):
                data = result([{'block_label': label, 'block_content': '![Original](figure.png)',
                                'block_bbox': [80, 180, 900, 375]}])
                project_native_pdf_pages(data, pdf, [figure([80, 180, 900, 375])])
                visual = next(b for b in data['ocrResults'][0]['sourceOrderBlocks']
                              if b.get('sourceModelIndex') == 0)
                self.assertEqual(visual['block_label'], label)
                self.assertEqual(visual['sourceModelContent'], '![Original](figure.png)')

    def test_inline_formula_retains_emitted_math_delimiters(self):
        pdf = document([(50, 60, 'x = 1'),
                        (50, 160, 'A native paragraph supplies reliable body text for source ordering '
                         'and retains the independently recognized inline equation above.')])
        data = result([{'block_label': 'inline_formula', 'block_content': r'\(x=1\)'}])
        project_native_pdf_pages(data, pdf, [])
        self.assertIn(r'\(x=1\)', data['markdown'])
        self.assertEqual(data['ocrResults'][0]['sourceOrderBlocks'][0]['block_label'], 'inline_formula')

    def test_native_transfer_keeps_inline_emphasis_code_and_hard_line_boundaries(self):
        pdf = document([(50, 60, 'A recognized semantic paragraph.'),
                        (50, 160, 'A second source paragraph supplies sufficient native prose to trust '
                         'the source reading order and to keep this ordinary body text.')])
        for content in ('A *recognized* semantic paragraph.',
                        'A _recognized_ semantic paragraph.',
                        'A `recognized` semantic paragraph.',
                        '# A recognized semantic paragraph.',
                        '> A recognized semantic paragraph.',
                        'A recognized\nsemantic paragraph.'):
            with self.subTest(content=content):
                data = result([{'block_label': 'text', 'block_content': content}])
                project_native_pdf_pages(data, pdf, [])
                self.assertIn(content, data['markdown'])

    def test_anchored_plain_content_keeps_model_punctuation_exactly(self):
        pdf = document([(50, 60, 'Recognized semantic content.'),
                        (50, 160, 'A source body paragraph supplies sufficient native evidence for '
                         'a reliable source reading order projection of this page.')])
        data = result([{'block_label': 'text', 'block_content': 'Recognized semantic content!'}])
        project_native_pdf_pages(data, pdf, [])
        self.assertEqual(data['ocrResults'][0]['sourceOrderBlocks'][0]['block_content'],
                         'Recognized semantic content!')

    def test_bound_visual_keeps_model_caption_and_mermaid_in_main_markdown(self):
        pdf = document([(50, 60, 'A reliable source paragraph introduces the original figure.'),
                        (50, 320, 'Fig. 1. Source diagram.'),
                        (50, 360, 'An additional source paragraph follows the original figure.')])
        body = '\n\nCaption **recognized** exactly.\n\n```mermaid\ngraph LR\nA-->B\n```'
        data = result([{'block_label': 'chart', 'block_content': '![Original](figure.png)' + body,
                        'block_bbox': [80, 180, 900, 375]}])
        project_native_pdf_pages(data, pdf, [figure([80, 180, 900, 375])])
        self.assertIn(body, data['markdown'])

    def test_native_projection_retains_semantic_subblocks_and_markdown(self):
        pdf = document([(50, 60, 'ABSTRACT\nA detailed study of reliable document structure.\n'
                         'First semantic paragraph.\nSecond semantic paragraph.'),
                        (50, 160, 'An additional native paragraph supplies enough source text.')])
        model = [
            {'block_label': 'abstract', 'block_content': '**ABSTRACT**\n\nA detailed study of reliable document structure.'},
            {'block_label': 'text', 'block_content': 'First semantic paragraph.\n\nSecond semantic paragraph.'}]
        data = result(model)
        project_native_pdf_pages(data, pdf, [])
        blocks = data['ocrResults'][0]['sourceOrderBlocks']
        abstract = next(b for b in blocks if b['block_label'] == 'abstract')
        self.assertEqual(abstract['block_content'], model[0]['block_content'])
        self.assertIn(model[1]['block_content'], data['markdown'])
        self.assertEqual(data['ocrResults'][0]['parsing_res_list'], model)

    def test_unbound_semantic_category_falls_back_without_losing_its_content(self):
        pdf = document([(50, 60, 'Native prose supplies enough reliable text for source projection.'),
                        (50, 100, 'More native prose has no matching algorithm or inline formula.')])
        for label in ('algorithm', 'inline_formula', 'seal', 'header_image', 'formula_number'):
            with self.subTest(label=label):
                data = result([{'block_label': label, 'block_content': 'recognized semantic content'}])
                data['ocrResults'][0]['markdown']['text'] = 'recognized semantic content'
                data['markdown'] = 'recognized semantic content'
                project_native_pdf_pages(data, pdf, [])
                self.assertEqual(data['markdown'], 'recognized semantic content')
                self.assertEqual(data['ocrResults'][0]['sourceOrderDiagnostic']['method'], 'model-preserved')

    def test_fallback_replaces_legacy_flattened_projection_with_original(self):
        pdf = document([(50, 60, 'Native source cannot anchor this recognized semantic algorithm.'),
                        (50, 100, 'Another source paragraph provides sufficient native text.')])
        data = result([{'block_label': 'algorithm', 'block_content': 'for each item: return item'}])
        page = data['ocrResults'][0]
        page['originalMarkdown'] = '```python\nfor item in items: return item\n```'
        page['sourceOrder'] = {'method': 'pdf-native', 'version': 1}
        page['sourceOrderBlocks'] = [{'block_label': 'text', 'block_content': 'flattened'}]
        project_native_pdf_pages(data, pdf, [])
        self.assertEqual(data['markdown'], page['originalMarkdown'])
        self.assertNotIn('sourceOrderBlocks', page)

    def test_same_source_block_keeps_separate_model_titles(self):
        pdf = document([(50, 60, 'First recognized title\nSecond recognized title', 22),
                        (50, 200, 'A long native body paragraph provides enough ordinary text to identify '
                         'the source body and confidently distinguish the two titles.', 10)])
        data = result([{'block_label': 'doc_title', 'block_content': '# First recognized title'},
                       {'block_label': 'doc_title', 'block_content': '# Second recognized title'}])
        project_native_pdf_pages(data, pdf, [])
        titles = [b for b in data['ocrResults'][0]['sourceOrderBlocks'] if b['block_label'] == 'doc_title']
        self.assertEqual([b['block_content'] for b in titles],
                         ['# First recognized title', '# Second recognized title'])

    def test_merged_reference_block_splits_entries_and_source_geometry(self):
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        lines = [(fitz.Rect(x, y, 300, y + 8), text, (1, 0)) for x, y, text in [
            (50, 60, '[9] A. Author, First source title,'),
            (65, 70, 'Journal of reliable source document parsing, 2024.'),
            (50, 80, '[10] B. Author, Second source title,'),
            (65, 90, 'Conference on verified document understanding, 2025.')]]
        blocks = _text_parts(lines, {'fonts': [(8, 200)]}, page, 10, False)
        md = '\n\n'.join(block['block_content'] for block in blocks)
        self.assertIn('Journal of reliable source document parsing, 2024.\n\n[10]', md)
        self.assertEqual(len(blocks), 2)
        self.assertLess(blocks[0]['block_bbox'][3], blocks[1]['block_bbox'][1])
        doc.close()

    def test_body_inline_citations_remain_one_paragraph(self):
        pdf = document([(50, 60, 'Our detailed study [9] compares the two source document methods'),
                        (50, 72, 'and [10] confirms the independently verified source result.')])
        data = result()
        project_native_pdf_pages(data, pdf, [])
        self.assertEqual(data['markdown'], 'Our detailed study [9] compares the two source document methods and [10] confirms the independently verified source result.')

    def test_body_reference_does_not_anchor_figure(self):
        pdf = document([(50, 60, 'The discussion cites Fig. 1 before the actual diagram.'),
                        (50, 320, 'Fig. 1. Source diagram.'),
                        (50, 360, 'Following discussion continues with source evidence.')])
        data = result()
        raw = copy.deepcopy(data['ocrResults'][0])
        project_native_pdf_pages(data, pdf, [figure([80, 180, 900, 375])])
        md = data['markdown']
        self.assertLess(md.index('The discussion'), md.index('![Figure'))
        self.assertLess(md.index('![Figure'), md.index('Fig. 1. Source diagram.'))
        self.assertLess(md.index('Fig. 1. Source diagram.'), md.index('Following discussion'))
        self.assertEqual(data['ocrResults'][0]['parsing_res_list'], raw['parsing_res_list'])
        self.assertEqual(data['ocrResults'][0]['sourceTextBlocks'], raw['sourceTextBlocks'])
        self.assertEqual(data['originalMarkdown'], 'OCR duplicated prose')

    def test_right_figure_follows_entire_left_column(self):
        pdf = document([(50, 70, 'Left upper paragraph with enough natural source text.'),
                        (50, 500, 'Left lower paragraph must remain before the right column.'),
                        (320, 260, 'Fig. 1. Source diagram.'),
                        (320, 320, 'Right lower paragraph follows the illustration.')])
        data = result()
        project_native_pdf_pages(data, pdf, [figure([530, 80, 940, 290])])
        md = data['markdown']
        self.assertLess(md.index('Left lower'), md.index('![Figure'))
        self.assertLess(md.index('![Figure'), md.index('Right lower'))
        self.assertEqual(md.count('Fig. 1. Source diagram.'), 1)

    def test_full_width_middle_figure_splits_column_bands(self):
        pdf = document([(50, 60, 'Left before full width illustration paragraph.'),
                        (320, 60, 'Right before full width illustration paragraph.'),
                        (50, 350, 'Fig. 1. Source diagram.'),
                        (50, 410, 'Left after illustration source paragraph.'),
                        (320, 410, 'Right after illustration source paragraph.')])
        data = result()
        project_native_pdf_pages(data, pdf, [figure([80, 150, 940, 420])])
        md = data['markdown']
        self.assertLess(md.index('Right before'), md.index('![Figure'))
        self.assertLess(md.index('![Figure'), md.index('Left after'))
        self.assertLess(md.index('Left after'), md.index('Right after'))

    def test_table_caption_native_geometry_and_model_html(self):
        pdf = document([(260, 60, 'TABLE I'),
                        (70, 85, 'A SOURCE COMPARISON WITH VERIFIED COLUMN HEADINGS'),
                        (70, 125, 'Method'), (320, 125, 'Score'),
                        (70, 145, 'Ada'), (320, 145, '42'),
                        (50, 200, 'Left source body below the full width table.'),
                        (320, 200, 'Right source body below the full width table.')], table=True)
        html = '<table><tr><td>Ada</td><td>42</td></tr></table>'
        data = result([{'block_label': 'table', 'block_bbox': [80, 130, 930, 190],
                       'block_content': html}])
        project_native_pdf_pages(data, pdf, [])
        md = data['markdown']
        self.assertLess(md.index('TABLE I'), md.index(html))
        self.assertLess(md.index(html), md.index('Left source body'))
        self.assertEqual(md.count('Ada'), 1)
        self.assertEqual(md.count('TABLE I'), 1)

    def test_source_numbered_equation_preserves_model_latex_with_wrong_model_y(self):
        pdf = document([(50, 60, 'Before the equation is a complete source sentence.'),
                        (80, 120, 'x = y + 1'), (260, 120, '(1)'),
                        (50, 180, 'After the equation is another complete source sentence.')])
        latex = '$$x = \\frac{a}{b}$$'
        data = result([{'block_label': 'display_formula', 'block_bbox': [10, 600, 999, 650],
                       'block_content': latex}])
        project_native_pdf_pages(data, pdf, [])
        md = data['markdown']
        self.assertLess(md.index('Before the equation'), md.index('x = \\frac{a}{b}'))
        self.assertLess(md.index('x = \\frac{a}{b}'), md.index('After the equation'))
        self.assertIn('\\tag{1}', md)
        self.assertNotIn('x = y + 1', md)

    def test_empty_and_rotated_native_pages_preserve_everything(self):
        for pdf in (document([]), document([(50, 60, 'This page contains reliable source text but is rotated.')], rotation=90)):
            data = result()
            before = copy.deepcopy(data)
            project_native_pdf_pages(data, pdf, [])
            data['ocrResults'][0].pop('sourceOrderDiagnostic', None)
            self.assertEqual(data, before)

    def test_footer_and_vertical_aside_do_not_interrupt_body(self):
        pdf = document([(50, 60, 'Left opening source paragraph for a double column page.'),
                        (50, 500, 'Left ending source paragraph.'),
                        (320, 60, 'Right opening source paragraph.'),
                        (90, 780, 'Authorized licensed footer preserved at the page end.')])
        data = result()
        project_native_pdf_pages(data, pdf, [])
        self.assertLess(data['markdown'].index('Right opening'), data['markdown'].index('Authorized licensed'))

    def test_centered_short_title_line_precedes_both_columns(self):
        pdf = document([(240, 60, 'Centered source title continuation'),
                        (50, 150, 'Left opening paragraph contains native source evidence.'),
                        (50, 500, 'Left final paragraph precedes the right column.'),
                        (320, 150, 'Right opening paragraph follows the complete left column.')])
        data = result()
        project_native_pdf_pages(data, pdf, [])
        self.assertLess(data['markdown'].index('Centered source title'), data['markdown'].index('Left opening'))

    def test_cross_page_merge_and_unanchored_tables_preserve_original(self):
        pdf = document([(50, 60, 'This is reliable native text containing a full source paragraph.'),
                        (50, 100, 'A second source paragraph makes the native layer readable.')])
        for data in (dict(result(), enhancement={'crossPageTables': [{'pages': [0, 1]}]}),
                     result([{'block_label': 'table', 'block_content': '<table>Inventory</table>'}])):
            before = copy.deepcopy(data)
            project_native_pdf_pages(data, pdf, [])
            data['ocrResults'][0].pop('sourceOrderDiagnostic', None)
            self.assertEqual(data, before)

    def test_unanchored_formula_keeps_original_output(self):
        pdf = document([(50, 60, 'This source paragraph has reliable text but no numbered equation.'),
                        (50, 100, 'More native text precedes a model formula with unknown position.')])
        data = result([{'block_label': 'display_formula', 'block_content': '$$x=1$$'}])
        before = copy.deepcopy(data)
        project_native_pdf_pages(data, pdf, [])
        self.assertEqual(data['ocrResults'][0].pop('sourceOrderDiagnostic')['method'], 'model-preserved')
        self.assertEqual(data, before)

    def test_author_header_rows_precede_body_and_keep_left_to_right_order(self):
        body = 'Source body line has enough prose to identify the paragraph.\n' * 3
        pdf = document([(120, 60, 'A source title spanning the center of the paper'),
                        (50, 130, 'Author Left\nDepartment of Science'),
                        (230, 130, 'Author Middle\nDepartment of Artificial Intelligence'),
                        (440, 130, 'Author Right\nDepartment of Science'),
                        (50, 240, body), (320, 240, body.replace('Source', 'Right'))])
        data = result()
        project_native_pdf_pages(data, pdf, [])
        md = data['markdown']
        self.assertLess(md.index('Author Left'), md.index('Author Middle'))
        self.assertLess(md.index('Author Middle'), md.index('Author Right'))
        self.assertLess(md.index('Author Right'), md.index('Source body'))

    def test_large_native_title_lines_merge_without_promoting_authors(self):
        pdf = document([(70, 60, 'AdaCache: Efficient and Robust', 22),
                        (150, 88, 'Semantic Caching for RAG', 22),
                        (80, 130, 'Shulin Chen and Lintong Zhang', 12),
                        (50, 220, 'Native body paragraph remains ordinary source text.\n'
                         'It provides enough evidence for the dominant body font.\n'
                         'The complete original paragraph must survive projection.', 10)])
        data = result()
        project_native_pdf_pages(data, pdf, [])
        md = data['markdown']
        self.assertIn('# AdaCache: Efficient and Robust Semantic Caching for RAG', md)
        self.assertEqual(sum(line.startswith('# ') for line in md.splitlines()), 1)
        self.assertNotIn('# Shulin', md)
        self.assertIn('The complete original paragraph must survive projection.', md)

    def test_native_mixed_heading_and_body_blocks_keep_markdown_semantics(self):
        pdf = document([(50, 60, 'IV. EXPERIMENTS\nA. Datasets and Metrics\n'
                         'We utilized representative source datasets for evaluation.\n'
                         'All remaining source body sentences must remain visible.'),
                        (50, 220, 'B. Baselines\nWe compare the approach with reliable source methods.')])
        data = result()
        project_native_pdf_pages(data, pdf, [])
        md = data['markdown']
        self.assertIn('## IV. EXPERIMENTS\n\n### A. Datasets and Metrics\n\nWe utilized', md)
        self.assertIn('### B. Baselines\n\nWe compare', md)
        self.assertIn('All remaining source body sentences must remain visible.', md)

    def test_formulas_bind_by_column_and_position_after_model_y_sort(self):
        pdf = document([(50, 60, 'Left source body has enough native prose for reliable projection.'),
                        (330, 100, 'Right source body precedes its own numbered equation.'),
                        (330, 150, 'x = 2'), (550, 150, '(2)'),
                        (50, 300, 'x = 1'), (270, 300, '(1)')])
        data = result([
            {'block_label': 'display_formula', 'block_bbox': [540, 170, 950, 205], 'block_content': '$$x=2$$'},
            {'block_label': 'display_formula', 'block_bbox': [80, 360, 480, 390], 'block_content': '$$x=1$$'}])
        project_native_pdf_pages(data, pdf, [])
        formulas = [b for b in data['ocrResults'][0]['sourceOrderBlocks'] if b['block_label'] == 'display_formula']
        self.assertEqual([b['sourceEquationNumber'] for b in formulas], [1, 2])
        for number, block in enumerate(formulas, 1):
            self.assertIn(f'x={number}', block['block_content'])
            self.assertIn(f'\\tag{{{number}}}', block['block_content'])

    def test_existing_equation_tag_is_preserved_once(self):
        pdf = document([(50, 60, 'This reliable native source paragraph introduces a numbered equation.'),
                        (50, 120, 'x = 1'), (270, 120, '(1)'),
                        (50, 180, 'The following source paragraph explains the equation in full.')])
        formula = '$$x=1 \\tag{1}$$'
        data = result([{'block_label': 'display_formula', 'block_bbox': [80, 130, 480, 170],
                       'block_content': formula}])
        project_native_pdf_pages(data, pdf, [])
        self.assertIn(formula, data['markdown'])
        self.assertEqual(data['markdown'].count('\\tag{1}'), 1)

    def test_tables_bind_to_native_columns_instead_of_global_y_order(self):
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        for x, y, text in [(50, 60, 'Left source prose establishes a readable native text layer.'),
                           (330, 80, 'Right source prose precedes its table.'),
                           (50, 400, 'TABLE I'), (330, 150, 'TABLE II')]:
            page.insert_text((x, y), text, fontsize=10)
        for x, y in [(50, 420), (330, 170)]:
            for dy in (0, 20, 40):
                page.draw_line((x, y + dy), (x + 200, y + dy))
            for dx in (0, 100, 200):
                page.draw_line((x + dx, y), (x + dx, y + 40))
            for dx, dy, text in [(5, 15, 'Method'), (105, 15, 'Score'), (5, 35, 'Ada'), (105, 35, '42')]:
                page.insert_text((x + dx, y + dy), text, fontsize=10)
        pdf = doc.tobytes()
        doc.close()
        html1, html2 = '<table><td>first-table</td></table>', '<table><td>second-table</td></table>'
        data = result([{'block_label': 'table', 'block_bbox': [80, 520, 420, 580], 'block_content': html1},
                       {'block_label': 'table', 'block_bbox': [550, 210, 890, 270], 'block_content': html2}])
        project_native_pdf_pages(data, pdf, [])
        md = data['markdown']
        self.assertLess(md.index('TABLE I\n'), md.index(html1))
        self.assertLess(md.index(html1), md.index('TABLE II\n'))
        self.assertLess(md.index('TABLE II\n'), md.index(html2))

    def test_unrecovered_model_visual_preserves_whole_page(self):
        pdf = document([(50, 60, 'A reliable source text layer surrounds an uncaptioned model image.'),
                        (50, 100, 'The image must survive when native recovery cannot identify it.')])
        for label in ('image', 'chart'):
            data = result([{'block_label': label, 'block_bbox': [100, 250, 500, 600],
                            'block_content': '![Uncaptioned](unconfirmed.png)'}])
            data['ocrResults'][0]['markdown']['text'] += '\n![Uncaptioned](unconfirmed.png)'
            before = copy.deepcopy(data)
            project_native_pdf_pages(data, pdf, [])
            self.assertEqual(data['ocrResults'][0].pop('sourceOrderDiagnostic')['method'], 'model-preserved')
            self.assertEqual(data, before)


if __name__ == '__main__':
    unittest.main()
