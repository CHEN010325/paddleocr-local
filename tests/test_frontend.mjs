import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';


const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const appPath = path.join(rootDir, 'static', 'app.js');
const storage = new Map();
const localStorage = {
    getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
    },
    setItem(key, value) {
        storage.set(key, String(value));
    },
    removeItem(key) {
        storage.delete(key);
    },
    clear() {
        storage.clear();
    }
};
const document = {
    addEventListener() {},
    getElementById() {
        return null;
    },
    querySelector() {
        return null;
    },
    body: {},
    documentElement: {},
    title: 'PaddleOCR Local'
};
const window = {
    sessionStorage: localStorage,
    PANDOCR_I18N: {
        defaultLanguage: 'zh-CN',
        supportedLanguages: ['zh-CN', 'en'],
        titles: {
            'zh-CN': 'PaddleOCR Local',
            en: 'PaddleOCR Local'
        },
        dictionaries: {
            en: {}
        }
    },
    location: {
        href: 'http://localhost:8000/',
        origin: 'http://localhost:8000'
    }
};
const context = vm.createContext({
    Blob,
    Headers,
    URL,
    Uint8Array,
    atob,
    btoa,
    clearTimeout,
    console,
    document,
    fetch: async () => {
        throw new Error('Unexpected network access from frontend unit test');
    },
    localStorage,
    setTimeout,
    window
});
vm.runInContext(fs.readFileSync(appPath, 'utf8'), context, { filename: appPath });


function evaluate(expression) {
    return vm.runInContext(expression, context);
}


function plain(value) {
    return JSON.parse(JSON.stringify(value));
}


test.beforeEach(() => {
    storage.clear();
    evaluate("currentLanguage = 'zh-CN'");
});


test('language normalization and interpolation use safe fallbacks', () => {
    assert.equal(evaluate("normalizeLanguage('en')"), 'en');
    assert.equal(evaluate("normalizeLanguage('fr')"), 'zh-CN');
    assert.equal(
        evaluate("interpolateI18n('Page {page} of {total}', { page: 2 })"),
        'Page 2 of {total}'
    );
    assert.equal(evaluate("hasCjk('中文')"), true);
    assert.equal(evaluate("hasCjk('plain text')"), false);
});


test('API authentication is attached only to same-origin API URLs', () => {
    window.sessionStorage.setItem('pandocr.apiToken', 'secret-token');

    assert.equal(evaluate("isLocalApiUrl('/api/models')"), true);
    assert.equal(evaluate("isLocalApiUrl('http://localhost:8000/api/tasks')"), true);
    assert.equal(evaluate("isLocalApiUrl('https://example.com/api/tasks')"), false);
    assert.equal(
        evaluate("authHeaders({}, '/api/models').get('authorization')"),
        'Bearer secret-token'
    );
    assert.equal(
        evaluate("authHeaders({}, 'https://example.com/api/models').get('authorization')"),
        null
    );
});


test('model and task normalization preserve the newest meaningful state', () => {
    const models = plain(evaluate(`normalizeModelList({
        data: [
            'legacy',
            { id: 'pp-ocrv6', label: 'PP OCR', endpoint: '/pp-ocrv6' }
        ]
    })`));
    assert.equal(models[0].id, 'legacy');
    assert.equal(models[1].endpoint, '/pp-ocrv6');
    assert.equal(evaluate("normalizeUnlimitedOcrBackend('SGLANG')"), 'sglang');
    assert.equal(evaluate("normalizeUnlimitedOcrBackend('invalid')"), 'transformers');

    const tasks = plain(evaluate(`dedupeTasks([
        { id: 'old', name: 'same.pdf', size: 10, pageCount: 1, updatedAt: 1 },
        { id: 'new', name: 'same.pdf', size: 10, pageCount: 1, updatedAt: 2 },
        { id: 'other', name: 'other.pdf', size: 10, pageCount: 1, updatedAt: 3 }
    ])`));
    assert.deepEqual(tasks.map((task) => task.id), ['other', 'new']);

    const completed = plain(evaluate(`reconcileTaskStatus({
        id: 'task',
        status: 'processing',
        sourceUrl: '/api/tasks/task/source',
        batches: [{ status: 'completed' }],
        ocrResults: [{}]
    })`));
    assert.equal(completed.status, 'completed');
});

test('task list status follows the active processing task marker', () => {
    evaluate("isProcessing=true; processingTaskId='active-task'");
    assert.equal(
        evaluate("statusText({id:'active-task',status:'pending',pageCount:5,batches:[]})"),
        '0/5 解析中'
    );
    evaluate("isProcessing=false; processingTaskId=null");
});


test('runtime readiness requires the selected model to be uniquely running and ready', () => {
    evaluate(`
      availableModels=[
        {id:'pp-ocrv6',label:'PP-OCRv6'},
        {id:'ovisocr2',label:'OvisOCR2'}
      ];
      selectedModelId='pp-ocrv6';
      modelRuntime={
        controlAvailable:true,
        activeModelId:'pp-ocrv6',
        runningModelIds:['pp-ocrv6','ovisocr2'],
        readyModelIds:['pp-ocrv6','ovisocr2'],
        exclusivityViolation:true,
        models:{'pp-ocrv6':{ready:true},ovisocr2:{ready:true}}
      };
    `);

    assert.equal(evaluate("isModelRuntimeReady('pp-ocrv6')"), false);
    assert.equal(evaluate("modelRuntimeDotClass('pp-ocrv6')"), 'dot error');
    assert.match(evaluate("modelRuntimeStatusText(availableModels[0])"), /互斥异常/);
    assert.match(evaluate("modelRuntimeFailureDetail('pp-ocrv6')"), /pp-ocrv6, ovisocr2/);
    assert.equal(evaluate('syncSelectedModelWithRuntime()'), false);

    evaluate(`
      modelRuntime.runningModelIds=['pp-ocrv6'];
      modelRuntime.readyModelIds=['pp-ocrv6'];
      modelRuntime.exclusivityViolation=false;
    `);
    assert.equal(evaluate("isModelRuntimeReady('pp-ocrv6')"), true);

    evaluate(`modelRuntime={activeModelId:'pp-ocrv6',models:{'pp-ocrv6':{ready:true}}}`);
    assert.equal(evaluate("isModelRuntimeReady('pp-ocrv6')"), true);
});


test('PDF batching covers every page without oversized final ranges', () => {
    const batches = plain(evaluate('createPdfBatchDescriptors(5, 2)'));
    assert.deepEqual(
        batches.map(({ startPage, endPage, pageCount }) => ({ startPage, endPage, pageCount })),
        [
            { startPage: 1, endPage: 2, pageCount: 2 },
            { startPage: 3, endPage: 4, pageCount: 2 },
            { startPage: 5, endPage: 5, pageCount: 1 }
        ]
    );
    assert.equal(evaluate("clampPdfBatchSize('0')"), 1);
    assert.equal(evaluate("clampPdfBatchSize('999')"), 400);
    assert.equal(evaluate("clampPdfBatchSize('invalid')"), 1);
});

test('failed batches can be reset without touching completed results', () => {
    const task = {
        status: 'error',
        error: 'page failed',
        completedPages: 0,
        markdown: '# done\n\n# partial',
        batches: [
            { id: 'done', status: 'completed', pageCount: 2, markdown: '# done' },
            { id: 'failed', status: 'error', pageCount: 1, error: 'timeout', markdown: '# partial' },
            { id: 'pending', status: 'pending', pageCount: 1, markdown: '' }
        ]
    };
    assert.equal(evaluate('countFailedBatches(' + JSON.stringify(task) + ')'), 1);
    assert.equal(evaluate('completedPagesFromBatches(' + JSON.stringify(task) + ')'), 2);
    const retried = evaluate(`(() => {
        const task = ${JSON.stringify(task)};
        const count = prepareFailedBatchesForRetry(task);
        rebuildTaskMarkdownFromBatches(task);
        syncTaskCompletedPages(task);
        return { count, task, persisted: taskForPersistence(task) };
    })()`);
    assert.equal(retried.count, 1);
    assert.equal(retried.task.status, 'pending');
    assert.equal(retried.task.error, null);
    assert.equal(retried.task.batches[0].status, 'completed');
    assert.equal(retried.task.batches[0].markdown, '# done');
    assert.equal(retried.task.batches[1].status, 'pending');
    assert.equal(retried.task.batches[1].error, null);
    assert.equal(retried.task.batches[1].markdown, '');
    assert.equal(retried.task.markdown.trim(), '# done');
    assert.equal(retried.task.markdown.includes('# partial'), false);
    assert.equal(retried.task.completedPages, 2);
    assert.equal(retried.persisted.completedPages, 2);
    assert.equal(retried.persisted.markdown.trim(), '# done');
});


test('task persistence strips transient payloads and status placeholders', () => {
    const metadataOnly = plain(evaluate(`taskForPersistence({
        id: 'task',
        sourceUrl: '/api/tasks/task/source',
        sourceDataUrl: 'data:application/pdf;base64,AA==',
        markdown: 'result',
        images: { image: 'base64' },
        ocrResults: [{}],
        batches: [{
            id: 'batch',
            markdown: 'batch result',
            payloadDataUrl: 'data:application/pdf;base64,AA==',
            payloadBlob: { size: 10 },
            _streamStatus: 'loading'
        }]
    }, { includeResults: false })`));

    assert.equal(metadataOnly._preserveResult, true);
    assert.equal('sourceDataUrl' in metadataOnly, false);
    assert.equal('markdown' in metadataOnly, false);
    assert.equal('images' in metadataOnly, false);
    assert.equal('ocrResults' in metadataOnly, false);
    assert.equal('payloadDataUrl' in metadataOnly.batches[0], false);
    assert.equal('payloadBlob' in metadataOnly.batches[0], false);
    assert.equal('markdown' in metadataOnly.batches[0], false);

    assert.equal(
        evaluate(`stripStreamStatusMarkdown(
            'Final text\\n\\n**Unlimited-OCR-2 status**\\n\\nLoading model'
        )`),
        'Final text'
    );
});


test('all later-batch results keep absolute source pages regardless of parser', () => {
    const navidc = plain(evaluate(`compactOCRJsonResult(
        { parser: 'navidc-ocr', pageIndex: 2, markdown: { text: 'later page', images: {} } },
        { id: 'navidc-batch-2', startPage: 6 },
        2
    )`));
    const paddle = plain(evaluate(`compactOCRJsonResult(
        { page_index: 0, markdown: { text: 'default model page', images: {} } },
        { id: 'paddle-batch-2', startPage: 3 },
        0
    )`));

    assert.equal(navidc.sourcePage, 8);
    assert.equal(navidc.batchId, 'navidc-batch-2');
    assert.equal(paddle.sourcePage, 3);
    assert.equal(paddle.batchId, 'paddle-batch-2');
});


test('stream events and normalized coordinates are validated defensively', () => {
    assert.deepEqual(
        plain(evaluate(`parseStreamingOCREvent('{"type":"progress","page":2}')`)),
        { type: 'progress', page: 2 }
    );
    assert.equal(evaluate("parseStreamingOCREvent('not-json')"), null);

    const position = plain(evaluate(`streamingSourcePosition({
        source: {
            pageIndex: 8,
            pageProgress: 2,
            bbox: [10, 20, 30, 40],
            pageWidth: 1024,
            pageHeight: 1024,
            label: 'text'
        }
    }, { startPage: 3, endPage: 4, pageCount: 2 })`));
    assert.equal(position.pageNumber, 4);
    assert.equal(position.pageProgress, 1);
    assert.equal(position.pageWidth, 1000);
    assert.equal(position.pageHeight, 1000);
    assert.equal(
        evaluate("streamingSourcePosition({ source: { bbox: ['bad'] } }, {})"),
        null
    );
});


test('HPD-Parsing layout boxes use the official normalized coordinate space', () => {
    assert.equal(evaluate("isHPDParsingResult({parser:'hpd-parsing'})"), true);
    assert.equal(evaluate("isHPDParsingResult({}, {parser:'hpd-parsing'})"), true);
    assert.equal(evaluate("isHPDParsingResult({parser:'paddleocr-vl-1.6'})"), false);
    assert.equal(evaluate("looksLikeHPDParsingNormalizedBox([110,64,892,133])"), true);
    assert.equal(evaluate("looksLikeHPDParsingNormalizedBox([110,64,1701,133])"), false);
    assert.equal(evaluate("looksLikeHPDParsingNormalizedBox([110,64,'bad',133])"), false);
    assert.deepEqual(
        plain(evaluate(`layoutCoordinateBoundsForBlock(
            {parser:'hpd-parsing'},
            {width:1700,height:2200},
            [110,64,892,133]
        )`)),
        { pageWidth: 1000, pageHeight: 1000 }
    );
    assert.deepEqual(
        plain(evaluate(`layoutCoordinateBoundsForBlock(
            {parser:'paddleocr-vl-1.6'},
            {width:1700,height:2200},
            [110,64,892,133]
        )`)),
        { pageWidth: 1700, pageHeight: 2200 }
    );
});


test('HPD-Parsing page batches merge only genuine paragraph continuations', () => {
    assert.equal(
        evaluate(`joinTaskBatchMarkdown(
            {modelId:'hpd-parsing'},
            ['A Probing-RAG Core preserves', 'knowledge adaptivity across pages.']
        )`),
        'A Probing-RAG Core preserves knowledge adaptivity across pages.'
    );
    assert.equal(
        evaluate(`joinTaskBatchMarkdown(
            {modelId:'hpd-parsing'},
            ['A complete paragraph.', '## Next Section']
        )`),
        'A complete paragraph.\n\n## Next Section'
    );
    assert.equal(
        evaluate(`joinTaskBatchMarkdown(
            {modelId:'paddleocr-vl-1.6'},
            ['first', 'second']
        )`),
        'first\n\nsecond'
    );
    assert.equal(
        evaluate(`joinTaskBatchMarkdown(
            {ocrResults:[{parser:'hpd-parsing'}]},
            ['unfinished;', 'lowercase but separate.']
        )`),
        'unfinished;\n\nlowercase but separate.'
    );
    assert.equal(
        evaluate(`joinTaskBatchMarkdown(
            {modelId:'hpd-parsing'},
            ['![figure](images/figure.jpg)', 'lowercase caption text.']
        )`),
        '![figure](images/figure.jpg)\n\nlowercase caption text.'
    );
});


test('OCR markdown and result compaction remove transport-only data', () => {
    const markdown = evaluate(`cleanUnlimitedOCRMarkdown(
        '<|det|>header [1,2,3,4]<|/det|>skip ' +
        '<|det|>title [1,2,3,4]<|/det|>Title ' +
        '<|det|>formula [1,2,3,4]<|/det|>x^2'
    )`);
    assert.equal(markdown.includes('skip'), false);
    assert.equal(markdown.includes('# Title'), true);
    assert.equal(markdown.includes('$$\nx^2\n$$'), true);

    const prepared = plain(evaluate(`prepareBatchResult({
        markdown: '![figure](images/figure.jpg)',
        images: { 'images/figure.jpg': 'base64-image' }
    }, 'batch-1')`));
    assert.equal(
        prepared.markdown,
        '![figure](ocr_images/batch-1_figure.jpg)'
    );
    assert.deepEqual(
        prepared.images,
        { 'ocr_images/batch-1_figure.jpg': 'base64-image' }
    );

    const compact = plain(evaluate(`stripLargeOCRFields({
        inputImage: 'large',
        nested: { outputImages: ['large'], keep: 1 }
    })`));
    assert.deepEqual(compact, { nested: { keep: 1 } });
});


test('binary and filename helpers produce safe deterministic values', () => {
    assert.deepEqual(
        plain(evaluate("Array.from(dataUrlToUint8Array('data:text/plain;base64,SGk='))")),
        [72, 105]
    );
    assert.deepEqual(
        plain(evaluate("Array.from(base64ToBytes('SGk='))")),
        [72, 105]
    );
    assert.equal(
        evaluate(`safeDownloadName('bad:name?.pdf', 'md')`),
        'bad_name_.md'
    );
    assert.equal(
        evaluate(`imageValueToSrc('SGVsbG8=')`),
        'data:image/jpeg;base64,SGVsbG8='
    );
});

test('standalone HTML export embeds images and escapes metadata', () => {
    const markdown = evaluate(`embedTaskImagesInMarkdown({
        markdown: '![figure](ocr_images/figure.png)',
        images: { 'ocr_images/figure.png': 'aGVsbG8=' }
    })`);
    assert.equal(markdown.includes('data:image/png;base64,aGVsbG8='), true);
    assert.equal(evaluate("imageMimeTypeForPath('figure.JPG')"), 'image/jpeg');
    assert.equal(evaluate("imageMimeTypeForPath('figure.unknown')"), 'application/octet-stream');

    const html = evaluate(`standaloneHtmlForTask({
        name: '<unsafe>.pdf',
        modelName: 'Model & One',
        markdown: '# Title',
        images: {}
    })`);
    assert.equal(html.startsWith('<!doctype html>'), true);
    assert.equal(html.includes('&lt;unsafe&gt;.pdf'), true);
    assert.equal(html.includes('Model &amp; One'), true);
    assert.equal(html.includes('<meta charset="utf-8">'), true);
});

test('Markdown tables export to standards-compliant CSV', () => {
    const tables = plain(evaluate(`extractMarkdownTables([
        '| Name | Note |',
        '| --- | :---: |',
        '| Alice | a\\\\|b |',
        '| Bob | says "hi" |',
        '| Formula | =2+2 |',
        '',
        '~~~text',
        '| ignored | table |',
        '| --- | --- |',
        '~~~'
    ].join('\\n'))`));
    assert.equal(tables.length, 1);
    assert.deepEqual(tables[0], [
        ['Name', 'Note'],
        ['Alice', 'a|b'],
        ['Bob', 'says "hi"'],
        ['Formula', '=2+2']
    ]);
    const csv = evaluate(`markdownTableToCsv(${JSON.stringify(tables[0])})`);
    assert.equal(csv, '"Name","Note"\r\n"Alice","a|b"\r\n"Bob","says ""hi"""\r\n"Formula","\'=2+2"');
    evaluate("cachedMarkdownTableSource=null;cachedMarkdownTables=[]");
    const first = evaluate("cachedExtractMarkdownTables('| A | B |\\n| --- | --- |\\n| 1 | 2 |')");
    const second = evaluate("cachedExtractMarkdownTables('| A | B |\\n| --- | --- |\\n| 1 | 2 |')");
    assert.equal(first, second);
});

test('Markdown table parsing preserves literal backslashes and pipe escape parity', () => {
    const pathRow = String.raw`| Path | C:\temp\file.txt |`;
    const formulaRow = String.raw`| Formula | \alpha + \beta |`;
    const escapedPipeRow = String.raw`| Escaped | x\|y |`;
    const evenBackslashesRow = String.raw`| Even | left\\|right |`;
    const oddBackslashesRow = String.raw`| Odd | left\\\|right | tail |`;
    const escapedTrailingPipeRow = String.raw`| Tail | value\|`;

    assert.deepEqual(
        plain(evaluate(`splitMarkdownTableRow(${JSON.stringify(pathRow)})`)),
        ['Path', String.raw`C:\temp\file.txt`]
    );
    assert.deepEqual(
        plain(evaluate(`splitMarkdownTableRow(${JSON.stringify(formulaRow)})`)),
        ['Formula', String.raw`\alpha + \beta`]
    );
    assert.deepEqual(
        plain(evaluate(`splitMarkdownTableRow(${JSON.stringify(escapedPipeRow)})`)),
        ['Escaped', 'x|y']
    );
    assert.deepEqual(
        plain(evaluate(`splitMarkdownTableRow(${JSON.stringify(evenBackslashesRow)})`)),
        ['Even', String.raw`left\\`, 'right']
    );
    assert.deepEqual(
        plain(evaluate(`splitMarkdownTableRow(${JSON.stringify(oddBackslashesRow)})`)),
        ['Odd', String.raw`left\\|right`, 'tail']
    );
    assert.deepEqual(
        plain(evaluate(`splitMarkdownTableRow(${JSON.stringify(escapedTrailingPipeRow)})`)),
        ['Tail', 'value|']
    );
});

test('Markdown newline normalization changes real newlines but preserves literal slash sequences', () => {
    const windowsPath = String.raw`C:\new\report`;
    const literalEscapes = String.raw`before\nafter\r\nend`;
    assert.equal(
        evaluate(`normalizeMarkdownNewlines(${JSON.stringify(windowsPath)})`),
        windowsPath
    );
    assert.equal(
        evaluate(`normalizeMarkdownNewlines(${JSON.stringify(literalEscapes)})`),
        literalEscapes
    );
    assert.equal(evaluate("normalizeMarkdownNewlines('a\\r\\nb\\rc')"), 'a\nb\nc');
});

test('server exports download the requested format and surface API errors', async () => {
    evaluate(`
        tasks=[{id:'task-export',name:'sample.pdf',markdown:'# Result',sourceUrl:'/api/tasks/task-export/source',batches:[]}];
        activeTaskId='task-export';
        globalThis.exportCapture=null;
        globalThis.exportAlert=null;
        downloadBlob=(blob,name)=>{globalThis.exportCapture={size:blob.size,name}};
        updateActionState=()=>{};
        alert=(message)=>{globalThis.exportAlert=message};
        apiFetch=async (url)=>({ok:true,blob:async()=>new Blob(['docx']),url});
    `);
    await evaluate("downloadTaskServerExport('docx','docx',{disabled:false})");
    assert.deepEqual(plain(evaluate('globalThis.exportCapture')), { size: 4, name: 'sample.docx' });

    evaluate(`apiFetch=async ()=>({ok:false,text:async()=>JSON.stringify({detail:'no tables'})})`);
    await evaluate("downloadTaskServerExport('xlsx','xlsx',{disabled:false})");
    assert.equal(evaluate('globalThis.exportAlert'), '导出失败：no tables');
});
