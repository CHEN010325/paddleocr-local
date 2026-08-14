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
    localStorage.setItem('pandocr.apiToken', 'secret-token');

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
            'Final text\\n\\n**Unlimited-OCR status**\\n\\nLoading model'
        )`),
        'Final text'
    );
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
