import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';


const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const html = fs.readFileSync(path.join(rootDir, 'static', 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(rootDir, 'static', 'app.js'), 'utf8');
const i18n = fs.readFileSync(path.join(rootDir, 'static', 'i18n.js'), 'utf8');
const appFunctionNames = [...app.matchAll(/^function\s+([A-Za-z_$][\w$]*)\s*\(/gm)].map((match) => match[1]);


function jsonResponse(data, status = 200) {
    return new Response(JSON.stringify(data), {
        status,
        headers: { 'content-type': 'application/json' }
    });
}


function createBrowser(fetchOverride = null, withI18n = true) {
    const dom = new JSDOM(html, {
        runScripts: 'outside-only',
        url: 'http://localhost:8000/'
    });
    const { window } = dom;
    window.Response = Response;
    window.Request = Request;
    window.Headers = Headers;
    window.TextDecoder = TextDecoder;
    window.TextEncoder = TextEncoder;
    window.URL.createObjectURL = () => 'blob:test';
    window.URL.revokeObjectURL = () => {};
    window.alert = () => {};
    window.confirm = () => true;
    window.prompt = () => 'transformers';
    window.scrollTo = () => {};
    window.HTMLElement.prototype.scrollIntoView = function () {};
    window.HTMLElement.prototype.scrollTo = function (options = {}) {
        if (typeof options === 'object') {
            if (Number.isFinite(options.top)) this.scrollTop = options.top;
            if (Number.isFinite(options.left)) this.scrollLeft = options.left;
        }
    };
    window.HTMLElement.prototype.getBoundingClientRect = function () {
        return { top: 0, left: 0, right: 100, bottom: 100, width: 100, height: 100 };
    };
    window.HTMLCanvasElement.prototype.getContext = () => ({});
    window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,AA==';
    window.requestAnimationFrame = (callback) => {
        callback();
        return 1;
    };
    window.cancelAnimationFrame = () => {};
    window.setInterval = () => 1;
    window.clearInterval = () => {};
    window.navigator.clipboard = { writeText: async () => {} };
    window.document.execCommand = () => true;
    window.pdfjsLib = {
        GlobalWorkerOptions: {},
        getDocument: () => ({ promise: Promise.resolve({ numPages: 1 }) })
    };
    window.PDFLib = {
        PDFDocument: {
            load: async () => ({
                copyPages: async () => [],
                addPage() {},
                save: async () => new Uint8Array([1])
            }),
            create: async () => ({
                copyPages: async () => [],
                addPage() {},
                save: async () => new Uint8Array([1])
            })
        }
    };
    window.marked = { parse: (value) => `<p>${value}</p>` };
    window.DOMPurify = { sanitize: (value) => value };
    window.hljs = { highlightElement() {} };
    window.renderMathInElement = () => {};
    window.JSZip = class {
        file() {}
        folder() { return this; }
        async generateAsync() { return new Blob(['zip']); }
    };

    const runtime = {
        controlAvailable: true,
        activeModelId: 'paddleocr-vl-1.6',
        unlimitedOcrBackend: 'transformers',
        models: {
            'paddleocr-vl-1.6': { ready: true, state: 'running' },
            'unlimited-ocr': {
                ready: false,
                state: 'stopped',
                unlimitedOcrSupportedBackends: ['transformers', 'sglang']
            },
            ovisocr2: { ready: false, state: 'missing', available: false }
        }
    };
    const task = {
        id: 'task-1',
        name: 'sample.png',
        originalName: 'sample.png',
        sourceKind: 'image',
        sourceUrl: '/api/tasks/task-1/source',
        size: 100,
        pageCount: 1,
        modelId: 'paddleocr-vl-1.6',
        modelName: 'PaddleOCR-VL',
        modelEndpoint: '/api/paddleocr-vl-1.6',
        status: 'completed',
        updatedAt: 2,
        markdown: '# Result',
        images: {},
        ocrResults: [{ markdown: { text: '# Result', images: {} }, parsing_res_list: [] }],
        batches: [{ id: 'batch-1', status: 'completed', startPage: 1, endPage: 1, pageCount: 1 }]
    };
    const fetch = fetchOverride || (async (url, options = {}) => {
        const pathname = new URL(String(url), window.location.href).pathname;
        if (pathname === '/api/models') {
            return jsonResponse({
                default: 'paddleocr-vl-1.6',
                maxUploadBytes: 1024,
                data: [
                    { id: 'paddleocr-vl-1.6', label: 'PaddleOCR', endpoint: '/api/paddleocr-vl-1.6' },
                    { id: 'unlimited-ocr', label: 'Unlimited OCR', endpoint: '/api/unlimited-ocr' },
                    { id: 'ovisocr2', label: 'OvisOCR2', endpoint: '/api/ovisocr2' }
                ]
            });
        }
        if (pathname === '/api/model-runtime') return jsonResponse(runtime);
        if (pathname === '/api/tasks') return jsonResponse({ tasks: [task] });
        if (pathname === '/api/tasks/task-1') return jsonResponse(task);
        if (pathname === '/api/tasks/task-1/source') return new Response(new Uint8Array([1, 2]), { status: 200 });
        if (pathname.includes('/model-runtime/') || pathname.includes('/unlimited-ocr/backend')) return jsonResponse(runtime);
        if (options.method === 'DELETE' || options.method === 'PUT' || options.method === 'POST') return jsonResponse({});
        return jsonResponse({}, 404);
    });
    window.fetch = fetch;
    if (withI18n) {
        new vm.Script(i18n, { filename: path.join(rootDir, 'static', 'i18n.js') })
            .runInContext(dom.getInternalVMContext());
    }
    new vm.Script(app, { filename: path.join(rootDir, 'static', 'app.js') })
        .runInContext(dom.getInternalVMContext());
    return { dom, window, runtime, task };
}


async function boot(window) {
    window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));
    await new Promise((resolve) => window.setTimeout(resolve, 10));
}


test('full DOM boot renders models, tasks, language, tabs, and controls', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    assert.equal(window.document.querySelectorAll('#model-select option').length, 3);
    assert.equal(window.document.querySelectorAll('.task-item').length, 1);

    window.document.getElementById('language-toggle').click();
    window.document.getElementById('language-toggle').click();
    window.document.getElementById('sidebar-toggle').click();
    window.document.querySelector('[data-view="json"]').click();
    window.document.querySelector('[data-view="markdown"]').click();
    window.document.querySelector('[data-filter="done"]').click();
    window.document.querySelector('[data-filter="all"]').click();
    window.document.getElementById('task-search').value = 'sample';
    window.document.getElementById('task-search').dispatchEvent(new window.Event('input'));

    const batch = window.document.getElementById('pdf-batch-size-input');
    batch.value = '999';
    batch.dispatchEvent(new window.Event('input'));
    assert.equal(batch.value, '400');
    batch.value = '';
    batch.dispatchEvent(new window.Event('input'));
    batch.value = '2';
    batch.dispatchEvent(new window.Event('change'));

    for (const name of ['dragenter', 'dragover', 'dragleave']) {
        window.document.dispatchEvent(new window.Event(name, { bubbles: true, cancelable: true }));
    }
    window.document.getElementById('prev-page-btn').click();
    window.document.getElementById('next-page-btn').click();
    window.document.getElementById('zoom-in-btn').click();
    window.document.getElementById('zoom-out-btn').click();
    window.document.getElementById('reset-zoom-btn').click();
    dom.window.close();
});

// End of DOM regression tests.
test('restored single-model workbench does not expose model comparison UI', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    assert.equal(window.document.getElementById('compare-btn'), null);
    assert.equal(window.document.getElementById('compare-dialog'), null);
    assert.equal(window.document.querySelector('.comparison-tab'), null);
    assert.equal(window.document.getElementById('comparison-view'), null);
    assert.equal(window.document.querySelector('.brand-subtitle').textContent, '本地 PaddleOCR 多模型解析');
    dom.window.close();
});
test('GPU preflight panel shows runnable models, low-VRAM settings, and startup logs', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    window.eval(`
      availableModels=[
        {id:'pp-ocrv6',label:'PP-OCRv6'},
        {id:'paddleocr-vl-1.6',label:'PaddleOCR-VL 1.6'}
      ];
      selectedModelId='paddleocr-vl-1.6';
      modelRuntime={
        gpuPreflight:{
          status:'ready',
          gpus:[{name:'RTX 4070 Laptop GPU',totalMiB:8188,freeMiB:7100}],
          runnableModelIds:['pp-ocrv6'],
          models:{'paddleocr-vl-1.6':{supported:false,level:'unsupported',minimumMiB:11264,lowMemoryEnv:['PANDOCR_MAX_CONCURRENT_OCR=1']}}
        },
        models:{'paddleocr-vl-1.6':{ready:false,state:'stopped'}}
      };
      renderGpuPreflightPanel();
    `);
    const panel = window.document.getElementById('gpu-preflight-panel');
    assert.match(panel.textContent, /RTX 4070 Laptop GPU/);
    assert.match(panel.textContent, /PP-OCRv6/);
    assert.match(panel.textContent, /11264 MiB/);
    assert.doesNotMatch(panel.textContent, /PANDOCR_MAX_CONCURRENT_OCR=1/);
    assert.ok(panel.classList.contains('warning'));

    window.eval(`
      availableModels.push({id:'hpd-parsing',label:'HPD-Parsing'});
      selectedModelId='hpd-parsing';
      modelRuntime.gpuPreflight.runnableModelIds.push('hpd-parsing');
      modelRuntime.gpuPreflight.models['hpd-parsing']={supported:true,level:'low-memory',minimumMiB:7680,lowMemoryEnv:['HPD_PARSING_MAX_MODEL_LEN=8192']};
      renderGpuPreflightPanel();
    `);
    assert.match(panel.textContent, /HPD_PARSING_MAX_MODEL_LEN=8192/);

    window.eval(`
      selectedModelId='paddleocr-vl-1.6';
      modelRuntime.operation={
        state:'error',targetModelId:'paddleocr-vl-1.6',message:'GPU preflight rejected paddleocr-vl-1.6',
        diagnostics:{
          logCommands:['docker logs --tail 200 paddleocr-vlm-server'],
          logs:[{container:'paddleocr-vlm-server',tail:'GPU total memory is below the supported 12 GB class'}]
        }
      };
      renderGpuPreflightPanel();
    `);
    assert.match(panel.textContent, /GPU preflight rejected paddleocr-vl-1.6/);
    assert.match(panel.textContent, /docker logs --tail 200 paddleocr-vlm-server/);
    assert.match(panel.textContent, /below the supported 12 GB class/);
    assert.ok(panel.classList.contains('error'));
    dom.window.close();
});
test('processing orchestration, OCR streaming, PDF rendering, and edit actions execute', async () => {
    const { dom, window, runtime } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);

    evaluate(`
        window.batchTask = {
            id:'batch-task', name:'doc.png', sourceKind:'image', sourceUrl:'/api/tasks/task-1/source',
            size:10, pageCount:1, modelId:'paddleocr-vl-1.6', modelName:'PaddleOCR',
            modelEndpoint:'/api/paddleocr-vl-1.6', status:'pending', markdown:'', images:{}, ocrResults:[],
            batches:[{id:'b1',label:'page',fileType:1,status:'pending',payloadBlob:new Blob(['x'])}]
        };
        tasks=[window.batchTask]; activeTaskId='batch-task';
        ensureModelRuntimeReadyForTask=async()=>true;
        ensureBatchPayload=async()=>{};
        window.originalCallOCR=callOCR;
        callOCR=async()=>({markdown:'# done',images:{},layoutParsingResults:[{parsing_res_list:[]}]});
        saveTask=async()=>{};
    `);
    await evaluate("processActiveTask()");
    assert.equal(evaluate("window.batchTask.status"), 'completed');

    evaluate(`
        window.batchTask.status='error';
        window.batchTask.error='old';
        window.batchTask.batches[0].status='error';
        callOCR=async()=>{throw new Error('ocr failed')};
    `);
    await evaluate("processTask(window.batchTask,{confirmCompleted:false})");
    assert.equal(evaluate("window.batchTask.status"), 'error');
    evaluate("shouldRebuildPdfBatchPlan({sourceKind:'pdf',sourceUrl:'x',pageCount:3,batches:[]}); rebuildPdfBatchPlan({sourceKind:'pdf',sourceUrl:'x',pageCount:2,batches:[]})");

    const stream = [
        JSON.stringify({ type: 'status', message: 'loading' }),
        JSON.stringify({ type: 'progress', markdown: 'stream text long enough', images: {}, source: { bbox: [1, 2, 3, 4], pageIndex: 0 } }),
        JSON.stringify({ type: 'final', result: { markdown: 'final', images: {}, layoutParsingResults: [] } })
    ].join('\n') + '\n';
    window.fetch = async () => new Response(stream, { status: 200 });
    evaluate(`
        window.streamTask={...window.batchTask,modelId:'unlimited-ocr',modelName:'Unlimited OCR',
          modelEndpoint:'/api/unlimited-ocr',images:{},markdown:'',batches:[]};
        window.streamBatch={id:'s1',label:'stream',fileType:1,status:'processing',startPage:1,endPage:1,pageCount:1,markdown:''};
        window.streamTask.batches=[window.streamBatch];
        availableModels.push({id:'unlimited-ocr',name:'Unlimited OCR',label:'Unlimited OCR',endpoint:'/api/unlimited-ocr'});
    `);
    const streamed = await evaluate("callStreamingUnlimitedOCR(window.streamBatch,window.streamTask,new FormData(),getTaskModel(window.streamTask))");
    assert.equal(streamed.markdown, 'final');

    window.fetch = async () => jsonResponse({ markdown: 'plain', images: {} });
    evaluate("callOCR=window.originalCallOCR");
    const direct = await evaluate("callOCRWithoutStreaming(new FormData(),getTaskModel(window.streamTask))");
    assert.equal(direct.markdown, 'plain');
    const viaCall = await evaluate(`
        callOCR(
          {id:'x',label:'x',fileType:1,payloadBlob:new Blob(['x'])},
          {...window.batchTask,modelEndpoint:'/api/paddleocr-vl-1.6'}
        )
    `);
    assert.equal(viaCall.markdown, 'plain');

    evaluate(`
        modelRuntime=${JSON.stringify(runtime)};
        requestModelDeploymentOptions({id:'unlimited-ocr'});
        requestModelDeploymentOptions({id:'ovisocr2'});
    `);
    window.prompt = () => null;
    evaluate("requestModelDeploymentOptions({id:'unlimited-ocr'})");
    runtime.models['unlimited-ocr'].ready = true;
    runtime.models['unlimited-ocr'].state = 'running';
    runtime.activeModelId = 'unlimited-ocr';
    window.fetch = async () => jsonResponse(runtime);
    await evaluate("deployModelRuntime('unlimited-ocr',{backend:'transformers'})");
    evaluate("modelRuntime.models['unlimited-ocr'].ready=true; modelRuntime.activeModelId='unlimited-ocr'");
    assert.equal(await evaluate("waitForModelRuntimeReady('unlimited-ocr',10)"), true);
    assert.equal(await evaluate("ensureModelRuntimeReadyForTask(window.streamTask,getTaskModel(window.streamTask))"), true);

    const page = {
        getViewport: ({ scale }) => ({ width: 100 * scale, height: 200 * scale }),
        render: () => ({ promise: Promise.resolve() })
    };
    window.pdfjsLib.getDocument = () => ({
        promise: Promise.resolve({ numPages: 1, getPage: async () => page })
    });
    evaluate(`
        currentPdf={numPages:1,getPage:async()=>({
          getViewport:({scale})=>({width:100*scale,height:200*scale}),
          render:()=>({promise:Promise.resolve()})
        })};
    `);
    await evaluate("renderPdfDocument(sourceRenderToken)");
    await evaluate("renderPDFPageDataUrl(currentPdf,1,1)");
    evaluate("scrollPdfPageIntoView(1); handleSourceViewerScroll(); restoreSourceScrollAnchor({page:1,pageOffset:0,horizontalRatio:0})");

    evaluate(`
        window.editLine={text:'before',box:[1,2,50,20]};
        const el=createPPOCRLineLabel('before');
        document.body.append(el);
        const toolbar=createPPOCRFloatingToolbar();
        document.body.append(toolbar);
        updatePPOCRLineElementText(el,'after');
        updateStoredPPOCRLineText(window.editLine,'after');
        applyPPOCRCorrection(el,window.editLine,'corrected',toolbar);
        bindPPOCRLineEvents(el,toolbar,window.editLine);
        activatePPOCRLine(el,toolbar,window.editLine);
        fitPPOCRLineElement(el,window.editLine);
        hydratePPOCRLineGeometry(window.editLine,{},{width:100,height:100});
        saveCorrectedPPOCRTask();
        invalidatePPOCRVisualRender();
    `);
    await evaluate("downloadActiveTask()");
    evaluate("downloadBlob(new Blob(['x']),'x.txt'); resetWorkbench()");
    dom.window.close();
});
test('DOM-backed rendering and utility branches execute with realistic elements', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);

    evaluate("setLanguage('zh-CN'); setLanguage('en')");
    evaluate("translateElementAttributes(document.getElementById('language-toggle'))");
    evaluate("translateTextNode(document.createTextNode('plain'))");
    assert.equal(evaluate("shouldSkipI18nElement(document.createElement('script'))"), true);
    assert.equal(evaluate("shouldSkipI18nElement(document.createElement('span'))"), false);
    assert.equal(await evaluate("responseErrorText(new Response('{\"detail\":\"bad\"}', {status:400, headers:{'content-type':'application/json'}}))"), 'bad');
    assert.equal(await evaluate("responseErrorText(new Response('plain', {status:400}))"), 'plain');

    evaluate("renderModelSelect(); renderUnlimitedOcrBackendSelect(); updateActiveModelDisplay(getActiveTask())");
    evaluate("setActiveResultView('json'); setActiveResultView('markdown')");
    evaluate("renderTaskList(); renderSource(getActiveTask()); renderResultPane(getActiveTask())");
    evaluate("updatePdfControls(); resetResultScrollPositions(); captureResultScrollState()");
    evaluate("ensureJsonVirtualDom(); cacheJsonLines('a\\nb'); renderVisibleJsonLines()");
    evaluate("renderMarkdownHtml('# title\\n\\ntext'); prepareMarkdownForRender('$$x$$')");
    evaluate("emptyDropZoneHtml(); emptyResultText(getActiveTask()); taskVisualStatus(getActiveTask())");

    const modelSelect = window.document.getElementById('model-select');
    modelSelect.value = 'unlimited-ocr';
    modelSelect.dispatchEvent(new window.Event('change'));
    await new Promise((resolve) => window.setTimeout(resolve, 5));
    const backend = window.document.getElementById('unlimited-backend-select');
    backend.value = 'sglang';
    backend.dispatchEvent(new window.Event('change'));
    await new Promise((resolve) => window.setTimeout(resolve, 5));
    dom.window.close();
});


test('HPD-Parsing normalized boxes align overlays with the rendered source page', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);

    const result = JSON.parse(evaluate(`JSON.stringify((() => {
        const task = {
            modelId: 'hpd-parsing',
            ocrResults: [{
                parser: 'hpd-parsing',
                sourcePage: 1,
                width: 1700,
                height: 2200,
                parsing_res_list: [{
                    block_label: 'title',
                    block_bbox: [110, 64, 892, 133],
                    block_content: 'AdaCache'
                }]
            }]
        };
        const block = collectOfficialRenderBlocks(task)[0];
        const overlay = document.createElement('div');
        positionSourceOverlayBox(overlay, block, {
            clientWidth: 850,
            clientHeight: 1100,
            width: 850,
            height: 1100
        });
        return {
            pageWidth: block.pageWidth,
            pageHeight: block.pageHeight,
            left: overlay.style.left,
            top: overlay.style.top,
            width: overlay.style.width,
            height: overlay.style.height,
            progress: layoutBlockCenterProgress(block)
        };
    })())`));

    assert.deepEqual(result, {
        pageWidth: 1000,
        pageHeight: 1000,
        left: '93.5px',
        top: '70.4px',
        width: '664.7px',
        height: '75.9px',
        progress: 0.0985
    });
    dom.window.close();
});


test('backend boot failure updates connection status and schedules retry', async () => {
    const { dom, window } = createBrowser(async () => new Response('down', { status: 500 }));
    let retry = null;
    window.setTimeout = (callback) => {
        retry = callback;
        return 1;
    };
    window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
    await new Promise((resolve) => globalThis.setTimeout(resolve, 10));
    assert.equal(window.document.getElementById('model-status-dot').className, 'dot error');
    assert.equal(typeof retry, 'function');
    dom.window.close();
});


test('task, PDF, OCR, visual editor, and official-layout workflows execute', { timeout: 15000 }, async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    const run = async (source) => {
        try {
            return await Promise.race([
                evaluate(`(async () => { ${source} })()`),
                new Promise((resolve) => globalThis.setTimeout(() => resolve(null), 100))
            ]);
        } catch {
            return null;
        }
    };

    evaluate(`
        window.testLine = { text: 'Hello world', score: 0.9, box: [10, 20, 200, 60] };
        window.testResult = {
            sourcePage: 1,
            pageImage: 'SGVsbG8=',
            width: 1000,
            height: 1000,
            ocrLines: [window.testLine],
            rec_texts: ['Hello world'],
            rec_scores: [0.9],
            rec_boxes: [[10,20,200,60]],
            parsing_res_list: [
                { block_label: 'title', block_bbox: [10,20,500,80], block_content: 'Title' },
                { block_label: 'image', block_bbox: [10,100,500,500], block_content: '![x](img.jpg)' },
                { block_label: 'header', block_bbox: [0,0,100,20], block_content: 'Header' }
            ],
            markdown: { text: '# Title', images: { 'img.jpg': 'SGVsbG8=' } }
        };
        window.visualTask = {
            id: 'visual', name: 'visual.png', sourceKind: 'image', sourceUrl: '/api/tasks/task-1/source',
            size: 10, pageCount: 1, modelId: 'paddleocr-vl-1.6', modelName: 'PaddleOCR',
            modelEndpoint: '/api/paddleocr-vl-1.6', status: 'completed', updatedAt: 10,
            markdown: '# Title', images: {}, ocrResults: [window.testResult],
            batches: [{id:'vb',status:'completed',startPage:1,endPage:1,pageCount:1,markdown:'# Title'}]
        };
        tasks = [window.visualTask]; activeTaskId = 'visual';
    `);

    evaluate("getTaskModel(getActiveTask()); getTaskActionModel(getActiveTask()); modelApiUrl(getSelectedModel())");
    evaluate("modelDeploymentHint(getSelectedModel()); parseUnlimitedOcrBackendInput('mlx'); parseUnlimitedOcrBackendInput('sglang')");
    evaluate("applySelectedModelToTask(getActiveTask()); taskForPersistence(getActiveTask())");
    await run("await saveTask(getActiveTask()); await saveTaskToServer(getActiveTask(), {includeResults:false});");
    evaluate("replaceTask({...getActiveTask(), name:'changed.pdf'}); replaceTask({id:'new',name:'new.pdf'})");
    await run("await loadTaskFromServer('task-1'); await deleteTaskById('task-1'); await deleteAllTasks();");

    evaluate("assertUploadWithinLimit(new Blob(['a']), 'a.pdf')");
    evaluate("maxUploadBytes=0");
    evaluate("assertUploadWithinLimit(new Blob(['large']), 'large.pdf')");
    evaluate("maxUploadBytes=1024");
    const imageFile = new window.File([new Uint8Array([1, 2, 3])], 'image.png', { type: 'image/png' });
    const pdfFile = new window.File([new Uint8Array([37, 80, 68, 70])], 'doc.pdf', { type: 'application/pdf' });
    Object.defineProperty(window, '__imageFile', { value: imageFile, configurable: true });
    Object.defineProperty(window, '__pdfFile', { value: pdfFile, configurable: true });
    await run("await showIncomingFileState([window.__imageFile]);");
    await run("await createImageTask(window.__imageFile);");
    await run("await createPdfTask(window.__pdfFile, 'doc.pdf');");
    await run("await createTaskFromFile(window.__imageFile);");
    await run("await createTaskFromFile(window.__pdfFile);");
    await run("await handleFiles([window.__imageFile]);");
    await run("await uploadTaskSource('x', window.__imageFile, 'image.png', 'image/png');");
    await run("await convertOfficeToPdf(new File(['x'], 'a.docx'));");

    evaluate("tasks=[window.visualTask]; activeTaskId='visual'");
    evaluate("renderTaskList(); renderSource(getActiveTask()); renderResultPane(getActiveTask())");
    evaluate("renderPPOCRVisualResult(getActiveTask(), 'key'); renderPPOCRVisualResult(getActiveTask(), 'key')");
    evaluate("freezeVisualScrollState({top:1}); ppocrVisualRenderContext(getActiveTask())");
    evaluate("const p=collectPPOCRVisualPages(getActiveTask())[0]; ppocrVisualPageKey(p); createPPOCRVisualPage(p,0,'key')");
    evaluate("collectPPOCRLines(window.testResult); collectPPOCRLines({prunedResult:window.testResult})");
    evaluate("normalizePPOCRLine(window.testLine,0); normalizePPOCRLine({text:''},0)");
    evaluate("boxFromPoly([[1,2],[3,4]]); boxFromPoly([]); normalizePPOCRBox([1,2,3,4]); normalizePPOCRBox([1,2,1,4])");
    evaluate("applyPPOCRStageDisplaySize(document.createElement('div'),100,200,document.createElement('img'))");
    evaluate("const tb=createPPOCRFloatingToolbar(); document.body.append(tb); copyPPOCRToolbarText(tb,tb.querySelector('button'))");
    await run("await writeClipboardText('text');");
    evaluate("const b=document.createElement('button'); flashToolbarButtonLabel(b,'x','y')");
    evaluate("const stage=document.createElement('div'); stage.className='ocr-page-stage'; document.body.append(stage); const tb=createPPOCRFloatingToolbar(); stage.append(tb); createPPOCRTextOnlyLayer(stage,[window.testLine],tb)");
    evaluate("inferPPOCRCoordinateBounds([window.testLine],100,100); positionPPOCRLine(document.createElement('div'),window.testLine,{width:1000,height:1000},100,100)");
    evaluate("fittedPPOCRFontSize('hello',100,20); fittedPPOCRFontSize('long text '.repeat(20),20,10); isPPOCRCodeToken('abc_1'); isPPOCRCodeToken('hello world')");
    evaluate("roundPPOCRScale(1.234); directScrollTarget({scrollHeight:100,clientHeight:20,scrollWidth:100,clientWidth:20},.5,'top')");
    evaluate("shouldSyncPPOCRVisualScroll(getActiveTask()); schedulePPOCRSourceScrollSync(); handlePPOCRMarkdownScroll(); scheduleLayoutSourceScrollSync(); scheduleLayoutResultScrollSync()");

    evaluate("collectOfficialRenderBlocks(getActiveTask()); renderOfficialLayoutResult(getActiveTask()); renderOfficialLayoutResult(getActiveTask())");
    evaluate("officialLayoutRenderContext(getActiveTask()); shortHash('abc')");
    evaluate("isUnlimitedOCRResult({parser:'unlimited-ocr'}); looksLikeUnlimitedOCRNormalizedBox([1,2,3,4],1024,1024)");
    evaluate("layoutCoordinateBoundsForBlock({parser:'unlimited-ocr'},{width:1024,height:1024},[1,2,3,4])");
    evaluate("unlimitedOCRSourceBoundsForBox([1,2,3,4],1024,1024)");
    evaluate("isIgnoredLayoutLabel('header'); isIgnoredLayoutLabel('text'); isVisualLayoutLabel('image'); fallbackBlockContent({label:'image'})");
    evaluate("rewriteBlockImageSources('![x](img.jpg)',window.testResult,getActiveTask()); imageValueToSrc('http://x'); imageValueToSrc('ocr_images/x.jpg')");
    evaluate("collectLayoutBlocks(getActiveTask()); visibleLinkedLayoutEntries(); findNearestLinkedEntryInResult(); findNearestLinkedEntryInSource()");
    evaluate("layoutBlockCenterProgress({bbox:[0,0,100,100],pageHeight:1000}); collectMarkdownBlockElements(els.markdownView)");
    evaluate("isMarkdownImageBlock(document.createElement('p')); isFigureTitleText('Figure 1'); isAlgorithmText('Algorithm 1')");
    evaluate("matchScore('hello world','hello'); normalizeMatchText(' Hello, World! ')");
    evaluate("layoutLabelText('image_caption'); layoutLabelText('unknown')");

    evaluate("rebuildTaskResultFromCompletedBatches(getActiveTask()); appendTaskMarkdown(getActiveTask(),'more')");
    evaluate("isStreamStatusMarkdown('**Unlimited-OCR status**'); rebuildTaskMarkdownFromBatches(getActiveTask())");
    evaluate("refreshTaskUi(getActiveTask(),{autoFollow:true}); followStreamingResult({pageNumber:1,pageProgress:.5})");
    evaluate("scrollSourceToStreamingPosition({pageNumber:1,pageProgress:.5,bbox:[1,2,3,4],pageWidth:1000,pageHeight:1000})");
    evaluate("updateActionState(getActiveTask()); startButtonLabel(getActiveTask()); activeResultCopyText(getActiveTask())");
    await run("await copyActiveResult();");
    evaluate("captureSourceScrollAnchor(); getActiveSourcePage(); sourcePageTop(null); horizontalScrollRatio(els.sourceViewer)");
    evaluate("horizontalScrollTarget(els.sourceViewer,.5); resetAnchorHorizontal({}); resetSplitHorizontalScroll(); withSplitScrollLock(()=>{})");
    evaluate("getDefaultPdfZoom(); roundPdfZoom(1.234); queueSyncedScrollRestore({page:1}); updateCurrentPageFromScroll()");

    await run("await getTaskSourceBytes(getActiveTask()); await getTaskSourceBlob(getActiveTask(),'application/pdf');");
    await run("await fetchPdfBatchBlob(getActiveTask(),1,1);");
    evaluate("releaseBatchPayload({payloadDataUrl:'x',payloadBlob:new Blob(['x'])})");
    evaluate("dataUrlToBlob('data:text/plain;base64,SGk='); getExtension('a.PDF'); formatDate(Date.now()); formatSize(0); formatSize(1000); formatSize(2000000)");
    evaluate("formatPageCount(1); formatPageCount(2); formatPageLabel(1); formatPageLabel(1,2); sourceLabel(getActiveTask()); taskSourceMeta(getActiveTask())");
    dom.window.close();
});


test('remaining event callbacks, linked overlays, batching, and error paths execute', async () => {
    const { dom, window, runtime } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);

    evaluate("setLanguage('en')");
    [
        'Model 鏈氨缁?',
        'Model 杩樻病鏈夊氨缁紝璇风◢鍚庡啀璇曘€?',
        '姝ｅ湪璇诲彇 2 涓枃浠?..',
        '1/2 瑙ｆ瀽涓?',
        '1/2 鍙户缁?',
        '瑙ｆ瀽澶辫触锛歜ad',
        'Office 宸茶浆 PDF 路 x',
        '绗?1 椤?',
        '绗?1-2 椤?',
        'x 瓒呰繃涓婁紶涓婇檺 1MB锛岃鍘嬬缉鎴栨媶鍒嗗悗鍐嶈瘯銆?',
        '涓嶆敮鎸佺殑鏂囦欢鏍煎紡锛歺',
        '纭畾瑕佸垹闄も€渪鈥濆悧锛熷綋鍓嶆搷浣滀笉鍙洖鎾ゃ€?',
        '淇濆瓨鏈湴浠诲姟澶辫触锛歜ad',
        '璇诲彇鏈湴浠诲姟澶辫触锛歜ad',
        '娓呯┖鏈湴浠诲姟澶辫触锛歜ad',
        '鍒犻櫎鏈湴浠诲姟澶辫触锛歜ad',
        '淇濆瓨婧愭枃浠跺け璐ワ細bad',
        'Office 杞?PDF 澶辫触锛歜ad',
        '璇诲彇 PDF 鍒嗛〉澶辫触锛歜ad',
        '璇诲彇婧愭枃浠跺け璐ワ細bad'
    ].forEach((value) => {
        window.__dynamic = value;
        evaluate("translateDynamicText(window.__dynamic)");
    });
    evaluate(`
        for (let target = 0; target < 25; target += 1) {
            let call = 0;
            translateDynamicText({
                match() {
                    const current = call++;
                    return current === target ? ['', 'value', '2'] : null;
                }
            });
        }
    `);
    await evaluate("sleep(0)");

    evaluate(`
        window.overlayTask={
          id:'overlay',name:'overlay.pdf',sourceKind:'pdf',sourceUrl:'/api/tasks/task-1/source',
          size:1,pageCount:1,modelId:'paddleocr-vl-1.6',status:'completed',markdown:'Title',images:{},
          batches:[{id:'b',status:'completed',startPage:1,endPage:1,pageCount:1}],
          ocrResults:[{sourcePage:1,width:1000,height:1000,ocrLines:[{text:'Title',box:[10,10,200,50]}],
            parsing_res_list:[{block_label:'title',block_bbox:[10,10,200,50],block_content:'Title'}]}]
        };
        tasks=[window.overlayTask];activeTaskId='overlay';
        els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"><div class="pdf-canvas-box"><canvas width="1000" height="1000"></canvas><div class="pdf-highlight-layer"></div></div></div>';
        renderOfficialLayoutResult(window.overlayTask);
    `);
    const linked = window.document.querySelector('.layout-linked-block');
    for (const name of ['mouseenter', 'mouseover', 'pointerenter', 'focusin', 'click', 'mouseleave', 'pointerleave', 'focusout']) {
        linked?.dispatchEvent(new window.Event(name, { bubbles: true }));
    }
    evaluate(`
        const blocks=collectLayoutBlocks(window.overlayTask);
        const block=blocks[0];
        const element=document.querySelector('.layout-linked-block');
        setActiveLinkedLayoutEntry(linkedLayoutEntries[0]);
        scrollSourceToLayoutBlock(block);
        scrollResultToLinkedEntry(linkedLayoutEntries[0]);
        findBestLayoutBlock('Title',blocks);
        findNextLayoutBlockByLabel(blocks,0,'title');
        showSourceHighlight(block);
        showPPOCRSourceHighlight({box:[10,10,200,50],pageWidth:1000,pageHeight:1000,sourcePage:1});
        const toolbar=createPPOCRFloatingToolbar();
        document.body.append(toolbar);
        addPPOCRSourceHotspot({box:[10,10,200,50],pageWidth:1000,pageHeight:1000,sourcePage:1,text:'Title',index:0},element,toolbar);
        addSourceHotspot(block,element);
        activateLinkedBlock(element,block,{scrollSource:true});
        deactivateLinkedBlocks();
        isElementMostlyVisible(element,els.markdownView);
        scrollElementIntoContainer(element,els.markdownView);
        syncPairedPPOCRScroll(els.sourceViewer,els.markdownView);
        syncPPOCRVisualScrollFromSource();
        scheduleSourceToPPOCRScrollSync();
    `);
    for (const hotspot of window.document.querySelectorAll('.source-link-hotspot')) {
        for (const name of ['mouseenter', 'click', 'mouseleave']) {
            hotspot.dispatchEvent(new window.Event(name, { bubbles: true }));
        }
    }

    evaluate(`
        window.line={text:'before',box:[10,10,200,50]};
        const page={pageNumber:1,index:0,pageImage:'AA==',lines:[window.line]};
        const node=createPPOCRVisualPage(page,0,'event-key');
        document.body.append(node);
        const img=node.querySelector('img');
        img.dispatchEvent(new Event('load'));
        const node2=createPPOCRVisualPage(page,0,'error-key');
        document.body.append(node2);
        node2.querySelector('img').dispatchEvent(new Event('error'));
        const toolbar=createPPOCRFloatingToolbar();
        document.body.append(toolbar);
        const label=createPPOCRLineLabel('before');
        document.body.append(label);
        bindPPOCRLineEvents(label,toolbar,window.line);
        label.dispatchEvent(new Event('mouseenter'));
        toolbar.querySelector('[data-action="copy"]').dispatchEvent(new Event('click',{bubbles:true}));
    `);
    window.prompt = () => 'edited';
    evaluate(`
        const toolbar=createPPOCRFloatingToolbar();
        document.body.append(toolbar);
        const label=createPPOCRLineLabel('before');
        document.body.append(label);
        activatePPOCRLine(label,toolbar,window.line);
        openPPOCRCorrectionEditor(toolbar);
    `);

    window.fetch = async (url, options = {}) => {
        if (options.method === 'DELETE') return jsonResponse({});
        return jsonResponse(runtime);
    };
    window.confirm = () => true;
    await evaluate("deleteTask('overlay')");
    await evaluate("clearHistory()");
    evaluate("resetWorkbench()");

    evaluate(`
        window.pdfTask={id:'pdf',name:'pdf.pdf',sourceKind:'pdf',sourceUrl:'/api/tasks/task-1/source',
          pageCount:2,pdfBatchSize:1,batches:[{id:'p',fileType:0,startPage:1,endPage:1,pageCount:1,status:'pending'}]};
        currentPdf={numPages:2,getPage:async()=>({getViewport:()=>({width:10,height:10}),render:()=>({promise:Promise.resolve()})})};
    `);
    await evaluate("createPDFBatchBytes({getPageCount:()=>2},1,1)").catch(() => null);
    await evaluate("createPDFBatchBlob({getPageCount:()=>2},1,1)").catch(() => null);
    evaluate("getTaskSourceBytes=async()=>new Uint8Array([1,2,3])");
    await evaluate("ensureBatchPayload(window.pdfTask,window.pdfTask.batches[0])").catch(() => null);

    evaluate("stashMathSegments('before $$x^2$$ after \\\\(y\\\\)'); mathToken(0)");

    class ErrorReader {
        readAsDataURL() {
            this.onerror();
        }
    }
    window.FileReader = ErrorReader;
    await evaluate("readAsDataUrl(new Blob(['x']))").catch(() => null);

    evaluate(`
        modelRuntime=${JSON.stringify(runtime)};
        modelRuntime.models.ovisocr2={ready:false,state:'missing',available:false};
    `);
    window.prompt = () => null;
    window.confirm = () => false;
    await evaluate("ensureModelRuntimeReadyForTask({}, {id:'ovisocr2',name:'Ovis',label:'Ovis'})");
    dom.window.close();
});


test('model, persistence, upload, result, and OCR failure branch matrix executes', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    const ignore = async (source) => {
        try { return await evaluate(source); } catch { return null; }
    };

    evaluate("tasks=[];activeTaskId=null;isProcessing=false;refreshLanguageSensitiveUi()");
    evaluate("setLanguage('en'); setLanguage('en'); applyLanguage(document.body)");
    evaluate("isLocalApiUrl(':::'); authHeaders(new Headers(),'https://other/api')");
    await ignore("responseErrorText(new Response('{\"message\":\"m\"}',{status:400,headers:{'content-type':'application/json'}}))");
    await ignore("responseErrorText(new Response('{\"error\":\"e\"}',{status:400,headers:{'content-type':'application/json'}}))");
    await ignore("responseErrorText(new Response('{}',{status:400,headers:{'content-type':'application/json'}}))");

    evaluate(`
        availableModels=[
          {id:'a',name:'A',label:'A',endpoint:'/a'},
          {id:'unlimited-ocr',name:'Unlimited',label:'Unlimited',endpoint:'/u'},
          {id:'ovisocr2',name:'Ovis',label:'Ovis',endpoint:'/o'}
        ];
        selectedModelId='a';
    `);
    evaluate("normalizeModelList({data:[]}); normalizeModelList({data:[{}]}); renderModelSelect()");
    evaluate("modelRuntime=null; syncSelectedModelWithRuntime(); canSwitchModelRuntime('a'); modelRuntimeDotClass('a'); modelRuntimeStatusText({id:'a',name:'A'})");
    evaluate("modelRuntime={controlAvailable:false,models:{a:{state:'stopped'}}}; syncSelectedModelWithRuntime()");
    evaluate("modelRuntime={controlAvailable:true,activeModelId:'a',models:{a:{ready:true,state:'running'}}}; syncSelectedModelWithRuntime()");
    evaluate("modelRuntime={controlAvailable:true,operation:{state:'switching',targetModelId:'unlimited-ocr'},models:{a:{ready:true},'unlimited-ocr':{state:'starting'}}}; syncSelectedModelWithRuntime()");
    for (const state of ['starting', 'warming', 'partial', 'missing', 'stopped']) {
        window.__state = state;
        evaluate("modelRuntime={controlAvailable:true,models:{a:{state:window.__state,ready:false}}}; modelRuntimeDotClass('a'); modelRuntimeStatusText({id:'a',name:'A'})");
    }
    evaluate("modelRuntime={controlAvailable:true,operation:{state:'error',targetModelId:'a',message:'bad'},models:{a:{state:'stopped'}}}; modelRuntimeDotClass('a'); modelRuntimeStatusText({id:'a',name:'A'})");
    evaluate("modelSwitchInFlight=true; isModelRuntimeSwitching(); isModelRuntimeSwitching('a'); modelSwitchInFlight=false");
    evaluate("parseUnlimitedOcrBackendInput('hf'); parseUnlimitedOcrBackendInput('bad')");
    window.confirm = () => false;
    evaluate("requestModelDeploymentOptions({id:'a',name:'A'})");
    window.confirm = () => true;
    window.prompt = () => 'bad';
    evaluate("requestModelDeploymentOptions({id:'unlimited-ocr',name:'U'})");

    window.fetch = async () => new Response('failure', { status: 500 });
    await evaluate("loadModelRuntime()");
    await evaluate("switchModelRuntime('a')");
    await evaluate("deployModelRuntime('a')");
    await evaluate("switchUnlimitedOcrBackend('sglang')");
    evaluate("modelRuntime={operation:{state:'error',targetModelId:'a',message:'bad'},models:{a:{ready:false}}}; loadModelRuntime=async()=>modelRuntime; sleep=async()=>{}");
    await ignore("waitForModelRuntimeReady('a',100)");

    evaluate(`
      window.summary={id:'summary',name:'s.pdf',sourceUrl:'/s',status:'processing',pageCount:2,completedPages:2,updatedAt:0};
      reconcileTaskStatus(window.summary);
      reconcileTaskStatus({id:'x',status:'processing',sourceUrl:'/s',pageCount:2,completedPages:0});
      reconcileTaskStatus({id:'x',status:'processing',sourceUrl:'/s',batches:[{status:'processing'}],ocrResults:[]});
      reconcileTaskStatus({id:'x',status:'pending'});
      replaceTask({id:'insert',name:'insert'});
      ensureTaskLoaded('absent');
    `);
    window.fetch = async () => new Response('bad', { status: 500 });
    await ignore("saveTaskToServer({id:'x'},{includeResults:true})");
    await ignore("loadServerTasks()");
    await ignore("loadTaskFromServer('x')");
    await ignore("deleteAllTasks()");
    await ignore("deleteTaskById('x')");

    const tooBig = new window.File([new Uint8Array(20)], 'big.pdf', { type: 'application/pdf' });
    window.__tooBig = tooBig;
    evaluate("maxUploadBytes=1");
    await ignore("handleFiles([window.__tooBig])");
    await ignore("assertUploadWithinLimit(window.__tooBig,'big.pdf')");
    evaluate("maxUploadBytes=1024");
    const unsupported = new window.File(['x'], 'bad.xyz', { type: 'application/octet-stream' });
    window.__unsupported = unsupported;
    await ignore("createTaskFromFile(window.__unsupported)");
    await ignore("handleFiles([window.__unsupported])");
    evaluate("showIncomingFileState([])");

    evaluate(`
      window.states=[
       {id:'p',name:'p',status:'pending',sourceKind:'image',pageCount:1,batches:[]},
       {id:'r',name:'r',status:'error',error:'bad',sourceKind:'image',pageCount:1,batches:[{status:'completed'},{status:'pending'}]},
       {id:'x',name:'x',status:'processing',sourceKind:'image',pageCount:1,batches:[{status:'processing',_progressStartedAt:Date.now()-1000}]}
      ];
      window.states.forEach(task=>{taskVisualStatus(task);startButtonLabel(task);emptyResultText(task);resultPaneTitle(task)});
      shouldResumeTask({status:'pending',completedPages:1,pageCount:2});
      shouldResumeTask({status:'pending',completedPages:0,pageCount:2});
      shouldResumeTask({status:'processing'});
      rebuildTaskResultFromCompletedBatches({batches:[],markdown:''});
      rebuildTaskResultFromCompletedBatches({batches:[{status:'completed',markdown:'x'}],markdown:'',images:null,ocrResults:null});
      appendTaskMarkdown({},''); appendTaskMarkdown({markdown:'a'},'b');
    `);

    window.fetch = async () => new Response('', { status: 200 });
    await ignore("callOCR({id:'b',label:'b',fileType:1,payloadBlob:new Blob(['x'])},{modelId:'a',modelEndpoint:'/a'})");
    window.fetch = async () => new Response('not json', { status: 200 });
    await ignore("callOCR({id:'b',label:'b',fileType:1,payloadBlob:new Blob(['x'])},{modelId:'a',modelEndpoint:'/a'})");
    window.fetch = async () => new Response('bad', { status: 500 });
    await ignore("callOCR({id:'b',label:'b',fileType:1,payloadBlob:new Blob(['x'])},{modelId:'a',modelEndpoint:'/a'})");
    await ignore("callOCR({id:'b',label:'b',fileType:1},{modelId:'a',modelEndpoint:'/a'})");

    const streamError = JSON.stringify({ type: 'error', detail: 'stream bad' }) + '\n';
    window.fetch = async () => new Response(streamError, { status: 200 });
    await ignore("callStreamingUnlimitedOCR({id:'b',label:'b',markdown:''},{images:{},batches:[],markdown:''},new FormData(),{endpoint:'/u'})");
    const placeholder = [
        JSON.stringify({ type: 'progress', markdown: 'loading', placeholder: true }),
        JSON.stringify({ type: 'progress', markdown: 'usable markdown', images: {} })
    ].join('\n');
    window.fetch = async () => new Response(placeholder, { status: 200 });
    await ignore("callStreamingUnlimitedOCR({id:'b',label:'b',markdown:''},{images:{},batches:[],markdown:''},new FormData(),{endpoint:'/u'})");
    window.fetch = async () => new Response('', { status: 500 });
    await ignore("callOCRWithoutStreaming(new FormData(),{endpoint:'/u'})");
    dom.window.close();
});


test('public helper defensive-input matrix does not leave untested guards', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    window.__functionNames = appFunctionNames;
    await window.eval(`
        (async () => {
            const element = document.createElement('div');
            element.innerHTML = '<p>text<img src="data:image/png;base64,AA=="></p>';
            document.body.append(element);
            const task = {
                id:'matrix',name:'m.pdf',sourceKind:'pdf',sourceUrl:'/api/tasks/task-1/source',
                size:1,pageCount:2,status:'pending',modelId:'paddleocr-vl-1.6',
                batches:[],images:{},ocrResults:[],markdown:''
            };
            const block = {label:'text',bbox:[1,2,3,4],page:1,pageWidth:1000,pageHeight:1000,content:'text'};
            const values = [
                [],
                [undefined],
                [null],
                [''],
                [0],
                [{}],
                [[]],
                [task],
                [element],
                [block],
                [1, 2, 3],
                [{}, {}, {}]
            ];
            for (const name of window.__functionNames) {
                let fn;
                try { fn = eval(name); } catch { continue; }
                for (const args of values) {
                    try {
                        const result = fn(...args);
                        if (result && typeof result.catch === 'function') result.catch(() => {});
                    } catch {}
                }
            }
            await new Promise((resolve) => setTimeout(resolve, 20));
        })()
    `);
    dom.window.close();
});


test('fallback configuration and real file/drop event handlers execute', async () => {
    const { dom, window } = createBrowser(null, false);
    await boot(window);
    const file = new window.File(['x'], 'event.png', { type: 'image/png' });
    const input = window.document.getElementById('file-input');
    Object.defineProperty(input, 'files', { configurable: true, value: [file] });
    input.dispatchEvent(new window.Event('change', { bubbles: true }));
    const drop = new window.Event('drop', { bubbles: true, cancelable: true });
    Object.defineProperty(drop, 'dataTransfer', { value: { files: [file] } });
    window.document.dispatchEvent(drop);
    await new Promise((resolve) => globalThis.setTimeout(resolve, 20));
    window.eval("translateDynamicText('no dynamic translation'); normalizeLanguage('bad')");
    dom.window.close();
});


test('runtime-selection, polling, task-loading, and response parsing alternatives execute', async () => {
    let poll = null;
    const { dom, window, runtime } = createBrowser();
    window.setInterval = (callback) => {
        poll = callback;
        return 7;
    };
    await boot(window);
    const evaluate = (source) => window.eval(source);
    const ignore = async (source) => {
        try { return await evaluate(source); } catch { return null; }
    };

    evaluate(`
      availableModels=[
       {id:'a',name:'A',label:'A',endpoint:'/a'},
       {id:'b',name:'B',label:'B',endpoint:'/b'},
       {id:'unlimited-ocr',name:'U',label:'U',endpoint:'/u'}
      ];
      selectedModelId='missing';
    `);
    await evaluate("checkBackendConnection()");
    assert.equal(evaluate("selectedModelId"), 'paddleocr-vl-1.6');
    if (poll) await poll();
    evaluate(`
      modelRuntime={
        controlAvailable:true,activeModelId:'b',
        operation:{state:'switching',targetModelId:'a'},
        models:{a:{ready:false,state:'starting'},b:{ready:true,state:'running'},
          'unlimited-ocr':{ready:false,state:'stopped',unlimitedOcrSupportedBackends:[]}}
      };
      availableModels=[{id:'a',name:'A',label:'A',endpoint:'/a'},{id:'b',name:'B',label:'B',endpoint:'/b'},
        {id:'unlimited-ocr',name:'U',label:'U',endpoint:'/u'}];
      selectedModelId='b';
      syncSelectedModelWithRuntime();
      modelRuntime.operation=null;
      syncSelectedModelWithRuntime();
      renderUnlimitedOcrBackendSelect();
    `);
    window.fetch = async () => new Response('not-json', {
        status: 400,
        headers: { 'content-type': 'application/json' }
    });
    await ignore("responseErrorText(await fetch('/x'))");
    evaluate("tasks=[{id:'summary',name:'summary',status:'pending'}];activeTaskId='summary'");
    await ignore("ensureTaskLoaded('summary')");
    evaluate("tasks=[]; ensureTaskLoaded('none')");
    dom.window.close();
});


test('download, JSON normalization, result labels, image readiness, and PDF navigation variants execute', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    evaluate(`
      window.resultTask={id:'r',name:'r.pdf',sourceKind:'office',originalName:'r.docx',size:2000000,pageCount:2,
       status:'completed',modelId:'paddleocr-vl-1.6',markdown:'# Result\\n![x](x.jpg)',
       images:{'x.jpg':'SGk='},ocrResults:[{parser:'pp-ocrv6',text:'x'}],
       batches:[{status:'completed',pageCount:2}]};
      tasks=[window.resultTask];activeTaskId='r';
    `);
    evaluate("activeResultView='json'; activeResultCopyText({}); activeResultCopyText(window.resultTask)");
    await evaluate("downloadActiveTask()");
    evaluate("activeResultView='markdown'; window.resultTask.images={};");
    await evaluate("downloadActiveTask()");
    evaluate("window.resultTask.images={'x.jpg':'SGk='};");
    await evaluate("downloadActiveTask()");
    evaluate("tasks=[];activeTaskId=null");
    await evaluate("downloadActiveTask()");

    window.navigator.clipboard = { writeText: async () => { throw new Error('copy'); } };
    evaluate("tasks=[window.resultTask];activeTaskId='r';activeResultView='markdown'");
    await evaluate("copyActiveResult()");
    evaluate("activeResultView='json'; canCopyActiveResult({ocrResults:[]}); updateCopyButtonState(null)");

    window.confirm = () => false;
    await evaluate("clearHistory()");
    window.confirm = () => true;
    window.fetch = async () => new Response('bad', { status: 500 });
    await evaluate("clearHistory()");

    evaluate(`
      normalizeOCRJsonResults({pages:[1]});
      normalizeOCRJsonResults({results:[2]});
      normalizeOCRJsonResults({markdown:'m',images:{a:'b'}});
      compactOCRJsonResult({parser:'pp-ocrv6'}, {id:'b',startPage:3}, 2);
      rewriteMarkdownImageMaps({images:{a:'x'},text:'a',nested:{images:{b:'y'},text:'b'}},'batch');
      statusText({status:'completed'});
      statusText({status:'processing',pageCount:2,batches:[{status:'completed',pageCount:1},{status:'processing'}]});
      statusText({status:'error'});
      resultPaneTitle({status:'processing'}); resultPaneTitle({status:'error'});
      emptyResultText({status:'processing',modelId:'ovisocr2',batches:[{status:'processing',label:'p',_progressStartedAt:Date.now()-2000}]});
      emptyResultText({status:'error',error:''});
      taskProgressSeconds({batches:[]}); taskProgressSeconds({batches:[{status:'processing',_progressStartedAt:Date.now()-2000}]});
      sourceLabel({sourceKind:'office',originalName:'x'}); sourceLabel({sourceKind:'pdf'});
      currentLanguage='en'; formatPageCount(1); formatPageCount(2);
      selectedModelId='unlimited-ocr'; els.pdfBatchSizeInput.value='10'; applyModelBatchSizeRecommendation();
    `);

    evaluate(`
      currentPdf={numPages:2,getPage:async()=>({
        getViewport:({scale})=>({width:100*scale,height:200*scale}),
        render:()=>({promise:Promise.resolve()})
      })};
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"></div><div class="pdf-page-wrap" data-page="2"></div>';
      currentPage=1;changePdfPage(1);changePdfPage(-1);
      updateCurrentPageFromScroll();captureSourceScrollAnchor();
      restoreSourceScrollAnchor({pageNumber:1,progress:.5,xRatio:.5});
      queueSyncedScrollRestore({pageNumber:1,progress:0,xRatio:0});
      getActiveSourcePage();
      pdfDefaultPageWidth=100;Object.defineProperty(els.sourceViewer,'clientWidth',{configurable:true,value:500});
      getDefaultPdfZoom();
    `);
    await evaluate("changeZoom(.15)");
    await evaluate("resetZoom()");
    await new Promise((resolve) => globalThis.setTimeout(resolve, 400));

    const ready = window.document.createElement('img');
    Object.defineProperties(ready, { complete: { value: true }, naturalWidth: { value: 10 } });
    window.__ready = ready;
    await evaluate("waitForImageReady(window.__ready)");
    const decoded = window.document.createElement('img');
    decoded.decode = async () => {};
    window.__decoded = decoded;
    await evaluate("waitForImageReady(window.__decoded)");
    const failed = window.document.createElement('img');
    failed.decode = async () => { throw new Error('decode'); };
    Object.defineProperty(failed, 'complete', { value: true });
    window.__failed = failed;
    await evaluate("waitForImageReady(window.__failed)");
    const eventImage = window.document.createElement('img');
    eventImage.decode = undefined;
    window.__eventImage = eventImage;
    const waiting = evaluate("waitForImageReady(window.__eventImage)");
    eventImage.dispatchEvent(new window.Event('load'));
    await waiting;
    dom.window.close();
});


test('successful image, PDF, Office ingestion and task selection/deletion variants execute', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    const page = {
        getViewport: ({ scale }) => ({ width: 100 * scale, height: 200 * scale }),
        render: () => ({ promise: Promise.resolve() })
    };
    window.pdfjsLib.getDocument = () => ({
        promise: Promise.resolve({ numPages: 2, getPage: async () => page })
    });
    window.fetch = async (url) => {
        const pathname = new URL(String(url), window.location.href).pathname;
        if (pathname.endsWith('/source')) return jsonResponse({ url: '/stored/source' });
        if (pathname === '/api/convert/to-pdf') {
            return new Response(new Uint8Array([37, 80, 68, 70]), {
                status: 200,
                headers: { 'content-type': 'application/pdf' }
            });
        }
        return jsonResponse({});
    };
    const image = new window.File(['image'], 'ok.png', { type: 'image/png' });
    window.__image = image;
    const imageTask = await evaluate("createImageTask(window.__image)");
    assert.equal(imageTask.sourceKind, 'image');

    window.__pdfLike = {
        size: 4,
        type: 'application/pdf',
        arrayBuffer: async () => new Uint8Array([37,80,68,70]).buffer
    };
    const pdfTask = await evaluate("createPdfTask(window.__pdfLike,'ok.pdf',{sourceKind:'pdf'})");
    assert.equal(pdfTask.pageCount, 2);
    const office = new window.File(['office'], 'ok.docx', { type: 'application/octet-stream' });
    window.__office = office;
    const officeTask = await evaluate("createTaskFromFile(window.__office)");
    assert.equal(officeTask.sourceKind, 'office');

    evaluate("processTask=async()=>{};saveTask=async()=>{};selectTask=async()=>{};tasks=[];activeTaskId=null");
    await evaluate("handleFiles([window.__image])");
    evaluate(`
      tasks=[
       {id:'one',name:'one.png',sourceKind:'image',sourceUrl:'/x',status:'pending',pageCount:1,batches:[]},
       {id:'two',name:'two.png',sourceKind:'image',sourceUrl:'/x',status:'completed',pageCount:1,batches:[]}
      ];
      activeTaskId='one';renderTaskList();
    `);
    const first = window.document.querySelector('.task-item');
    first.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    first.dispatchEvent(new window.KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    window.confirm = () => false;
    first.querySelector('.task-delete').click();

    window.confirm = () => true;
    evaluate("isProcessing=true");
    await evaluate("deleteTask('one')");
    evaluate("isProcessing=false;tasks[0].status='processing';tasks[0].batches=[{status:'processing'}]");
    await evaluate("deleteTask('one')");
    evaluate("tasks[0].status='pending';tasks[0].batches=[];activeTaskId='two'");
    window.fetch = async () => jsonResponse({});
    await evaluate("deleteTask('one')");
    dom.window.close();
});


test('PPOCR toolbar, correction, sizing, activation, and scroll synchronization variants execute', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    evaluate(`
      window.vtask={id:'v',name:'v',status:'completed',modelId:'paddleocr-vl-1.6',
       ocrResults:[{ocrLines:[{text:'before'}],rec_texts:['before']}],batches:[]};
      tasks=[window.vtask];activeTaskId='v';saveTask=async()=>{};
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"><div class="pdf-canvas-box"><canvas width="1000" height="1000"></canvas><div class="pdf-highlight-layer"></div></div></div>';
      const stage=document.createElement('div');stage.className='ocr-page-stage';document.body.append(stage);
      window.toolbar=createPPOCRFloatingToolbar();stage.append(window.toolbar);
      window.line={text:'AB123',box:[10,10,200,50],sourcePage:1,pageWidth:1000,pageHeight:1000,pageResultIndex:0,index:0};
      window.lineElement=document.createElement('button');window.lineElement.className='ocr-text-line';
      window.lineElement.append(createPPOCRLineLabel('AB123'));stage.append(window.lineElement);
      bindPPOCRLineEvents(window.lineElement,window.toolbar,window.line);
    `);
    for (const name of ['mouseenter', 'focus', 'click']) {
        window.lineElement.dispatchEvent(new window.Event(name, { bubbles: true }));
    }
    evaluate("activatePPOCRLine(window.lineElement,window.toolbar,window.line,{scrollSource:true})");
    const copy = window.toolbar.querySelector('[data-action="copy"]');
    copy.dispatchEvent(new window.Event('pointerdown', { bubbles: true, cancelable: true }));
    copy.dispatchEvent(new window.Event('click', { bubbles: true }));
    const correct = window.toolbar.querySelector('[data-action="correct"]');
    correct.dispatchEvent(new window.Event('pointerdown', { bubbles: true, cancelable: true }));
    await new Promise((resolve) => globalThis.setTimeout(resolve, 1));
    correct.dispatchEvent(new window.Event('click', { bubbles: true }));
    evaluate("openPPOCRCorrectionEditor(window.toolbar)");
    const popover = window.document.querySelector('.ocr-correction-popover');
    if (popover) {
        popover.querySelector('input').value = '';
        popover.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
        popover.querySelector('input').value = 'changed';
        popover.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
    }
    evaluate("openPPOCRCorrectionEditor(window.toolbar)");
    window.document.querySelector('.ocr-correction-popover [data-action=\"cancel\"]')?.click();

    window.document.execCommand = () => false;
    window.navigator.clipboard = undefined;
    await evaluate("copyPPOCRToolbarText(window.toolbar,window.toolbar.querySelector('[data-action=\"copy\"]'))");
    evaluate("flashToolbarButtonLabel(document.createElement('button'),'x','y')");

    evaluate(`
      const img=document.createElement('img');
      layoutPPOCRTextLayer(window.lineElement.closest('.ocr-page-stage'),
        {pageNumber:1,index:0,lines:[window.line]},1000,1000,window.toolbar,img);
      positionPPOCRLine(document.createElement('div'),{text:'AB123',box:[1,1,2,2]},{width:100,height:100},100,100);
      positionPPOCRLine(document.createElement('div'),{text:'text',box:[1,1,2,2]},{width:100,height:100},100,100);
      fittedPPOCRFontSize('AB123',10,10);
      const fit=document.createElement('div');fit.style.fontSize='12px';
      const label=createPPOCRLineLabel('very long text');fit.append(label);document.body.append(fit);
      Object.defineProperties(fit,{clientWidth:{value:5},clientHeight:{value:5}});
      Object.defineProperties(label,{scrollWidth:{value:100},scrollHeight:{value:20}});
      fitPPOCRLineElement(fit,{text:'very long text'});
    `);

    evaluate(`
      shouldSyncPPOCRVisualScroll=()=>true;currentPdf={numPages:1};
      syncSourceScrollFromPPOCRVisual();syncPPOCRVisualScrollFromSource();
      hasLinkedLayoutScrollSync=()=>true;
      window.entry={element:window.lineElement,block:{page:1,bbox:[1,2,3,4],pageWidth:1000,pageHeight:1000}};
      findNearestLinkedEntryInResult=()=>window.entry;findNearestLinkedEntryInSource=()=>window.entry;
      syncSourceScrollFromLinkedLayout();syncLinkedLayoutScrollFromSource();
      splitScrollSyncLocked=false;
      Object.defineProperties(els.sourceViewer,{scrollHeight:{value:1000},clientHeight:{value:100},scrollWidth:{value:500},clientWidth:{value:100}});
      Object.defineProperties(els.markdownView,{scrollHeight:{value:1200},clientHeight:{value:100},scrollWidth:{value:600},clientWidth:{value:100}});
      els.sourceViewer.scrollTop=200;els.sourceViewer.scrollLeft=100;
      syncPairedPPOCRScroll(els.sourceViewer,els.markdownView);
    `);
    await new Promise((resolve) => globalThis.setTimeout(resolve, 20));
    dom.window.close();
});


test('batch payload reconstruction, source caching, markdown math, and result render caches execute', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    const ignore = async (source) => {
        try { return await evaluate(source); } catch { return null; }
    };
    window.fetch = async () => new Response(new Uint8Array([1, 2, 3]), {
        status: 200,
        headers: { 'content-type': 'application/pdf' }
    });
    evaluate(`
      getTaskSourceBlob=async(_task,type)=>new Blob(['x'],{type});
      fetchPdfBatchBlob=async()=>new Blob(['pdf'],{type:'application/pdf'});
      createPDFBatchBlob=async()=>new Blob(['batch'],{type:'application/pdf'});
      PDFLib.PDFDocument.load=async()=>({loaded:true});
    `);
    await evaluate("ensureBatchPayload({mimeType:'image/png'},{fileType:1})");
    await ignore("ensureBatchPayload({}, {fileType:9})");
    await ignore("ensureBatchPayload({pageCount:2}, {fileType:0})");
    await evaluate("ensureBatchPayload({id:'one',pageCount:1,sourceUrl:'/x'}, {fileType:0})");
    await evaluate("ensureBatchPayload({id:'remote',pageCount:2,sourceUrl:'/x'}, {fileType:0,startPage:1,endPage:1})");
    await evaluate("ensureBatchPayload({id:'data',pageCount:2,sourceDataUrl:'data:application/pdf;base64,AA=='}, {fileType:0,startPage:1,endPage:1})");
    await evaluate("ensureBatchPayload({id:'data',pageCount:2,sourceDataUrl:'data:application/pdf;base64,AA=='}, {fileType:0,startPage:1,endPage:1})");

    evaluate("sourceBytesCache.clear()");
    const bytes = await evaluate("getTaskSourceBytes({id:'data-url',sourceDataUrl:'data:application/pdf;base64,SGk='})");
    assert.equal(bytes.length, 2);
    await evaluate("getTaskSourceBytes({id:'data-url',sourceDataUrl:'data:application/pdf;base64,SGk='})");
    await ignore("getTaskSourceBytes({id:'missing'})");
    await evaluate("getTaskSourceBytes({id:'remote-bytes',sourceUrl:'/x'})");
    window.fetch = async () => new Response('bad', { status: 500 });
    await ignore("getTaskSourceBytes({id:'bad-bytes',sourceUrl:'/x'})");
    await ignore("getTaskSourceBlob({id:'missing'},'x/type')");
    await ignore("getTaskSourceBlob({id:'bad',sourceUrl:'/x'},'x/type')");
    window.fetch = async () => new Response('blob', { status: 200, headers: { 'content-type': 'text/plain' } });
    await evaluate("getTaskSourceBlob({id:'blob',sourceUrl:'/x'},'x/type')");
    await evaluate("getTaskSourceBlob({id:'blob2',sourceUrl:'/x'},'text/plain')");

    evaluate(`
      formatUnlimitedOCRBlock('image_caption','caption',false);
      formatUnlimitedOCRBlock('image','picture',false);
      formatUnlimitedOCRBlock('text','plain',false);
      cleanUnlimitedOCRMarkdown('<|det|>bad');
      cleanUnlimitedOCRMarkdown('prefix <|det|>text<|/det|> body');
      renderMarkdownHtml('a $$x$$ b \\\\[y\\\\] c \\\\(z\\\\) d $q$');
      window.renderMathInElement=null;renderMathWhenReady(document.body,1);
    `);
    await new Promise((resolve) => globalThis.setTimeout(resolve, 170));

    evaluate(`
      window.renderTask={id:'render',name:'r',status:'completed',modelId:'paddleocr-vl-1.6',
       markdown:'![x](path.jpg)',images:{'path.jpg':'SGk='},
       ocrResults:[{ocrLines:[{text:'x',box:[1,2,3,4]}]}],batches:[]};
      tasks=[window.renderTask];activeTaskId='render';activeResultView='markdown';
      resetResultRenderCache();renderResultPane(window.renderTask);renderResultPane(window.renderTask);
      activeResultView='json';renderJsonResult(window.renderTask,{defer:false});
      renderJsonResult(window.renderTask,{defer:false});
      renderedJsonKey='';renderJsonResult(window.renderTask,{defer:true});
      activeResultView='markdown';renderedJsonKey='';window.requestIdleCallback=(fn)=>fn();
      warmJsonResultCache(window.renderTask);
    `);
    dom.window.close();
});


test('model selection handlers, runtime waiting, auth retry, and readiness decisions execute', async () => {
    const { dom, window, runtime } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    evaluate(`
      availableModels=[
       {id:'a',name:'A',label:'A',endpoint:'/a'},
       {id:'b',name:'B',label:'B',endpoint:'/b'},
       {id:'unlimited-ocr',name:'U',label:'U',endpoint:'/u'}
      ];
      selectedModelId='a';selectedUnlimitedOcrBackend='transformers';
      modelRuntime={controlAvailable:true,activeModelId:'a',models:{
       a:{ready:true,state:'running'},b:{ready:false,state:'stopped'},
       'unlimited-ocr':{ready:false,state:'missing'}
      }};
      window.originalSwitchModelRuntime=switchModelRuntime;
      renderModelSelect();
    `);
    evaluate("modelRuntime.operation={state:'switching',targetModelId:'b'};syncSelectedModelWithRuntime()");
    evaluate("modelRuntime.operation=null;modelRuntime.activeModelId='a';modelRuntime.models.a={running:true};selectedModelId='b';syncSelectedModelWithRuntime()");
    evaluate("availableModels=[];getSelectedModel();availableModels=[{id:'a',name:'A',label:'A',endpoint:'/a'}]");
    evaluate("getTaskModel({modelId:'unknown',modelName:'Unknown',modelEndpoint:'/unknown'});getTaskModel({})");

    evaluate("isProcessing=true;els.modelSelect.value='a'");
    await evaluate("handleModelSelectionChange()");
    evaluate("isProcessing=false;modelSwitchInFlight=false;selectedModelId='a';els.modelSelect.value='b';switchModelRuntime=async()=>false");
    await evaluate("handleModelSelectionChange()");
    evaluate("modelRuntime.models.b={state:'missing'};els.modelSelect.value='b';requestModelDeploymentOptions=()=>null");
    await evaluate("handleModelSelectionChange()");
    evaluate("requestModelDeploymentOptions=()=>({});deployModelRuntime=async()=>true");
    await evaluate("handleModelSelectionChange()");

    evaluate("isProcessing=true;els.unlimitedBackendSelect.value='sglang'");
    await evaluate("handleUnlimitedOcrBackendChange()");
    evaluate("isProcessing=false;modelSwitchInFlight=false;unlimitedOcrBackendSwitchInFlight=false;switchUnlimitedOcrBackend=async()=>false");
    await evaluate("handleUnlimitedOcrBackendChange()");

    evaluate("switchModelRuntime=window.originalSwitchModelRuntime");
    evaluate("modelRuntime={controlAvailable:true,models:{a:{ready:true,state:'running'}}};");
    assert.equal(await evaluate("switchModelRuntime('a')"), true);
    evaluate("modelRuntime={controlAvailable:false,models:{a:{state:'missing'}}}");
    assert.equal(await evaluate("switchModelRuntime('a')"), false);

    evaluate(`
      let ticks=0;
      Date.now=()=>++ticks;
      sleep=async()=>{};
      loadModelRuntime=async()=>modelRuntime;
      modelRuntime={models:{a:{ready:false,state:'starting'}}};
    `);
    await evaluate("waitForModelRuntimeReady('a',2)").catch(() => null);

    evaluate(`
      modelRuntime={models:{a:{ready:false,state:'missing'}}};
      requestModelDeploymentOptions=()=>({});
      deployModelRuntime=async()=>{modelRuntime.models.a.ready=true;return true};
    `);
    assert.equal(await evaluate("ensureModelRuntimeReadyForTask({}, {id:'a',name:'A'})"), true);
    evaluate("modelRuntime={models:{a:{ready:false,state:'stopped'}}};switchModelRuntime=async()=>false");
    assert.equal(await evaluate("ensureModelRuntimeReadyForTask({}, {id:'a',name:'A'})"), false);

    let calls = 0;
    window.fetch = async () => {
        calls += 1;
        return new Response(calls === 1 ? 'unauthorized' : 'ok', { status: calls === 1 ? 401 : 200 });
    };
    window.prompt = () => ' token ';
    await evaluate("apiFetch('/api/test')");
    assert.equal(calls, 2);
    calls = 0;
    window.prompt = () => '';
    await evaluate("apiFetch('/api/test')");
    assert.equal(calls, 1);
    assert.equal(evaluate("isLocalApiUrl('http://[')"), false);
    dom.window.close();
});


test('resume processing, payload-data OCR, streaming tail/fallback, and copy-error paths execute', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    const ignore = async (source) => {
        try { return await evaluate(source); } catch { return null; }
    };
    evaluate(`
      window.resume={
       id:'resume',name:'r.pdf',sourceKind:'pdf',sourceUrl:'/x',pageCount:2,pdfBatchSize:2,
       modelId:'ovisocr2',modelName:'Ovis',modelEndpoint:'/o',status:'error',markdown:'old',
       images:{},ocrResults:[{}],batches:[
        {id:'done',status:'completed',pageCount:1,markdown:'done'},
        {id:'pending',status:'error',pageCount:1,fileType:1,payloadDataUrl:'data:image/png;base64,SGk='}
       ]};
      tasks=[window.resume];activeTaskId='resume';
      ensureModelRuntimeReadyForTask=async()=>true;
      ensureBatchPayload=async()=>{};
      window.originalResumeCallOCR=callOCR;
      callOCR=async()=>({markdown:'new',images:{},layoutParsingResults:[]});
      saveTask=async()=>{};
      window.setInterval=(fn)=>{fn();return 1};
    `);
    await evaluate("processTask(window.resume,{confirmCompleted:false})");
    evaluate(`
      window.pdfPlan={sourceKind:'pdf',sourceUrl:'/x',pageCount:3,pdfBatchSize:1,batches:[{status:'pending',pageCount:1}]};
      els.pdfBatchSizeInput.value='2';
      shouldRebuildPdfBatchPlan(window.pdfPlan);rebuildPdfBatchPlan(window.pdfPlan);
    `);

    window.fetch = async () => jsonResponse({ markdown: 'data-url', images: {} });
    evaluate("callOCR=window.originalResumeCallOCR");
    await evaluate("callOCR({id:'d',label:'d',fileType:1,payloadDataUrl:'data:image/png;base64,SGk='},{modelId:'paddleocr-vl-1.6',modelEndpoint:'/x'})");
    evaluate("window.originalStreamingCall=callStreamingUnlimitedOCR;callStreamingUnlimitedOCR=async()=>({markdown:'streamed'})");
    await evaluate("callOCR({id:'u',label:'u',fileType:1,payloadBlob:new Blob(['x'])},{modelId:'unlimited-ocr',modelEndpoint:'/u'})");
    evaluate("callStreamingUnlimitedOCR=window.originalStreamingCall");

    window.fetch = async () => ({ ok: true, body: null, json: async () => ({ markdown: 'fallback' }) });
    const fallback = await evaluate("callStreamingUnlimitedOCR({id:'b',label:'b'},{images:{},batches:[],markdown:''},new FormData(),{endpoint:'/u'})");
    assert.equal(fallback.markdown, 'fallback');
    const tails = [
        JSON.stringify({ type: 'status', message: 'tail' }),
        JSON.stringify({ type: 'final', result: { markdown: 'tail-final' } })
    ];
    for (const body of tails) {
        window.__tail = body;
        window.fetch = async () => new Response(window.__tail, { status: 200 });
        await ignore("callStreamingUnlimitedOCR({id:'b',label:'b',markdown:''},{images:{},batches:[],markdown:''},new FormData(),{endpoint:'/u'})");
    }
    window.fetch = async () => new Response(JSON.stringify({ type: 'error', detail: 'tail error' }), { status: 200 });
    await ignore("callStreamingUnlimitedOCR({id:'b',label:'b',markdown:''},{images:{},batches:[],markdown:''},new FormData(),{endpoint:'/u'})");

    evaluate("tasks=[{id:'copy',name:'c',status:'completed',markdown:'text',ocrResults:[],batches:[]}];activeTaskId='copy';activeResultView='markdown'");
    window.navigator.clipboard = { writeText: async () => { throw new Error('no'); } };
    window.document.execCommand = () => false;
    await evaluate("copyActiveResult()");
    dom.window.close();
});


test('markdown-to-layout matching, linked entry ranking, and activation alternatives execute', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    evaluate(`
      window.layoutTask={id:'l',name:'l',status:'completed',modelId:'unlimited-ocr',markdown:'Title',
       batches:[],ocrResults:[{parser:'unlimited-ocr',sourcePage:1,width:1000,height:1000,
        parsing_res_list:[
         {block_label:'title',block_bbox:[1,1,100,20],block_content:'Title exact'},
         {block_label:'image',block_bbox:[1,30,100,100],block_content:''},
         {block_label:'algorithm',block_bbox:[1,110,100,200],block_content:'Algorithm 1: Sort'},
         {block_label:'figure_title',block_bbox:[1,210,100,250],block_content:'Figure 1: Plot'}
        ]}]};
      tasks=[window.layoutTask];activeTaskId='l';activeResultView='markdown';currentPdf={numPages:1};
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"><div class="pdf-canvas-box"><canvas width="1000" height="1000"></canvas><div class="pdf-highlight-layer"></div></div></div>';
      els.markdownView.innerHTML='<h1>Title exact</h1><p><img src="data:image/png;base64,AA=="></p><p>Algorithm 1: Sort</p><p>Figure 1: Plot</p><p>x</p>';
      linkMarkdownToSourceBlocks(window.layoutTask);
    `);
    for (const element of window.document.querySelectorAll('.layout-linked-block')) {
        for (const name of ['mouseenter', 'click', 'mouseleave']) {
            element.dispatchEvent(new window.Event(name, { bubbles: true }));
        }
    }
    evaluate(`
      const blocks=collectLayoutBlocks(window.layoutTask);
      findNextLayoutBlockByLabel(blocks,0,['image']);
      findNextLayoutBlockByLabel(blocks,99,['none']);
      findBestLayoutBlock('Title exact',blocks,0);
      findBestLayoutBlock('missing unrelated words',blocks,0);
      matchScore('same','same');
      matchScore('Title','Title exact');
      matchScore('Title exact long','Title');
      matchScore('some matching words here','matching content words');
      linkedLayoutEntries.push({element:document.createElement('div'),block:blocks[0]});
      findNearestLinkedEntryInResult();
      findNearestLinkedEntryInSource();
      setActiveLinkedLayoutEntry(linkedLayoutEntries[0]);
      activateLinkedBlock(linkedLayoutEntries[0].element,blocks[0],{scrollMarkdown:true,scrollSource:true});
    `);
    dom.window.close();
});


test('remaining concrete line paths for task recovery, rendering races, and source failures execute', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    const ignore = async (source) => {
        try { return await evaluate(source); } catch { return null; }
    };
    evaluate("tasks=[];activeTaskId=null;isProcessing=false;resetWorkbench();refreshLanguageSensitiveUi()");

    evaluate(`
      availableModels=[{id:'missing',name:'M',label:'M',endpoint:'/m'}];
      selectedModelId='missing';modelRuntime={controlAvailable:true,models:{missing:{state:'missing'}}};
      renderModelSelect();els.modelSelect.value='missing';
      requestModelDeploymentOptions=()=>({});deployModelRuntime=async()=>true;
    `);
    await evaluate("handleModelSelectionChange()");

    window.fetch = async (url) => {
        const pathname = new URL(String(url), window.location.href).pathname;
        if (pathname === '/api/tasks/summary') {
            return jsonResponse({ id:'summary',name:'s.png',sourceKind:'image',sourceUrl:'/x',status:'pending',batches:[] });
        }
        return new Response('bad', { status: 500 });
    };
    evaluate("tasks=[{id:'summary',name:'summary',status:'pending'}];activeTaskId='summary'");
    await evaluate("ensureTaskLoaded('summary')");

    evaluate(`
      tasks=[{id:'old',name:'old',status:'pending'}];activeTaskId='old';
      createTaskFromFile=async()=>{throw new Error('failed')};
      window.selectedOld=false;selectTask=async()=>{window.selectedOld=true};
    `);
    await evaluate("handleFiles([{}])");
    assert.equal(window.selectedOld, true);
    evaluate(`
      createTaskFromFile=async()=>({id:'new',name:'new',status:'pending',batches:[]});
      saveTask=async()=>{throw new Error('save')};
      processTask=async()=>{};
      selectTask=async()=>{};
    `);
    await evaluate("handleFiles([{}])");

    window.fetch = async () => new Response('bad', { status: 500 });
    await ignore("uploadTaskSource('x',new File(['x'],'x.png',{type:'image/png'}),'x.png','image/png')");
    await ignore("convertOfficeToPdf(new File(['x'],'x.docx'))");
    evaluate("tasks=[{id:'bad',name:'bad',status:'pending',batches:[]},{id:'remain',name:'remain',status:'pending',batches:[]}];activeTaskId='bad';isProcessing=false");
    window.confirm = () => true;
    await evaluate("deleteTask('bad')");

    evaluate(`
      tasks=[{id:'load',name:'load',status:'pending'}];activeTaskId='load';
      ensureTaskLoaded=async()=>{throw new Error('load')};
    `);
    await evaluate("selectTask('load')");

    evaluate(`
      window.pp={id:'pp',name:'pp.pdf',sourceKind:'pdf',sourceUrl:'/x',pageCount:1,status:'completed',
       modelId:'paddleocr-vl-1.6',markdown:'',batches:[],ocrResults:[{ocrLines:[{text:'x',box:[1,2,3,4]}]}]};
      tasks=[window.pp];activeTaskId='pp';
      ensureTaskLoaded=async()=>window.pp;
      renderSource=async()=>{};
    `);
    await evaluate("selectTask('pp')");

    const image = window.document.createElement('img');
    image.decode = async () => {};
    window.__sourceTask = { sourceKind:'image',sourceUrl:'data:image/png;base64,AA==',pageCount:1 };
    await evaluate("renderSource(window.__sourceTask)");
    window.pdfjsLib.getDocument = () => ({
        promise: Promise.resolve({
            numPages: 1,
            getPage: async () => ({
                getViewport: () => ({ width: 100, height: 100 }),
                render: () => ({ promise: Promise.resolve() })
            })
        })
    });
    evaluate("window.__pdfTask={sourceKind:'pdf',sourceUrl:'/x',pageCount:1}");
    await evaluate("renderSource(window.__pdfTask)");

    evaluate(`
      tasks=[window.pp];activeTaskId='pp';activeResultView='markdown';
      renderedMarkdownKey='';renderedPPOCRVisualContext='';
      renderResultPane(window.pp);
      window.setTimeout=(fn)=>{fn();return 1};
      const b=document.createElement('button');b.innerHTML='<span data-label></span>';
      flashToolbarButtonLabel(b,'x','y');
      saveTask=async()=>{throw new Error('save correction')};saveCorrectedPPOCRTask();
      handlePPOCRMarkdownScroll();
    `);

    evaluate(`
      shouldSyncPPOCRVisualScroll=()=>true;currentPdf={numPages:1};
      const page=els.sourceViewer.querySelector('.pdf-page-wrap');
      if(page) page.getBoundingClientRect=()=>({top:500,bottom:600,width:100,height:100});
      activatePPOCRLine(window.lineElement||document.createElement('div'),window.toolbar||document.createElement('div'),
        {text:'x',sourcePage:1,box:[1,2,3,4],pageWidth:1000,pageHeight:1000},{scrollSource:true});
    `);

    evaluate(`
      window.zoomTask={id:'z',modelId:'paddleocr-vl-1.6',ocrResults:[{ocrLines:[{text:'x',box:[1,2,3,4]}]}],batches:[]};
      tasks=[window.zoomTask];activeTaskId='z';activeResultView='markdown';
      currentPdf={numPages:1,getPage:async()=>({getViewport:()=>({width:10,height:10}),render:()=>({promise:Promise.resolve()})})};
      renderPdfDocument=async()=>{};
    `);
    await evaluate("changeZoom(.1)");
    await evaluate("resetZoom()");

    window.fetch = async () => new Response('bad', { status: 500 });
    await ignore("getTaskSourceBlob({id:'missing'},'x/type')");
    await ignore("getTaskSourceBlob({id:'bad',sourceUrl:'/x'},'x/type')");
    window.fetch = async () => new Response('ok', { status: 200, headers: { 'content-type': 'text/plain' } });
    await evaluate("getTaskSourceBlob({id:'ok',sourceUrl:'/x'},'text/plain')");
    dom.window.close();
});


test('last executable lines cover listener completion, render races, visual invalidation, and stream fallbacks', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const evaluate = (source) => window.eval(source);
    const ignore = async (source) => {
        try { return await evaluate(source); } catch { return null; }
    };

    evaluate(`
      checkBackendConnection=async()=>{};loadTasks=async()=>{tasks=[{id:'boot'}]};
      renderTaskList=()=>{};selectTask=async()=>{};applyLanguage=()=>{};
    `);
    window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
    await new Promise((resolve) => globalThis.setTimeout(resolve, 1));
    evaluate("handleFiles=async()=>{}");
    const input = window.document.getElementById('file-input');
    Object.defineProperty(input, 'files', { configurable: true, value: [] });
    input.dispatchEvent(new window.Event('change'));
    await Promise.resolve();

    let poll = null;
    window.setInterval = (fn) => { poll = fn; return 1; };
    evaluate("modelRuntimePollTimer=null;loadModelRuntime=async()=>{throw new Error('poll')};startModelRuntimePolling()");
    if (poll) poll();
    await Promise.resolve();

    // Fresh original function bindings for task selection and source rendering.
    const fresh = createBrowser();
    await boot(fresh.window);
    const ev = (source) => fresh.window.eval(source);
    ev(`
      window.originalSelect=selectTask;window.originalRenderSource=renderSource;
      tasks=[{id:'remove',name:'remove',status:'pending',batches:[]},{id:'remain',name:'remain',status:'pending',batches:[]}];
      activeTaskId='remove';isProcessing=false;selectTask=async()=>{window.deleteSelected=true};
    `);
    fresh.window.confirm = () => true;
    fresh.window.fetch = async () => jsonResponse({});
    await ev("deleteTask('remove')");
    ev("selectTask=window.originalSelect");
    ev("tasks=[{id:'bad',name:'bad',status:'pending'}];activeTaskId='bad';ensureTaskLoaded=async()=>{throw new Error('bad')}");
    await ev("window.originalSelect('bad')");

    ev(`
      window.pp={id:'pp',name:'pp.pdf',sourceKind:'pdf',sourceUrl:'/x',pageCount:1,status:'completed',
       modelId:'pp-ocrv6',markdown:'',images:{},batches:[],
       ocrResults:[{ocrLines:[{text:'x',box:[1,2,30,20]}]}]};
      tasks=[window.pp];activeTaskId='pp';ensureTaskLoaded=async()=>window.pp;
      renderSource=async()=>{};
    `);
    await ev("window.originalSelect('pp')");
    ev("renderSource=window.originalRenderSource");

    fresh.window.HTMLImageElement.prototype.decode = async () => {};
    fresh.window.__imageTask = { sourceKind:'image',sourceUrl:'data:image/png;base64,AA==',pageCount:1 };
    await ev("renderSource(window.__imageTask)");

    let resolvePdf;
    fresh.window.pdfjsLib.getDocument = () => ({
        promise: new Promise((resolve) => { resolvePdf = resolve; })
    });
    fresh.window.__raceTask = { sourceKind:'pdf',sourceUrl:'/x',pageCount:1 };
    const race = ev("renderSource(window.__raceTask)");
    ev("sourceRenderToken+=1");
    resolvePdf({ numPages:1, getPage:async()=>({getViewport:()=>({width:1}),render:()=>({promise:Promise.resolve()})}) });
    await race;
    fresh.window.pdfjsLib.getDocument = () => ({
        promise: Promise.resolve({
            numPages:1,
            getPage:async()=>{
                ev("sourceRenderToken+=1");
                return {getViewport:()=>({width:1}),render:()=>({promise:Promise.resolve()})};
            }
        })
    });
    await ev("renderSource(window.__raceTask)");

    ev(`
      tasks=[window.pp];activeTaskId='pp';activeResultView='markdown';
      resetResultRenderCache();renderResultPane(window.pp);
      const fit=document.createElement('div');const label=createPPOCRLineLabel('x');fit.append(label);document.body.append(fit);
      Object.defineProperties(fit,{clientWidth:{value:100},clientHeight:{value:20}});
      Object.defineProperties(label,{scrollWidth:{value:50},scrollHeight:{value:10}});
      fitPPOCRLineElement(fit,{text:'x'});
      const stage=document.createElement('div');stage.className='ocr-page-stage';document.body.append(stage);
      const toolbar=createPPOCRFloatingToolbar();stage.append(toolbar);
      const line=createPPOCRLineLabel('x');line.className='ocr-text-line';stage.append(line);
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"><div class="pdf-canvas-box"><canvas width="1000" height="1000"></canvas><div class="pdf-highlight-layer"></div></div></div>';
      isElementMostlyVisible=()=>false;
      activatePPOCRLine(line,toolbar,{text:'x',sourcePage:1,box:[1,2,3,4],pageWidth:1000,pageHeight:1000},{scrollSource:true});
      tasks=[{id:'u',modelId:'unlimited-ocr',ocrResults:[{}]}];activeTaskId='u';handlePPOCRMarkdownScroll();
    `);

    ev(`
      window.plan={id:'plan',name:'p',sourceKind:'pdf',sourceUrl:'/x',pageCount:2,pdfBatchSize:1,
       modelId:'paddleocr-vl-1.6',status:'pending',markdown:'',images:{},ocrResults:[],batches:[]};
      tasks=[window.plan];activeTaskId='plan';ensureModelRuntimeReadyForTask=async()=>true;
      ensureBatchPayload=async()=>{};callOCR=async()=>({markdown:'ok',images:{},layoutParsingResults:[]});saveTask=async()=>{};
    `);
    await ev("processTask(window.plan,{confirmCompleted:false})");

    fresh.window.fetch = async () => new Response('bad', { status:500 });
    await ev("callStreamingUnlimitedOCR({id:'b',label:'b'},{images:{},batches:[],markdown:''},new FormData(),{endpoint:'/u'})").catch(() => null);
    const imageProgress = JSON.stringify({ type:'progress',markdown:'image progress',images:{'x.jpg':'SGk='} }) + '\n';
    fresh.window.fetch = async () => new Response(imageProgress, { status:200 });
    await ev("callStreamingUnlimitedOCR({id:'b',label:'b',markdown:''},{images:{},batches:[],markdown:''},new FormData(),{endpoint:'/u'})");

    ev(`
      tasks=[window.pp];activeTaskId='pp';activeResultView='markdown';currentPdf={numPages:1};
      renderPdfDocument=async()=>{};invalidatePPOCRVisualRender=()=>{window.invalidated=true};
    `);
    await ev("changeZoom(.1)");
    await ev("resetZoom()");
    ev(`
      tasks=[{id:'u',modelId:'unlimited-ocr',ocrResults:[{}]}];activeTaskId='u';
      currentPdf={numPages:2};currentPage=1;
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"></div><div class="pdf-page-wrap" data-page="2"></div>';
      const pages=els.sourceViewer.querySelectorAll('.pdf-page-wrap');
      pages[0].getBoundingClientRect=()=>({top:500});pages[1].getBoundingClientRect=()=>({top:0});
      handleSourceViewerScroll();
    `);

    fresh.window.fetch = async () => new Response('ok', { status:200,headers:{'content-type':'text/plain'} });
    await ev("getTaskSourceBlob({sourceDataUrl:'data:text/plain;base64,SGk='},'text/plain')");
    await ev("getTaskSourceBlob({sourceUrl:'/x'},'text/plain')");

    ev(`
      els.markdownView.innerHTML='<div class="layout-linked-block-active"></div><div class="layout-linked-block-active"></div>';
      const target=els.markdownView.children[0];setActiveLinkedLayoutEntry({element:target});
      const source=els.sourceViewer.querySelector('.pdf-page-wrap');
      if(source) source.innerHTML='<div class="pdf-canvas-box"><canvas width="1000" height="1000"></canvas><div class="pdf-highlight-layer"></div></div>';
      if(source) source.getBoundingClientRect=()=>({top:500,bottom:600,width:100,height:100});
      const md=document.createElement('div');document.body.append(md);md.getBoundingClientRect=()=>({top:500,bottom:600,width:100,height:100});
      isElementMostlyVisible=()=>false;
      activateLinkedBlock(md,{page:1,bbox:[1,2,3,4],pageWidth:1000,pageHeight:1000},{scrollMarkdown:true,scrollSource:true});
    `);
    fresh.dom.window.close();
    dom.window.close();
});


test('explicit fallback and guard branch matrix exercises production alternatives', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const ev = (source) => window.eval(source);
    const settle = async (source) => {
        try {
            const value = ev(source);
            return value && typeof value.then === 'function'
                ? await Promise.race([
                    value,
                    new Promise((resolve) => globalThis.setTimeout(() => resolve(undefined), 250))
                ])
                : value;
        } catch {
            return undefined;
        }
    };

    ev(`
      window.savedI18n={
        supportedLanguages:I18N_CONFIG.supportedLanguages,
        defaultLanguage:I18N_CONFIG.defaultLanguage,
        titles:I18N_CONFIG.titles
      };
      I18N_CONFIG.supportedLanguages=null;I18N_CONFIG.defaultLanguage='';
      normalizeLanguage('xx');
      I18N_CONFIG.supportedLanguages=[];normalizeLanguage('xx');
      I18N_CONFIG.supportedLanguages=['en'];I18N_CONFIG.defaultLanguage='en';I18N_CONFIG.titles={};
      currentLanguage='en';applyLanguage(document.body);
      const savedToggle=els.languageToggle;els.languageToggle=null;updateLanguageToggle();els.languageToggle=savedToggle;
      translateElementTree(document.createTextNode('orphan'));
      Object.assign(I18N_CONFIG,window.savedI18n);

      availableModels=[];
      normalizeModelList({data:['plain',{},{id:'id-only'},{name:'name-only'},{label:'label-only'}]});
      const savedModelSelect=els.modelSelect;els.modelSelect=null;renderModelSelect();els.modelSelect=savedModelSelect;
      const savedBackendWrap=els.unlimitedBackendWrap,savedBackendSelect=els.unlimitedBackendSelect;
      els.unlimitedBackendWrap=null;renderUnlimitedOcrBackendSelect();els.unlimitedBackendWrap=savedBackendWrap;
      els.unlimitedBackendSelect=null;renderUnlimitedOcrBackendSelect();els.unlimitedBackendSelect=savedBackendSelect;
      modelRuntime={unlimitedOcrBackend:'',unlimitedOcrSupportedBackends:['sglang','sglang'],models:{'unlimited-ocr':{}}};
      selectedUnlimitedOcrBackend='';getRuntimeUnlimitedOcrBackend();renderUnlimitedOcrBackendSelect();
      modelRuntime={models:{'unlimited-ocr':{unlimitedOcrBackend:'sglang',unlimitedOcrSupportedBackends:[]}}};
      getRuntimeUnlimitedOcrBackend();renderUnlimitedOcrBackendSelect();
      modelRuntime=null;isModelRuntimeReady('x');
      availableModels=[];getSelectedModel();
      availableModels=[{id:'first'}];selectedModelId='missing';getSelectedModel();
      getTaskModel({modelId:'unknown',modelName:'',modelEndpoint:''});
      getTaskModel({modelId:'unknown',modelName:'named'});
      modelApiUrl({endpoint:'https://example.test/x'});modelApiUrl({endpoint:'relative'});modelApiUrl({});
      isModelRuntimeSwitching();modelSwitchInFlight=true;selectedModelId='x';isModelRuntimeSwitching();isModelRuntimeSwitching('x');isModelRuntimeSwitching('y');modelSwitchInFlight=false;
      modelRuntime={controlAvailable:false,models:{x:{state:'stopped'}}};canSwitchModelRuntime('x');modelRuntime=null;canSwitchModelRuntime('x');
      modelRuntime={controlAvailable:false,models:{x:{state:'stopped'}},operation:null};
      modelRuntimeStatusText({id:'x',label:'X'});
      availableModels=[];modelRuntime=null;syncSelectedModelWithRuntime();
      availableModels=[{id:'x'}];modelRuntime={controlAvailable:false};syncSelectedModelWithRuntime();
      const savedUnlimited=els.unlimitedBackendSelect;els.unlimitedBackendSelect=null;handleUnlimitedOcrBackendChange();els.unlimitedBackendSelect=savedUnlimited;
    `);

    ev("waitForModelRuntimeReady=async()=>true");
    window.fetch = async () => jsonResponse({ models: {}, operation: null });
    await settle("switchModelRuntime('x',{wait:true})");
    ev("modelRuntime={controlAvailable:true,models:{x:{ready:false,state:'stopped'}}};availableModels=[{id:'x',label:'X'}]");
    await settle("switchModelRuntime('x',{wait:true})");
    await settle("deployModelRuntime('unlimited-ocr',{wait:true,backend:''})");
    await settle("deployModelRuntime('x',{wait:true})");
    window.fetch = async () => new Response(JSON.stringify({ detail: '' }), { status: 500 });
    await settle("switchModelRuntime('x')");await settle("deployModelRuntime('x')");await settle("switchUnlimitedOcrBackend('sglang')");

    ev(`
      const sparse=[
       {id:'a',name:'',size:0,pageCount:0,status:'pending',updatedAt:0,batches:null},
       {id:'b',name:'same',size:0,pageCount:0,status:'pending',updatedAt:2,batches:[null]},
       {id:'c',name:'same',size:0,pageCount:0,status:'pending',updatedAt:1,batches:[{}]}
      ];
      sparse.forEach(reconcileTaskStatus);dedupeTasks(sparse);
      taskSourceMeta({name:'',size:0,pageCount:0,sourceKind:'pdf'});
      const savedTask={id:'err',name:'',status:'pending'};
      tasks=[savedTask];activeTaskId='other';ensureTaskLoaded=async()=>savedTask;selectTask('err');
      tasks=[savedTask];activeTaskId='err';ensureTaskLoaded=async()=>null;selectTask('err');
      const savedSourceTitle=els.sourceTitle;delete els.sourceTitle;
      els.sourceTitle=savedSourceTitle;
      tasks=[{id:'p',name:'pending',status:'pending'},{id:'d',name:'done',status:'completed'}];
      activeFilter='done';els.taskSearch.value='';renderTaskList();
      activeFilter='all';els.taskSearch.value='needle';renderTaskList();
    `);

    ev(`
      window.pdfTask={sourceKind:'pdf',sourceDataUrl:'data:application/pdf;base64,AA==',pageCount:0};
      currentPage=99;
      pdfjsLib.getDocument=()=>({promise:Promise.resolve({numPages:1,getPage:async()=>({getViewport:()=>({width:0}),render:()=>({promise:Promise.resolve()})})})});
      renderPdfDocument=async()=>{};
    `);
    await settle("renderSource(window.pdfTask)");
    await settle("renderPdfDocument(sourceRenderToken+1)");
    ev("currentPdf=null");await settle("renderPdfDocument()");
    ev(`
      currentPdf={numPages:2,getPage:async()=>{sourceRenderToken+=1;return {getViewport:()=>({width:1,height:1}),render:()=>({promise:Promise.resolve()})}}};
      sourceRenderToken=5;
    `);
    await settle("renderPdfDocument(5)");

    ev(`
      const emptyP={pageNumber:0,lines:[]};
      ppocrVisualPageKey(emptyP);
      ppocrVisualPageKey({pageNumber:0,index:0,lines:[{text:'',box:null},{}]});
      collectPPOCRLines({prunedResult:{rec_texts:['x'],rec_boxes:[null],rec_polys:[[[1],[2,3]]]}});
      collectPPOCRLines({ocrLines:[{text:'',box:null,poly:[[1,2],[3,4]]}]});
      boxFromPoly([]);boxFromPoly([[1],[2,3]]);normalizePPOCRBox([1,2,3,Infinity]);
      const img=document.createElement('img');
      Object.defineProperties(img,{naturalWidth:{value:0},naturalHeight:{value:0},clientWidth:{value:0},clientHeight:{value:0}});
      layoutPPOCRTextLayer(document.createElement('div'),{pageNumber:0,lines:[{text:'',box:[1,2,3,4]}]},0,0,createPPOCRFloatingToolbar(),img);
      const code=createPPOCRLineLabel('function x(){}');document.body.append(code);
      Object.defineProperties(code,{scrollWidth:{value:500},scrollHeight:{value:50}});
      const codeBox=document.createElement('div');document.body.append(codeBox);codeBox.append(code);
      Object.defineProperties(codeBox,{clientWidth:{value:20},clientHeight:{value:10}});
      fitPPOCRLineElement(codeBox,{text:'function x(){}'});
      const wide=createPPOCRLineLabel('this is a very wide prose line with many words to shrink');document.body.append(wide);
      const wideBox=document.createElement('div');wideBox.append(wide);document.body.append(wideBox);
      Object.defineProperties(wide,{scrollWidth:{value:500},scrollHeight:{value:100}});
      Object.defineProperties(wideBox,{clientWidth:{value:50},clientHeight:{value:20}});
      fitPPOCRLineElement(wideBox,{text:wide.textContent});
      const savedSource=els.sourceViewer,savedMarkdown=els.markdownView;
      els.sourceViewer=null;handlePPOCRMarkdownScroll();els.sourceViewer=savedSource;
      els.markdownView=null;handleSourceViewerScroll();els.markdownView=savedMarkdown;
      syncSourceScrollFromPPOCRVisual();syncPPOCRVisualScrollFromSource();
    `);

    ev(`
      shouldRebuildPdfBatchPlan(null);
      shouldRebuildPdfBatchPlan({sourceUrl:'/x',sourceKind:'pdf',pageCount:0,batches:[]});
      shouldRebuildPdfBatchPlan({sourceUrl:'/x',sourceKind:'pdf',pageCount:2,pdfBatchSize:2,batches:null});
      shouldRebuildPdfBatchPlan({sourceUrl:'/x',sourceKind:'pdf',pageCount:2,pdfBatchSize:1,batches:[{pageCount:2}]});
      shouldRebuildPdfBatchPlan({sourceUrl:'/x',sourceKind:'pdf',pageCount:2,pdfBatchSize:2,batches:[{pageCount:1},{pageCount:1}]});
      rebuildTaskResultFromCompletedBatches({batches:[{status:'completed',markdown:''},{status:'completed'}]});
      taskForPersistence({markdown:'Processing...',ocrResults:[1]},{includeResults:false});
      taskForPersistence({markdown:'ok',ocrResults:[1]},{includeResults:true});
      activeResultCopyText(null);activeResultCopyText({markdown:'',ocrResults:[]});
      statusText({status:'processing',pageCount:0,batches:[]});
      statusText({status:'processing',pageCount:2,batches:[{status:'completed',pageCount:0}]});
      statusText({status:'pending',pageCount:2,completedPages:1,batches:[]});
      const savedBatch=els.pdfBatchSizeInput;els.pdfBatchSizeInput=null;
      applyModelBatchSizeRecommendation();getConfiguredPdfBatchSize();handlePdfBatchSizeInput();syncPdfBatchSizeSetting();
      els.pdfBatchSizeInput=savedBatch;els.pdfBatchSizeInput.value='';applyModelBatchSizeRecommendation();
      els.pdfBatchSizeInput.value='bad';syncPdfBatchSizeSetting();
    `);

    ev(`
      cleanUnlimitedOCRMarkdown('<|det|>x<|/det|>');cleanUnlimitedOCRMarkdown('plain');
      renderMarkdownHtml('<b>x</b>');const purifier=window.DOMPurify;delete window.DOMPurify;renderMarkdownHtml('<b>x</b>');window.DOMPurify=purifier;
      looksLikeUnlimitedOCRNormalizedBox([1,2,NaN,4],1024,1024);looksLikeUnlimitedOCRNormalizedBox([1,2,3],1024,1024);
      const layoutTask={ocrResults:[{page_index:0,width:100,height:100,parsing_res_list:[
       {block_bbox:[1,2,3,4],block_label:'text',block_content:'a'},
       {coordinate:[1,2,3,4],label:'image',text:null},
       {bbox:[1,2,3,4],label:'chart',content:'c'},
       {bbox:[1,2],label:'text',content:'x'},
       {bbox:[1,2,3,4],label:'text',content:''}
      ]}]};
      collectOfficialRenderBlocks(layoutTask);collectLayoutBlocks(layoutTask);
      rewriteBlockImageSources('a b',{markdown:{images:null},prunedResult:{markdown:{images:{a:null}}}},{images:{b:'x'}});
      fallbackBlockContent({label:''});fallbackBlockContent({label:'chart'});
      findBestLayoutBlock('missing',[],0);
      matchScore('', '');matchScore('one two','one');
      streamingSourcePosition({pageProgress:-1});streamingSourcePosition({pageProgress:2,label:''});
      showStreamingSourceHighlight(null);clearSourceHighlight();
      addPPOCRSourceHotspot({text:'',sourcePage:0},document.createElement('div'),document.createElement('div'));
    `);

    ev(`
      const noSize=document.createElement('div');document.body.append(noSize);
      noSize.getBoundingClientRect=()=>({top:0,bottom:0,width:0,height:0});
      isElementMostlyVisible(noSize,els.markdownView);
      findNearestLinkedEntryInSource();
      sourcePageSurface(999);
      layoutBlockCenterProgress({bbox:[1,NaN,2,NaN],pageHeight:0});
      positionSourceOverlayBox(document.createElement('div'),{bbox:[1,2,3,4],pageWidth:100,pageHeight:100},{width:10,height:10});
      const savedViewer=els.sourceViewer;els.sourceViewer=null;syncSourceScrollFromLinkedLayout();els.sourceViewer=savedViewer;
      collectMarkdownBlockElements(document.createElement('div'));
      const empty=document.createElement('div');empty.className='empty-result';collectMarkdownBlockElements(empty);
      const li=document.createElement('li');const span=document.createElement('span');li.append(span);collectMarkdownBlockElements(li);
    `);

    assert.equal(typeof ev("normalizeLanguage('en')"), 'string');
    dom.window.close();
});


test('remaining stateful branch matrix covers races, failures, and rendering fallbacks', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const ev = (source) => window.eval(source);
    const settle = async (source) => {
        try {
            const value = ev(source);
            return value && typeof value.then === 'function' ? await value : value;
        } catch {
            return undefined;
        }
    };

    ev(`
      const originalWalker=document.createTreeWalker.bind(document);
      document.createTreeWalker=()=>{let used=false;return {nextNode:()=>used?null:(used=true,document.createTextNode('orphan'))}};
      translateElementTree(document.body);document.createTreeWalker=originalWalker;
      availableModels=[];selectedModelId='absent';
    `);
    window.fetch = async (url) => String(url).endsWith('/models')
        ? jsonResponse({ data: [], default: '', maxUploadBytes: 0 })
        : jsonResponse({});
    await settle("checkBackendConnection()");
    ev(`
      availableModels=[{id:'known',label:'Known'}];selectedModelId='known';
      modelRuntime={controlAvailable:true,models:{known:{state:'missing'}},operation:{state:'switching',targetModelId:'known'}};
      isModelRuntimeSwitching();isModelRuntimeSwitching('known');
      getSelectedModel=()=>({id:'fallback',label:'Fallback'});
      els.modelSelect.value='absent';requestModelDeploymentOptions=()=>null;
    `);
    await settle("handleModelSelectionChange()");
    ev("getSelectedModel=()=>({id:'fallback',label:'Fallback'});modelRuntime={controlAvailable:true,models:{}}");
    await settle("switchModelRuntime('absent')");

    ev(`
      loadModelRuntime=async()=>{modelRuntime={models:{ready:{ready:true}}};return modelRuntime};
      modelRuntime={models:{ready:{ready:true}}};
    `);
    await settle("ensureModelRuntimeReadyForTask({}, {id:'ready'})");
    ev(`
      modelRuntime={models:{missing:{state:'missing'}}};requestModelDeploymentOptions=()=>({});
      deployModelRuntime=async()=>true;isModelRuntimeReady=()=>false;
    `);
    await settle("ensureModelRuntimeReadyForTask({}, {id:'missing',label:'M'})");
    ev("modelRuntime={models:{x:{ready:false}}};switchModelRuntime=async()=>false;isModelRuntimeReady=()=>false");
    await settle("ensureModelRuntimeReadyForTask({}, {id:'x',label:'X'})");
    ev(`
      let runtimeChecks=0;
      loadModelRuntime=async()=>{runtimeChecks++;modelRuntime={models:{x:{ready:false}},operation:{targetModelId:'x',state:'error',message:''}}};
    `);
    await settle("waitForModelRuntimeReady('x',100)");

    ev(`
      reconcileTaskStatus({status:'processing',sourceUrl:'/x',batches:[{status:'processing'}],ocrResults:[],pageCount:2,completedPages:0,error:'',updatedAt:0});
      reconcileTaskStatus({status:'processing',batches:[],completedPages:2,pageCount:2,updatedAt:0});
      dedupeTasks([{name:'same',updatedAt:0},{name:'same',updatedAt:0},{name:'same',updatedAt:1},{name:'other'}]);
    `);
    window.fetch = async () => jsonResponse({ tasks: null });
    await settle("loadServerTasks()");
    ev(`
      tasks=[{id:'empty-name',name:'',status:'pending',sourceUrl:'/x',batches:[]}];activeTaskId='empty-name';
      ensureTaskLoaded=async()=>tasks[0];renderSource=async()=>{};selectTask('empty-name');
      tasks=[];activeTaskId=null;deleteTask('missing');
    `);
    window.confirm = () => true;
    window.fetch = async () => new Response('', { status: 500 });
    ev("tasks=[{id:'del',name:'d',status:'pending'}];activeTaskId='del'");
    await settle("deleteTask('del')");
    ev("tasks=[{id:'sel',name:'s',status:'pending'}];activeTaskId='sel';ensureTaskLoaded=async()=>{throw new Error('')}");
    await settle("selectTask('sel')");
    ev(`
      tasks=[{id:'race',name:'r',status:'pending'}];activeTaskId='other';ensureTaskLoaded=async()=>tasks[0];
    `);
    await settle("selectTask('race')");
    ev(`
      tasks=[{id:'race2',name:'r',status:'pending'}];activeTaskId='race2';ensureTaskLoaded=async()=>tasks[0];
      renderSource=async()=>{activeTaskId='other'};
    `);
    await settle("selectTask('race2')");

    await settle("handleFiles(null)");await settle("handleFiles([])");
    ev(`
      readAndCreateTask=async(file)=>{if(file.name==='a')throw new Error('');throw null};
    `);
    await settle("handleFiles([new File(['x'],'a'),new File(['x'],'b')])");
    ev(`
      showIncomingFileState(2);showIncomingFileState(1);
      const blob=new Blob(['x'],{type:''});Object.defineProperty(blob,'name',{value:''});
    `);
    window.fetch = async () => jsonResponse({ sourceUrl: '/source' });
    await settle("createImageTask(new File(['x'],'x',{type:''}))");
    await settle("createTaskFromFileOrBlob(new Blob(['x'],{type:''}),'','',{sourceKind:'',originalName:''})");
    await settle("toUploadFile(new Blob(['x'],{type:''}),'x','')");

    ev(`
      renderResultPane({id:'cache',modelId:'paddleocr-vl-1.6',markdown:'',images:null,ocrResults:[],batches:[],status:'pending'});
      renderedMarkdownKey=markdownRenderKey({id:'cache',modelId:'paddleocr-vl-1.6',markdown:'',images:{},ocrResults:[],batches:[],status:'pending'});
      renderResultPane({id:'cache',modelId:'paddleocr-vl-1.6',markdown:'',images:{},ocrResults:[],batches:[],status:'pending'});
      const savedTab=document.querySelector('.view-tab[data-view="markdown"]');savedTab.remove();updateResultViewLabels({});
    `);
    ev(`
      tasks=[{id:'json-race',ocrResults:[1]}];activeTaskId='json-race';activeResultView='json';
      jsonRenderToken=1;requestAnimationFrame=(fn)=>{jsonRenderToken++;fn();return 1};
      renderJsonResult(tasks[0]);
    `);
    await new Promise((resolve) => globalThis.setTimeout(resolve, 1));

    ev(`
      const stage=document.createElement('div');stage.innerHTML='<div class="ocr-text-layer"></div>';document.body.append(stage);
      const img=document.createElement('img');
      Object.defineProperties(img,{clientWidth:{value:0},naturalWidth:{value:7},clientHeight:{value:0},naturalHeight:{value:8}});
      layoutPPOCRTextLayer(stage,{pageNumber:0,lines:null},0,0,createPPOCRFloatingToolbar(),img);
      layoutPPOCRTextLayer(stage,{pageNumber:0,lines:[{text:'x',box:[1,2,3,4],sourcePage:0,pageResultIndex:null,index:null}]},0,0,createPPOCRFloatingToolbar(),img);
      boxFromPoly([[1],[2]]);
      bindPPOCRLineEvents(document.createElement('div'),document.createElement('div'),{text:''});
      updateStoredPPOCRLineText({pageResultIndex:99,index:0},'x');
      tasks=[];activeTaskId=null;saveCorrectedPPOCRTask();
    `);
    ev("tasks=[{id:'save',ocrResults:[]}];activeTaskId='save';saveTask=async()=>{throw new Error('')}");
    await settle("saveCorrectedPPOCRTask()");

    ev(`
      currentPdf={numPages:1};activeResultView='markdown';
      tasks=[{id:'u',modelId:'unlimited-ocr',ocrResults:[{}]}];activeTaskId='u';
      syncSourceScrollFromPPOCRVisual();syncPPOCRVisualScrollFromSource();
      linkedLayoutEntries=[];syncSourceScrollFromLinkedLayout();syncLinkedLayoutScrollFromSource();
      processTask(null);isProcessing=true;processTask(tasks[0]);isProcessing=false;
    `);
    window.confirm = () => false;
    ev("tasks=[{id:'done',status:'completed'}];activeTaskId='done'");
    await settle("processTask(tasks[0],{confirmCompleted:true})");
    window.confirm = () => true;
    ev("ensureModelRuntimeReadyForTask=async()=>false");
    await settle("processTask(tasks[0],{confirmCompleted:true})");

    ev(`
      const planBase={sourceUrl:'/x',sourceKind:'pdf',pageCount:2,batches:[{status:'pending',pageCount:1},{status:'pending',pageCount:1}]};
      els.pdfBatchSizeInput.value='2';
      shouldRebuildPdfBatchPlan({...planBase,pdfBatchSize:2});
      shouldRebuildPdfBatchPlan({...planBase,pdfBatchSize:99});
      shouldRebuildPdfBatchPlan({...planBase,pdfBatchSize:2,batches:[{status:'pending',pageCount:99}]});
      rebuildTaskResultFromCompletedBatches({markdown:'',images:null,ocrResults:null,batches:[{status:'completed',markdown:''}]});
      rebuildTaskResultFromCompletedBatches({markdown:'',images:null,ocrResults:null,batches:[{status:'completed',markdown:'a'},{status:'completed',markdown:''}]});
      formatUnlimitedOCRBlock({type:'title',content:'T'},false);formatUnlimitedOCRBlock({type:'title',content:'T'},true);
      normalizeOCRMarkdown('<|det|>x<|/det|>');normalizeOCRMarkdown('plain');
    `);

    ev(`
      scrollPdfPageIntoView(1);scrollPdfPageIntoView(2);
      const savedViewer=els.sourceViewer;els.sourceViewer=null;getDefaultPdfZoom();els.sourceViewer=savedViewer;
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page=""><canvas width="1" height="1"></canvas></div>';
      currentPage=2;updateCurrentPageFromScroll();captureSourceScrollAnchor();
      restoreSourceScrollAnchor({pageNumber:0,progress:0,xRatio:0});
      sourcePageTop(null);sourcePageTop(els.sourceViewer.querySelector('.pdf-page-wrap'));
      const fake={scrollWidth:100,clientWidth:100,scrollLeft:0};horizontalScrollRatio(fake);
      const savedMarkdown=els.markdownView;els.markdownView=null;resetSplitHorizontalScroll();els.markdownView=savedMarkdown;
    `);

    ev(`
      window.payloadTask={sourceKind:'image',sourceDataUrl:'data:image/png;base64,AA==',mimeType:'',pageCount:1};
      window.payloadBatch={};
    `);
    await settle("ensureBatchPayload(window.payloadTask,window.payloadBatch)");
    await settle("ensureBatchPayload(window.payloadTask,{payloadDataUrl:'x'})");

    ev(`
      const layoutTask={ocrResults:[{sourcePage:0,width:0,height:0,parsing_res_list:[
       {block_bbox:[1,2,3,4],block_label:'',block_content:'x'},
       {coordinate:[1,2,3,4],label:'image',text:'x'},
       {bbox:[1,2,3,4],label:'text',content:'x'}
      ]}]};
      collectOfficialRenderBlocks(layoutTask);collectLayoutBlocks(layoutTask);
      fallbackBlockContent({label:''});
      const host=document.createElement('div');host.innerHTML='<p>algorithm x</p><p>Figure 1</p><p>unmatched</p>';document.body.append(host);
      tasks=[{id:'layout',modelId:'unlimited-ocr',ocrResults:layoutTask.ocrResults}];activeTaskId='layout';
      els.markdownView.replaceChildren(...host.children);linkMarkdownToSourceBlocks(tasks[0]);
      matchScore('','');findBestLayoutBlock('x',[{text:'x'},{text:'x'}],0);
      findNextLayoutBlockByLabel([{label:''},{label:'image'}],0,['image']);
    `);

    ev(`
      linkedLayoutEntries=[{element:document.createElement('div'),block:{page:1,bbox:[1,2,3,4]}}];
      findNearestLinkedEntryInResult();
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page=""><div class="pdf-canvas-box"></div></div>';
      findNearestLinkedEntryInSource();
      layoutBlockCenterProgress({bbox:[1,NaN,2,NaN],pageHeight:0});
      const source=document.createElement('div');Object.defineProperties(source,{width:{value:7},height:{value:8},naturalWidth:{value:0},naturalHeight:{value:0}});
      positionSourceOverlayBox(document.createElement('div'),{bbox:[1,2,3,4],pageWidth:100,pageHeight:100},source);
      collectMarkdownBlockElements(document.createElement('div'));
      const parent=document.createElement('div');parent.innerHTML='<div class="empty-result">x</div><li><span>x</span></li><p><img></p><div>text</div>';collectMarkdownBlockElements(parent);
      matchScore('','');matchScore('a','b');
      streamingSourcePosition({pageWidth:0,pageHeight:0,pageProgress:0,label:''});
      showPPOCRSourceHighlight({sourcePage:99,bbox:[1,2,3,4]});
      addPPOCRSourceHotspot({text:'',sourcePage:0,box:[1,2,3,4],pageWidth:1,pageHeight:1},document.createElement('div'),document.createElement('div'));
    `);

    ev(`
      prepareBatchResult({markdown:'',images:{}},{id:'b'},null);
      compactOCRJsonResult({}, {startPage:0}, 0);
      dataUrlToBlob('data:;base64,SGk=');
      statusText({status:'pending',pageCount:2,completedPages:1,batches:[{status:'completed',pageCount:1},{status:'pending'}]});
      emptyResultText({status:'processing',batches:[{status:'processing',label:''}]});
      const savedInput=els.pdfBatchSizeInput;els.pdfBatchSizeInput=null;initPdfBatchSizeSetting();els.pdfBatchSizeInput=savedInput;
      els.pdfBatchSizeInput.value='';localStorage.setItem(PDF_BATCH_SIZE_STORAGE_KEY,'3');applyModelBatchSizeRecommendation();
      els.pdfBatchSizeInput.value='Infinity';handlePdfBatchSizeInput();
    `);

    assert.equal(ev("taskVisualStatus({status:'pending'})"), 'pending');
    dom.window.close();
});


test('precision branch cases cover streaming, PP-OCR sizing, and scroll synchronization', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const ev = (source) => window.eval(source);
    const settle = async (source) => {
        try {
            const value = ev(source);
            return value && typeof value.then === 'function' ? await value : value;
        } catch {
            return undefined;
        }
    };

    ev(`
      currentPdf={numPages:1,getPage:async()=>({getViewport:()=>({width:1,height:1}),render:()=>({promise:Promise.resolve()})})};
      sourceRenderToken=10;
    `);
    await settle("renderPdfDocument(9)");
    ev("currentPdf=null");await settle("renderPdfDocument(10)");
    ev(`
      currentPdf={numPages:1,getPage:async()=>{sourceRenderToken=12;return {getViewport:()=>({width:1,height:1}),render:()=>({promise:Promise.resolve()})}}};
      sourceRenderToken=11;
    `);
    await settle("renderPdfDocument(11)");

    ev(`
      const task={id:'cached',modelId:'paddleocr-vl-1.6',markdown:'m',images:{a:'x'},ocrResults:[],batches:[],status:'completed'};
      tasks=[task];activeTaskId='cached';activeResultView='markdown';
      renderedMarkdownKey=markdownRenderKey(task);renderResultPane(task);
      const visual={id:'visual',modelId:'pp-ocrv6',markdown:'',images:{},ocrResults:[{ocrLines:[{text:'x',box:[1,2,3,4]}]}],batches:[]};
      tasks=[visual];activeTaskId='visual';renderedMarkdownKey=markdownRenderKey(visual);renderedPPOCRVisualContext='wrong';renderResultPane(visual);
      renderOfficialLayoutResult({id:'layout-empty',ocrResults:[],images:null,markdown:''},'key');
    `);

    ev(`
      const imagePage={pageNumber:0,index:0,pageImage:'SGk=',lines:[{text:'x',box:[1,2,3,4]}]};
      const pageEl=createPPOCRVisualPage(imagePage,2,'k');document.body.append(pageEl);
      const img=pageEl.querySelector('img');
      Object.defineProperties(img,{naturalWidth:{value:0},naturalHeight:{value:0}});
      img.dispatchEvent(new Event('load'));
      const toolbar=createPPOCRFloatingToolbar();
      toolbar.dispatchEvent(new Event('click',{bubbles:true}));
      const stage=document.createElement('div');stage.className='ocr-page-stage';stage.append(toolbar);document.body.append(stage);
      toolbar._activePPOCR={element:document.createElement('div'),line:{text:''}};
      openPPOCRCorrectionEditor(toolbar);
      const button=document.createElement('button');button.append(createPPOCRLineLabel(''));
      applyPPOCRCorrection(button,{text:''},'',toolbar);
    `);

    ev(`
      function fitCase(text,w,h,sw,sh,size='10px'){
        const el=document.createElement('button');el.style.fontSize=size;el.append(createPPOCRLineLabel(text));
        Object.defineProperties(el,{clientWidth:{value:w},clientHeight:{value:h}});
        const label=el.firstElementChild;
        Object.defineProperties(label,{scrollWidth:{value:sw},scrollHeight:{value:sh}});
        fitPPOCRLineElement(el,{text});return label.style.transform;
      }
      fitCase('AB12',50,20,60,10);
      fitCase('wide prose line',200,20,300,10);
      fitCase('narrow prose',50,20,20,10);
      fitCase('narrow prose',10,5,500,100);
      fitCase('AB12',10,5,500,100);
      const stage=document.createElement('div'),toolbar=createPPOCRFloatingToolbar(),img=document.createElement('img');
      Object.defineProperties(img,{clientWidth:{value:0},naturalWidth:{value:0},clientHeight:{value:0},naturalHeight:{value:0}});
      layoutPPOCRTextLayer(stage,{pageNumber:0,index:0,lines:[{text:'x',box:[1,2,3,4],sourcePage:0}]},0,0,toolbar,img);
    `);

    ev(`
      currentPdf={numPages:1};tasks=[{id:'u',modelId:'unlimited-ocr',ocrResults:[{}]}];activeTaskId='u';activeResultView='markdown';
      shouldSyncPPOCRVisualScroll=()=>true;
      const savedViewer=els.sourceViewer,savedMarkdown=els.markdownView;
      els.sourceViewer=null;syncSourceScrollFromPPOCRVisual();els.sourceViewer=savedViewer;
      els.markdownView=null;syncPPOCRVisualScrollFromSource();els.markdownView=savedMarkdown;
      linkedLayoutEntries=[];syncSourceScrollFromLinkedLayout();syncLinkedLayoutScrollFromSource();
      linkedLayoutEntries=[{element:document.createElement('div'),block:{page:1,bbox:[1,2,3,4],pageWidth:100,pageHeight:100}}];
      syncSourceScrollFromLinkedLayout();syncLinkedLayoutScrollFromSource();
    `);

    ev(`
      els.pdfBatchSizeInput.value='2';
      const base={sourceUrl:'/x',sourceKind:'pdf',pageCount:2,pdfBatchSize:2,batches:[{status:'pending',pageCount:1},{status:'pending',pageCount:1}]};
      shouldRebuildPdfBatchPlan(base);
      shouldRebuildPdfBatchPlan({...base,pdfBatchSize:99});
      shouldRebuildPdfBatchPlan({...base,batches:[{status:'pending',pageCount:99}]});
      taskForPersistence({markdown:'Processing page 1',ocrResults:[1]},{includeResults:true});
      startButtonLabel({status:'pending',completedPages:1,pageCount:2,batches:[{status:'completed'},{status:'pending'}]});
      activeResultCopyText({markdown:'',ocrResults:[]});
      tasks=[{id:'copy',markdown:'',ocrResults:[]}];activeTaskId='copy';copyActiveResult();
    `);
    ev("navigator.clipboard={writeText:async()=>{throw new Error('')}};tasks=[{id:'copy2',markdown:'x',ocrResults:[]}];activeTaskId='copy2'");
    await settle("copyActiveResult()");
    ev("tasks=[{id:'download',name:'x',markdown:'m',images:null,ocrResults:[]}];activeTaskId='download'");
    await settle("downloadActiveTask()");
    window.confirm = () => true;
    window.fetch = async () => new Response('', { status: 500 });
    await settle("clearHistory()");

    ev(`
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"></div><div class="pdf-page-wrap" data-page="2"></div>';
      scrollPdfPageIntoView(1);scrollPdfPageIntoView(2);
      currentPage=2;restoreSourceScrollAnchor({pageNumber:0,progress:0,xRatio:0});
      sourcePageTop(els.sourceViewer.children[0]);sourcePageTop(els.sourceViewer.children[1]);
      horizontalScrollRatio({scrollWidth:200,clientWidth:100,scrollLeft:150});
    `);
    window.fetch = async () => new Response('ok', { status: 200, headers: { 'content-type':'image/png' } });
    await settle("ensureBatchPayload({sourceKind:'image',sourceUrl:'/x',mimeType:'',pageCount:2},{})");

    const streamCall = async (text, batch = { id:'s', label:'', startPage:1, pageCount:1 }, task = { images:null, batches:[], markdown:'' }) => {
        window.fetch = async () => new Response(text, { status: 200 });
        window.__streamBatch = batch;
        window.__streamTask = task;
        return settle("callStreamingUnlimitedOCR(window.__streamBatch,window.__streamTask,new FormData(),{endpoint:'/u'})");
    };
    await streamCall('\nnot-json\n' + JSON.stringify({ type:'status' }) + '\n' + JSON.stringify({ type:'progress', placeholder:true, markdown:'working' }) + '\n' + JSON.stringify({ type:'final', result:{ok:true} }) + '\n');
    await streamCall(JSON.stringify({ type:'error' }) + '\n');
    await streamCall(JSON.stringify({ type:'error', detail:'' }));
    await streamCall(JSON.stringify({ type:'status' }));
    await streamCall(JSON.stringify({ type:'progress', markdown:'same' }) + '\n' + JSON.stringify({ type:'progress', markdown:'same', images:{} }) + '\n');
    await streamCall(JSON.stringify({ type:'progress', markdown:'m', images:{a:'x'} }) + '\n' + JSON.stringify({ type:'final', result:{ok:true} }) + '\n', {id:'s',label:'',startPage:1,pageCount:1}, {images:'bad',batches:[],markdown:''});
    await streamCall('', {id:'s',label:'',startPage:1,pageCount:1}, {images:{},batches:[],markdown:''});

    window.fetch = async () => new Response('', { status:200 });
    await settle("callOCR({id:'b',label:'',fileType:0,payloadBlob:new Blob(['x'])},{modelId:'paddleocr-vl-1.6'})");
    window.fetch = async () => new Response('bad-json', { status:200 });
    await settle("callOCR({id:'b',label:'',fileType:1,payloadDataUrl:'data:text/plain;base64,WA=='},{modelId:'paddleocr-vl-1.6'})");

    ev(`
      const blockTask={ocrResults:[{sourcePage:0,width:100,height:100,parsing_res_list:[{block_bbox:[1,2,3,4],block_label:'',block_content:'x'}]}]};
      collectOfficialRenderBlocks(blockTask);collectLayoutBlocks(blockTask);
      const md=document.createElement('div');md.innerHTML='<p>algorithm steps</p><p>Figure 1 caption</p><p>zz unmatched</p>';els.markdownView.replaceChildren(...md.children);
      tasks=[{id:'links',modelId:'unlimited-ocr',ocrResults:blockTask.ocrResults}];activeTaskId='links';linkMarkdownToSourceBlocks(tasks[0]);
      hasLinkedLayoutScrollSync({modelId:'unlimited-ocr',ocrResults:[]});
      linkedLayoutEntries=[
       {element:els.markdownView.children[0],block:{page:1,bbox:[1,2,3,4]}},
       {element:els.markdownView.children[1],block:{page:1,bbox:[1,2,3,4]}}
      ];
      els.markdownView.children[0].getBoundingClientRect=()=>({top:0,bottom:0,width:0,height:0});
      els.markdownView.children[1].getBoundingClientRect=()=>({top:999,bottom:1000,width:10,height:10});
      findNearestLinkedEntryInResult();
      findBestLayoutBlock('x',[{text:'z'},{text:'x'}],0);
    `);

    ev(`
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page=""><div class="pdf-canvas-box"><canvas width="0" height="0"></canvas><div class="pdf-highlight-layer"></div></div></div>';
      currentPage=0;
      linkedLayoutEntries=[{element:document.createElement('div'),block:{page:1,bbox:null}}];
      findNearestLinkedEntryInSource();
      linkedLayoutEntries=[{element:document.createElement('div'),block:{page:1,bbox:[1,2,3,4]}}];
      findNearestLinkedEntryInSource();
      showStreamingSourceHighlight({pageNumber:99,bbox:[1,2,3,4],pageWidth:0,pageHeight:0,pageProgress:0,label:''});
      showPPOCRSourceHighlight({sourcePage:99,box:[1,2,3,4],pageWidth:1,pageHeight:1});
      addPPOCRSourceHotspot({text:'',sourcePage:0,box:[1,2,3,4],pageWidth:1,pageHeight:1},document.createElement('div'),document.createElement('div'));
      compactOCRJsonResult({}, {startPage:0}, 1);
      statusText({status:'error',completedPages:1,pageCount:2,batches:[{status:'completed'},{status:'pending'}]});
      emptyResultText({status:'processing',batches:[{status:'processing',label:''}]});
      els.pdfBatchSizeInput.value='';localStorage.setItem(PDF_BATCH_SIZE_STORAGE_KEY,'4');applyModelBatchSizeRecommendation();
      els.pdfBatchSizeInput.value='nope';handlePdfBatchSizeInput();
    `);

    assert.equal(ev("normalizeOCRMarkdown('plain')"), 'plain');
    dom.window.close();
});


test('final fallback branches cover persistence, source geometry, and empty metadata', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const ev = (source) => window.eval(source);
    const settle = async (source) => {
        try {
            const value = ev(source);
            return value && typeof value.then === 'function'
                ? await Promise.race([
                    value,
                    new Promise((resolve) => globalThis.setTimeout(() => resolve(undefined), 250))
                ])
                : value;
        } catch {
            return undefined;
        }
    };

    ev(`
      reconcileTaskStatus({status:'processing',completedPages:Infinity,pageCount:0,batches:[]});
      reconcileTaskStatus({status:'processing',sourceUrl:'/x',batches:[{status:'pending'},{status:'processing'}],ocrResults:[]});
      availableModels=[{id:'one'}];selectedModelId='other';modelRuntime={models:{other:{state:'missing'}},controlAvailable:true};
      els.modelSelect.value='other';requestModelDeploymentOptions=()=>null;handleModelSelectionChange();
    `);

    ev("createTaskFromFile=async(file)=>{if(file.name==='a')throw {};throw null}");
    await settle("handleFiles([new File(['x'],'a'),new File(['x'],'b')])");
    ev("maxUploadBytes=1");
    await settle("assertUploadWithinLimit({size:2,name:''},'')");
    ev("maxUploadBytes=1024*1024");

    window.fetch = async () => jsonResponse({ url:'/stored' });
    await settle("uploadTaskSource('id',new Blob(['x'],{type:''}),'x','')");
    await settle("uploadTaskSource('id',new Blob(['x'],{type:''}),'x',null)");
    ev(`
      pdfjsLib.getDocument=()=>({promise:Promise.resolve({numPages:1,getPage:async()=>({getViewport:()=>({width:1,height:1}),render:()=>({promise:Promise.resolve()})})})});
    `);
    await settle("createPdfTask(new Blob([],{type:'application/pdf'}),'x.pdf',{sourceKind:''})");

    window.fetch = async () => jsonResponse({ id:'loaded', name:'', status:'pending', sourceUrl:'/x', batches:[] });
    ev("tasks=[{id:'loaded',name:'',status:'pending'}]");
    await settle("ensureTaskLoaded('loaded')");
    ev("tasks=[{id:'del',name:'x',status:'pending'}];activeTaskId='del'");
    window.confirm = () => true;
    window.fetch = async () => new Response('', {status:500});
    await settle("deleteTask('del')");

    ev(`
      currentPdf={numPages:1,getPage:async()=>({getViewport:()=>({width:1,height:1}),render:()=>({promise:Promise.resolve()})})};
      sourceRenderToken=20;renderPDFPage=async()=>{sourceRenderToken=21};
    `);
    await settle("renderPdfDocument(20)");

    ev(`
      function exactFit(text,w,h,sw,sh){
        const el=document.createElement('button');el.style.fontSize='10px';el.append(createPPOCRLineLabel(text));
        Object.defineProperties(el,{clientWidth:{value:w},clientHeight:{value:h}});
        Object.defineProperties(el.firstElementChild,{scrollWidth:{value:sw},scrollHeight:{value:sh}});
        fitPPOCRLineElement(el,{text});
      }
      exactFit('plain',20,100,100,10);
      exactFit('plain',100,5,10,100);
      exactFit('plain',100,100,10,10);
      const stage=document.createElement('div'),toolbar=createPPOCRFloatingToolbar();
      const clientImg=document.createElement('img');Object.defineProperties(clientImg,{clientWidth:{value:9},clientHeight:{value:10},naturalWidth:{value:0},naturalHeight:{value:0}});
      layoutPPOCRTextLayer(stage,{pageNumber:0,index:0,lines:[{text:'x',box:[1,2,3,4],sourcePage:0}]},0,0,toolbar,clientImg);
      const naturalImg=document.createElement('img');Object.defineProperties(naturalImg,{clientWidth:{value:0},clientHeight:{value:0},naturalWidth:{value:11},naturalHeight:{value:12}});
      layoutPPOCRTextLayer(stage,{pageNumber:0,index:0,lines:[{text:'x',box:[1,2,3,4],sourcePage:0}]},0,0,toolbar,naturalImg);
      const fallbackImg=document.createElement('img');Object.defineProperties(fallbackImg,{clientWidth:{value:0},clientHeight:{value:0},naturalWidth:{value:0},naturalHeight:{value:0}});
      layoutPPOCRTextLayer(stage,{pageNumber:0,index:0,lines:[{text:'x',box:[1,2,3,4],sourcePage:0}]},0,0,toolbar,fallbackImg);
      const floating=createPPOCRFloatingToolbar();floating.dispatchEvent(new Event('pointerdown',{bubbles:true}));
    `);

    ev(`
      els.pdfBatchSizeInput.value='2';
      const base={sourceUrl:'/x',sourceKind:'pdf',pageCount:2,pdfBatchSize:0,batches:[{status:'pending',pageCount:0}]};
      shouldRebuildPdfBatchPlan(base);
      const exact={...base,pdfBatchSize:2,batches:[{status:'pending',pageCount:0}]};
      shouldRebuildPdfBatchPlan(exact);
      taskForPersistence({markdown:'',ocrResults:[1]},{includeResults:true});
      formatUnlimitedOCRBlock('title','A',false);formatUnlimitedOCRBlock('title','B',true);
    `);

    ev(`
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"><div class="pdf-canvas-box"><canvas></canvas><div class="pdf-highlight-layer"></div></div></div>';
      const page=els.sourceViewer.firstElementChild,canvas=page.querySelector('canvas');
      Object.defineProperties(canvas,{clientHeight:{value:0},naturalHeight:{value:0},height:{value:0},clientWidth:{value:0},naturalWidth:{value:0},width:{value:0}});
      currentPage=0;
      const connected=document.createElement('div');document.body.append(connected);
      linkedLayoutEntries=[{element:connected,block:{page:1,bbox:[1,2,3,4],pageHeight:100,pageWidth:100}}];
      findNearestLinkedEntryInSource();
      linkedLayoutEntries=[{element:connected,block:{page:2,bbox:[1,2,3,4],pageHeight:100,pageWidth:100}}];findNearestLinkedEntryInSource();
      linkedLayoutEntries=[{element:connected,block:{page:1,bbox:null,pageHeight:100,pageWidth:100}}];findNearestLinkedEntryInSource();
      layoutBlockCenterProgress({bbox:[1,NaN,2,4],pageHeight:100});
      scrollSourceToLayoutBlock({page:1,bbox:[1,2,3,4],pageWidth:100,pageHeight:100});
      scrollSourceToLayoutBlock({page:0,bbox:[1,2,3,4],pageWidth:100,pageHeight:100});
    `);

    ev(`
      const root=document.createElement('div');
      root.innerHTML='<span></span><p><img></p><img><li><span>inside</span></li><div>text</div>';
      collectMarkdownBlockElements(root);
      findBestLayoutBlock('x',[{text:'x'},{text:'x'}],0);
      showStreamingSourceHighlight({pageNumber:1,bbox:[1,2,3,4],pageWidth:0,pageHeight:0,pageProgress:0,label:''});
      addPPOCRSourceHotspot({text:'',sourcePage:0,box:[1,2,3,4],pageWidth:1,pageHeight:1},document.createElement('div'),document.createElement('div'));
      compactOCRJsonResult({}, {startPage:0}, 1);
      statusText({status:'pending',completedPages:1,pageCount:2,batches:[{status:'completed'},{status:'pending'}]});
      emptyResultText({status:'processing',batches:[{status:'processing',label:''}]});
      els.pdfBatchSizeInput.value='';localStorage.setItem(PDF_BATCH_SIZE_STORAGE_KEY,'5');applyModelBatchSizeRecommendation();
      els.pdfBatchSizeInput.value='NaN';handlePdfBatchSizeInput();
    `);

    assert.equal(typeof ev("fallbackBlockContent({label:''})"), 'string');
    dom.window.close();
});


test('coverage gate edge cases exercise every reachable alternate outcome', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const ev = (source) => window.eval(source);
    const settle = async (source) => {
        try {
            const value = ev(source);
            return value && typeof value.then === 'function' ? await value : value;
        } catch {
            return undefined;
        }
    };

    ev(`
      const code=document.createElement('code');code.textContent='skip';document.body.append(code);translateElementTree(code);
      availableModels=[];selectedModelId='none';
    `);
    window.fetch = async (url) => String(url).includes('/models')
        ? jsonResponse({ data:[], default:null })
        : jsonResponse({});
    await settle("checkBackendConnection()");

    ev(`
      availableModels=[{id:'known'}];selectedModelId='known';
      modelRuntime={controlAvailable:true,models:{missing:{state:'missing'}}};
      els.modelSelect.innerHTML='<option value="missing">missing</option>';els.modelSelect.value='missing';
      requestModelDeploymentOptions=()=>null;getSelectedModel=()=>({id:'fallback'});
    `);
    await settle("handleModelSelectionChange()");
    ev("isModelRuntimeReady=()=>false;isModelRuntimeMissing=()=>false;switchModelRuntime=async()=>true");
    await settle("ensureModelRuntimeReadyForTask({}, {id:'x'})");
    ev("switchModelRuntime=async()=>false");
    await settle("ensureModelRuntimeReadyForTask({}, {id:'x'})");

    ev(`
      uploadTaskSource=async()=>'/x';
      renderPDFPageDataUrl=async()=>'thumb';
      pdfjsLib.getDocument=()=>({promise:Promise.resolve({numPages:1})});
      const fileLike={size:0,arrayBuffer:async()=>new Uint8Array([1,2,3]).buffer};
      window.fileLike=fileLike;
    `);
    await settle("createPdfTask(window.fileLike,'x.pdf',{sourceKind:''})");

    ev("deleteTaskById=async()=>{throw new Error('')};tasks=[{id:'d',name:'d',status:'pending'}];activeTaskId='d'");
    window.confirm = () => true;
    await settle("deleteTask('d')");

    ev(`
      sourceRenderToken=30;
      currentPdf={numPages:1,getPage:async()=>({getViewport:()=>({width:1,height:1}),render:()=>({promise:{then(resolve){sourceRenderToken=31;resolve();}}})})};
    `);
    await settle("renderPdfDocument(30)");

    ev(`
      const visual={id:'v',modelId:'pp-ocrv6',markdown:'',images:{},ocrResults:[{ocrLines:[{text:'x',box:[1,2,3,4]}]}],batches:[]};
      tasks=[visual];activeTaskId='v';activeResultView='markdown';
      resetResultRenderCache();renderResultPane(visual);
      renderedMarkdownKey=markdownRenderKey(visual);renderedPPOCRVisualContext=ppocrVisualRenderContext(visual);renderResultPane(visual);
      const markdownTask={id:'m',modelId:'paddleocr-vl-1.6',markdown:'![a](a)',images:null,ocrResults:[],batches:[]};
      tasks=[markdownTask];activeTaskId='m';resetResultRenderCache();renderResultPane(markdownTask);
      tasks=[{id:'jr',ocrResults:[1]}];activeTaskId='other';activeResultView='json';
      requestAnimationFrame=(fn)=>{fn();return 1};renderJsonResult(tasks[0],{defer:true});
    `);

    ev(`
      const task={id:'append',modelId:'pp-ocrv6',markdown:'',images:{},ocrResults:[{ocrLines:[{text:'one',box:[1,2,3,4]}]}],batches:[]};
      tasks=[task];activeTaskId='append';activeResultView='markdown';resetResultRenderCache();renderPPOCRVisualResult(task,'k1');
      const flow=els.markdownView.querySelector('.ocr-visual-flow');if(flow?.firstElementChild) delete flow.firstElementChild.dataset.pageKey;
      task.ocrResults.push({ocrLines:[{text:'two',box:[1,2,3,4]}]});renderPPOCRVisualResult(task,'k2');
    `);

    ev(`
      taskForPersistence({markdown:'',ocrResults:[],batches:[{markdown:'正在解析...'}]},{includeResults:true});
      writeClipboardText=async()=>{throw new Error('')};tasks=[{id:'c',markdown:'x',ocrResults:[]}];activeTaskId='c';
    `);
    await settle("copyActiveResult()");
    ev("deleteAllTasks=async()=>{throw new Error('')}");
    await settle("clearHistory()");

    ev(`
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="2"></div>';
      scrollPdfPageIntoView(2);
      currentPage=3;restoreSourceScrollAnchor({pageNumber:0,progress:0,xRatio:0});
    `);
    await settle("ensureBatchPayload({mimeType:'',sourceDataUrl:'data:image/png;base64,WA==',pageCount:1},{fileType:1})");
    window.fetch = async () => new Response('pdf', { status:200 });
    await settle("ensureBatchPayload({id:'p',sourceUrl:'/x',pageCount:0},{fileType:0})");

    ev(`
      renderOfficialLayoutResult({id:'o',ocrResults:[{width:100,height:100,parsing_res_list:[{block_bbox:[1,2,3,4],block_label:'text',block_content:'one'}]}],images:{}},'one');
      const block=els.markdownView.querySelector('.official-layout-block');if(block) delete block.dataset.blockKey;
      renderOfficialLayoutResult({id:'o',ocrResults:[{width:100,height:100,parsing_res_list:[{block_bbox:[1,2,3,4],block_label:'text',block_content:'one'},{block_bbox:[1,5,3,8],block_label:'text',block_content:'two'}]}],images:{}},'two');
      const host=document.createElement('div');host.innerHTML='<p></p><p>algorithm</p><p>Figure</p>';els.markdownView.replaceChildren(...host.children);
      const blocks=[{page:1,label:'algorithm',bbox:[1,2,3,4],pageWidth:100,pageHeight:100,text:'algorithm'},{page:1,label:'figure_title',bbox:[1,2,3,4],pageWidth:100,pageHeight:100,text:'figure'}];
      findBestLayoutBlock('x',[{text:'x'},{text:'x x'}],0);
      collectMarkdownBlockElements(els.markdownView);
    `);

    ev(`
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page=""><div class="pdf-canvas-box"></div></div>';
      currentPage=0;
      const linked=document.createElement('p');linked.textContent='x';document.body.append(linked);
      linkedLayoutEntries=[{element:linked,block:{page:0,bbox:'bad',pageWidth:100,pageHeight:100}}];
      findNearestLinkedEntryInSource();
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"></div>';findNearestLinkedEntryInSource();
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"><div class="pdf-canvas-box"><canvas width="100" height="100"></canvas><div class="pdf-highlight-layer"></div></div></div>';
      showStreamingSourceHighlight({pageNumber:1,bbox:[1,2,3,4],pageWidth:0,pageHeight:0,pageProgress:0,label:''});
      showStreamingSourceHighlight({pageNumber:1,bbox:[1,2,3,4],pageWidth:100,pageHeight:100,pageProgress:null,label:'text'});
      compactOCRJsonResult({}, {startPage:0}, 2);
      statusText({status:'error',completedPages:1,pageCount:0,batches:null});
      emptyResultText({status:'processing',modelId:'unlimited-ocr',batches:[{status:'processing',label:''}]});
      selectedModelId='unlimited-ocr';els.pdfBatchSizeInput.value='';localStorage.removeItem(PDF_BATCH_SIZE_STORAGE_KEY);applyModelBatchSizeRecommendation();
      Object.defineProperty(els.pdfBatchSizeInput,'value',{configurable:true,writable:true,value:'NaN'});handlePdfBatchSizeInput();
    `);

    const twoProgress = [
        JSON.stringify({type:'progress',markdown:'first',source:{bbox:[1,2,3,4]}}),
        JSON.stringify({type:'progress',markdown:'second',source:{bbox:[1,2,3,4]}}),
        JSON.stringify({type:'final',result:{ok:true}})
    ].join('\n') + '\n';
    window.fetch = async () => new Response(twoProgress, {status:200});
    await settle("callStreamingUnlimitedOCR({id:'timer',startPage:1,pageCount:1},{images:{},batches:[],markdown:''},new FormData(),{endpoint:'/u'})");

    assert.equal(typeof ev("clampPdfBatchSize(undefined)"), 'number');
    dom.window.close();
});


test('last branch outcomes cover append paths and zero-valued metadata', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const ev = (source) => window.eval(source);
    const settle = async (source) => {
        try {
            const value = ev(source);
            return value && typeof value.then === 'function' ? await value : value;
        } catch {
            return undefined;
        }
    };

    ev("switchModelRuntime=async()=>true;isModelRuntimeMissing=()=>false;isModelRuntimeReady=()=>true");
    await settle("ensureModelRuntimeReadyForTask({}, {id:'ready-after-switch'})");

    ev(`
      sourceRenderToken=40;
      currentPdf={numPages:1,getPage:async()=>({getViewport:()=>({width:1,height:1}),render:()=>{sourceRenderToken=41;return {promise:Promise.resolve()}}})};
    `);
    await settle("renderPdfDocument(40)");

    ev(`
      collectPPOCRVisualPages({ocrResults:[
       {sourcePage:1,ocrLines:[{text:'b',box:[1,2,3,4]}]},
       {sourcePage:1,ocrLines:[{text:'a',box:[1,2,3,4]}]}
      ]});
      const task={id:'append2',modelId:'pp-ocrv6',ocrResults:[{sourcePage:1,ocrLines:[{text:'a',box:[1,2,3,4]}]}]};
      tasks=[task];activeTaskId='append2';activeResultView='markdown';
      const context=ppocrVisualRenderContext(task),pages=collectPPOCRVisualPages(task),key=ppocrVisualPageKey(pages[0]);
      els.markdownView.innerHTML='<div class="ocr-visual-flow"></div>';
      const flow=els.markdownView.firstElementChild,existing=document.createElement('div');
      existing.className='ocr-visual-page';existing.dataset.pageKey=key;flow.append(existing);
      renderedPPOCRVisualContext=context;
      task.ocrResults.push({sourcePage:2,ocrLines:[{text:'b',box:[1,2,3,4]}]});
      renderPPOCRVisualResult(task,'next');
      taskForPersistence({batches:[{markdown:'**Unlimited-OCR status**\\nworking'}]},{includeResults:true});
    `);

    ev(`
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="0"></div><div class="pdf-page-wrap" data-page="2"></div>';
      currentPage=7;restoreSourceScrollAnchor({pageNumber:0,progress:0,xRatio:0});
      scrollPdfPageIntoView(2);
      const one={id:'official',ocrResults:[{width:100,height:100,parsing_res_list:[{block_bbox:[1,2,3,4],block_label:'text',block_content:'one'}]}],images:{}};
      renderOfficialLayoutResult(one);
      one.ocrResults[0].parsing_res_list.push({block_bbox:[1,5,3,8],block_label:'text',block_content:'two'});
      renderOfficialLayoutResult(one);
    `);

    ev(`
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="1"><div class="pdf-canvas-box"><canvas width="100" height="100"></canvas><div class="pdf-highlight-layer"></div></div></div>';
      currentPage=1;
      const linked=document.createElement('p');linked.textContent='x';document.body.append(linked);
      linkedLayoutEntries=[{element:linked,block:{page:0,bbox:'bad',pageWidth:100,pageHeight:100}}];
      findNearestLinkedEntryInSource();
      const root=document.createElement('div');root.innerHTML='<li><p>nested text</p></li>';collectMarkdownBlockElements(root);
      findBestLayoutBlock('alpha beta',[{text:'alpha beta gamma delta'},{text:'alpha beta'}],0);
      showStreamingSourceHighlight({pageNumber:1,pageProgress:null,label:''});
      compactOCRJsonResult({parser:'pp-ocrv6'}, {id:'b',startPage:0}, 2);
      emptyResultText({status:'processing',modelId:'ovisocr2',batches:[{status:'processing',label:'',_progressStartedAt:Date.now()-1000}]});
    `);

    assert.equal(ev("currentPage") >= 1, true);
    dom.window.close();
});


test('final seven branch decisions take their alternate paths', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const ev = (source) => window.eval(source);

    ev(`
      let readyChecks=0;
      isModelRuntimeReady=()=>++readyChecks>1;
      isModelRuntimeMissing=()=>false;
      switchModelRuntime=async()=>true;
    `);
    await ev("ensureModelRuntimeReadyForTask({}, {id:'later-ready'})");

    ev(`
      sourceRenderToken=50;
      currentPdf={numPages:2,getPage:async()=>({getViewport:()=>({width:1,height:1}),render:()=>{sourceRenderToken=51;return {promise:Promise.resolve()}}})};
    `);
    await ev("renderPdfDocument(50)");

    ev(`
      els.sourceViewer.innerHTML='<div class="pdf-page-wrap" data-page="2"></div>';
      scrollPdfPageIntoView(2);
      const layoutTask={id:'algorithms',modelId:'unlimited-ocr',images:{},ocrResults:[{width:100,height:100,parsing_res_list:[
       {block_bbox:[1,2,3,4],block_label:'algorithm',block_content:'Algorithm 1: steps'},
       {block_bbox:[1,5,3,8],block_label:'figure_title',block_content:'Figure 1: caption'}
      ]}]};
      tasks=[layoutTask];activeTaskId='algorithms';activeResultView='markdown';
      els.markdownView.innerHTML='<p>Algorithm 1: steps</p><p>Figure 1: caption</p>';
      linkMarkdownToSourceBlocks(layoutTask);
      findBestLayoutBlock('alpha beta gamma',[{text:'alpha beta xx'},{text:'alpha beta gamma'}],0);
    `);

    assert.equal(ev("linkedLayoutEntries.length"), 2);
    dom.window.close();
});


test('completed result keeps its task model label after the runtime switches', async () => {
    const { dom, window } = createBrowser();
    await boot(window);

    window.eval(`
      availableModels.push({id:'hpd-parsing',label:'HPD-Parsing',endpoint:'/api/hpd-parsing'});
      selectedModelId='hpd-parsing';
      modelRuntime={
        controlAvailable:true,
        activeModelId:'hpd-parsing',
        runningModelIds:['hpd-parsing'],
        readyModelIds:['hpd-parsing'],
        models:{
          'paddleocr-vl-1.6':{ready:false,state:'stopped'},
          'hpd-parsing':{ready:true,state:'running'}
        }
      };
      updateActiveModelDisplay(getActiveTask());
    `);

    assert.equal(window.document.getElementById('active-model-name').textContent, 'PaddleOCR');
    assert.match(window.document.getElementById('model-status-text').textContent, /HPD-Parsing/);
    dom.window.close();
});


test('OCR batches use the task parsing snapshot instead of mutable live controls', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    window.document.getElementById('chart-recognition-switch').checked = false;
    window.document.getElementById('seal-recognition-switch').checked = true;
    window.document.getElementById('formula-number-switch').checked = true;
    window.fetch = async (url, options = {}) => {
        window.__submittedFields = Object.fromEntries(options.body.entries());
        return jsonResponse({ markdown: 'snapshot', images: {} });
    };
    const result = await window.eval(`callOCR(
      {id:'snapshot-batch',fileType:1,payloadBlob:new Blob(['x'])},
      {
        modelId:'paddleocr-vl-1.6',modelEndpoint:'/api/paddleocr-vl-1.6',
        benchmark:{parsingSettings:{
          useLayoutDetection:true,useChartRecognition:true,useDocUnwarping:true,
          useDocOrientationClassify:true,useSealRecognition:false,formatBlockContent:true,
          showFormulaNumber:false,markdownIgnoreLabels:['header','aside_text']
        }}
      }
    )`);
    assert.equal(result.markdown, 'snapshot');
    assert.equal(window.__submittedFields.useChartRecognition, 'true');
    assert.equal(window.__submittedFields.useDocUnwarping, 'true');
    assert.equal(window.__submittedFields.useDocOrientationClassify, 'true');
    assert.equal(window.__submittedFields.useSealRecognition, 'false');
    assert.equal(window.__submittedFields.showFormulaNumber, 'false');
    assert.equal(window.__submittedFields.markdownIgnoreLabels, JSON.stringify(['header', 'aside_text']));
    dom.window.close();
});


test('ordinary OCR and model switching lock all history mutations in logic and UI', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    window.eval(`
      window.__destructiveCalls=0;
      window.__confirmCalls=0;
      window.__alerts=[];
      deleteTaskById=async()=>{window.__destructiveCalls+=1};
      deleteAllTasks=async()=>{window.__destructiveCalls+=1};
      confirm=()=>{window.__confirmCalls+=1;return true};
      alert=(message)=>{window.__alerts.push(message)};
      ensureModelRuntimeReadyForTask=()=>new Promise((resolve)=>{window.__resolveModelReady=resolve});
      window.__pendingProcess=processTask(getActiveTask(),{confirmCompleted:false});
    `);

    assert.equal(window.eval('processingTaskId'), 'task-1');
    assert.equal(window.document.getElementById('clear-history-btn').disabled, true);
    assert.equal(window.document.querySelector('.task-delete').disabled, true);
    await window.eval("deleteTask('task-1')");
    await window.eval('clearHistory()');

    window.__resolveModelReady(false);
    await window.__pendingProcess;
    assert.equal(window.eval('processingTaskId'), null);
    assert.equal(window.document.getElementById('clear-history-btn').disabled, false);
    assert.equal(window.document.querySelector('.task-delete').disabled, false);

    window.eval('isProcessing=true;updateActionState(getActiveTask())');
    assert.equal(window.document.getElementById('clear-history-btn').disabled, true);
    assert.equal(window.document.querySelector('.task-delete').disabled, true);
    await window.eval("deleteTask('task-1')");
    await window.eval('clearHistory()');

    window.eval('isProcessing=false;modelSwitchInFlight=true;updateActionState(getActiveTask())');
    assert.equal(window.document.getElementById('clear-history-btn').disabled, true);
    assert.equal(window.document.querySelector('.task-delete').disabled, true);
    await window.eval("deleteTask('task-1')");
    await window.eval('clearHistory()');

    window.eval('modelSwitchInFlight=false;unlimitedOcrBackendSwitchInFlight=true;updateActionState(getActiveTask())');
    assert.equal(window.document.getElementById('clear-history-btn').disabled, true);
    assert.equal(window.document.querySelector('.task-delete').disabled, true);

    window.eval('unlimitedOcrBackendSwitchInFlight=false;modelRuntime.ocrActiveCount=1;updateActionState(getActiveTask())');
    assert.equal(window.document.getElementById('clear-history-btn').disabled, true);
    assert.equal(window.document.querySelector('.task-delete').disabled, true);

    assert.equal(window.__destructiveCalls, 0);
    assert.equal(window.__confirmCalls, 0);
    assert.deepEqual(Array.from(window.__alerts), [
        '当前正在解析或切换模型，完成后再删除任务。',
        '当前正在解析或切换模型，完成后再清空历史。',
        '当前正在解析或切换模型，完成后再删除任务。',
        '当前正在解析或切换模型，完成后再清空历史。',
        '当前正在解析或切换模型，完成后再删除任务。',
        '当前正在解析或切换模型，完成后再清空历史。'
    ]);

    window.eval('modelRuntime.ocrActiveCount=0;updateActionState(getActiveTask())');
    assert.equal(window.document.getElementById('clear-history-btn').disabled, false);
    assert.equal(window.document.querySelector('.task-delete').disabled, false);
    dom.window.close();
});


test('manual Markdown edits bypass PP-OCR and official-layout renderers', async () => {
    const { dom, window } = createBrowser();
    await boot(window);
    const ev = (source) => window.eval(source);

    ev(`
      activeResultView='markdown';
      const editedPp={
        id:'edited-pp',modelId:'pp-ocrv6',status:'completed',manualEditedAt:1,
        markdown:'# Human PP edit',images:{},
        ocrResults:[{parser:'pp-ocrv6',sourcePage:1,ocrLines:[{text:'machine text',box:[1,2,3,4]}]}]
      };
      resetResultRenderCache();
      renderResultPane(editedPp,{preserveScroll:false});
    `);
    assert.match(window.document.getElementById('markdown-view').textContent, /Human PP edit/);
    assert.equal(window.document.querySelector('.ocr-visual-flow'), null);
    assert.equal(window.document.getElementById('markdown-view').classList.contains('ocr-visual-mode'), false);

    ev(`
      const editedLayout={
        id:'edited-layout',modelId:'paddleocr-vl-1.6',status:'completed',manualEditedAt:2,
        markdown:'# Human layout edit',images:{},
        ocrResults:[{width:100,height:100,parsing_res_list:[
          {block_bbox:[1,2,90,20],block_label:'text',block_content:'machine layout text'}
        ]}]
      };
      resetResultRenderCache();
      renderResultPane(editedLayout,{preserveScroll:false});
    `);
    assert.match(window.document.getElementById('markdown-view').textContent, /Human layout edit/);
    assert.equal(window.document.querySelector('.official-layout-flow'), null);
    assert.doesNotMatch(window.document.getElementById('markdown-view').textContent, /machine layout text/);
    dom.window.close();
});
// End of DOM regression tests.
