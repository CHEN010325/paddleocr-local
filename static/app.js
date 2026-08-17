const API_BASE = '/api';
const DEFAULT_PDF_BATCH_SIZE = 1;
const MAX_PDF_BATCH_SIZE = 400;
const UNLIMITED_OCR_MODEL_ID = 'unlimited-ocr';
const OVIS_OCR_MODEL_ID = 'ovisocr2';
const HPD_PARSING_MODEL_ID = 'hpd-parsing';
const UNLIMITED_OCR_RECOMMENDED_PDF_BATCH_SIZE = 1;
const UNLIMITED_OCR_COORDINATE_SIZE = 1000;
const HPD_PARSING_COORDINATE_SIZE = 1000;
const UNLIMITED_OCR_BACKENDS = ['transformers', 'sglang'];
const STREAM_RENDER_MIN_INTERVAL_MS = 140;
const STREAM_STATUS_MARKDOWN_RE = /^\s*\*\*Unlimited-OCR status\*\*/i;
const PDF_BATCH_SIZE_STORAGE_KEY = 'pandocr.pdfBatchSize';
const MODEL_STORAGE_KEY = 'pandocr.selectedModelId';
const API_TOKEN_STORAGE_KEY = 'pandocr.apiToken';
const LANGUAGE_STORAGE_KEY = 'pandocr.language';
const DEFAULT_MODEL_ID = 'paddleocr-vl-1.6';
const DEFAULT_APP_VERSION = '0.2.0';
const DEFAULT_PDF_ZOOM = 1;
const PDF_DEFAULT_PAGE_WIDTH = 595;
const PDF_FIT_WIDTH_GUTTER = 12;
const MAX_DEFAULT_PDF_ZOOM = 1.3;
const DEFAULT_MAX_UPLOAD_BYTES = 512 * 1024 * 1024;
const I18N_CONFIG = window.PANDOCR_I18N || {
    defaultLanguage: 'zh-CN',
    supportedLanguages: ['zh-CN'],
    titles: {
        'zh-CN': 'PaddleOCR Local - 本地 OCR 解析工作台'
    },
    dictionaries: {}
};

let availableModels = [{
    id: DEFAULT_MODEL_ID,
    name: 'PaddleOCR-VL-1.6-0.9B',
    label: 'PaddleOCR-VL 1.6',
    endpoint: '/api/paddleocr-vl-1.6'
}];
let selectedModelId = localStorage.getItem(MODEL_STORAGE_KEY) || DEFAULT_MODEL_ID;
let tasks = [];
let activeTaskId = null;
let activeFilter = 'all';
let activeResultView = 'markdown';
let isProcessing = false;
let processingTaskId = null;
let currentPdf = null;
let currentPage = 1;
let currentZoom = DEFAULT_PDF_ZOOM;
let pdfDefaultPageWidth = PDF_DEFAULT_PAGE_WIDTH;
let sourceRenderToken = 0;
let renderedResultTaskId = null;
let renderedMarkdownKey = '';
let renderedOfficialLayoutContext = '';
let renderedPPOCRVisualContext = '';
let renderedJsonKey = '';
let cachedJsonLines = [];
let cachedJsonMaxLineLength = 0;
let jsonRenderToken = 0;
let ppocrScrollSyncFrame = 0;
let sourceScrollSyncFrame = 0;
let layoutResultScrollSyncFrame = 0;
let layoutSourceScrollSyncFrame = 0;
let splitScrollSyncLocked = false;
let modelRuntime = null;
let modelRuntimePollTimer = null;
let modelRuntimeLoadInFlight = false;
let modelSwitchInFlight = false;
let comparisonInFlight = false;
let activeComparisonGroupId = null;
let unlimitedOcrBackendSwitchInFlight = false;
let selectedUnlimitedOcrBackend = 'transformers';
let maxUploadBytes = DEFAULT_MAX_UPLOAD_BYTES;
let appMetadata = { version: DEFAULT_APP_VERSION, commit: '' };
let currentLanguage = normalizeLanguage(localStorage.getItem(LANGUAGE_STORAGE_KEY) || I18N_CONFIG.defaultLanguage);
const sourcePdfCache = new Map();
const sourceBytesCache = new Map();
const linkedLayoutBlockByElement = new WeakMap();
let linkedLayoutEntries = [];
const comparisonGroupsLoading = new Set();
const i18nTextSources = new WeakMap();
const i18nAttributeSources = new WeakMap();
const I18N_ATTRIBUTES = ['title', 'placeholder', 'aria-label'];
const JSON_LINE_HEIGHT = 21;
const JSON_PADDING_TOP = 34;
const JSON_PADDING_RIGHT = 40;
const JSON_PADDING_BOTTOM = 34;
const JSON_PADDING_LEFT = 40;
const JSON_OVERSCAN_LINES = 10;

const els = {
    sidebar: document.getElementById('sidebar'),
    sidebarToggle: document.getElementById('sidebar-toggle'),
    fileInput: document.getElementById('file-input'),
    browseBtn: document.getElementById('browse-btn'),
    newTaskBtn: document.getElementById('new-task-btn'),
    dropZone: document.getElementById('drop-zone'),
    taskList: document.getElementById('task-list'),
    taskSearch: document.getElementById('task-search'),
    clearHistoryBtn: document.getElementById('clear-history-btn'),
    languageToggle: document.getElementById('language-toggle'),
    statusDot: document.getElementById('model-status-dot'),
    statusText: document.getElementById('model-status-text'),
    gpuPreflightPanel: document.getElementById('gpu-preflight-panel'),
    modelSelect: document.getElementById('model-select'),
    unlimitedBackendWrap: document.getElementById('unlimited-backend-wrap'),
    unlimitedBackendSelect: document.getElementById('unlimited-backend-select'),
    activeModelName: document.getElementById('active-model-name'),
    resultPane: document.querySelector('.result-pane'),
    sourceTitle: document.getElementById('source-title'),
    sourceMeta: document.getElementById('source-meta'),
    sourceViewer: document.getElementById('source-viewer'),
    pdfControls: document.getElementById('pdf-controls'),
    pageIndicator: document.getElementById('page-indicator'),
    prevPageBtn: document.getElementById('prev-page-btn'),
    nextPageBtn: document.getElementById('next-page-btn'),
    zoomInBtn: document.getElementById('zoom-in-btn'),
    zoomOutBtn: document.getElementById('zoom-out-btn'),
    resetZoomBtn: document.getElementById('reset-zoom-btn'),
    resultTitle: document.getElementById('result-title'),
    startBtn: document.getElementById('start-btn'),
    compareBtn: document.getElementById('compare-btn'),
    editBtn: document.getElementById('edit-btn'),
    copyBtn: document.getElementById('copy-btn'),
    downloadBtn: document.getElementById('download-btn'),
    markdownView: document.getElementById('markdown-view'),
    jsonView: document.getElementById('json-view'),
    comparisonView: document.getElementById('comparison-view'),
    comparisonTab: document.querySelector('.comparison-tab'),
    compareDialog: document.getElementById('compare-dialog'),
    compareForm: document.getElementById('compare-form'),
    compareModelList: document.getElementById('compare-model-list'),
    compareStartBtn: document.getElementById('compare-start-btn'),
    chartRecognitionSwitch: document.getElementById('chart-recognition-switch'),
    docUnwarpingSwitch: document.getElementById('doc-unwarping-switch'),
    docOrientationSwitch: document.getElementById('doc-orientation-switch'),
    sealRecognitionSwitch: document.getElementById('seal-recognition-switch'),
    formulaNumberSwitch: document.getElementById('formula-number-switch'),
    ignoreHeaderSwitch: document.getElementById('ignore-header-switch'),
    ignoreFooterSwitch: document.getElementById('ignore-footer-switch'),
    ignoreNumberSwitch: document.getElementById('ignore-number-switch'),
    pdfBatchSizeInput: document.getElementById('pdf-batch-size-input'),
    taskTemplate: document.getElementById('task-item-template')
};

async function initializeApp() {
    pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.mjs';
    initLanguage();
    initPdfBatchSizeSetting();
    setupEventListeners();
    renderModelSelect();
    await checkBackendConnection();
    await loadTasks();
    renderTaskList();
    if (tasks.length > 0) {
        await selectTask(tasks[0].id);
    }
    applyLanguage(document.body);
}

if (globalThis.pdfjsLib) {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeApp, { once: true });
    } else {
        initializeApp();
    }
}

function setupEventListeners() {
    [els.browseBtn, els.newTaskBtn].forEach((button) => {
        button?.addEventListener('click', () => els.fileInput.click());
    });

    els.fileInput.addEventListener('change', async (event) => {
        await handleFiles(event.target.files);
        els.fileInput.value = '';
    });

    ['dragenter', 'dragover'].forEach((name) => {
        document.addEventListener(name, (event) => {
            event.preventDefault();
            if (!comparisonInFlight) els.dropZone?.classList.add('drag-over');
        });
    });

    ['dragleave', 'drop'].forEach((name) => {
        document.addEventListener(name, (event) => {
            event.preventDefault();
            els.dropZone?.classList.remove('drag-over');
        });
    });

    document.addEventListener('drop', async (event) => {
        await handleFiles(event.dataTransfer.files);
    });

    els.sidebarToggle.addEventListener('click', () => {
        document.body.classList.toggle('sidebar-collapsed');
    });

    els.taskSearch.addEventListener('input', renderTaskList);
    els.clearHistoryBtn.addEventListener('click', clearHistory);
    els.startBtn.addEventListener('click', () => processActiveTask());
    els.compareBtn?.addEventListener('click', openComparisonDialog);
    els.editBtn?.addEventListener('click', startMarkdownEdit);
    els.copyBtn.addEventListener('click', copyActiveResult);
    els.downloadBtn.addEventListener('click', downloadActiveTask);
    els.prevPageBtn.addEventListener('click', () => changePdfPage(-1));
    els.nextPageBtn.addEventListener('click', () => changePdfPage(1));
    els.zoomInBtn.addEventListener('click', () => changeZoom(0.15));
    els.zoomOutBtn.addEventListener('click', () => changeZoom(-0.15));
    els.resetZoomBtn?.addEventListener('click', resetZoom);
    els.sourceViewer.addEventListener('scroll', handleSourceViewerScroll);
    els.markdownView.addEventListener('scroll', handlePPOCRMarkdownScroll);
    els.jsonView.addEventListener('scroll', renderVisibleJsonLines);
    els.compareForm?.addEventListener('submit', handleComparisonSubmit);
    els.modelSelect?.addEventListener('change', handleModelSelectionChange);
    els.unlimitedBackendSelect?.addEventListener('change', handleUnlimitedOcrBackendChange);
    els.languageToggle?.addEventListener('click', toggleLanguage);
    els.pdfBatchSizeInput?.addEventListener('input', handlePdfBatchSizeInput);
    ['change', 'blur'].forEach((eventName) => {
        els.pdfBatchSizeInput?.addEventListener(eventName, syncPdfBatchSizeSetting);
    });

    document.querySelectorAll('.task-tab').forEach((button) => {
        button.addEventListener('click', () => {
            document.querySelectorAll('.task-tab').forEach((tab) => tab.classList.remove('active'));
            button.classList.add('active');
            activeFilter = button.dataset.filter;
            renderTaskList();
        });
    });

    document.querySelectorAll('.view-tab').forEach((button) => {
        button.addEventListener('click', () => {
            setActiveResultView(button.dataset.view);
        });
    });
}

function initLanguage() {
    currentLanguage = normalizeLanguage(currentLanguage);
    localStorage.setItem(LANGUAGE_STORAGE_KEY, currentLanguage);
    applyLanguage(document.body);
}

function normalizeLanguage(language) {
    const supported = Array.isArray(I18N_CONFIG.supportedLanguages)
        ? I18N_CONFIG.supportedLanguages
        : ['zh-CN'];
    if (supported.includes(language)) return language;
    return I18N_CONFIG.defaultLanguage || supported[0] || 'zh-CN';
}

function toggleLanguage() {
    setLanguage(currentLanguage === 'en' ? 'zh-CN' : 'en');
}

function setLanguage(language) {
    const nextLanguage = normalizeLanguage(language);
    if (nextLanguage === currentLanguage) {
        applyLanguage(document.body);
        return;
    }
    currentLanguage = nextLanguage;
    localStorage.setItem(LANGUAGE_STORAGE_KEY, currentLanguage);
    refreshLanguageSensitiveUi();
}

function refreshLanguageSensitiveUi() {
    const task = getActiveTask();
    renderTaskList();
    updateActiveModelDisplay(task);
    updateResultViewLabels(task);

    if (task) {
        els.sourceMeta.textContent = taskSourceMeta(task);
        els.resultTitle.textContent = resultPaneTitle(task);
        renderResultPane(task);
    } else if (!isProcessing) {
        els.sourceTitle.textContent = t('等待上传文件');
        els.sourceMeta.textContent = t('PDF、图片、Office 文档');
        if (els.sourceViewer.querySelector('#drop-zone')) {
            els.sourceViewer.innerHTML = emptyDropZoneHtml();
            els.dropZone = document.getElementById('drop-zone');
            els.browseBtn = document.getElementById('browse-btn');
            els.browseBtn?.addEventListener('click', () => els.fileInput.click());
        }
        renderResultPane(null);
    }

    updateActionState(task);
    applyLanguage(document.body);
}

function applyLanguage(root = document.body) {
    if (!root) return;
    document.documentElement.lang = currentLanguage;
    document.title = I18N_CONFIG.titles?.[currentLanguage] || I18N_CONFIG.titles?.[I18N_CONFIG.defaultLanguage] || document.title;
    updateLanguageToggle();
    translateElementTree(root);
}

function updateLanguageToggle() {
    if (!els.languageToggle) return;
    els.languageToggle.dataset.lang = currentLanguage === 'en' ? 'en' : 'zh-CN';
    const labelSource = currentLanguage === 'en' ? '切换到中文' : '切换到英文';
    const label = t(labelSource);
    els.languageToggle.setAttribute('title', label);
    els.languageToggle.setAttribute('aria-label', label);
    const sources = getI18nAttributeSources(els.languageToggle);
    sources.set('title', labelSource);
    sources.set('aria-label', labelSource);
}

function translateElementTree(root) {
    const startingElement = root.nodeType === Node.ELEMENT_NODE ? root : root.parentElement;
    if (startingElement) translateElementAttributes(startingElement);

    const walker = document.createTreeWalker(
        root,
        NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
        {
            acceptNode(node) {
                if (node.nodeType === Node.ELEMENT_NODE) {
                    return shouldSkipI18nElement(node)
                        ? NodeFilter.FILTER_REJECT
                        : NodeFilter.FILTER_ACCEPT;
                }
                const parent = node.parentElement;
                if (!parent || shouldSkipI18nElement(parent)) return NodeFilter.FILTER_REJECT;
                return NodeFilter.FILTER_ACCEPT;
            }
        }
    );

    let node = walker.nextNode();
    while (node) {
        if (node.nodeType === Node.ELEMENT_NODE) {
            translateElementAttributes(node);
        } else {
            translateTextNode(node);
        }
        node = walker.nextNode();
    }
}

function shouldSkipI18nElement(element) {
    if (!element || element.closest('script, style, pre, code, #json-view')) return true;
    const markdownView = element.closest('#markdown-view');
    if (!markdownView) return false;
    if (element.id === 'markdown-view') return false;
    return Boolean(!element.closest('.empty-result') && !element.querySelector?.('.empty-result'));
}

function translateElementAttributes(element) {
    I18N_ATTRIBUTES.forEach((attribute) => {
        if (!element.hasAttribute(attribute)) return;
        const value = element.getAttribute(attribute);
        const sources = getI18nAttributeSources(element);
        if (!sources.has(attribute) && hasCjk(value)) {
            sources.set(attribute, value);
        }
        const source = sources.get(attribute);
        if (source) {
            element.setAttribute(attribute, t(source));
        }
    });
}

function getI18nAttributeSources(element) {
    let sources = i18nAttributeSources.get(element);
    if (!sources) {
        sources = new Map();
        i18nAttributeSources.set(element, sources);
    }
    return sources;
}

function translateTextNode(node) {
    const value = node.nodeValue || '';
    const trimmed = value.trim();
    if (!trimmed) return;
    if (!i18nTextSources.has(node) && hasCjk(trimmed)) {
        i18nTextSources.set(node, trimmed);
    }
    const source = i18nTextSources.get(node);
    if (!source) return;
    const leading = value.match(/^\s*/)?.[0] || '';
    const trailing = value.match(/\s*$/)?.[0] || '';
    node.nodeValue = `${leading}${t(source)}${trailing}`;
}

function hasCjk(value) {
    return /[\u3400-\u9fff]/.test(String(value || ''));
}

function languageLocale() {
    return currentLanguage === 'en' ? 'en-US' : 'zh-CN';
}

function t(source, params = {}) {
    const text = String(source ?? '');
    const translated = currentLanguage === normalizeLanguage(I18N_CONFIG.defaultLanguage)
        ? text
        : (I18N_CONFIG.dictionaries?.[currentLanguage]?.[text] || translateDynamicText(text) || text);
    return interpolateI18n(translated, params);
}

function interpolateI18n(text, params = {}) {
    return String(text).replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key) => (
        Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : match
    ));
}

function translateDynamicText(text) {
    if (currentLanguage === normalizeLanguage(I18N_CONFIG.defaultLanguage)) return text;
    const dynamicPatterns = [
        [/^(.+) 状态检查中$/, (name) => `${name} ${t('状态检查中')}`],
        [/^(.+) 就绪$/, (name) => `${name} ${t('就绪')}`],
        [/^(.+) 启动中$/, (name) => `${name} ${t('启动中')}`],
        [/^(.+) 启动失败$/, (name) => `${name} ${t('启动失败')}`],
        [/^(.+) 待启动$/, (name) => `${name} ${t('待启动')}`],
        [/^(.+) 未就绪$/, (name) => `${name} ${t('未就绪')}`],
        [/^(.+) 还没有就绪，请稍后再试。$/, (name) => t('{name} 还没有就绪，请稍后再试。', { name })],
        [/^正在读取 (\d+) 个文件\.\.\.$/, (count) => t('正在读取 {count} 个文件...', { count })],
        [/^(\d+)\/(\d+) 解析中$/, (done, total) => t('{done}/{total} 解析中', { done, total })],
        [/^(\d+)\/(\d+) 可继续$/, (done, total) => t('{done}/{total} 可继续', { done, total })],
        [/^解析失败：(.+)$/, (detail) => t('解析失败：{detail}', { detail })],
        [/^Office 已转 PDF · (.+)$/, (name) => t('Office 已转 PDF · {name}', { name })],
        [/^第 (\d+) 页$/, (start) => t('第 {start} 页', { start })],
        [/^第 (\d+)-(\d+) 页$/, (start, end) => t('第 {start}-{end} 页', { start, end })],
        [/^(.+) 超过上传上限 (.+)，请压缩或拆分后再试。$/, (name, limit) => t('{name} 超过上传上限 {limit}，请压缩或拆分后再试。', { name, limit })],
        [/^不支持的文件格式：(.+)$/, (name) => t('不支持的文件格式：{name}', { name })],
        [/^确定要删除“(.+)”吗？当前操作不可回撤。$/, (name) => t('确定要删除“{name}”吗？当前操作不可回撤。', { name })],
        [/^保存本地任务失败：(.+)$/, (detail) => t('保存本地任务失败：{detail}', { detail })],
        [/^读取本地任务失败：(.+)$/, (detail) => t('读取本地任务失败：{detail}', { detail })],
        [/^清空本地任务失败：(.+)$/, (detail) => t('清空本地任务失败：{detail}', { detail })],
        [/^删除本地任务失败：(.+)$/, (detail) => t('删除本地任务失败：{detail}', { detail })],
        [/^保存源文件失败：(.+)$/, (detail) => t('保存源文件失败：{detail}', { detail })],
        [/^Office 转 PDF 失败：(.+)$/, (detail) => t('Office 转 PDF 失败：{detail}', { detail })],
        [/^读取 PDF 分页失败：(.+)$/, (detail) => t('读取 PDF 分页失败：{detail}', { detail })],
        [/^读取源文件失败：(.+)$/, (detail) => t('读取源文件失败：{detail}', { detail })]
    ];

    for (const [pattern, translate] of dynamicPatterns) {
        const match = text.match(pattern);
        if (match) return translate(...match.slice(1));
    }
    return '';
}

function isLocalApiUrl(url) {
    const text = String(url || '');
    if (text.startsWith(API_BASE) || text.startsWith('/api/')) return true;
    try {
        const parsed = new URL(text, window.location.href);
        return parsed.origin === window.location.origin && parsed.pathname.startsWith('/api/');
    } catch (error) {
        return false;
    }
}

function authHeaders(headers = {}, url = '') {
    const merged = new Headers(headers);
    if (isLocalApiUrl(url)) {
        const token = window.sessionStorage?.getItem(API_TOKEN_STORAGE_KEY);
        if (token) merged.set('Authorization', `Bearer ${token}`);
    }
    return merged;
}

async function apiFetch(url, options = {}) {
    const requestOptions = {
        ...options,
        headers: authHeaders(options.headers, url)
    };
    let response = await fetch(url, requestOptions);
    if (response.status !== 401 || !isLocalApiUrl(url)) return response;

    const token = window.prompt(t('请输入 PaddleOCR Local API Token'));
    if (!token) return response;
    window.sessionStorage?.setItem(API_TOKEN_STORAGE_KEY, token.trim());
    response = await fetch(url, {
        ...options,
        headers: authHeaders(options.headers, url)
    });
    return response;
}

async function responseErrorText(response) {
    const text = await response.text();
    try {
        const data = JSON.parse(text);
        return data.detail || text;
    } catch (error) {
        return text;
    }
}

async function checkBackendConnection() {
    try {
        const response = await apiFetch(`${API_BASE}/models`);
        if (!response.ok) throw new Error('API Error');
        const data = await response.json();
        availableModels = normalizeModelList(data);
        appMetadata = {
            version: String(data.version || appMetadata.version || DEFAULT_APP_VERSION),
            commit: String(data.commit || '')
        };
        document.documentElement.dataset.version = appMetadata.version;
        if (appMetadata.commit) {
            document.documentElement.dataset.commit = appMetadata.commit;
        } else {
            delete document.documentElement.dataset.commit;
        }
        if (Number.isFinite(Number(data.maxUploadBytes)) && Number(data.maxUploadBytes) > 0) {
            maxUploadBytes = Number(data.maxUploadBytes);
        }
        if (!availableModels.some((model) => model.id === selectedModelId)) {
            selectedModelId = data.default || availableModels[0]?.id || DEFAULT_MODEL_ID;
        }
        localStorage.setItem(MODEL_STORAGE_KEY, selectedModelId);
        renderModelSelect();
        await loadModelRuntime({ silent: true });
        applyModelBatchSizeRecommendation();
        startModelRuntimePolling();
        updateActiveModelDisplay(getActiveTask());
    } catch (error) {
        els.statusDot.className = 'dot error';
        els.statusText.textContent = t('模型未连接');
        setTimeout(checkBackendConnection, 5000);
    }
}

function startModelRuntimePolling() {
    if (modelRuntimePollTimer) return;
    modelRuntimePollTimer = window.setInterval(() => {
        loadModelRuntime({ silent: true }).catch((error) => {
            console.warn('Model runtime polling failed', error);
        });
    }, 2500);
}

async function loadModelRuntime({ silent = false } = {}) {
    if (modelRuntimeLoadInFlight) return modelRuntime;
    modelRuntimeLoadInFlight = true;
    try {
        const response = await apiFetch(`${API_BASE}/model-runtime`, { cache: 'no-store' });
        if (!response.ok) throw new Error(await response.text());
        modelRuntime = await response.json();
        syncSelectedModelWithRuntime();
        syncUnlimitedOcrBackendWithRuntime();
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
        return modelRuntime;
    } catch (error) {
        if (!silent) console.warn('Model runtime status failed', error);
        updateActiveModelDisplay(getActiveTask());
        return modelRuntime;
    } finally {
        modelRuntimeLoadInFlight = false;
    }
}

function normalizeModelList(data) {
    const models = Array.isArray(data?.data) ? data.data : [];
    if (!models.length) return availableModels;

    return models.map((model) => {
        if (typeof model === 'string') {
            return {
                id: model,
                name: model,
                label: model,
                endpoint: '/api/paddleocr-vl-1.6'
            };
        }
        return {
            id: model.id || model.name || DEFAULT_MODEL_ID,
            name: model.name || model.id || DEFAULT_MODEL_ID,
            label: model.label || model.name || model.id || DEFAULT_MODEL_ID,
            endpoint: model.endpoint || '/api/paddleocr-vl-1.6',
            kind: model.kind || 'document_parsing'
        };
    });
}

function renderModelSelect() {
    if (!els.modelSelect) return;
    els.modelSelect.innerHTML = '';
    availableModels.forEach((model) => {
        const option = document.createElement('option');
        option.value = model.id;
        option.textContent = modelDisplayName(model);
        option.selected = model.id === selectedModelId;
        els.modelSelect.appendChild(option);
    });
    renderUnlimitedOcrBackendSelect();
}

function normalizeUnlimitedOcrBackend(value) {
    const backend = String(value || '').trim().toLowerCase();
    return UNLIMITED_OCR_BACKENDS.includes(backend) ? backend : 'transformers';
}

function unlimitedOcrBackendLabel(backend) {
    return normalizeUnlimitedOcrBackend(backend) === 'sglang' ? 'SGLang' : 'Transformers';
}

function getRuntimeUnlimitedOcrBackend() {
    const status = modelRuntime?.models?.[UNLIMITED_OCR_MODEL_ID];
    return normalizeUnlimitedOcrBackend(
        modelRuntime?.unlimitedOcrBackend
        || status?.unlimitedOcrBackend
        || selectedUnlimitedOcrBackend
    );
}

function syncUnlimitedOcrBackendWithRuntime() {
    const backend = getRuntimeUnlimitedOcrBackend();
    if (selectedUnlimitedOcrBackend !== backend) {
        selectedUnlimitedOcrBackend = backend;
    }
    renderUnlimitedOcrBackendSelect();
}

function renderUnlimitedOcrBackendSelect() {
    if (!els.unlimitedBackendWrap || !els.unlimitedBackendSelect) return;
    const hasUnlimited = availableModels.some((model) => model.id === UNLIMITED_OCR_MODEL_ID);
    const shouldShow = hasUnlimited && selectedModelId === UNLIMITED_OCR_MODEL_ID;
    els.unlimitedBackendWrap.classList.toggle('hidden', !shouldShow);

    const status = modelRuntime?.models?.[UNLIMITED_OCR_MODEL_ID];
    const supported = Array.isArray(status?.unlimitedOcrSupportedBackends)
        ? status.unlimitedOcrSupportedBackends
        : (Array.isArray(modelRuntime?.unlimitedOcrSupportedBackends) ? modelRuntime.unlimitedOcrSupportedBackends : UNLIMITED_OCR_BACKENDS);
    const backends = supported.map(normalizeUnlimitedOcrBackend).filter((backend, index, list) => list.indexOf(backend) === index);
    els.unlimitedBackendSelect.innerHTML = '';
    (backends.length ? backends : UNLIMITED_OCR_BACKENDS).forEach((backend) => {
        const option = document.createElement('option');
        option.value = backend;
        option.textContent = unlimitedOcrBackendLabel(backend);
        option.selected = backend === selectedUnlimitedOcrBackend;
        els.unlimitedBackendSelect.appendChild(option);
    });
    els.unlimitedBackendSelect.value = selectedUnlimitedOcrBackend;
}

function syncSelectedModelWithRuntime() {
    if (!modelRuntime || !availableModels.length || modelSwitchInFlight) return false;
    if (modelRuntime.controlAvailable === false) return false;
    if (modelRuntime.exclusivityViolation) return false;
    const knownModelIds = new Set(availableModels.map((model) => model.id));
    const operation = modelRuntime.operation;
    let runtimeModelId = null;

    if (operation?.state === 'switching' && knownModelIds.has(operation.targetModelId)) {
        runtimeModelId = operation.targetModelId;
    } else if (knownModelIds.has(modelRuntime.activeModelId)) {
        const activeStatus = getModelRuntimeStatus(modelRuntime.activeModelId);
        if (activeStatus?.running || activeStatus?.ready) {
            runtimeModelId = modelRuntime.activeModelId;
        }
    }

    if (!runtimeModelId || runtimeModelId === selectedModelId) return false;
    selectedModelId = runtimeModelId;
    localStorage.setItem(MODEL_STORAGE_KEY, selectedModelId);
    renderModelSelect();
    applyModelBatchSizeRecommendation();
    return true;
}

async function handleUnlimitedOcrBackendChange() {
    if (!els.unlimitedBackendSelect) return;
    const nextBackend = normalizeUnlimitedOcrBackend(els.unlimitedBackendSelect.value);
    if (isProcessing || processingTaskId || comparisonInFlight || modelSwitchInFlight || unlimitedOcrBackendSwitchInFlight || isModelRuntimeSwitching()) {
        els.unlimitedBackendSelect.value = selectedUnlimitedOcrBackend;
        alert(t('当前正在解析或切换模型，请完成后再切换。'));
        return;
    }

    const previousBackend = selectedUnlimitedOcrBackend;
    selectedUnlimitedOcrBackend = nextBackend;
    renderUnlimitedOcrBackendSelect();
    updateActiveModelDisplay(getActiveTask());
    updateActionState(getActiveTask());

    const switched = await switchUnlimitedOcrBackend(nextBackend);
    if (!switched) {
        selectedUnlimitedOcrBackend = previousBackend;
        renderUnlimitedOcrBackendSelect();
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
    }
}

async function handleModelSelectionChange() {
    const nextModelId = els.modelSelect.value || DEFAULT_MODEL_ID;
    if (isProcessing || processingTaskId || comparisonInFlight || modelSwitchInFlight) {
        els.modelSelect.value = selectedModelId;
        alert(t('当前正在解析或切换模型，请完成后再切换。'));
        return;
    }
    const previousModelId = selectedModelId;
    selectedModelId = nextModelId;
    localStorage.setItem(MODEL_STORAGE_KEY, selectedModelId);
    renderModelSelect();
    updateActiveModelDisplay(getActiveTask());
    updateActionState(getActiveTask());

    if (isModelRuntimeMissing(nextModelId)) {
        const missingModel = availableModels.find((model) => model.id === nextModelId) || getSelectedModel();
        const deployOptions = requestModelDeploymentOptions(missingModel);
        if (deployOptions) {
            const deployed = await deployModelRuntime(nextModelId, { wait: false, ...deployOptions });
            if (deployed) applyModelBatchSizeRecommendation();
        }
        return;
    }

    const switched = await switchModelRuntime(nextModelId, { wait: false });
    if (!switched) {
        selectedModelId = previousModelId;
        localStorage.setItem(MODEL_STORAGE_KEY, selectedModelId);
        renderModelSelect();
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
    } else {
        applyModelBatchSizeRecommendation();
    }
}

function getSelectedModel() {
    return availableModels.find((model) => model.id === selectedModelId)
        || availableModels[0]
        || {
            id: DEFAULT_MODEL_ID,
            name: 'PaddleOCR-VL-1.6-0.9B',
            label: 'PaddleOCR-VL 1.6',
            endpoint: '/api/paddleocr-vl-1.6'
        };
}

function getTaskModel(task) {
    if (task?.modelId) {
        const known = availableModels.find((model) => model.id === task.modelId);
        if (known) return known;
        return {
            id: task.modelId,
            name: task.modelName || task.modelId,
            label: task.modelName || task.modelId,
            endpoint: task.modelEndpoint || '/api/paddleocr-vl-1.6'
        };
    }
    return getSelectedModel();
}

function getTaskActionModel(task) {
    if (!task) return getSelectedModel();
    if (isTaskActivelyProcessing(task) || shouldResumeTask(task)) {
        return getTaskModel(task);
    }
    return getSelectedModel();
}

function modelDisplayName(model) {
    return model?.label || model?.name || model?.id || DEFAULT_MODEL_ID;
}

function modelShortName(model) {
    return modelDisplayName(model).replace('PaddleOCR-', '').replace('PaddleOCR ', '');
}

function modelApiUrl(model) {
    const endpoint = model?.endpoint || '/api/paddleocr-vl-1.6';
    if (/^https?:\/\//i.test(endpoint)) return endpoint;
    if (endpoint.startsWith('/api/')) return endpoint;
    return `${API_BASE}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`;
}

function getModelRuntimeStatus(modelId) {
    return modelRuntime?.models?.[modelId] || null;
}

function isModelRuntimeReady(modelId) {
    if (!modelRuntime) return true;
    if (!getModelRuntimeStatus(modelId)?.ready || modelRuntime.exclusivityViolation) return false;
    const activeModelId = modelRuntime.activeModelId;
    if (activeModelId && activeModelId !== modelId) return false;
    for (const key of ['runningModelIds', 'readyModelIds']) {
        const modelIds = modelRuntime[key];
        if (Array.isArray(modelIds) && (modelIds.length !== 1 || modelIds[0] !== modelId)) return false;
    }
    return true;
}

function isModelRuntimeMissing(modelId) {
    return getModelRuntimeStatus(modelId)?.state === 'missing';
}

function modelDeploymentHint(model) {
    return t('{name} 还没有部署。请重新运行一键部署脚本并选择这个模型，脚本会下载/构建所需镜像和容器。', {
        name: modelDisplayName(model)
    });
}

function parseUnlimitedOcrBackendInput(value) {
    const backend = String(value || '').trim().toLowerCase();
    if (backend === 'sglang') return 'sglang';
    if (['transformers', 'transformer', 'hf'].includes(backend)) return 'transformers';
    return null;
}

function requestModelDeploymentOptions(model) {
    const confirmed = window.confirm(`${modelDeploymentHint(model)}\n\n${t('现在下载并部署吗？')}`);
    if (!confirmed) return null;
    if (model?.id !== UNLIMITED_OCR_MODEL_ID) return {};

    const input = window.prompt(
        t('请输入 Unlimited-OCR 后端：transformers 或 sglang'),
        selectedUnlimitedOcrBackend
    );
    if (input === null) return null;
    const backend = parseUnlimitedOcrBackendInput(input);
    if (!backend) {
        alert(t('不支持的 Unlimited-OCR 后端，请输入 transformers 或 sglang。'));
        return null;
    }
    selectedUnlimitedOcrBackend = backend;
    renderUnlimitedOcrBackendSelect();
    updateActiveModelDisplay(getActiveTask());
    updateActionState(getActiveTask());
    return { backend };
}

function isModelRuntimeSwitching(modelId = null) {
    const operation = modelRuntime?.operation;
    if (modelSwitchInFlight) return !modelId || selectedModelId === modelId;
    return operation?.state === 'switching' && (!modelId || operation.targetModelId === modelId);
}

function canSwitchModelRuntime(modelId) {
    if (!modelRuntime) return true;
    const status = getModelRuntimeStatus(modelId);
    return Boolean(modelRuntime.controlAvailable && status?.state !== 'missing');
}

function modelRuntimeDotClass(modelId) {
    const status = getModelRuntimeStatus(modelId);
    const operation = modelRuntime?.operation;
    if (modelRuntime?.exclusivityViolation) return 'dot error';
    if (
        !modelRuntime
        || isModelRuntimeSwitching(modelId)
        || status?.state === 'starting'
        || status?.state === 'warming'
        || status?.state === 'partial'
    ) {
        return 'dot connecting';
    }
    if (status?.ready) return 'dot connected';
    if (operation?.state === 'error' && operation.targetModelId === modelId) return 'dot error';
    if (status?.state === 'missing') return 'dot error';
    return 'dot connecting';
}

function modelRuntimeStatusText(model) {
    const modelName = model?.id === UNLIMITED_OCR_MODEL_ID
        ? `${modelDisplayName(model)} ${unlimitedOcrBackendLabel(selectedUnlimitedOcrBackend)}`
        : modelDisplayName(model);
    const status = getModelRuntimeStatus(model.id);
    const operation = modelRuntime?.operation;
    if (!modelRuntime) return t('{modelName} 状态检查中', { modelName });
    if (modelRuntime.exclusivityViolation) return t('模型互斥异常：检测到多个模型同时运行');
    if (isModelRuntimeReady(model.id)) return t('{modelName} 就绪', { modelName });
    if (
        isModelRuntimeSwitching(model.id)
        || status?.state === 'starting'
        || status?.state === 'warming'
        || status?.state === 'partial'
    ) {
        return t('{modelName} 启动中', { modelName });
    }
    if (operation?.state === 'error' && operation.targetModelId === model.id) {
        return t('{modelName} 启动失败', { modelName });
    }
    if (status?.state === 'missing') return t('{modelName} 未部署', { modelName });
    if (status?.state === 'stopped') return t('{modelName} 待启动', { modelName });
    if (modelRuntime.controlAvailable === false) return t('{modelName} 未就绪', { modelName });
    return t('{modelName} 未就绪', { modelName });
}

function modelRuntimeFailureDetail(modelId = selectedModelId) {
    if (modelRuntime?.exclusivityViolation) {
        const running = Array.isArray(modelRuntime.runningModelIds) ? modelRuntime.runningModelIds.join(', ') : '';
        const ready = Array.isArray(modelRuntime.readyModelIds) ? modelRuntime.readyModelIds.join(', ') : '';
        return t('模型互斥异常：同时运行 {running}；就绪 {ready}。请选择目标模型以执行安全清理。', {
            running: running || t('无'),
            ready: ready || t('无')
        });
    }
    const operation = modelRuntime?.operation;
    if (operation?.state !== 'error' || operation.targetModelId !== modelId) return '';
    const diagnostics = operation.diagnostics || {};
    const commands = Array.isArray(diagnostics.logCommands) ? diagnostics.logCommands : [];
    const logs = Array.isArray(diagnostics.logs) ? diagnostics.logs : [];
    const tail = logs.map((entry) => `${entry.container}:\n${entry.tail || ''}`).join('\n\n');
    return [operation.message, commands.length ? `${t('查看启动日志')}：${commands.join('；')}` : '', tail]
        .filter(Boolean)
        .join('\n')
        .slice(-5000);
}

function renderGpuPreflightPanel() {
    const panel = els.gpuPreflightPanel;
    if (!panel) return;
    panel.classList.remove('hidden', 'warning', 'error');

    const failure = modelRuntimeFailureDetail();
    if (failure) {
        panel.classList.add('error');
        panel.textContent = `${t('模型启动失败')}：${failure}`;
        return;
    }

    const preflight = modelRuntime?.gpuPreflight;
    if (!preflight) {
        panel.classList.add('hidden');
        panel.textContent = '';
        return;
    }
    if (preflight.status !== 'ready') {
        panel.classList.add('warning');
        panel.textContent = `${t('GPU 显存预检不可用')}：${preflight.reason || t('未检测到 NVIDIA GPU')}`;
        return;
    }

    const gpu = preflight.gpus?.[0] || {};
    const compatibility = preflight.models?.[selectedModelId] || {};
    const runnableNames = (preflight.runnableModelIds || [])
        .map((id) => availableModels.find((model) => model.id === id))
        .filter(Boolean)
        .map(modelDisplayName);
    const memory = `${gpu.name || 'GPU'} · ${gpu.totalMiB || 0} MiB ${t('显存')} · ${gpu.freeMiB || 0} MiB ${t('当前空闲')}`;
    const runnable = `${t('可运行模型')}：${runnableNames.join('、') || t('无')}`;
    const unsupported = compatibility.supported === false
        ? t('当前模型至少需要 {memory} MiB 显存，不能在此显卡启动', { memory: compatibility.minimumMiB || 0 })
        : '';
    const lowMemory = compatibility.supported !== false
        && Array.isArray(compatibility.lowMemoryEnv)
        && compatibility.lowMemoryEnv.length
        ? `${t('低显存参数')}：${compatibility.lowMemoryEnv.join('；')}`
        : '';
    if (!compatibility.supported || compatibility.level === 'low-memory') panel.classList.add('warning');
    panel.textContent = [memory, runnable, unsupported, lowMemory].filter(Boolean).join('  |  ');
}

function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function switchModelRuntime(modelId, { wait = false } = {}) {
    const model = availableModels.find((item) => item.id === modelId) || getSelectedModel();
    if (isModelRuntimeReady(modelId)) {
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
        return true;
    }
    if (!canSwitchModelRuntime(modelId)) {
        if (isModelRuntimeMissing(modelId)) {
            alert(modelDeploymentHint(model));
        }
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
        return false;
    }

    modelSwitchInFlight = true;
    updateActiveModelDisplay(getActiveTask());
    updateActionState(getActiveTask());
    try {
        const response = await apiFetch(`${API_BASE}/model-runtime/switch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ modelId })
        });
        if (!response.ok) throw new Error(await responseErrorText(response));
        modelRuntime = await response.json();
        syncSelectedModelWithRuntime();
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
        if (wait) return await waitForModelRuntimeReady(modelId);
        return true;
    } catch (error) {
        console.error(error);
        els.statusDot.className = 'dot error';
        els.statusText.textContent = t('{modelName} 启动失败', { modelName: modelDisplayName(model) });
        const detail = modelRuntimeFailureDetail(modelId) || error.message || t('模型启动失败');
        els.statusText.title = detail;
        renderGpuPreflightPanel();
        alert(detail);
        return false;
    } finally {
        modelSwitchInFlight = false;
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
    }
}

async function deployModelRuntime(modelId, { wait = false, backend = null } = {}) {
    const model = availableModels.find((item) => item.id === modelId) || getSelectedModel();
    modelSwitchInFlight = true;
    updateActiveModelDisplay(getActiveTask());
    updateActionState(getActiveTask());
    try {
        const payload = { modelId };
        if (modelId === UNLIMITED_OCR_MODEL_ID) {
            payload.backend = normalizeUnlimitedOcrBackend(backend || selectedUnlimitedOcrBackend);
            selectedUnlimitedOcrBackend = payload.backend;
        }
        const response = await apiFetch(`${API_BASE}/model-runtime/deploy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!response.ok) throw new Error(await responseErrorText(response));
        modelRuntime = await response.json();
        syncSelectedModelWithRuntime();
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
        if (wait) return await waitForModelRuntimeReady(modelId, 60 * 60 * 1000);
        return true;
    } catch (error) {
        console.error(error);
        els.statusDot.className = 'dot error';
        els.statusText.textContent = t('{modelName} 下载/部署失败', { modelName: modelDisplayName(model) });
        const detail = modelRuntimeFailureDetail(modelId) || error.message || t('模型启动失败');
        els.statusText.title = detail;
        renderGpuPreflightPanel();
        alert(detail);
        return false;
    } finally {
        modelSwitchInFlight = false;
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
    }
}

async function switchUnlimitedOcrBackend(backend) {
    const resolvedBackend = normalizeUnlimitedOcrBackend(backend);
    unlimitedOcrBackendSwitchInFlight = true;
    updateActiveModelDisplay(getActiveTask());
    updateActionState(getActiveTask());
    try {
        const response = await apiFetch(`${API_BASE}/unlimited-ocr/backend`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ backend: resolvedBackend })
        });
        if (!response.ok) throw new Error(await responseErrorText(response));
        modelRuntime = await response.json();
        syncUnlimitedOcrBackendWithRuntime();
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
        return true;
    } catch (error) {
        console.error(error);
        els.statusDot.className = 'dot error';
        els.statusText.textContent = t('Unlimited-OCR 后端切换失败');
        return false;
    } finally {
        unlimitedOcrBackendSwitchInFlight = false;
        updateActiveModelDisplay(getActiveTask());
        updateActionState(getActiveTask());
    }
}

async function waitForModelRuntimeReady(modelId, timeoutMs = 20 * 60 * 1000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        await loadModelRuntime({ silent: true });
        if (isModelRuntimeReady(modelId)) return true;
        const operation = modelRuntime?.operation;
        if (operation?.targetModelId === modelId && operation.state === 'error') {
            throw new Error(operation.message || t('模型启动失败'));
        }
        await sleep(2500);
    }
    throw new Error(t('模型启动超时'));
}

async function ensureModelRuntimeReadyForTask(task, model) {
    if (isModelRuntimeReady(model.id)) return true;
    if (isModelRuntimeMissing(model.id)) {
        const deployOptions = requestModelDeploymentOptions(model);
        if (deployOptions) {
            const deployed = await deployModelRuntime(model.id, { wait: true, ...deployOptions });
            if (deployed && isModelRuntimeReady(model.id)) return true;
        }
        updateActionState(task);
        return false;
    }
    const switched = await switchModelRuntime(model.id, { wait: true });
    if (switched && isModelRuntimeReady(model.id)) return true;
    if (!modelRuntimeFailureDetail(model.id)) {
        alert(t('{name} 还没有就绪，请稍后再试。', { name: modelDisplayName(model) }));
    }
    updateActionState(task);
    return false;
}

function updateActiveModelDisplay(task = null) {
    const selectedModel = getSelectedModel();
    // The pane describes the result currently on screen, so keep its model
    // attribution tied to the task even when the runtime has since switched
    // to another model. Re-processing actions may still use the selected
    // runtime model through getTaskActionModel().
    const resultModel = task ? getTaskModel(task) : selectedModel;
    renderUnlimitedOcrBackendSelect();
    els.statusDot.className = modelRuntimeDotClass(selectedModel.id);
    els.statusText.textContent = modelRuntimeStatusText(selectedModel);
    els.statusText.title = modelRuntimeFailureDetail(selectedModel.id);
    els.activeModelName.textContent = modelShortName(resultModel);
    renderGpuPreflightPanel();
}

function applySelectedModelToTask(task) {
    const model = getSelectedModel();
    task.modelId = model.id;
    task.modelName = modelDisplayName(model);
    task.modelEndpoint = model.endpoint;
    return model;
}

async function saveTask(task, { includeResults = true } = {}) {
    await saveTaskToServer(task, { includeResults });
}

async function saveTaskToServer(task, { includeResults = true } = {}) {
    const response = await apiFetch(`${API_BASE}/tasks/${encodeURIComponent(task.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(taskForPersistence(task, { includeResults }))
    });
    if (!response.ok) {
        throw new Error(t('保存本地任务失败：{detail}', { detail: await responseErrorText(response) }));
    }
}

async function loadTasks() {
    const localTasks = await loadServerTasks();
    tasks = dedupeTasks(localTasks.map(reconcileTaskStatus));
}

function reconcileTaskStatus(task) {
    if (task?.status !== 'processing') return task;

    const batches = Array.isArray(task.batches) ? task.batches : [];
    const allBatchesCompleted = batches.length > 0 && batches.every((batch) => batch.status === 'completed');
    const hasAllOcrResults = Array.isArray(task.ocrResults) && task.ocrResults.length >= batches.length;
    if (task.status === 'processing' && allBatchesCompleted && hasAllOcrResults) {
        return { ...task, status: 'completed', updatedAt: task.updatedAt || Date.now() };
    }
    if (!isTaskDetailLoaded(task) && Number(task.completedPages || 0) >= Number(task.pageCount || Infinity)) {
        return { ...task, status: 'completed', updatedAt: task.updatedAt || Date.now() };
    }
    const reconciled = {
        ...task,
        status: 'pending',
        error: task.error || t('上次解析中断，可继续解析。'),
        updatedAt: task.updatedAt || Date.now()
    };
    if (isTaskDetailLoaded(task)) {
        reconciled.batches = batches.map((batch) => (
            batch.status === 'processing'
                ? { ...batch, status: 'pending' }
                : batch
        ));
    }
    return reconciled;
}

function dedupeTasks(taskItems) {
    const byFingerprint = new Map();
    taskItems.forEach((task) => {
        const fingerprint = [
            task.name,
            task.originalName || '',
            task.sourceKind || '',
            task.size || 0,
            task.pageCount || 0,
            task.modelId || '',
            task.comparisonGroupId || ''
        ].join('|');
        const existing = byFingerprint.get(fingerprint);
        if (!existing || (task.updatedAt || 0) > (existing.updatedAt || 0)) {
            byFingerprint.set(fingerprint, task);
        }
    });
    return Array.from(byFingerprint.values()).sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
}

async function loadServerTasks() {
    try {
        const response = await apiFetch(`${API_BASE}/tasks`);
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        return data.tasks || [];
    } catch (error) {
        console.warn('读取本地任务目录失败', error);
        return [];
    }
}

async function loadTaskFromServer(taskId) {
    const response = await apiFetch(`${API_BASE}/tasks/${encodeURIComponent(taskId)}`);
    if (!response.ok) {
        throw new Error(t('读取本地任务失败：{detail}', { detail: await responseErrorText(response) }));
    }
    return reconcileTaskStatus(await response.json());
}

function isTaskDetailLoaded(task) {
    return Boolean((task?.sourceDataUrl || task?.sourceUrl) && Array.isArray(task?.batches));
}

function replaceTask(task) {
    const index = tasks.findIndex((item) => item.id === task.id);
    if (index === -1) {
        tasks.unshift(task);
        return task;
    }
    tasks[index] = { ...tasks[index], ...task, detailLoaded: true };
    return tasks[index];
}

async function ensureTaskLoaded(taskId) {
    let task = tasks.find((item) => item.id === taskId);
    if (!task) return null;
    if (isTaskDetailLoaded(task)) return task;

    els.sourceTitle.textContent = task.name || t('正在加载任务');
    els.sourceMeta.textContent = t('正在加载本地任务详情...');
    els.resultTitle.textContent = t('正在加载');
    els.markdownView.innerHTML = `<div class="empty-result">${escapeHtml(t('正在加载任务详情...'))}</div>`;
    els.jsonView.textContent = '';
    updateActionState(null);

    task = await loadTaskFromServer(taskId);
    return replaceTask(task);
}

async function deleteAllTasks() {
    const response = await apiFetch(`${API_BASE}/tasks`, { method: 'DELETE' });
    if (!response.ok) {
        throw new Error(t('清空本地任务失败：{detail}', { detail: await responseErrorText(response) }));
    }
}

async function deleteTaskById(taskId) {
    const response = await apiFetch(`${API_BASE}/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' });
    if (!response.ok) {
        throw new Error(t('删除本地任务失败：{detail}', { detail: await responseErrorText(response) }));
    }
}

async function handleFiles(files) {
    if (comparisonInFlight) {
        alert(t('模型对比正在进行，完成后再添加文件。'));
        return [];
    }
    if (!files || files.length === 0) return [];

    const previousActiveTaskId = activeTaskId;
    const fileList = Array.from(files);
    showIncomingFileState(fileList);
    const results = await Promise.allSettled(fileList.map((file) => createTaskFromFile(file)));
    const newTasks = results
        .filter((result) => result.status === 'fulfilled')
        .map((result) => result.value);
    const failed = results.filter((result) => result.status === 'rejected');

    if (failed.length > 0) {
        console.warn('Some files could not be added', failed.map((result) => result.reason));
        const message = failed
            .map((result) => result.reason?.message || String(result.reason || t('文件读取失败')))
            .join('\n');
        els.markdownView.innerHTML = `<div class="empty-result">${escapeHtml(message)}</div>`;
    }
    if (newTasks.length === 0) {
        if (previousActiveTaskId && tasks.some((task) => task.id === previousActiveTaskId)) {
            await selectTask(previousActiveTaskId);
        } else {
            resetWorkbench();
        }
        return;
    }

    tasks = [...newTasks, ...tasks];
    renderTaskList();
    await selectTask(newTasks[0].id);
    const saveResults = await Promise.allSettled(newTasks.map((task) => saveTask(task)));
    const saveFailures = saveResults.filter((result) => result.status === 'rejected');
    if (saveFailures.length > 0) {
        console.warn('Some tasks could not be saved before processing', saveFailures.map((result) => result.reason));
    }

    for (const task of newTasks) {
        await processTask(task, { confirmCompleted: false });
    }
}

function showIncomingFileState(fileList) {
    const filesToAdd = Array.from(fileList || []);
    const primaryFile = filesToAdd[0];
    const fileCount = filesToAdd.length;

    activeTaskId = null;
    sourceRenderToken += 1;
    currentPdf = null;
    currentPage = 1;
    renderTaskList();
    resetResultRenderCache();
    resetResultScrollPositions();
    activeResultView = 'markdown';
    document.querySelectorAll('.view-tab').forEach((tab) => {
        tab.classList.toggle('active', tab.dataset.view === 'markdown');
    });
    showResultView('markdown');
    updateActionState(null);

    els.sourceTitle.textContent = primaryFile?.name || t('正在读取新文件');
    els.sourceMeta.textContent = fileCount > 1
        ? t('正在读取 {count} 个文件...', { count: fileCount })
        : t('正在读取文件...');
    els.pdfControls.classList.add('hidden');
    els.sourceViewer.innerHTML = `<div class="empty-result">${escapeHtml(t('正在读取文件，请稍候...'))}</div>`;
    els.sourceViewer.scrollTop = 0;
    els.resultTitle.textContent = t('准备解析');
    els.markdownView.innerHTML = `<div class="empty-result">${escapeHtml(t('正在读取新文件，解析结果会显示在这里。'))}</div>`;
    els.jsonView.textContent = '';
}

function assertUploadWithinLimit(fileOrBlob, filename = '') {
    const size = Number(fileOrBlob?.size || 0);
    if (!maxUploadBytes || !size || size <= maxUploadBytes) return;
    const name = filename || fileOrBlob.name || t('文件');
    throw new Error(t('{name} 超过上传上限 {limit}，请压缩或拆分后再试。', {
        name,
        limit: formatSize(maxUploadBytes)
    }));
}

async function createTaskFromFile(file) {
    assertUploadWithinLimit(file);
    const ext = getExtension(file.name);
    const officeExts = ['ppt', 'pptx', 'doc', 'docx'];
    const imageExts = ['png', 'jpg', 'jpeg', 'bmp', 'webp', 'tiff', 'tif', 'gif'];

    if (officeExts.includes(ext)) {
        const converted = await convertOfficeToPdf(file);
        return createPdfTask(converted.blob, file.name.replace(/\.[^.]+$/, '.pdf'), {
            originalName: file.name,
            sourceKind: 'office'
        });
    }

    if (ext === 'pdf' || file.type === 'application/pdf') {
        return createPdfTask(file, file.name, { sourceKind: 'pdf' });
    }

    if (imageExts.includes(ext)) {
        return createImageTask(file);
    }

    alert(t('不支持的文件格式：{name}', { name: file.name }));
    throw new Error(`Unsupported file type: ${file.name}`);
}

async function createImageTask(file) {
    const id = createId();
    const dataUrl = await readAsDataUrl(file);
    const sourceUrl = await uploadTaskSource(id, file, file.name, file.type || 'application/octet-stream');
    const now = Date.now();
    const task = {
        id,
        name: file.name,
        sourceKind: 'image',
        mimeType: file.type || 'image/*',
        size: file.size,
        createdAt: now,
        updatedAt: now,
        status: 'pending',
        pageCount: 1,
        sourceUrl,
        sourceDataUrl: dataUrl,
        thumbnail: dataUrl,
        batches: [{
            id: createId(),
            label: formatPageLabel(1),
            fileType: 1,
            pageCount: 1,
            payloadDataUrl: dataUrl,
            status: 'pending'
        }],
        markdown: '',
        images: {},
        ocrResults: []
    };
    applySelectedModelToTask(task);
    return task;
}

async function createPdfTask(fileOrBlob, name, extra = {}) {
    const id = createId();
    const arrayBuffer = await fileOrBlob.arrayBuffer();
    const sourceUrl = await uploadTaskSource(id, fileOrBlob, name, 'application/pdf');
    const pdf = await pdfjsLib.getDocument({ data: arrayBuffer.slice(0), isEvalSupported: false }).promise;
    const pageCount = pdf.numPages;
    const thumbnail = await renderPDFPageDataUrl(pdf, 1, 0.35);
    const pdfBatchSize = getConfiguredPdfBatchSize();
    const batches = createPdfBatchDescriptors(pageCount, pdfBatchSize);

    const now = Date.now();
    const task = {
        id,
        name,
        sourceKind: extra.sourceKind || 'pdf',
        originalName: extra.originalName || name,
        mimeType: 'application/pdf',
        size: fileOrBlob.size || arrayBuffer.byteLength,
        createdAt: now,
        updatedAt: now,
        status: 'pending',
        pageCount,
        pdfBatchSize,
        sourceUrl,
        thumbnail,
        batches,
        markdown: '',
        images: {},
        ocrResults: []
    };
    applySelectedModelToTask(task);
    return task;
}

async function uploadTaskSource(taskId, fileOrBlob, filename, mimeType) {
    assertUploadWithinLimit(fileOrBlob, filename);
    const formData = new FormData();
    const source = fileOrBlob instanceof File
        ? fileOrBlob
        : new File([fileOrBlob], filename, { type: mimeType || fileOrBlob.type || 'application/octet-stream' });
    formData.append('file', source, filename);
    const response = await apiFetch(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/source`, {
        method: 'POST',
        body: formData
    });
    if (!response.ok) {
        throw new Error(t('保存源文件失败：{detail}', { detail: await responseErrorText(response) }));
    }
    const data = await response.json();
    return data.url;
}

async function convertOfficeToPdf(file) {
    assertUploadWithinLimit(file);
    const formData = new FormData();
    formData.append('file', file);
    const response = await apiFetch(`${API_BASE}/convert/to-pdf`, {
        method: 'POST',
        body: formData
    });
    if (!response.ok) {
        const detail = await responseErrorText(response);
        throw new Error(t('Office 转 PDF 失败：{detail}', { detail }));
    }
    return { blob: await response.blob() };
}

function renderTaskList() {
    const keyword = els.taskSearch.value.trim().toLowerCase();
    els.taskList.innerHTML = '';
    const visibleTasks = tasks.filter((task) => {
        if (activeFilter === 'done' && task.status !== 'completed') return false;
        return !keyword || task.name.toLowerCase().includes(keyword);
    });

    if (visibleTasks.length === 0) {
        els.taskList.innerHTML = `<div class="task-empty">${escapeHtml(t('暂无任务'))}</div>`;
        return;
    }

    for (const task of visibleTasks) {
        const clone = els.taskTemplate.content.cloneNode(true);
        const item = clone.querySelector('.task-item');
        item.dataset.taskId = task.id;
        item.classList.toggle('active', task.id === activeTaskId);
        item.classList.add(`status-${taskVisualStatus(task)}`);
        item.querySelector('.task-icon').innerHTML = taskIcon(task);
        item.querySelector('.task-name').textContent = task.name;
        item.querySelector('.task-meta').textContent = `${formatDate(task.updatedAt)} · ${formatPageCount(task.pageCount || 1)}`;
        item.querySelector('.task-state').textContent = statusText(task);
        const deleteButton = item.querySelector('.task-delete');
        deleteButton.setAttribute('title', t('删除任务'));
        deleteButton.setAttribute('aria-label', t('删除任务'));
        deleteButton.disabled = historyMutationsLocked();
        item.addEventListener('click', () => selectTask(task.id));
        item.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectTask(task.id);
            }
        });
        deleteButton.addEventListener('click', async (event) => {
            event.stopPropagation();
            await deleteTask(task.id);
        });
        els.taskList.appendChild(item);
    }
}

async function deleteTask(taskId) {
    const task = tasks.find((item) => item.id === taskId);
    if (!task) return;
    if (comparisonInFlight) {
        alert(t('模型对比正在进行，完成后再删除任务。'));
        return;
    }
    if (historyMutationsLocked()) {
        alert(t('当前正在解析或切换模型，完成后再删除任务。'));
        return;
    }
    if (task.status === 'processing' && !shouldResumeTask(task)) {
        alert(t('当前文件正在解析中，完成后再删除。'));
        return;
    }
    if (!confirm(t('确定要删除“{name}”吗？当前操作不可回撤。', { name: task.name }))) return;

    const wasActive = activeTaskId === taskId;
    try {
        await deleteTaskById(taskId);
    } catch (error) {
        console.error(error);
        alert(error.message || t('删除失败，请稍后重试。'));
        return;
    }
    tasks = tasks.filter((item) => item.id !== taskId);

    if (!wasActive) {
        renderTaskList();
        return;
    }

    activeTaskId = tasks[0]?.id || null;
    if (activeTaskId) {
        await selectTask(activeTaskId);
    } else {
        resetWorkbench();
    }
}

async function selectTask(taskId) {
    activeTaskId = taskId;
    renderTaskList();
    let task;
    try {
        task = await ensureTaskLoaded(taskId);
    } catch (error) {
        console.error(error);
        els.sourceTitle.textContent = t('任务加载失败');
        els.sourceMeta.textContent = '';
        els.resultTitle.textContent = t('加载失败');
        els.markdownView.textContent = error.message || t('任务详情加载失败');
        updateActionState(null);
        return;
    }
    if (activeTaskId !== taskId) return;
    if (!task) return;
    renderTaskList();
    updateActiveModelDisplay(task);
    els.sourceTitle.textContent = task.name;
    els.sourceMeta.textContent = taskSourceMeta(task);
    els.resultTitle.textContent = resultPaneTitle(task);
    const deferPPOCRVisualResult = isPPOCRVisualTask(task) && task.sourceKind !== 'image';
    if (!deferPPOCRVisualResult) {
        renderResultPane(task);
    } else {
        resetResultRenderCache(task.id);
        resetResultScrollPositions();
        updateResultViewLabels(task);
        syncResultMode(task);
        showResultView('markdown');
    }
    updateActionState(task);
    await renderSource(task);
    if (activeTaskId !== taskId) return;
    if (deferPPOCRVisualResult) {
        invalidatePPOCRVisualRender();
    }
    renderResultPane(task);
    updateActionState(task);
}

function getActiveTask() {
    return tasks.find((task) => task.id === activeTaskId);
}

async function renderSource(task) {
    const renderToken = ++sourceRenderToken;
    currentPdf = null;
    els.pdfControls.classList.add('hidden');
    els.sourceViewer.innerHTML = '';
    els.sourceViewer.scrollTop = 0;
    els.sourceViewer.scrollLeft = 0;
    els.markdownView.scrollLeft = 0;

    if (task.sourceKind === 'image') {
        const wrap = document.createElement('div');
        wrap.className = 'source-image-wrap';
        wrap.dataset.page = '1';
        const imageBox = document.createElement('div');
        imageBox.className = 'source-image-box';
        const img = document.createElement('img');
        img.className = 'source-image';
        img.src = task.sourceDataUrl || task.sourceUrl;
        imageBox.appendChild(img);
        const highlightLayer = document.createElement('div');
        highlightLayer.className = 'pdf-highlight-layer';
        imageBox.appendChild(highlightLayer);
        wrap.appendChild(imageBox);
        els.sourceViewer.appendChild(wrap);
        await waitForImageReady(img);
        return;
    }

    currentPage = Math.min(Math.max(currentPage, 1), task.pageCount || 1);
    els.pdfControls.classList.remove('hidden');
    const pdf = task.sourceDataUrl
        ? await pdfjsLib.getDocument({ data: dataUrlToUint8Array(task.sourceDataUrl), isEvalSupported: false }).promise
        : await pdfjsLib.getDocument({ url: task.sourceUrl, isEvalSupported: false }).promise;
    if (renderToken !== sourceRenderToken) return;
    const firstPage = await pdf.getPage(1);
    if (renderToken !== sourceRenderToken) return;
    pdfDefaultPageWidth = firstPage.getViewport({ scale: 1 }).width || PDF_DEFAULT_PAGE_WIDTH;
    currentZoom = getDefaultPdfZoom();
    currentPdf = pdf;
    await renderPdfDocument(renderToken);
}

async function renderPdfDocument(renderToken = sourceRenderToken, scrollAnchor = null) {
    if (renderToken !== sourceRenderToken) return;
    if (!currentPdf) return;
    currentPage = Math.min(Math.max(currentPage, 1), currentPdf.numPages);
    updatePdfControls();
    els.sourceViewer.innerHTML = '';
    const flow = document.createElement('div');
    flow.className = 'pdf-document-flow';
    els.sourceViewer.appendChild(flow);

    for (let pageNumber = 1; pageNumber <= currentPdf.numPages; pageNumber += 1) {
        if (renderToken !== sourceRenderToken) return;

        const page = await currentPdf.getPage(pageNumber);
        const viewport = page.getViewport({ scale: currentZoom });
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        canvas.width = viewport.width;
        canvas.height = viewport.height;

        const wrap = document.createElement('div');
        wrap.className = 'pdf-page-wrap';
        wrap.dataset.page = String(pageNumber);
        const canvasBox = document.createElement('div');
        canvasBox.className = 'pdf-canvas-box';
        canvasBox.appendChild(canvas);
        const highlightLayer = document.createElement('div');
        highlightLayer.className = 'pdf-highlight-layer';
        canvasBox.appendChild(highlightLayer);
        wrap.appendChild(canvasBox);
        flow.appendChild(wrap);

        await page.render({ canvasContext: context, viewport }).promise;
    }

    if (scrollAnchor) {
        restoreSourceScrollAnchor(scrollAnchor, 'auto');
    } else {
        scrollPdfPageIntoView(currentPage, 'auto');
        resetSplitHorizontalScroll();
    }
    updateCurrentPageFromScroll();
}

function setActiveResultView(view) {
    if (!view || view === activeResultView) return;
    activeResultView = view;
    document.querySelectorAll('.view-tab').forEach((tab) => {
        tab.classList.toggle('active', tab.dataset.view === view);
    });
    renderResultPane(getActiveTask(), { deferJson: true });
    updateActionState(getActiveTask());
}

function resultDataKey(task) {
    if (!task) return '';
    const visibleMarkdown = visibleTaskMarkdown(task);
    return [
        task.id,
        task.status,
        task.updatedAt || 0,
        visibleMarkdown.length,
        task.ocrResults?.length || 0,
        taskProgressSeconds(task)
    ].join(':');
}

function markdownRenderKey(task) {
    return `${resultDataKey(task)}:${sourceRenderToken}:${currentZoom}:${currentLanguage}`;
}

function resetResultRenderCache(taskId = null) {
    renderedResultTaskId = taskId;
    renderedMarkdownKey = '';
    renderedOfficialLayoutContext = '';
    renderedPPOCRVisualContext = '';
    renderedJsonKey = '';
    linkedLayoutEntries = [];
    cachedJsonLines = [];
    cachedJsonMaxLineLength = 0;
    jsonRenderToken += 1;
}

function invalidatePPOCRVisualRender() {
    renderedMarkdownKey = '';
    renderedPPOCRVisualContext = '';
}

function resetResultScrollPositions() {
    els.markdownView.scrollTop = 0;
    els.markdownView.scrollLeft = 0;
    els.jsonView.scrollTop = 0;
    els.jsonView.scrollLeft = 0;
    if (els.comparisonView) {
        els.comparisonView.scrollTop = 0;
        els.comparisonView.scrollLeft = 0;
    }
}

function captureResultScrollState() {
    const element = activeResultView === 'json'
        ? els.jsonView
        : (activeResultView === 'comparison' ? els.comparisonView : els.markdownView);
    const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
    const bottomOffset = maxScrollTop - element.scrollTop;
    return {
        element,
        scrollTop: element.scrollTop,
        scrollLeft: element.scrollLeft,
        stickToBottom: bottomOffset <= 32
    };
}

function restoreResultScrollState(state) {
    if (!state?.element) return;

    const restore = () => {
        const maxScrollTop = Math.max(0, state.element.scrollHeight - state.element.clientHeight);
        state.element.scrollTop = state.stickToBottom
            ? maxScrollTop
            : Math.min(state.scrollTop, maxScrollTop);
        const maxScrollLeft = Math.max(0, state.element.scrollWidth - state.element.clientWidth);
        state.element.scrollLeft = Math.min(state.scrollLeft || 0, maxScrollLeft);
    };

    restore();
    requestAnimationFrame(restore);
    setTimeout(restore, 80);
}

function showResultView(view) {
    const showJson = view === 'json';
    const showComparison = view === 'comparison';
    els.markdownView.classList.toggle('hidden', showJson || showComparison);
    els.jsonView.classList.toggle('hidden', !showJson);
    els.comparisonView?.classList.toggle('hidden', !showComparison);
}

function renderResultPane(task, { deferJson = false, preserveScroll = true } = {}) {
    if (!task) {
        resetResultRenderCache();
        resetResultScrollPositions();
        updateResultViewLabels(null);
        syncResultMode(null);
        showResultView('markdown');
        els.resultTitle.textContent = t('解析结果');
        els.markdownView.innerHTML = `<div class="empty-result">${escapeHtml(t('选择左侧任务，或上传一个新文件开始解析。'))}</div>`;
        els.jsonView.textContent = '';
        if (els.comparisonView) els.comparisonView.textContent = '';
        return;
    }

    const isSameRenderedTask = renderedResultTaskId === task.id;
    const scrollState = preserveScroll && isSameRenderedTask
        ? captureResultScrollState()
        : null;

    if (!isSameRenderedTask) {
        resetResultRenderCache(task.id);
        resetResultScrollPositions();
    }

    els.resultTitle.textContent = resultPaneTitle(task);
    updateResultViewLabels(task);
    syncResultMode(task);

    if (activeResultView === 'json') {
        showResultView('json');
        renderJsonResult(task, { defer: deferJson, scrollState });
        return;
    }

    if (activeResultView === 'comparison') {
        showResultView('comparison');
        renderComparisonResult(task);
        restoreResultScrollState(scrollState);
        return;
    }

    showResultView('markdown');
    const markdownKey = markdownRenderKey(task);
    const manuallyEdited = Boolean(task.manualEditedAt);
    const ppocrVisualTask = !manuallyEdited && isPPOCRVisualTask(task);
    const ppocrContext = ppocrVisualTask ? ppocrVisualRenderContext(task) : '';
    if (renderedMarkdownKey === markdownKey && (!ppocrVisualTask || renderedPPOCRVisualContext === ppocrContext)) {
        warmJsonResultCache(task);
        restoreResultScrollState(scrollState);
        return;
    }

    if (ppocrVisualTask) {
        renderedOfficialLayoutContext = '';
        renderPPOCRVisualResult(task, markdownKey, scrollState);
        warmJsonResultCache(task);
        return;
    }

    const officialRender = !manuallyEdited && shouldRenderOfficialLayout(task)
        ? renderOfficialLayoutResult(task)
        : { rendered: false, changed: false, mathRoots: [] };
    if (officialRender.rendered) {
        renderedMarkdownKey = markdownKey;
        if (officialRender.changed) {
            officialRender.mathRoots.forEach((root) => renderMathWhenReady(root));
        }
        warmJsonResultCache(task);
        restoreResultScrollState(scrollState);
        return;
    }

    const markdown = prepareMarkdownForRender(visibleTaskMarkdown(task));
    if (!markdown) {
        renderedOfficialLayoutContext = '';
        clearSourceHighlight();
        clearSourceHotspots();
        els.markdownView.innerHTML = `<div class="empty-result">${escapeHtml(emptyResultText(task))}</div>`;
        renderedMarkdownKey = markdownKey;
        warmJsonResultCache(task);
        restoreResultScrollState(scrollState);
        return;
    }

    let renderMarkdown = markdown;
    renderedOfficialLayoutContext = '';
    Object.entries(task.images || {}).forEach(([path, base64]) => {
        renderMarkdown = renderMarkdown.split(path).join(`data:image/jpeg;base64,${base64}`);
    });
    const html = renderMarkdownHtml(renderMarkdown);
    els.markdownView.innerHTML = html;
    linkMarkdownToSourceBlocks(task);
    renderedMarkdownKey = markdownKey;
    renderMathWhenReady(els.markdownView);
    warmJsonResultCache(task);
    restoreResultScrollState(scrollState);
}

function renderJsonResult(task, { defer = false, scrollState = null } = {}) {
    const key = resultDataKey(task);
    if (renderedJsonKey === key) {
        renderVisibleJsonLines();
        restoreResultScrollState(scrollState);
        return;
    }

    const render = () => {
        cacheJsonLines(JSON.stringify(toOfficialJson(task), null, 2));
        renderedJsonKey = key;
        if (!scrollState) {
            els.jsonView.scrollTop = 0;
        }
        renderVisibleJsonLines();
        restoreResultScrollState(scrollState);
    };

    if (!defer) {
        render();
        return;
    }

    const token = ++jsonRenderToken;
    renderVisibleJsonLines();
    requestAnimationFrame(() => {
        if (token !== jsonRenderToken || activeResultView !== 'json' || getActiveTask()?.id !== task.id) return;
        render();
    });
}

function warmJsonResultCache(task) {
    const key = resultDataKey(task);
    if (renderedJsonKey === key || !task?.ocrResults?.length) return;

    const warm = () => {
        if (renderedJsonKey === key || activeResultView === 'json' || getActiveTask()?.id !== task.id) return;
        cacheJsonLines(JSON.stringify(toOfficialJson(task), null, 2));
        renderedJsonKey = key;
    };

    if (window.requestIdleCallback) {
        requestIdleCallback(warm, { timeout: 1200 });
    } else {
        setTimeout(warm, 80);
    }
}

function cacheJsonLines(text) {
    cachedJsonLines = String(text || '').split('\n');
    cachedJsonMaxLineLength = cachedJsonLines.reduce((max, line) => Math.max(max, line.length), 0);
}

function updateResultViewLabels(task) {
    const markdownTab = document.querySelector('.view-tab[data-view="markdown"]');
    if (!markdownTab) return;
    markdownTab.textContent = isPPOCRVisualTask(task) ? t('文字识别') : t('文档解析');
    const groupTasks = comparisonTasksFor(task);
    const hasComparison = groupTasks.length > 1;
    els.comparisonTab?.classList.toggle('hidden', !hasComparison);
    if (!hasComparison && activeResultView === 'comparison') {
        activeResultView = 'markdown';
        document.querySelectorAll('.view-tab').forEach((tab) => {
            tab.classList.toggle('active', tab.dataset.view === 'markdown');
        });
    }
}

function syncResultMode(task) {
    const visualMode = !task?.manualEditedAt && isPPOCRVisualTask(task) && activeResultView === 'markdown';
    els.resultPane?.classList.toggle('ppocr-result-mode', visualMode);
    els.markdownView.classList.toggle('ocr-visual-mode', visualMode);
}

function isPPOCRVisualTask(task) {
    return task?.modelId === 'pp-ocrv6'
        || Boolean(task?.ocrResults?.some((pageResult) => pageResult?.parser === 'pp-ocrv6'));
}

function isUnlimitedOCRTask(task) {
    return task?.modelId === UNLIMITED_OCR_MODEL_ID
        || Boolean(task?.ocrResults?.some((pageResult) => pageResult?.parser === 'unlimited-ocr'));
}

function isOvisOCR2Task(task) {
    return task?.modelId === OVIS_OCR_MODEL_ID
        || Boolean(task?.ocrResults?.some((pageResult) => pageResult?.parser === OVIS_OCR_MODEL_ID));
}

function isHPDParsingTask(task) {
    return task?.modelId === HPD_PARSING_MODEL_ID
        || Boolean(task?.ocrResults?.some((pageResult) => pageResult?.parser === HPD_PARSING_MODEL_ID));
}

function shouldRenderOfficialLayout(task) {
    // These models return complete document Markdown, while their layout blocks
    // are partial metadata and must not replace the full Markdown result.
    return !isUnlimitedOCRTask(task) && !isOvisOCR2Task(task) && !isHPDParsingTask(task);
}

function renderPPOCRVisualResult(task, markdownKey, scrollState = null) {
    const pages = collectPPOCRVisualPages(task);
    const context = ppocrVisualRenderContext(task);
    const visualScrollState = freezeVisualScrollState(scrollState);

    if (!pages.length) {
        const hasEmptyResult = els.markdownView.children.length === 1
            && els.markdownView.firstElementChild?.classList.contains('empty-result')
            && renderedPPOCRVisualContext === context;
        if (!hasEmptyResult) {
            clearSourceHighlight();
            clearSourceHotspots();
            els.markdownView.innerHTML = `<div class="empty-result">${escapeHtml(emptyResultText(task))}</div>`;
        }
        renderedPPOCRVisualContext = context;
        renderedMarkdownKey = markdownKey;
        restoreResultScrollState(visualScrollState);
        return;
    }

    const expectedKeys = pages.map(ppocrVisualPageKey);
    let flow = els.markdownView.querySelector(':scope > .ocr-visual-flow');
    const existingPages = flow
        ? Array.from(flow.children).filter((element) => element.classList.contains('ocr-visual-page'))
        : [];
    const existingKeys = existingPages.map((element) => element.dataset.pageKey || '');
    const canAppend = Boolean(flow)
        && els.markdownView.children.length === 1
        && renderedPPOCRVisualContext === context
        && existingKeys.length <= expectedKeys.length
        && existingKeys.every((key, index) => key === expectedKeys[index]);

    if (canAppend && existingKeys.length === expectedKeys.length) {
        renderedMarkdownKey = markdownKey;
        restoreResultScrollState(visualScrollState);
        return;
    }

    if (!canAppend) {
        clearSourceHighlight();
        clearSourceHotspots();
        flow = document.createElement('div');
        flow.className = 'ocr-visual-flow';
        els.markdownView.replaceChildren(flow);
        renderedPPOCRVisualContext = context;
    }

    const startIndex = canAppend ? existingKeys.length : 0;
    pages.slice(startIndex).forEach((page, offset) => {
        const pageIndex = startIndex + offset;
        flow.appendChild(createPPOCRVisualPage(page, pageIndex, expectedKeys[pageIndex]));
    });
    renderedMarkdownKey = markdownKey;
    restoreResultScrollState(visualScrollState);
}

function freezeVisualScrollState(scrollState) {
    if (!scrollState) return null;
    return {
        ...scrollState,
        stickToBottom: false
    };
}

function ppocrVisualRenderContext(task) {
    return [
        task?.id || '',
        sourceRenderToken,
        currentZoom,
        currentLanguage
    ].join(':');
}

function ppocrVisualPageKey(page) {
    const firstLine = page.lines[0] || {};
    const lastLine = page.lines[page.lines.length - 1] || {};
    const signature = [
        firstLine.text || '',
        Array.isArray(firstLine.box) ? firstLine.box.join(',') : '',
        lastLine.text || '',
        Array.isArray(lastLine.box) ? lastLine.box.join(',') : ''
    ].join('|');
    return [
        page.pageNumber || '',
        page.index,
        page.pageImage ? String(page.pageImage).length : 0,
        page.lines.length,
        hashString(signature)
    ].join(':');
}

function hashString(value) {
    let hash = 0;
    const text = String(value || '');
    for (let index = 0; index < text.length; index += 1) {
        hash = ((hash << 5) - hash + text.charCodeAt(index)) >>> 0;
    }
    return hash.toString(36);
}

function collectPPOCRVisualPages(task) {
    if (!Array.isArray(task?.ocrResults)) return [];
    return task.ocrResults
        .map((pageResult, index) => {
            const lines = collectPPOCRLines(pageResult);
            const pageImage = pageResult?.pageImage || pageResult?.inputImage || null;
            return {
                index,
                pageNumber: Number(pageResult?.sourcePage || pageResult?.page_index || index + 1),
                pageImage,
                lines
            };
        })
        .filter((page) => page.pageImage || page.lines.length > 0)
        .sort((a, b) => (a.pageNumber - b.pageNumber) || (a.index - b.index));
}

function collectPPOCRLines(pageResult) {
    if (Array.isArray(pageResult?.ocrLines)) {
        return pageResult.ocrLines
            .map((line, index) => normalizePPOCRLine(line, index))
            .filter(Boolean);
    }

    const pruned = pageResult?.prunedResult || pageResult || {};
    const texts = Array.isArray(pruned.rec_texts) ? pruned.rec_texts : [];
    const scores = Array.isArray(pruned.rec_scores) ? pruned.rec_scores : [];
    const boxes = Array.isArray(pruned.rec_boxes) ? pruned.rec_boxes : [];
    const polys = Array.isArray(pruned.rec_polys) ? pruned.rec_polys : [];
    return texts.map((text, index) => normalizePPOCRLine({
        text,
        score: scores[index],
        box: boxes[index],
        poly: polys[index]
    }, index)).filter(Boolean);
}

function normalizePPOCRLine(line, index) {
    const text = String(line?.text || '').trim();
    if (!text) return null;
    const box = normalizePPOCRBox(line.box || boxFromPoly(line.poly));
    if (!box) return null;
    return {
        index,
        text,
        score: line.score,
        box
    };
}

function boxFromPoly(poly) {
    if (!Array.isArray(poly) || poly.length === 0) return null;
    const xs = [];
    const ys = [];
    poly.forEach((point) => {
        if (!Array.isArray(point) || point.length < 2) return;
        xs.push(Number(point[0]));
        ys.push(Number(point[1]));
    });
    if (!xs.length || !ys.length) return null;
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function normalizePPOCRBox(box) {
    if (!Array.isArray(box) || box.length < 4) return null;
    const values = box.slice(0, 4).map(Number);
    if (values.some((value) => !Number.isFinite(value))) return null;
    const [x1, y1, x2, y2] = values;
    if (x2 <= x1 || y2 <= y1) return null;
    return values;
}

function createPPOCRVisualPage(page, pageIndex, pageKey = '') {
    const pageElement = document.createElement('section');
    pageElement.className = 'ocr-visual-page';
    pageElement.dataset.page = String(page.pageNumber || pageIndex + 1);
    if (pageKey) pageElement.dataset.pageKey = pageKey;

    const stage = document.createElement('div');
    stage.className = 'ocr-page-stage loading';
    const displaySize = getPPOCRPageDisplaySize(page);
    if (displaySize) {
        applyPPOCRStageDisplaySize(stage, displaySize.width, displaySize.height);
    }
    pageElement.appendChild(stage);

    const toolbar = createPPOCRFloatingToolbar();
    stage.appendChild(toolbar);

    if (page.pageImage) {
        const img = document.createElement('img');
        img.className = 'ocr-page-image';
        img.alt = `OCR page ${page.pageNumber || pageIndex + 1}`;
        img.addEventListener('load', () => {
            stage.classList.remove('loading');
            const coordinateWidth = img.naturalWidth || displaySize?.width || 1;
            const coordinateHeight = img.naturalHeight || displaySize?.height || 1;
            layoutPPOCRTextLayer(stage, page, coordinateWidth, coordinateHeight, toolbar, img);
            syncPPOCRVisualScrollFromSource();
        }, { once: true });
        img.addEventListener('error', () => {
            stage.classList.remove('loading');
            createPPOCRTextOnlyLayer(stage, page.lines, toolbar);
        }, { once: true });
        if (displaySize) {
            applyPPOCRStageDisplaySize(stage, displaySize.width, displaySize.height, img);
        }
        stage.appendChild(img);
        img.src = imageValueToSrc(page.pageImage);
    } else {
        stage.classList.remove('loading');
        createPPOCRTextOnlyLayer(stage, page.lines, toolbar);
    }

    return pageElement;
}

function getPPOCRPageDisplaySize(page) {
    const sourceSize = getSourcePageDisplaySize(page.pageNumber);
    if (sourceSize?.width && sourceSize?.height) return sourceSize;
    return null;
}

function applyPPOCRStageDisplaySize(stage, width, height, imageElement = null) {
    if (!stage || !width || !height) return;
    const roundedWidth = Math.round(width);
    const roundedHeight = Math.round(height);
    stage.classList.add('sized');
    stage.style.width = `${roundedWidth}px`;
    stage.style.height = `${roundedHeight}px`;
    if (imageElement) {
        imageElement.style.width = `${roundedWidth}px`;
        imageElement.style.height = `${roundedHeight}px`;
    }
}

function createPPOCRFloatingToolbar() {
    const toolbar = document.createElement('div');
    toolbar.className = 'ocr-floating-toolbar hidden';
    toolbar.innerHTML = `
        <button type="button" data-action="copy">
            <svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            <span data-label>${escapeHtml(t('复制'))}</span>
        </button>
        <button type="button" data-action="correct">
            <svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
            <span data-label>${escapeHtml(t('纠正'))}</span>
        </button>
    `;
    const handleToolbarAction = async (event) => {
        const button = event.target.closest('button');
        if (!button) return;
        event.stopPropagation();
        if (event.type === 'pointerdown') {
            event.preventDefault();
            toolbar._lastPointerAction = {
                action: button.dataset.action,
                time: Date.now()
            };
        } else if (
            toolbar._lastPointerAction?.action === button.dataset.action
            && Date.now() - toolbar._lastPointerAction.time < 500
        ) {
            return;
        }
        if (button.dataset.action === 'copy') {
            await copyPPOCRToolbarText(toolbar, button);
        }
        if (button.dataset.action === 'correct') {
            openPPOCRCorrectionEditor(toolbar);
        }
    };
    toolbar.querySelectorAll('button').forEach((button) => {
        button.addEventListener('pointerdown', handleToolbarAction);
        button.addEventListener('click', handleToolbarAction);
    });
    return toolbar;
}

async function copyPPOCRToolbarText(toolbar, button) {
    const text = toolbar.dataset.text || '';
    if (!text) return;
    try {
        await writeClipboardText(text);
        flashToolbarButtonLabel(button, t('已复制'), t('复制'));
    } catch (error) {
        console.error(error);
        flashToolbarButtonLabel(button, t('复制失败'), t('复制'));
    }
}

async function writeClipboardText(text) {
    if (navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return;
        } catch (error) {
            console.warn('Clipboard API write failed, falling back to textarea copy.', error);
        }
    }
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    if (!copied) {
        throw new Error('copy command failed');
    }
}

function flashToolbarButtonLabel(button, text, restoreText) {
    const label = button.querySelector('[data-label]');
    if (!label) return;
    label.textContent = text;
    window.setTimeout(() => {
        label.textContent = restoreText;
    }, 900);
}

function openPPOCRCorrectionEditor(toolbar) {
    const stage = toolbar.closest('.ocr-page-stage');
    const active = toolbar._activePPOCR;
    if (!stage || !active?.element || !active?.line) return;
    stage.querySelector('.ocr-correction-popover')?.remove();

    const popover = document.createElement('form');
    popover.className = 'ocr-correction-popover';
    popover.innerHTML = `
        <input type="text" name="text" aria-label="${escapeHtml(t('纠正文字'))}">
        <button type="submit">${escapeHtml(t('保存'))}</button>
        <button type="button" data-action="cancel">${escapeHtml(t('取消'))}</button>
    `;
    const input = popover.querySelector('input');
    input.value = active.line.text || '';
    popover.addEventListener('submit', async (event) => {
        event.preventDefault();
        const nextText = input.value.trim();
        if (!nextText) return;
        await applyPPOCRCorrection(active.element, active.line, nextText, toolbar);
        popover.remove();
    });
    popover.querySelector('[data-action="cancel"]').addEventListener('click', () => popover.remove());
    stage.appendChild(popover);

    const toolbarRect = toolbar.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    const viewerRect = els.markdownView.getBoundingClientRect();
    const minLeft = viewerRect.left - stageRect.left + 8;
    const maxLeft = viewerRect.right - stageRect.left - 286;
    const minTop = viewerRect.top - stageRect.top + 8;
    const maxTop = viewerRect.bottom - stageRect.top - 48;
    const left = Math.min(
        Math.max(toolbarRect.left - stageRect.left, minLeft),
        Math.max(maxLeft, minLeft)
    );
    const top = Math.min(
        Math.max(toolbarRect.bottom - stageRect.top + 8, minTop),
        Math.max(maxTop, minTop)
    );
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
    input.focus();
    input.select();
}

async function applyPPOCRCorrection(element, line, nextText, toolbar) {
    const previousText = line.text || '';
    if (nextText === previousText) return;

    line.text = nextText;
    updatePPOCRLineElementText(element, nextText);
    element.classList.toggle('ocr-text-line-code', isPPOCRCodeToken(nextText));
    toolbar.dataset.text = nextText;
    updateStoredPPOCRLineText(line, nextText);
    fitPPOCRLineElement(element, line);
    await saveCorrectedPPOCRTask();
}

function updatePPOCRLineElementText(element, text) {
    const label = element.querySelector('.ocr-text-line-label');
    if (label) label.textContent = text;
    element.title = text;
    element.setAttribute('aria-label', text);
}

function updateStoredPPOCRLineText(line, text) {
    const task = getActiveTask();
    const pageResult = task?.ocrResults?.[line.pageResultIndex];
    if (!pageResult) return;

    if (Array.isArray(pageResult.ocrLines) && pageResult.ocrLines[line.index]) {
        pageResult.ocrLines[line.index].text = text;
    }
    const pruned = pageResult.prunedResult || pageResult;
    if (Array.isArray(pruned?.rec_texts) && pruned.rec_texts[line.index] !== undefined) {
        pruned.rec_texts[line.index] = text;
    }
    task.updatedAt = Date.now();
}

async function saveCorrectedPPOCRTask() {
    const task = getActiveTask();
    if (!task) return;
    renderedJsonKey = '';
    warmJsonResultCache(task);
    try {
        await saveTask(task);
    } catch (error) {
        console.error(error);
        alert(error.message || '\u4fdd\u5b58\u7ea0\u6b63\u5931\u8d25');
    }
}

function layoutPPOCRTextLayer(stage, page, width, height, toolbar, imageElement = null) {
    stage.querySelector('.ocr-text-layer')?.remove();
    const layer = document.createElement('div');
    layer.className = 'ocr-text-layer';
    stage.appendChild(layer);

    const lines = page.lines || [];
    let renderWidth = width || 1;
    let renderHeight = height || 1;
    const sourceSize = getSourcePageDisplaySize(page.pageNumber);
    if (sourceSize) {
        renderWidth = sourceSize.width;
        renderHeight = sourceSize.height;
    }
    if (imageElement) {
        renderWidth = renderWidth || imageElement.clientWidth || imageElement.naturalWidth || width || 1;
        renderHeight = renderHeight || imageElement.clientHeight || imageElement.naturalHeight || height || 1;
        stage.style.width = `${renderWidth}px`;
        stage.style.height = `${renderHeight}px`;
        layer.style.width = `${renderWidth}px`;
        layer.style.height = `${renderHeight}px`;
        imageElement.style.width = `${renderWidth}px`;
        imageElement.style.height = `${renderHeight}px`;
    }

    const bounds = inferPPOCRCoordinateBounds(lines, width, height);
    lines.forEach((line) => {
        hydratePPOCRLineGeometry(line, page, bounds);
        const element = document.createElement('button');
        element.type = 'button';
        element.className = 'ocr-text-line';
        element.appendChild(createPPOCRLineLabel(line.text));
        element.title = line.text;
        element.setAttribute('aria-label', line.text);
        element.dataset.page = String(line.sourcePage || page.pageNumber || '');
        element.dataset.pageResultIndex = String(line.pageResultIndex ?? '');
        element.dataset.lineIndex = String(line.index ?? '');
        positionPPOCRLine(element, line, bounds, renderWidth, renderHeight);
        bindPPOCRLineEvents(element, toolbar, line);
        layer.appendChild(element);
        addPPOCRSourceHotspot(line, element, toolbar);
        fitPPOCRLineElement(element, line);
    });
}

function getSourcePageDisplaySize(pageNumber) {
    const pageWrap = els.sourceViewer.querySelector(`.pdf-page-wrap[data-page="${pageNumber}"]`);
    const canvas = pageWrap?.querySelector('canvas');
    if (!canvas) return null;
    return {
        width: canvas.clientWidth || canvas.width,
        height: canvas.clientHeight || canvas.height
    };
}

function hydratePPOCRLineGeometry(line, page, bounds) {
    line.sourcePage = Number(page.pageNumber || line.sourcePage || 1);
    line.pageResultIndex = page.index;
    line.pageWidth = bounds.width;
    line.pageHeight = bounds.height;
}

function createPPOCRLineLabel(text) {
    const label = document.createElement('span');
    label.className = 'ocr-text-line-label';
    label.textContent = text;
    return label;
}

function createPPOCRTextOnlyLayer(stage, lines, toolbar) {
    const fallback = document.createElement('div');
    fallback.className = 'ocr-text-only';
    lines.forEach((line) => {
        const element = document.createElement('button');
        element.type = 'button';
        element.className = 'ocr-text-only-line';
        element.textContent = line.text;
        bindPPOCRLineEvents(element, toolbar, line);
        fallback.appendChild(element);
    });
    stage.appendChild(fallback);
}

function inferPPOCRCoordinateBounds(lines, width, height) {
    const maxX = Math.max(width, ...lines.map((line) => line.box[2]));
    const maxY = Math.max(height, ...lines.map((line) => line.box[3]));
    return {
        width: maxX || width || 1,
        height: maxY || height || 1
    };
}

function positionPPOCRLine(element, line, bounds, renderWidth, renderHeight) {
    const box = line.box;
    const [x1, y1, x2, y2] = box;
    const left = (x1 / bounds.width) * 100;
    const top = (y1 / bounds.height) * 100;
    const width = ((x2 - x1) / bounds.width) * 100;
    const height = ((y2 - y1) / bounds.height) * 100;
    const boxWidth = Math.max(1, (width / 100) * renderWidth);
    const boxHeight = Math.max(1, (height / 100) * renderHeight);
    const isCodeToken = isPPOCRCodeToken(line.text);
    const fontSize = fittedPPOCRFontSize(line.text, boxWidth, boxHeight);

    element.style.left = `${left}%`;
    element.style.top = `${top}%`;
    element.style.width = `${width}%`;
    element.style.height = `${height}%`;
    element.style.fontSize = `${fontSize}px`;
    if (isCodeToken) {
        element.classList.add('ocr-text-line-code');
    }
    if (!isCodeToken && (boxHeight < 6 || boxWidth < 6)) {
        element.classList.add('ocr-text-line-compact');
    }
}

function fittedPPOCRFontSize(text, boxWidth, boxHeight) {
    const availableHeight = Math.max(1, boxHeight - 2);
    const byHeight = availableHeight * 0.92;
    if (isPPOCRCodeToken(text)) {
        return Math.round(Math.max(4.2, Math.min(12, byHeight)) * 10) / 10;
    }
    const minReadable = boxWidth >= 120 ? 9.4 : 6;
    const byNarrowWidth = boxWidth < 18 ? Math.max(5, boxWidth * 0.72) : 14;
    return Math.round(Math.max(minReadable, Math.min(14, byHeight, byNarrowWidth)) * 10) / 10;
}

function isPPOCRCodeToken(text) {
    const value = String(text || '').trim();
    return /^[A-Za-z]{2,8}\d{2,8}[A-Za-z0-9-]*$/.test(value);
}

function fitPPOCRLineElement(element, line) {
    const label = element.querySelector('.ocr-text-line-label');
    if (!label) return;

    label.style.transform = 'none';
    const isCodeToken = isPPOCRCodeToken(line?.text);
    const isWideTextLine = element.clientWidth >= 120;
    const minScale = isCodeToken || isWideTextLine ? 0.48 : 0.62;
    const minHeightScale = isWideTextLine && !isCodeToken ? 0.82 : 1;
    const minFontSize = isCodeToken ? 3.8 : 4.8;

    for (let attempt = 0; attempt < 4; attempt += 1) {
        const availableWidth = Math.max(1, element.clientWidth - 1);
        const availableHeight = Math.max(1, element.clientHeight - 1);
        const naturalWidth = Math.max(1, label.scrollWidth || label.getBoundingClientRect().width);
        const naturalHeight = Math.max(1, label.scrollHeight || label.getBoundingClientRect().height);
        const widthScale = Math.min(1, availableWidth / naturalWidth);
        const heightScale = Math.min(1, availableHeight / naturalHeight);

        if (widthScale >= minScale && heightScale >= minHeightScale) {
            label.style.transform = widthScale < 1 ? `scaleX(${roundPPOCRScale(widthScale)})` : 'none';
            return;
        }

        const currentSize = Number.parseFloat(element.style.fontSize || getComputedStyle(element).fontSize) || 6;
        const targetWidthRatio = widthScale < minScale ? widthScale / minScale : 1;
        const targetHeightRatio = heightScale < minHeightScale ? heightScale / minHeightScale : 1;
        const ratio = Math.min(targetHeightRatio, targetWidthRatio, 1);
        const nextSize = Math.round(Math.max(minFontSize, currentSize * ratio * 0.98) * 10) / 10;
        if (nextSize >= currentSize - 0.05) break;
        element.style.fontSize = `${nextSize}px`;
    }

    const finalAvailableWidth = Math.max(1, element.clientWidth - 1);
    const finalNaturalWidth = Math.max(1, label.scrollWidth || label.getBoundingClientRect().width);
    const finalScale = Math.min(1, finalAvailableWidth / finalNaturalWidth);
    label.style.transform = finalScale < 1 ? `scaleX(${roundPPOCRScale(finalScale)})` : 'none';
}

function roundPPOCRScale(value) {
    return Math.round(Math.max(0.35, Math.min(1, value)) * 1000) / 1000;
}

function bindPPOCRLineEvents(element, toolbar, line) {
    const activate = () => activatePPOCRLine(element, toolbar, line);
    element.addEventListener('mouseenter', activate);
    element.addEventListener('focus', activate);
    element.addEventListener('click', activate);
}

function activatePPOCRLine(element, toolbar, line, { scrollSource = false } = {}) {
    const stage = element.closest('.ocr-page-stage');
    if (!stage) return;
    stage.querySelectorAll('.ocr-text-line.active, .ocr-text-only-line.active').forEach((item) => {
        item.classList.remove('active');
    });
    element.classList.add('active');
    toolbar.dataset.text = line.text;
    toolbar._activePPOCR = { element, line };
    toolbar.classList.remove('hidden');
    showPPOCRSourceHighlight(line);
    if (scrollSource) {
        const page = els.sourceViewer.querySelector(`.pdf-page-wrap[data-page="${line.sourcePage}"]`);
        if (page && !isElementMostlyVisible(page, els.sourceViewer)) {
            scrollPdfPageIntoView(line.sourcePage, 'smooth');
        }
    }

    const stageRect = stage.getBoundingClientRect();
    const elementRect = element.getBoundingClientRect();
    const viewerRect = els.markdownView.getBoundingClientRect();
    const minLeft = viewerRect.left - stageRect.left + 8;
    const maxLeft = viewerRect.right - stageRect.left - 152;
    const minTop = viewerRect.top - stageRect.top + 8;
    const maxTop = viewerRect.bottom - stageRect.top - 44;
    const left = Math.min(
        Math.max(elementRect.left - stageRect.left + elementRect.width - 142, minLeft),
        Math.max(maxLeft, minLeft)
    );
    const top = Math.min(
        Math.max(elementRect.bottom - stageRect.top + 8, minTop),
        Math.max(maxTop, minTop)
    );
    toolbar.style.left = `${left}px`;
    toolbar.style.top = `${top}px`;
}

function schedulePPOCRSourceScrollSync() {
    if (splitScrollSyncLocked || ppocrScrollSyncFrame) return;
    ppocrScrollSyncFrame = requestAnimationFrame(() => {
        ppocrScrollSyncFrame = 0;
        syncSourceScrollFromPPOCRVisual();
    });
}

function handlePPOCRMarkdownScroll() {
    const task = getActiveTask();
    if (isUnlimitedOCRTask(task)) {
        scheduleLayoutSourceScrollSync();
    }
}

function syncSourceScrollFromPPOCRVisual() {
    const task = getActiveTask();
    if (!shouldSyncPPOCRVisualScroll(task)) return;
    if (!currentPdf || !els.sourceViewer || !els.markdownView) return;
    syncPairedPPOCRScroll(els.markdownView, els.sourceViewer);
    updateCurrentPageFromScroll();
}

function syncPPOCRVisualScrollFromSource() {
    const task = getActiveTask();
    if (!shouldSyncPPOCRVisualScroll(task)) return;
    if (!currentPdf || !els.sourceViewer || !els.markdownView) return;
    syncPairedPPOCRScroll(els.sourceViewer, els.markdownView);
}

function shouldSyncPPOCRVisualScroll(_task) {
    return false;
}

function scheduleLayoutSourceScrollSync() {
    if (splitScrollSyncLocked || layoutResultScrollSyncFrame) return;
    layoutResultScrollSyncFrame = requestAnimationFrame(() => {
        layoutResultScrollSyncFrame = 0;
        syncSourceScrollFromLinkedLayout();
    });
}

function scheduleLayoutResultScrollSync() {
    if (splitScrollSyncLocked || layoutSourceScrollSyncFrame) return;
    layoutSourceScrollSyncFrame = requestAnimationFrame(() => {
        layoutSourceScrollSyncFrame = 0;
        syncLinkedLayoutScrollFromSource();
    });
}

function syncSourceScrollFromLinkedLayout() {
    const task = getActiveTask();
    if (!hasLinkedLayoutScrollSync(task)) return;
    const entry = findNearestLinkedEntryInResult();
    if (!entry) return;
    setActiveLinkedLayoutEntry(entry);
    showSourceHighlight(entry.block);
    scrollSourceToLayoutBlock(entry.block, 'auto');
}

function syncLinkedLayoutScrollFromSource() {
    const task = getActiveTask();
    if (!hasLinkedLayoutScrollSync(task)) return;
    const entry = findNearestLinkedEntryInSource();
    if (!entry) return;
    setActiveLinkedLayoutEntry(entry);
    showSourceHighlight(entry.block);
    scrollResultToLinkedEntry(entry, 'auto');
}

function syncPairedPPOCRScroll(fromContainer, toContainer) {
    if (splitScrollSyncLocked || !fromContainer || !toContainer) return;
    const targetTop = directScrollTarget(toContainer, fromContainer.scrollTop || 0, 'top');
    const targetLeft = directScrollTarget(toContainer, fromContainer.scrollLeft || 0, 'left');
    if (
        Math.abs((toContainer.scrollTop || 0) - targetTop) < 1
        && Math.abs((toContainer.scrollLeft || 0) - targetLeft) < 1
    ) {
        return;
    }
    withSplitScrollLock(() => {
        toContainer.scrollTo({
            top: targetTop,
            left: targetLeft,
            behavior: 'auto'
        });
    });
}

function directScrollTarget(container, value, axis) {
    const maxScroll = axis === 'left'
        ? Math.max(0, container.scrollWidth - container.clientWidth)
        : Math.max(0, container.scrollHeight - container.clientHeight);
    return Math.min(Math.max(value, 0), maxScroll);
}

function ensureJsonVirtualDom() {
    let spacer = els.jsonView.querySelector('.json-virtual-spacer');
    let lines = els.jsonView.querySelector('.json-virtual-lines');
    if (spacer && lines) return { spacer, lines };

    els.jsonView.textContent = '';
    spacer = document.createElement('div');
    spacer.className = 'json-virtual-spacer';
    lines = document.createElement('code');
    lines.className = 'json-virtual-lines';
    els.jsonView.append(spacer, lines);
    return { spacer, lines };
}

function renderVisibleJsonLines() {
    if (!cachedJsonLines.length) {
        els.jsonView.textContent = '';
        return;
    }

    const { spacer, lines } = ensureJsonVirtualDom();
    const totalHeight = cachedJsonLines.length * JSON_LINE_HEIGHT + JSON_PADDING_TOP + JSON_PADDING_BOTTOM;
    spacer.style.height = `${totalHeight}px`;
    spacer.style.width = `calc(${Math.max(cachedJsonMaxLineLength, 1)}ch + ${JSON_PADDING_LEFT + JSON_PADDING_RIGHT}px)`;

    const viewportHeight = els.jsonView.clientHeight || 1;
    const firstVisibleLine = Math.max(0, Math.floor((els.jsonView.scrollTop - JSON_PADDING_TOP) / JSON_LINE_HEIGHT));
    const visibleLineCount = Math.ceil(viewportHeight / JSON_LINE_HEIGHT) + JSON_OVERSCAN_LINES * 2;
    const start = Math.max(0, firstVisibleLine - JSON_OVERSCAN_LINES);
    const end = Math.min(cachedJsonLines.length, start + visibleLineCount);

    lines.style.transform = `translateY(${JSON_PADDING_TOP + start * JSON_LINE_HEIGHT}px)`;
    lines.textContent = cachedJsonLines.slice(start, end).join('\n');
}

async function processActiveTask() {
    const task = getActiveTask();
    await processTask(task, { confirmCompleted: true });
}

function openComparisonDialog() {
    const task = getActiveTask();
    if (!task || isProcessing || processingTaskId || comparisonInFlight || modelSwitchInFlight) return;
    els.compareModelList.innerHTML = '';
    availableModels.forEach((model) => {
        const status = getModelRuntimeStatus(model.id);
        const option = document.createElement('label');
        option.className = 'compare-model-option';
        const checked = model.id === task.modelId || model.id === selectedModelId;
        option.innerHTML = `
            <input type="checkbox" name="compare-model" value="${escapeHtml(model.id)}" ${checked ? 'checked' : ''}>
            <span>${escapeHtml(modelDisplayName(model))}</span>
            <small>${escapeHtml(status?.state === 'missing' ? t('未部署') : (status?.ready ? t('就绪') : t('待启动')))}</small>
        `;
        els.compareModelList.appendChild(option);
    });
    if (els.compareModelList.querySelectorAll('input:checked').length < 2) {
        const fallback = els.compareModelList.querySelector('input:not(:checked)');
        if (fallback) fallback.checked = true;
    }
    els.compareDialog?.showModal();
}

async function handleComparisonSubmit(event) {
    event.preventDefault();
    if (event.submitter?.value === 'cancel') {
        els.compareDialog?.close();
        return;
    }
    if (comparisonInFlight || isProcessing || processingTaskId || modelSwitchInFlight) return;
    const modelIds = Array.from(els.compareModelList.querySelectorAll('input:checked')).map((input) => input.value);
    if (modelIds.length < 2) {
        alert(t('请至少选择两个模型。'));
        return;
    }
    const sourceTask = getActiveTask();
    if (!sourceTask) return;
    els.compareDialog?.close();
    await runModelComparison(sourceTask, modelIds);
}

async function cloneTaskSource(sourceTaskId, targetTaskId) {
    const response = await apiFetch(
        `${API_BASE}/tasks/${encodeURIComponent(sourceTaskId)}/clone-source/${encodeURIComponent(targetTaskId)}`,
        { method: 'POST' }
    );
    if (!response.ok) throw new Error(await responseErrorText(response));
    const data = await response.json();
    return data.url;
}

async function copyComparisonSource(sourceTask, targetTaskId) {
    if (sourceTask.sourceUrl) {
        try {
            return await cloneTaskSource(sourceTask.id, targetTaskId);
        } catch (error) {
            if (!sourceTask.sourceDataUrl) throw error;
            console.warn('Stored source clone failed; migrating the legacy inline source.', error);
        }
    }
    if (!sourceTask.sourceDataUrl) {
        throw new Error(t('源文件不可用，请重新上传后再对比。'));
    }
    const blob = dataUrlToBlob(sourceTask.sourceDataUrl);
    return uploadTaskSource(
        targetTaskId,
        blob,
        sourceTask.originalName || sourceTask.name || 'source.bin',
        sourceTask.mimeType || blob.type || 'application/octet-stream'
    );
}

function comparisonTaskName(sourceTask, model) {
    const sourceName = String(sourceTask?.name || sourceTask?.originalName || t('未命名任务'));
    return `${sourceName.replace(/\s·\s.+$/, '')} · ${modelShortName(model)}`;
}

function comparisonTaskBase(sourceTask, model, groupId, id, comparisonStartedAt) {
    const pageCount = Number(sourceTask.pageCount || 1);
    const pdfBatchSize = Number(sourceTask.pdfBatchSize || getConfiguredPdfBatchSize());
    const now = Date.now();
    return {
        id,
        name: comparisonTaskName(sourceTask, model),
        sourceKind: sourceTask.sourceKind,
        originalName: sourceTask.originalName || sourceTask.name,
        mimeType: sourceTask.mimeType,
        size: sourceTask.size,
        createdAt: now,
        updatedAt: now,
        status: 'pending',
        pageCount,
        pdfBatchSize,
        thumbnail: sourceTask.thumbnail,
        modelId: model.id,
        modelName: modelDisplayName(model),
        modelEndpoint: model.endpoint,
        comparisonGroupId: groupId,
        comparisonStartedAt,
        comparisonSourceName: sourceTask.originalName || sourceTask.name
    };
}

async function createComparisonTask(sourceTask, model, groupId, comparisonStartedAt = Date.now()) {
    const id = createId();
    const sourceUrl = await copyComparisonSource(sourceTask, id);
    const task = comparisonTaskBase(sourceTask, model, groupId, id, comparisonStartedAt);
    const batches = task.sourceKind === 'image'
        ? [{ id: createId(), label: formatPageLabel(1), fileType: 1, pageCount: 1, status: 'pending' }]
        : createPdfBatchDescriptors(task.pageCount, task.pdfBatchSize);
    return {
        ...task,
        sourceUrl,
        batches,
        markdown: '',
        images: {},
        ocrResults: []
    };
}

function comparisonFailureDetail(error) {
    if (error instanceof Error && error.message) return error.message;
    return String(error || t('未知错误'));
}

function markComparisonTaskFailed(task, model, error, startedAt = Date.now()) {
    const completedAt = Date.now();
    task.status = 'error';
    task.error = task.error || comparisonFailureDetail(error);
    task.updatedAt = completedAt;
    task.benchmark = {
        ...createBenchmarkSnapshot(task, model, startedAt),
        ...(task.benchmark || {}),
        completedAt: task.benchmark?.completedAt || completedAt,
        durationMs: Number.isFinite(Number(task.benchmark?.durationMs))
            ? Number(task.benchmark.durationMs)
            : Math.max(0, completedAt - startedAt)
    };
    return task;
}

function createComparisonFailureTask(sourceTask, model, groupId, error, comparisonStartedAt, startedAt) {
    const task = comparisonTaskBase(sourceTask, model, groupId, createId(), comparisonStartedAt);
    task.sourceUrl = sourceTask.sourceUrl || '';
    if (!task.sourceUrl && sourceTask.sourceDataUrl) task.sourceDataUrl = sourceTask.sourceDataUrl;
    task.batches = [];
    task.markdown = '';
    task.images = {};
    task.ocrResults = [];
    return markComparisonTaskFailed(task, model, error, startedAt);
}

async function saveComparisonFailure(task) {
    try {
        await saveTask(task);
    } catch (error) {
        console.error('Failed to persist comparison failure record', error);
    }
}

async function runModelComparison(sourceTask, modelIds) {
    if (!sourceTask || isProcessing || processingTaskId || comparisonInFlight || modelSwitchInFlight) return [];
    const models = Array.from(new Set(Array.isArray(modelIds) ? modelIds : []))
        .map((id) => availableModels.find((model) => model.id === id))
        .filter(Boolean);
    if (models.length < 2) return [];
    const groupId = `compare-${createId()}`;
    activeComparisonGroupId = groupId;
    comparisonInFlight = true;
    updateActionState(sourceTask);
    const comparisonStartedAt = Date.now();
    const comparisonTasks = [];
    try {
        for (const model of models) {
            const attemptStartedAt = Date.now();
            let task = null;
            try {
                task = await createComparisonTask(sourceTask, model, groupId, comparisonStartedAt);
                comparisonTasks.push(task);
                tasks.unshift(task);
                await saveTask(task);
                activeTaskId = task.id;
                renderTaskList();
                await selectTask(task.id);
                const completed = await processTask(task, { confirmCompleted: false });
                if (!completed) {
                    markComparisonTaskFailed(
                        task,
                        model,
                        task.error || t('模型未就绪或启动已取消。'),
                        attemptStartedAt
                    );
                    await saveComparisonFailure(task);
                    refreshTaskUi(task);
                }
            } catch (error) {
                console.error(error);
                if (!task) {
                    task = createComparisonFailureTask(
                        sourceTask,
                        model,
                        groupId,
                        error,
                        comparisonStartedAt,
                        attemptStartedAt
                    );
                    comparisonTasks.push(task);
                    tasks.unshift(task);
                } else {
                    markComparisonTaskFailed(task, model, error, attemptStartedAt);
                }
                await saveComparisonFailure(task);
                renderTaskList();
            }
        }
    } finally {
        const target = comparisonTasks[comparisonTasks.length - 1];
        try {
            if (target) {
                try {
                    await selectTask(target.id);
                } catch (error) {
                    console.error('Failed to render the final comparison task', error);
                    activeTaskId = target.id;
                }
                activeResultView = 'comparison';
                document.querySelectorAll('.view-tab').forEach((tab) => {
                    tab.classList.toggle('active', tab.dataset.view === 'comparison');
                });
                renderResultPane(target, { preserveScroll: false });
            }
        } finally {
            comparisonInFlight = false;
            if (activeComparisonGroupId === groupId) activeComparisonGroupId = null;
            updateActionState(target || getActiveTask());
        }
    }
    return comparisonTasks;
}

function comparisonTasksFor(task) {
    if (!task?.comparisonGroupId) return [];
    return tasks
        .filter((item) => item.comparisonGroupId === task.comparisonGroupId)
        .sort((left, right) => (left.createdAt || 0) - (right.createdAt || 0));
}

function formatDuration(durationMs) {
    if (!Number.isFinite(Number(durationMs)) || Number(durationMs) < 0) return '—';
    const totalSeconds = Math.max(0, Math.round(Number(durationMs) / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function markdownTableCell(value) {
    return String(value ?? '').replace(/\r?\n/g, ' ').replace(/\|/g, '\\|');
}

function reportTimestamp(value) {
    const timestamp = Number(value);
    return Number.isFinite(timestamp) && timestamp > 0 ? new Date(timestamp).toISOString() : null;
}

function comparisonReproductionMetadata(groupTasks, task) {
    return {
        schemaVersion: 1,
        comparisonGroupId: task?.comparisonGroupId || null,
        comparisonStartedAt: reportTimestamp(task?.comparisonStartedAt || groupTasks[0]?.comparisonStartedAt),
        source: {
            name: task?.comparisonSourceName || task?.originalName || task?.name || null,
            kind: groupTasks[0]?.sourceKind || task?.sourceKind || null,
            mimeType: groupTasks[0]?.mimeType || task?.mimeType || null,
            sizeBytes: Number(groupTasks[0]?.size || task?.size || 0) || null,
            pageCount: Number(groupTasks[0]?.pageCount || task?.pageCount || 0) || null
        },
        application: {
            version: groupTasks.find((item) => item.benchmark?.projectVersion)?.benchmark.projectVersion
                || appMetadata.version
                || DEFAULT_APP_VERSION,
            commit: groupTasks.find((item) => item.benchmark?.projectCommit)?.benchmark.projectCommit
                || appMetadata.commit
                || null
        },
        models: groupTasks.map((item) => ({
            modelId: item.modelId || null,
            modelName: item.modelName || null,
            modelEndpoint: item.modelEndpoint || null,
            modelBackend: item.benchmark?.modelBackend || null,
            status: item.status || 'pending',
            error: item.error || null,
            startedAt: reportTimestamp(item.benchmark?.startedAt),
            completedAt: reportTimestamp(item.benchmark?.completedAt),
            durationMs: Number.isFinite(Number(item.benchmark?.durationMs))
                ? Number(item.benchmark.durationMs)
                : null,
            pdfBatchSize: Number(item.benchmark?.pdfBatchSize || item.pdfBatchSize || 0) || null,
            parsingSettings: item.benchmark?.parsingSettings || null,
            gpu: {
                name: item.benchmark?.gpuName || null,
                totalMiB: item.benchmark?.gpuTotalMiB || null,
                freeMiBBefore: item.benchmark?.gpuFreeMiBBefore || null
            },
            manualEditedAt: reportTimestamp(item.manualEditedAt)
        }))
    };
}

function comparisonReportMarkdown(task) {
    const groupTasks = comparisonTasksFor(task);
    const sourceName = task?.comparisonSourceName || task?.originalName || task?.name || '';
    const metadata = comparisonReproductionMetadata(groupTasks, task);
    const lines = [
        `# ${t('多模型对比报告')}`,
        '',
        `- ${t('源文件')}: ${sourceName}`,
        `- ${t('生成时间')}: ${new Date().toISOString()}`,
        `- ${t('对比任务 ID')}: ${task?.comparisonGroupId || '—'}`,
        `- ${t('项目版本')}: ${metadata.application.version}`,
        `- ${t('代码提交')}: ${metadata.application.commit || '—'}`,
        '',
        `| ${t('模型')} | ${t('模型 ID')} | ${t('状态')} | ${t('页数')} | ${t('解析耗时')} | ${t('输出字符')} | ${t('人工编辑')} |`,
        '| --- | --- | --- | ---: | ---: | ---: | --- |'
    ];
    groupTasks.forEach((item) => {
        lines.push(`| ${markdownTableCell(item.modelName || item.modelId)} | ${markdownTableCell(item.modelId)} | ${markdownTableCell(statusText(item))} | ${item.pageCount || 1} | ${formatDuration(item.benchmark?.durationMs)} | ${visibleTaskMarkdown(item).length} | ${item.manualEditedAt ? t('是') : t('否')} |`);
    });
    groupTasks.forEach((item) => {
        lines.push(
            '',
            `## ${item.modelName || item.modelId}`,
            '',
            `- ${t('模型 ID')}: ${item.modelId || '—'}`,
            `- ${t('模型端点')}: ${item.modelEndpoint || '—'}`,
            `- ${t('模型后端')}: ${item.benchmark?.modelBackend || '—'}`,
            `- ${t('人工编辑')}: ${item.manualEditedAt ? new Date(item.manualEditedAt).toISOString() : t('否')}`,
            '',
            visibleTaskMarkdown(item) || `> ${item.error || t('没有可用结果')}`
        );
    });
    lines.push('', `## ${t('可复现元数据')}`, '', '```json', JSON.stringify(metadata, null, 2), '```');
    return lines.join('\n');
}

function renderComparisonResult(task) {
    if (!els.comparisonView) return;
    const groupTasks = comparisonTasksFor(task);
    if (groupTasks.length < 2) {
        els.comparisonView.innerHTML = `<div class="empty-result">${escapeHtml(t('当前任务没有多模型对比结果。'))}</div>`;
        return;
    }
    const unloadedTasks = groupTasks.filter((item) => !isTaskDetailLoaded(item));
    if (unloadedTasks.length) {
        els.comparisonView.innerHTML = `<div class="empty-result">${escapeHtml(t('正在加载对比结果...'))}</div>`;
        loadComparisonGroupDetails(task.comparisonGroupId, unloadedTasks);
        return;
    }
    const rows = groupTasks.map((item) => `
        <tr>
            <td>${escapeHtml(item.modelName || item.modelId)}</td>
            <td class="comparison-status-${escapeHtml(item.status || 'pending')}">${escapeHtml(statusText(item))}</td>
            <td>${item.pageCount || 1}</td>
            <td>${escapeHtml(formatDuration(item.benchmark?.durationMs))}</td>
            <td>${visibleTaskMarkdown(item).length.toLocaleString(languageLocale())}</td>
        </tr>
    `).join('');
    const cards = groupTasks.map((item) => `
        <article class="comparison-card">
            <div class="comparison-card-header">
                <strong>${escapeHtml(item.modelName || item.modelId)}</strong>
                <span>${escapeHtml(statusText(item))}</span>
            </div>
            <pre>${escapeHtml(visibleTaskMarkdown(item) || item.error || t('没有可用结果'))}</pre>
        </article>
    `).join('');
    els.comparisonView.innerHTML = `
        <table class="comparison-summary">
            <thead><tr><th>${escapeHtml(t('模型'))}</th><th>${escapeHtml(t('状态'))}</th><th>${escapeHtml(t('页数'))}</th><th>${escapeHtml(t('解析耗时'))}</th><th>${escapeHtml(t('输出字符'))}</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
        <div class="comparison-grid">${cards}</div>
    `;
}

async function loadComparisonGroupDetails(groupId, groupTasks) {
    if (!groupId || comparisonGroupsLoading.has(groupId)) return;
    comparisonGroupsLoading.add(groupId);
    try {
        const results = await Promise.all(groupTasks.map((item) => loadTaskFromServer(item.id)));
        results.forEach(replaceTask);
        const activeTask = getActiveTask();
        if (activeTask?.comparisonGroupId === groupId && activeResultView === 'comparison') {
            renderComparisonResult(activeTask);
            updateActionState(activeTask);
        }
    } catch (error) {
        console.error(error);
        if (els.comparisonView) {
            els.comparisonView.innerHTML = `<div class="empty-result">${escapeHtml(t('加载对比结果失败：{detail}', { detail: error.message || t('未知错误') }))}</div>`;
        }
    } finally {
        comparisonGroupsLoading.delete(groupId);
    }
}

function startMarkdownEdit() {
    const task = getActiveTask();
    if (!task || isProcessing || comparisonInFlight || !visibleTaskMarkdown(task) || activeResultView !== 'markdown') return;
    const editor = document.createElement('textarea');
    editor.className = 'markdown-editor';
    editor.value = normalizeOCRMarkdown(visibleTaskMarkdown(task));
    const shell = document.createElement('div');
    shell.className = 'markdown-editor-shell';
    const actions = document.createElement('div');
    actions.className = 'markdown-editor-actions';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'secondary-button';
    cancel.textContent = t('取消');
    const save = document.createElement('button');
    save.type = 'button';
    save.className = 'primary-button';
    save.textContent = t('保存');
    cancel.addEventListener('click', () => {
        resetResultRenderCache(task.id);
        renderResultPane(task, { preserveScroll: false });
    });
    save.addEventListener('click', async () => {
        task.markdown = editor.value;
        task.manualEditedAt = Date.now();
        task.updatedAt = Date.now();
        await saveTask(task);
        resetResultRenderCache(task.id);
        renderResultPane(task, { preserveScroll: false });
        updateActionState(task);
    });
    actions.append(cancel, save);
    shell.append(editor, actions);
    els.markdownView.replaceChildren(shell);
    editor.focus();
}

function currentParsingSettingsSnapshot() {
    const markdownIgnoreLabels = [];
    if (els.ignoreNumberSwitch?.checked) markdownIgnoreLabels.push('number');
    markdownIgnoreLabels.push('footnote');
    if (els.ignoreHeaderSwitch?.checked) markdownIgnoreLabels.push('header', 'header_image');
    if (els.ignoreFooterSwitch?.checked) markdownIgnoreLabels.push('footer', 'footer_image');
    markdownIgnoreLabels.push('aside_text');
    return {
        useLayoutDetection: true,
        useChartRecognition: Boolean(els.chartRecognitionSwitch?.checked),
        useDocUnwarping: Boolean(els.docUnwarpingSwitch?.checked),
        useDocOrientationClassify: Boolean(els.docOrientationSwitch?.checked),
        useSealRecognition: Boolean(els.sealRecognitionSwitch?.checked),
        formatBlockContent: true,
        showFormulaNumber: Boolean(els.formulaNumberSwitch?.checked),
        markdownIgnoreLabels
    };
}

function createBenchmarkSnapshot(task, model, startedAt = Date.now()) {
    const gpu = modelRuntime?.gpuPreflight?.gpus?.[0] || {};
    const runtime = modelRuntime?.models?.[model?.id] || {};
    return {
        startedAt,
        projectVersion: appMetadata.version || DEFAULT_APP_VERSION,
        projectCommit: appMetadata.commit || null,
        modelId: model?.id || task?.modelId || null,
        modelName: model?.name || model?.label || task?.modelName || null,
        modelEndpoint: model?.endpoint || task?.modelEndpoint || null,
        modelBackend: model?.id === UNLIMITED_OCR_MODEL_ID
            ? (runtime.backend || selectedUnlimitedOcrBackend)
            : (runtime.backend || null),
        runtimeStateBefore: runtime.state || null,
        pdfBatchSize: Number(task?.pdfBatchSize || getConfiguredPdfBatchSize()),
        parsingSettings: currentParsingSettingsSnapshot(),
        source: {
            name: task?.comparisonSourceName || task?.originalName || task?.name || null,
            kind: task?.sourceKind || null,
            mimeType: task?.mimeType || null,
            sizeBytes: Number(task?.size || 0) || null,
            pageCount: Number(task?.pageCount || 0) || null
        },
        gpuName: gpu.name || null,
        gpuTotalMiB: Number(gpu.totalMiB || 0) || null,
        gpuFreeMiBBefore: Number(gpu.freeMiB || 0) || null
    };
}

async function processTask(task, { confirmCompleted = true } = {}) {
    const comparisonOwnedTask = Boolean(
        comparisonInFlight
        && activeComparisonGroupId
        && task?.comparisonGroupId === activeComparisonGroupId
    );
    if (!task || isProcessing || processingTaskId || (comparisonInFlight && !comparisonOwnedTask)) return false;
    if (confirmCompleted && task.status === 'completed' && !confirm(t('这个任务已经解析完成，要重新解析吗？'))) return;

    const resumeExistingResults = shouldResumeTask(task);
    const targetModel = resumeExistingResults
        ? getTaskModel(task)
        : ((confirmCompleted || !task.modelId) ? getSelectedModel() : getTaskModel(task));
    processingTaskId = task.id;
    updateActionState(getActiveTask());
    try {
        const modelReady = await ensureModelRuntimeReadyForTask(task, targetModel);
        if (!modelReady) return;

        isProcessing = true;
        const benchmarkStartedAt = Date.now();
        task.benchmark = {
            ...(task.benchmark || {}),
            ...createBenchmarkSnapshot(task, targetModel, benchmarkStartedAt)
        };
        try {
            if (shouldRebuildPdfBatchPlan(task)) {
                rebuildPdfBatchPlan(task);
            }
            if (resumeExistingResults) {
                task.batches.forEach((batch) => {
                    if (batch.status === 'processing' || batch.status === 'error') batch.status = 'pending';
                });
                rebuildTaskResultFromCompletedBatches(task);
            } else {
                if (confirmCompleted || !task.modelId) {
                    applySelectedModelToTask(task);
                }
                delete task.manualEditedAt;
                task.markdown = '';
                task.images = {};
                task.ocrResults = [];
                task.batches.forEach((batch) => {
                    batch.status = 'pending';
                    batch.markdown = '';
                });
            }
            task.status = 'processing';
            task.error = null;
            task.updatedAt = Date.now();
            await saveTask(task);
            refreshTaskUi(task);

            for (const batch of task.batches) {
                if (batch.status === 'completed') continue;
                batch.status = 'processing';
                task.updatedAt = Date.now();
                await saveTask(task, { includeResults: false });
                refreshTaskUi(task);

                let result;
                const showOvisProgress = isOvisOCR2Task(task);
                if (showOvisProgress) batch._progressStartedAt = Date.now();
                const progressTimer = showOvisProgress
                    ? window.setInterval(() => {
                        if (batch.status === 'processing') refreshTaskUi(task);
                    }, 1000)
                    : null;
                try {
                    await ensureBatchPayload(task, batch);
                    result = await callOCR(batch, task);
                } finally {
                    if (progressTimer) window.clearInterval(progressTimer);
                    delete batch._progressStartedAt;
                    releaseBatchPayload(batch);
                }
                const prepared = prepareBatchResult(result, batch.id);
                batch.status = 'completed';
                batch.markdown = prepared.markdown;
                rebuildTaskMarkdownFromBatches(task);
                Object.assign(task.images, prepared.images);
                task.ocrResults.push(...normalizeOCRJsonResults(result).map((pageResult, pageIndex) => (
                    compactOCRJsonResult(pageResult, batch, pageIndex)
                )));
                task.updatedAt = Date.now();
                await saveTask(task);
                refreshTaskUi(task);
            }
            task.status = 'completed';
        } catch (error) {
            console.error(error);
            task.status = 'error';
            task.error = error.message;
            const failedBatch = task.batches?.find((batch) => batch.status === 'processing');
            if (failedBatch) {
                failedBatch.status = 'error';
                failedBatch.error = error.message;
            }
        } finally {
            isProcessing = false;
            task.benchmark.completedAt = Date.now();
            task.benchmark.durationMs = task.benchmark.completedAt - benchmarkStartedAt;
            task.updatedAt = Date.now();
            await saveTask(task, { includeResults: false });
            refreshTaskUi(task);
        }
        return task.status === 'completed';
    } finally {
        if (processingTaskId === task.id) {
            isProcessing = false;
            processingTaskId = null;
        }
        updateActionState(getActiveTask());
    }
}

function shouldResumeTask(task) {
    if (isTaskActivelyProcessing(task)) return false;
    const canResumeStatus = task?.status === 'pending' || task?.status === 'error';
    if (!canResumeStatus) return false;

    if (Array.isArray(task?.batches)) {
        const completedBatchCount = task.batches.filter((batch) => batch.status === 'completed').length;
        const hasPendingBatch = task.batches.some((batch) => batch.status !== 'completed');
        return completedBatchCount > 0 && hasPendingBatch;
    }

    const completedPages = Number(task?.completedPages || 0);
    const pageCount = Number(task?.pageCount || 0);
    return completedPages > 0 && (!pageCount || completedPages < pageCount);
}

function isTaskActivelyProcessing(task) {
    return task?.status === 'processing'
        || Boolean(task?.batches?.some((batch) => batch.status === 'processing'));
}

function historyMutationsLocked() {
    return comparisonInFlight
        || isProcessing
        || Boolean(processingTaskId)
        || modelSwitchInFlight
        || unlimitedOcrBackendSwitchInFlight
        || Number(modelRuntime?.ocrActiveCount || 0) > 0
        || isModelRuntimeSwitching();
}

function shouldRebuildPdfBatchPlan(task) {
    if (!task || !(task.sourceDataUrl || task.sourceUrl) || !['pdf', 'office'].includes(task.sourceKind)) return false;
    const pageCount = Number(task.pageCount || 0);
    if (pageCount <= 0) return false;
    const batches = Array.isArray(task.batches) ? task.batches : [];
    const completedCount = batches.filter((batch) => batch.status === 'completed').length;
    if (completedCount > 0) return false;
    const configuredBatchSize = getConfiguredPdfBatchSize();
    if (batches.length === 0) return true;
    if (Number(task.pdfBatchSize || 0) !== configuredBatchSize) return true;
    return Number(task.pdfBatchSize || 0) > MAX_PDF_BATCH_SIZE
        || batches.some((batch) => Number(batch.pageCount || 0) > MAX_PDF_BATCH_SIZE);
}

function rebuildPdfBatchPlan(task) {
    const pageCount = Number(task.pageCount || 1);
    const batchSize = getConfiguredPdfBatchSize();
    task.pdfBatchSize = batchSize;
    task.batches = createPdfBatchDescriptors(pageCount, batchSize, task.sourceDataUrl);
    task.markdown = '';
    task.images = {};
    task.ocrResults = [];
}

function taskVisualStatus(task) {
    if (isTaskActivelyProcessing(task)) return 'processing';
    return shouldResumeTask(task) ? 'pending' : (task?.status || 'pending');
}

function rebuildTaskResultFromCompletedBatches(task) {
    const completedBatches = task.batches.filter((batch) => batch.status === 'completed');
    if (completedBatches.length === 0) return;

    const existingMarkdown = task.markdown || '';
    const hasBatchMarkdown = completedBatches.some((batch) => batch.markdown);
    if (!existingMarkdown && hasBatchMarkdown) {
        task.markdown = joinTaskBatchMarkdown(task, completedBatches.map((batch) => batch.markdown || ''));
    }

    if (!task.images || typeof task.images !== 'object') {
        task.images = {};
    }
    if (!Array.isArray(task.ocrResults)) {
        task.ocrResults = [];
    }
}

function appendTaskMarkdown(task, markdown) {
    const text = String(markdown || '');
    if (!text) return;
    if (task.markdown && !task.markdown.endsWith('\n\n')) {
        task.markdown += '\n\n';
    }
    task.markdown += `${text}\n\n`;
}

function isStreamStatusMarkdown(markdown) {
    return STREAM_STATUS_MARKDOWN_RE.test(String(markdown || ''));
}

function stripStreamStatusMarkdown(markdown) {
    return String(markdown || '')
        .replace(/(?:^|\n{2,})\s*\*\*Unlimited-OCR status\*\*\s*\n+\s*[^\n]*(?=\n{2,}|$)/gi, '\n\n')
        .trim();
}

function visibleTaskMarkdown(task) {
    return stripStreamStatusMarkdown(task?.markdown || '');
}

function shouldJoinHPDPageContinuation(left, right) {
    const previous = String(left || '').trimEnd();
    const next = String(right || '').trimStart();
    if (!previous || !next || !/^[a-z]/.test(next)) return false;
    if (/[.!?;:]$/.test(previous)) return false;
    if (/(?:^|\n)!\[[^\]]*\]\([^)]+\)\s*$/.test(previous)) return false;
    return /[A-Za-z0-9,)\]%'”’]$/.test(previous);
}

function joinTaskBatchMarkdown(task, markdownParts) {
    const parts = markdownParts
        .map((markdown) => String(markdown || '').trim())
        .filter((markdown) => markdown && !isStreamStatusMarkdown(markdown));
    if (!isHPDParsingTask(task)) return parts.join('\n\n');
    return parts.reduce((combined, part) => {
        if (!combined) return part;
        return shouldJoinHPDPageContinuation(combined, part)
            ? `${combined} ${part}`
            : `${combined}\n\n${part}`;
    }, '');
}

function rebuildTaskMarkdownFromBatches(task) {
    task.markdown = joinTaskBatchMarkdown(task, (task.batches || []).map((batch) => batch.markdown || ''));
    if (task.markdown) task.markdown += '\n\n';
}

function refreshTaskUi(task, { autoFollow = false, streamPosition = null } = {}) {
    renderTaskList();
    const activeTask = getActiveTask();
    if (task?.id === activeTaskId) {
        const shouldAutoFollow = autoFollow && isUnlimitedOCRTask(task);
        updateActiveModelDisplay(task);
        renderResultPane(task, { preserveScroll: !shouldAutoFollow });
        if (shouldAutoFollow) followStreamingResult(streamPosition);
    }
    updateActionState(activeTask);
}

function followStreamingResult(streamPosition = null) {
    requestAnimationFrame(() => {
        const resultMax = Math.max(0, els.markdownView.scrollHeight - els.markdownView.clientHeight);
        els.markdownView.scrollTop = resultMax;
        if (streamPosition) {
            showStreamingSourceHighlight(streamPosition);
            scrollSourceToStreamingPosition(streamPosition);
        }
    });
}

function scrollSourceToStreamingPosition(position) {
    if (!position || !els.sourceViewer) return;
    const pageNumber = Math.max(1, Number(position.pageNumber || 1));
    const surface = sourcePageSurface(pageNumber);
    const page = surface?.container;
    if (!page || !surface?.element) return;

    const pageProgress = Math.min(Math.max(Number(position.pageProgress || 0), 0), 1);
    const sourceHeight = surface.element.clientHeight || surface.element.height || surface.element.naturalHeight || page.offsetHeight;
    const targetTop = sourcePageTop(page) + (pageProgress * sourceHeight) - (els.sourceViewer.clientHeight * 0.42);
    withSplitScrollLock(() => {
        els.sourceViewer.scrollTo({
            top: Math.max(targetTop, 0),
            behavior: 'auto'
        });
    });
    currentPage = pageNumber;
    updatePdfControls();
}

async function callOCR(batch, task) {
    const model = getTaskModel(task);
    const parsingSettings = {
        ...currentParsingSettingsSnapshot(),
        ...(task?.benchmark?.parsingSettings || {})
    };
    const ignoreLabels = Array.isArray(parsingSettings.markdownIgnoreLabels)
        ? [...parsingSettings.markdownIgnoreLabels]
        : currentParsingSettingsSnapshot().markdownIgnoreLabels;

    const formData = new FormData();
    const filename = batch.fileType === 0 ? `${batch.id}.pdf` : `${batch.id}.image`;
    if (batch.payloadBlob) {
        formData.append('file', batch.payloadBlob, filename);
    } else if (batch.payloadDataUrl) {
        formData.append('file', dataUrlToBlob(batch.payloadDataUrl), filename);
    } else {
        throw new Error(t('无法重建当前批次的解析 payload'));
    }
    formData.append('fileType', String(batch.fileType));
    formData.append('useLayoutDetection', String(parsingSettings.useLayoutDetection !== false));
    formData.append('useChartRecognition', String(Boolean(parsingSettings.useChartRecognition)));
    formData.append('useDocUnwarping', String(Boolean(parsingSettings.useDocUnwarping)));
    formData.append('useDocOrientationClassify', String(Boolean(parsingSettings.useDocOrientationClassify)));
    formData.append('useSealRecognition', String(Boolean(parsingSettings.useSealRecognition)));
    formData.append('formatBlockContent', String(parsingSettings.formatBlockContent !== false));
    formData.append('showFormulaNumber', String(Boolean(parsingSettings.showFormulaNumber)));
    formData.append('markdownIgnoreLabels', JSON.stringify(ignoreLabels));
    formData.append('modelId', model.id);

    if (model.id === 'unlimited-ocr') {
        formData.append('backend', selectedUnlimitedOcrBackend);
        return callStreamingUnlimitedOCR(batch, task, formData, model);
    }

    const response = await apiFetch(modelApiUrl(model), {
        method: 'POST',
        body: formData
    });
    if (!response.ok) {
        throw new Error(await responseErrorText(response));
    }
    const text = await response.text();
    if (!text.trim()) {
        throw new Error(t('OCR 服务返回了空响应，请降低每批页数后重试：{label}', { label: batch.label || '' }));
    }
    try {
        return JSON.parse(text);
    } catch (error) {
        const preview = text.slice(0, 500);
        throw new Error(
            t('OCR 服务返回的 JSON 不完整或格式异常，请降低每批页数后重试：{label}。响应长度 {length} 字符，片段：{preview}', {
                label: batch.label || '',
                length: text.length,
                preview
            })
        );
    }
}

async function callStreamingUnlimitedOCR(batch, task, formData, model) {
    const streamUrl = `${modelApiUrl(model).replace(/\/$/, '')}/stream`;
    const response = await apiFetch(streamUrl, {
        method: 'POST',
        body: formData
    });
    if (!response.ok) {
        throw new Error(await responseErrorText(response));
    }
    if (!response.body) {
        return callOCRWithoutStreaming(formData, model);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalResult = null;
    let lastMarkdown = '';
    let lastMarkdownWasPlaceholder = false;
    let pendingStreamPosition = null;
    let streamRenderTimer = null;
    let lastStreamRenderAt = 0;

    const flushStreamRender = () => {
        if (streamRenderTimer) {
            window.clearTimeout(streamRenderTimer);
            streamRenderTimer = null;
        }
        if (!batch.markdown && !pendingStreamPosition) return;
        rebuildTaskMarkdownFromBatches(task);
        task.updatedAt = Date.now();
        refreshTaskUi(task, {
            autoFollow: true,
            streamPosition: pendingStreamPosition
        });
        pendingStreamPosition = null;
        lastStreamRenderAt = Date.now();
    };

    const scheduleStreamRender = (streamPosition) => {
        if (streamPosition) pendingStreamPosition = streamPosition;
        if (streamRenderTimer) return;
        const elapsed = Date.now() - lastStreamRenderAt;
        const delay = Math.max(0, STREAM_RENDER_MIN_INTERVAL_MS - elapsed);
        streamRenderTimer = window.setTimeout(flushStreamRender, delay);
    };

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
            const event = parseStreamingOCREvent(line);
            if (!event) continue;
            if (event.type === 'status') {
                batch._streamStatus = event.message || '';
                continue;
            }
            if (event.type === 'error') {
                throw new Error(event.detail || 'Unlimited-OCR stream failed');
            }
            if (event.type === 'progress' && typeof event.markdown === 'string') {
                const isPlaceholder = event.placeholder === true;
                if (isPlaceholder) {
                    batch._streamStatus = event.markdown;
                    continue;
                }
                const prepared = prepareBatchResult(
                    { markdown: event.markdown, images: event.images || {} },
                    batch.id,
                    batch._streamImagePathMap || {}
                );
                batch._streamImagePathMap = prepared.imagePathMap;
                if (!task.images || typeof task.images !== 'object') task.images = {};
                const hasNewImages = Object.entries(prepared.images).some(([path, base64]) => task.images[path] !== base64);
                if (hasNewImages) {
                    Object.assign(task.images, prepared.images);
                }
                if (prepared.markdown && (prepared.markdown !== lastMarkdown || hasNewImages)) {
                    lastMarkdown = prepared.markdown;
                    lastMarkdownWasPlaceholder = isPlaceholder;
                    batch.markdown = prepared.markdown;
                    scheduleStreamRender(streamingSourcePosition(event, batch));
                }
            }
            if (event.type === 'final' && event.result) {
                finalResult = event.result;
            }
        }
    }

    const tail = buffer.trim();
    if (tail) {
        const event = parseStreamingOCREvent(tail);
        if (event?.type === 'status') {
            batch._streamStatus = event.message || '';
        }
        if (event?.type === 'error') {
            throw new Error(event.detail || 'Unlimited-OCR stream failed');
        }
        if (event?.type === 'final' && event.result) {
            finalResult = event.result;
        }
    }

    flushStreamRender();

    if (!finalResult) {
        if (lastMarkdown && !lastMarkdownWasPlaceholder) {
            return { markdown: lastMarkdown, images: {}, layoutParsingResults: [] };
        }
        throw new Error(t('OCR 服务返回了空响应，请降低每批页数后重试：{label}', { label: batch.label || '' }));
    }
    return finalResult;
}

function streamingSourcePosition(event, batch) {
    const source = event?.source || {};
    const relativePageIndex = Number.isFinite(Number(source.pageIndex)) ? Number(source.pageIndex) : 0;
    const startPage = Math.max(1, Number(batch?.startPage || 1));
    const fallbackEndPage = startPage + Math.max(0, Number(batch?.pageCount || 1) - 1);
    const endPage = Math.max(startPage, Number(batch?.endPage || fallbackEndPage));
    const pageNumber = Math.min(endPage, Math.max(startPage, startPage + Math.max(0, relativePageIndex)));
    const bbox = Array.isArray(source.bbox) ? source.bbox.map(Number).slice(0, 4) : null;
    if (!bbox?.length || bbox.length < 4 || !bbox.every(Number.isFinite)) return null;

    const position = {
        pageNumber,
        pageProgress: Math.min(Math.max(Number(source.pageProgress || 0), 0), 1)
    };
    const bounds = unlimitedOCRSourceBoundsForBox(bbox, source.pageWidth, source.pageHeight);
    position.bbox = bbox;
    position.pageWidth = bounds.pageWidth;
    position.pageHeight = bounds.pageHeight;
    if (source.label) position.label = String(source.label);
    return position;
}

async function callOCRWithoutStreaming(formData, model) {
    const response = await apiFetch(modelApiUrl(model), {
        method: 'POST',
        body: formData
    });
    if (!response.ok) {
        throw new Error(await responseErrorText(response));
    }
    return response.json();
}

function parseStreamingOCREvent(line) {
    const text = String(line || '').trim();
    if (!text) return null;
    try {
        return JSON.parse(text);
    } catch (error) {
        console.warn('Ignoring malformed OCR stream event', text);
        return null;
    }
}

function createPdfBatchDescriptors(pageCount, pdfBatchSize, sourceDataUrl = '') {
    const batches = [];
    for (let startPage = 1; startPage <= pageCount; startPage += pdfBatchSize) {
        const endPage = Math.min(startPage + pdfBatchSize - 1, pageCount);
        const batch = {
            id: createId(),
            label: formatPageLabel(startPage, endPage),
            fileType: 0,
            startPage,
            endPage,
            pageCount: endPage - startPage + 1,
            status: 'pending'
        };
        if (pageCount === 1 && sourceDataUrl) {
            batch.payloadDataUrl = sourceDataUrl;
        }
        batches.push(batch);
    }
    return batches;
}

function taskForPersistence(task, { includeResults = true } = {}) {
    const persisted = { ...task };
    delete persisted.detailLoaded;
    delete persisted._storage;
    delete persisted._resultState;
    if (includeResults && persisted.markdown) {
        persisted.markdown = stripStreamStatusMarkdown(persisted.markdown);
    }
    if (persisted.sourceUrl) {
        delete persisted.sourceDataUrl;
    }
    if (!includeResults) {
        delete persisted.markdown;
        delete persisted.images;
        delete persisted.ocrResults;
        persisted._preserveResult = true;
    }
    persisted.batches = Array.isArray(task.batches)
        ? task.batches.map((batch) => {
            const copy = { ...batch };
            delete copy.payloadBlob;
            delete copy.payloadDataUrl;
            delete copy._streamStatus;
            delete copy._streamImagePathMap;
            if (!includeResults) delete copy.markdown;
            if (includeResults && isStreamStatusMarkdown(copy.markdown)) delete copy.markdown;
            return copy;
        })
        : [];
    return persisted;
}

function updateActionState(task) {
    const hasResult = Boolean(visibleTaskMarkdown(task)) || Boolean(task?.ocrResults?.length);
    const hasComparisonReport = activeResultView === 'comparison' && isComparisonReportReady(task);
    const taskModel = task ? getTaskActionModel(task) : getSelectedModel();
    const modelReady = !task || isModelRuntimeReady(taskModel.id);
    const canStartAfterSwitch = task && !modelReady && canSwitchModelRuntime(taskModel.id);
    const canDeployMissingModel = task && !modelReady && isModelRuntimeMissing(taskModel.id);
    const modelStarting = task && !modelReady && isModelRuntimeSwitching(taskModel.id);
    const comparisonInputLocked = comparisonInFlight;
    const parsingSettingsLocked = comparisonInFlight || isProcessing || Boolean(processingTaskId);
    if (els.fileInput) els.fileInput.disabled = comparisonInputLocked;
    [els.browseBtn, els.newTaskBtn].forEach((button) => {
        if (button) button.disabled = comparisonInputLocked;
    });
    if (els.dropZone) {
        els.dropZone.classList.toggle('input-locked', comparisonInputLocked);
        els.dropZone.setAttribute('aria-disabled', comparisonInputLocked ? 'true' : 'false');
        if (comparisonInputLocked) els.dropZone.classList.remove('drag-over');
    }
    [
        els.chartRecognitionSwitch,
        els.docUnwarpingSwitch,
        els.docOrientationSwitch,
        els.sealRecognitionSwitch,
        els.formulaNumberSwitch,
        els.ignoreHeaderSwitch,
        els.ignoreFooterSwitch,
        els.ignoreNumberSwitch,
        els.pdfBatchSizeInput
    ].forEach((control) => {
        if (control) control.disabled = parsingSettingsLocked;
    });
    if (els.modelSelect) {
        els.modelSelect.disabled = isProcessing || Boolean(processingTaskId) || comparisonInFlight || modelSwitchInFlight || isModelRuntimeSwitching();
    }
    if (els.unlimitedBackendSelect) {
        els.unlimitedBackendSelect.disabled = (
            selectedModelId !== UNLIMITED_OCR_MODEL_ID
            || isProcessing
            || Boolean(processingTaskId)
            || comparisonInFlight
            || modelSwitchInFlight
            || unlimitedOcrBackendSwitchInFlight
            || isModelRuntimeSwitching()
        );
    }
    els.startBtn.disabled = !task
        || !isTaskDetailLoaded(task)
        || isProcessing
        || Boolean(processingTaskId)
        || comparisonInFlight
        || modelStarting
        || (!modelReady && !canStartAfterSwitch && !canDeployMissingModel);
    if (els.compareBtn) {
        els.compareBtn.disabled = !task
            || !isTaskDetailLoaded(task)
            || isProcessing
            || Boolean(processingTaskId)
            || comparisonInFlight
            || modelSwitchInFlight
            || availableModels.length < 2;
    }
    if (els.editBtn) {
        els.editBtn.disabled = !task
            || isProcessing
            || comparisonInFlight
            || activeResultView !== 'markdown'
            || !Boolean(visibleTaskMarkdown(task));
    }
    if (els.clearHistoryBtn) {
        els.clearHistoryBtn.disabled = historyMutationsLocked();
    }
    if (els.taskList) {
        els.taskList.querySelectorAll('.task-delete').forEach((button) => {
            button.disabled = historyMutationsLocked();
        });
    }
    updateCopyButtonState(task);
    els.downloadBtn.disabled = activeResultView === 'comparison'
        ? !hasComparisonReport
        : !hasResult;
    const startLabel = startButtonLabel(task);
    const showProcessing = (isProcessing && task?.status === 'processing') || modelStarting;
    const startButtonHtml = showProcessing
        ? `<span class="spinner"></span>${modelStarting ? t('模型启动中') : t('解析中')}`
        : `<svg viewBox="0 0 24 24"><path d="m8 5 11 7-11 7V5Z"/></svg>${startLabel}`;
    if (els.startBtn.dataset.renderedHtml !== startButtonHtml) {
        els.startBtn.innerHTML = startButtonHtml;
        els.startBtn.dataset.renderedHtml = startButtonHtml;
    }
}

function startButtonLabel(task) {
    if (!task) return t('开始解析');
    const taskModel = getTaskActionModel(task);
    if (isModelRuntimeMissing(taskModel.id)) return t('下载并部署模型');
    if (!isModelRuntimeReady(taskModel.id)) return t('启动模型并解析');
    if (task.status === 'completed') return t('重新解析');
    if (task.status === 'error') return t('重试解析');
    if (shouldResumeTask(task)) return t('继续解析');
    return t('开始解析');
}

function hasJsonResult(task) {
    return Boolean(task?.ocrResults?.length);
}

function isComparisonReportReady(task) {
    const groupTasks = comparisonTasksFor(task);
    return groupTasks.length > 1 && groupTasks.every(isTaskDetailLoaded);
}

function canCopyActiveResult(task) {
    if (activeResultView === 'json') return hasJsonResult(task);
    if (activeResultView === 'comparison') return isComparisonReportReady(task);
    return Boolean(visibleTaskMarkdown(task));
}

function copyButtonLabel() {
    if (activeResultView === 'json') return t('复制 JSON');
    if (activeResultView === 'comparison') return t('复制对比报告');
    return t('复制 Markdown');
}

function updateCopyButtonState(task) {
    const label = copyButtonLabel();
    els.copyBtn.disabled = !canCopyActiveResult(task);
    els.copyBtn.setAttribute('title', label);
    els.copyBtn.setAttribute('aria-label', label);
}

function activeResultCopyText(task) {
    if (!task) return '';
    if (activeResultView === 'json') {
        if (!hasJsonResult(task)) return '';
        return JSON.stringify(toOfficialJson(task), null, 2);
    }
    if (activeResultView === 'comparison') {
        return isComparisonReportReady(task) ? comparisonReportMarkdown(task) : '';
    }
    const markdown = visibleTaskMarkdown(task);
    return markdown ? normalizeOCRMarkdown(markdown) : '';
}

async function copyActiveResult() {
    const task = getActiveTask();
    const text = activeResultCopyText(task);
    if (!text) return;
    try {
        await writeClipboardText(text);
        els.copyBtn.classList.add('success');
        setTimeout(() => els.copyBtn.classList.remove('success'), 900);
    } catch (error) {
        console.error(error);
        alert(error.message || t('复制失败'));
    }
}

async function downloadActiveTask() {
    const task = getActiveTask();
    if (!task) return;

    if (activeResultView === 'comparison') {
        if (!isComparisonReportReady(task)) return;
        const report = comparisonReportMarkdown(task);
        downloadBlob(new Blob([report], { type: 'text/markdown' }), safeDownloadName(task.comparisonSourceName || task.name, 'comparison.md'));
        return;
    }

    if (!task.markdown && !task.ocrResults?.length) return;

    if (activeResultView === 'json') {
        const json = JSON.stringify(toOfficialJson(task), null, 2);
        downloadBlob(new Blob([json], { type: 'application/json' }), safeDownloadName(task.name, 'json'));
        return;
    }

    const markdown = normalizeOCRMarkdown(visibleTaskMarkdown(task));
    const imageEntries = Object.entries(task.images || {});
    if (imageEntries.length === 0) {
        downloadBlob(new Blob([markdown], { type: 'text/markdown' }), safeDownloadName(task.name, 'md'));
        return;
    }

    const zip = new JSZip();
    let rewritten = markdown;
    const folder = zip.folder('ocr_images');
    for (const [path, base64] of imageEntries) {
        const filename = path.split('/').pop();
        rewritten = rewritten.split(path).join(`ocr_images/${filename}`);
        folder.file(filename, base64ToBytes(base64));
    }
    zip.file('README.md', rewritten);
    const blob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 6 } });
    downloadBlob(blob, safeDownloadName(task.name, 'zip'));
}

async function clearHistory() {
    if (comparisonInFlight) {
        alert(t('模型对比正在进行，完成后再清空历史。'));
        return;
    }
    if (historyMutationsLocked()) {
        alert(t('当前正在解析或切换模型，完成后再清空历史。'));
        return;
    }
    if (!confirm(t('确认清空所有本地任务历史吗？'))) return;
    try {
        await deleteAllTasks();
    } catch (error) {
        console.error(error);
        alert(error.message || t('清空失败，请稍后重试。'));
        return;
    }
    tasks = [];
    activeTaskId = null;
    resetWorkbench();
}

function resetWorkbench() {
    renderTaskList();
    sourceRenderToken += 1;
    currentPdf = null;
    els.sourceTitle.textContent = t('等待上传文件');
    els.sourceMeta.textContent = t('PDF、图片、Office 文档');
    els.pdfControls.classList.add('hidden');
    els.sourceViewer.innerHTML = emptyDropZoneHtml();
    els.dropZone = document.getElementById('drop-zone');
    els.browseBtn = document.getElementById('browse-btn');
    els.browseBtn.addEventListener('click', () => els.fileInput.click());
    renderResultPane(null);
    updateActiveModelDisplay(null);
    updateActionState(null);
}

function changePdfPage(delta) {
    if (!currentPdf) return;
    currentPage = Math.min(Math.max(currentPage + delta, 1), currentPdf.numPages);
    resetSplitHorizontalScroll();
    scrollPdfPageIntoView(currentPage, 'smooth');
    syncPPOCRVisualScrollFromSource();
    updatePdfControls();
}

async function changeZoom(delta) {
    if (!currentPdf) return;
    const scrollAnchor = captureSourceScrollAnchor();
    currentZoom = Math.min(2.2, Math.max(0.55, currentZoom + delta));
    await renderPdfDocument(++sourceRenderToken, scrollAnchor);
    const task = getActiveTask();
    if (task && activeResultView === 'markdown') {
        if (isPPOCRVisualTask(task)) {
            invalidatePPOCRVisualRender();
        }
        renderResultPane(task);
        queueSyncedScrollRestore(scrollAnchor);
    }
}

async function resetZoom() {
    if (!currentPdf) return;
    const scrollAnchor = resetAnchorHorizontal(captureSourceScrollAnchor());
    currentZoom = getDefaultPdfZoom();
    await renderPdfDocument(++sourceRenderToken, scrollAnchor);
    const task = getActiveTask();
    if (task && activeResultView === 'markdown') {
        if (isPPOCRVisualTask(task)) {
            invalidatePPOCRVisualRender();
        }
        renderResultPane(task);
        queueSyncedScrollRestore(scrollAnchor);
    }
}

function scrollPdfPageIntoView(pageNumber, behavior = 'smooth') {
    const page = sourcePageSurface(pageNumber)?.container;
    if (!page) return;
    const top = Number(pageNumber) <= 1 ? 0 : page.offsetTop - els.sourceViewer.offsetTop - 12;
    els.sourceViewer.scrollTo({ top: Math.max(top, 0), behavior });
}

function handleSourceViewerScroll() {
    updateCurrentPageFromScroll();
    const task = getActiveTask();
    if (isUnlimitedOCRTask(task)) {
        scheduleLayoutResultScrollSync();
    }
}

function updateCurrentPageFromScroll() {
    if (!currentPdf) return;
    const pages = Array.from(els.sourceViewer.querySelectorAll('.pdf-page-wrap'));
    if (!pages.length) return;

    const viewerTop = els.sourceViewer.getBoundingClientRect().top;
    let nearestPage = currentPage;
    let nearestDistance = Infinity;

    pages.forEach((page) => {
        const distance = Math.abs(page.getBoundingClientRect().top - viewerTop - 16);
        if (distance < nearestDistance) {
            nearestDistance = distance;
            nearestPage = Number(page.dataset.page);
        }
    });

    if (nearestPage !== currentPage) {
        currentPage = nearestPage;
        updatePdfControls();
    }
}

function updatePdfControls() {
    if (!currentPdf) return;
    els.pageIndicator.textContent = `${currentPage} / ${currentPdf.numPages}`;
    els.prevPageBtn.disabled = currentPage <= 1;
    els.nextPageBtn.disabled = currentPage >= currentPdf.numPages;
    if (els.resetZoomBtn) {
        els.resetZoomBtn.disabled = Math.abs(currentZoom - getDefaultPdfZoom()) < 0.01;
    }
}

function getDefaultPdfZoom() {
    const viewer = els.sourceViewer;
    if (!viewer) return DEFAULT_PDF_ZOOM;
    const styles = getComputedStyle(viewer);
    const horizontalPadding = (Number.parseFloat(styles.paddingLeft) || 0)
        + (Number.parseFloat(styles.paddingRight) || 0);
    const availableWidth = Math.max(0, viewer.clientWidth - horizontalPadding - PDF_FIT_WIDTH_GUTTER);
    if (!availableWidth || !pdfDefaultPageWidth) return DEFAULT_PDF_ZOOM;
    const fitZoom = availableWidth / pdfDefaultPageWidth;
    return roundPdfZoom(Math.max(DEFAULT_PDF_ZOOM, Math.min(MAX_DEFAULT_PDF_ZOOM, fitZoom)));
}

function roundPdfZoom(value) {
    return Math.round(value * 100) / 100;
}

function captureSourceScrollAnchor() {
    const page = getActiveSourcePage();
    if (!page) {
        return {
            pageNumber: currentPage,
            progress: 0,
            xRatio: horizontalScrollRatio(els.sourceViewer)
        };
    }

    const pageNumber = Number(page.dataset.page || currentPage);
    const pageTop = sourcePageTop(page);
    const scrollable = Math.max(1, page.offsetHeight - els.sourceViewer.clientHeight);
    return {
        pageNumber,
        progress: Math.min(Math.max((els.sourceViewer.scrollTop - pageTop) / scrollable, 0), 1),
        xRatio: horizontalScrollRatio(els.sourceViewer)
    };
}

function restoreSourceScrollAnchor(anchor, behavior = 'auto') {
    if (!anchor) return;
    const page = els.sourceViewer.querySelector(`.pdf-page-wrap[data-page="${anchor.pageNumber}"]`);
    if (!page) return;
    const scrollable = Math.max(0, page.offsetHeight - els.sourceViewer.clientHeight);
    const targetTop = sourcePageTop(page) + (anchor.progress || 0) * scrollable;
    els.sourceViewer.scrollTo({
        top: Math.max(targetTop, 0),
        left: horizontalScrollTarget(els.sourceViewer, anchor.xRatio || 0),
        behavior
    });
    currentPage = Number(anchor.pageNumber || currentPage);
    updatePdfControls();
}

function queueSyncedScrollRestore(anchor) {
    window.setTimeout(() => {
        restoreSourceScrollAnchor(anchor, 'auto');
        syncPPOCRVisualScrollFromSource();
        syncLinkedLayoutScrollFromSource();
    }, 120);
    window.setTimeout(() => {
        restoreSourceScrollAnchor(anchor, 'auto');
        syncPPOCRVisualScrollFromSource();
        syncLinkedLayoutScrollFromSource();
    }, 360);
}

function getActiveSourcePage() {
    const pages = Array.from(els.sourceViewer.querySelectorAll('.pdf-page-wrap'));
    if (!pages.length) return null;

    const viewerTop = els.sourceViewer.getBoundingClientRect().top;
    let nearestPage = pages[0];
    let nearestDistance = Infinity;
    pages.forEach((page) => {
        const distance = Math.abs(page.getBoundingClientRect().top - viewerTop - 16);
        if (distance < nearestDistance) {
            nearestDistance = distance;
            nearestPage = page;
        }
    });
    return nearestPage;
}

function sourcePageTop(page) {
    if (!page) return 0;
    const pageNumber = Number(page.dataset.page || 1);
    return pageNumber <= 1 ? 0 : page.offsetTop - els.sourceViewer.offsetTop - 12;
}

function horizontalScrollRatio(container) {
    const maxScroll = Math.max(0, container.scrollWidth - container.clientWidth);
    return maxScroll ? Math.min(Math.max(container.scrollLeft / maxScroll, 0), 1) : 0;
}

function horizontalScrollTarget(container, ratio) {
    const maxScroll = Math.max(0, container.scrollWidth - container.clientWidth);
    return maxScroll * Math.min(Math.max(ratio, 0), 1);
}

function resetAnchorHorizontal(anchor) {
    if (!anchor) return anchor;
    return {
        ...anchor,
        xRatio: 0
    };
}

function resetSplitHorizontalScroll() {
    if (!els.sourceViewer || !els.markdownView) return;
    withSplitScrollLock(() => {
        els.sourceViewer.scrollLeft = 0;
        els.markdownView.scrollLeft = 0;
    });
}

function withSplitScrollLock(callback) {
    splitScrollSyncLocked = true;
    try {
        callback();
    } finally {
        requestAnimationFrame(() => {
            splitScrollSyncLocked = false;
        });
    }
}

function scheduleSourceToPPOCRScrollSync() {
    if (splitScrollSyncLocked || sourceScrollSyncFrame) return;
    sourceScrollSyncFrame = requestAnimationFrame(() => {
        sourceScrollSyncFrame = 0;
        syncPPOCRVisualScrollFromSource();
    });
}

async function renderPDFPageDataUrl(pdf, pageNumber, scale) {
    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({ scale });
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    await page.render({ canvasContext: context, viewport }).promise;
    return canvas.toDataURL('image/jpeg', 0.78);
}

async function createPDFBatchBlob(sourcePdf, startPage, endPage) {
    return new Blob([await createPDFBatchBytes(sourcePdf, startPage, endPage)], { type: 'application/pdf' });
}

async function createPDFBatchBytes(sourcePdf, startPage, endPage) {
    const batchPdf = await PDFLib.PDFDocument.create();
    const pageIndices = [];
    for (let i = startPage - 1; i <= endPage - 1; i++) {
        pageIndices.push(i);
    }
    const copiedPages = await batchPdf.copyPages(sourcePdf, pageIndices);
    copiedPages.forEach((page) => batchPdf.addPage(page));
    return batchPdf.save();
}

async function ensureBatchPayload(task, batch) {
    if (batch.payloadBlob || batch.payloadDataUrl) return;
    if (batch.fileType === 1) {
        batch.payloadBlob = await getTaskSourceBlob(task, task.mimeType || 'image/jpeg');
        return;
    }

    if (batch.fileType !== 0) {
        throw new Error(t('无法重建当前批次的解析 payload'));
    }
    if (!(task.sourceDataUrl || task.sourceUrl)) {
        throw new Error(t('缺少源 PDF，无法继续解析'));
    }
    if ((task.pageCount || 1) === 1) {
        batch.payloadBlob = await getTaskSourceBlob(task, 'application/pdf');
        return;
    }

    if (task.sourceUrl && !task.sourceDataUrl) {
        batch.payloadBlob = await fetchPdfBatchBlob(task, batch.startPage, batch.endPage);
        return;
    }

    let sourcePdf = sourcePdfCache.get(task.id);
    if (!sourcePdf) {
        sourcePdf = await PDFLib.PDFDocument.load(await getTaskSourceBytes(task));
        sourcePdfCache.set(task.id, sourcePdf);
    }
    batch.payloadBlob = await createPDFBatchBlob(sourcePdf, batch.startPage, batch.endPage);
}

function releaseBatchPayload(batch) {
    delete batch.payloadBlob;
    delete batch.payloadDataUrl;
}

async function fetchPdfBatchBlob(task, startPage, endPage) {
    const url = `${API_BASE}/tasks/${encodeURIComponent(task.id)}/source/pages?start_page=${startPage}&end_page=${endPage}`;
    const response = await apiFetch(url);
    if (!response.ok) {
        throw new Error(t('读取 PDF 分页失败：{detail}', { detail: await responseErrorText(response) }));
    }
    return response.blob();
}

async function getTaskSourceBytes(task) {
    if (sourceBytesCache.has(task.id)) {
        return sourceBytesCache.get(task.id);
    }
    if (task.sourceDataUrl) {
        const bytes = dataUrlToUint8Array(task.sourceDataUrl);
        sourceBytesCache.set(task.id, bytes);
        return bytes;
    }
    if (!task.sourceUrl) {
        throw new Error(t('缺少源文件，无法继续解析'));
    }
    const response = await apiFetch(task.sourceUrl);
    if (!response.ok) {
        throw new Error(t('读取源文件失败：{detail}', { detail: await responseErrorText(response) }));
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    sourceBytesCache.set(task.id, bytes);
    return bytes;
}

async function getTaskSourceBlob(task, mimeType) {
    if (task.sourceDataUrl) {
        return dataUrlToBlob(task.sourceDataUrl);
    }
    if (!task.sourceUrl) {
        throw new Error(t('缺少源文件，无法继续解析'));
    }
    const response = await apiFetch(task.sourceUrl);
    if (!response.ok) {
        throw new Error(t('读取源文件失败：{detail}', { detail: await responseErrorText(response) }));
    }
    const blob = await response.blob();
    if (mimeType && blob.type !== mimeType) {
        return new Blob([blob], { type: mimeType });
    }
    return blob;
}

const unlimitedOCRDetPattern = /<\|det\|>\s*([A-Za-z_][\w-]*)\s*(\[[^\]]*\])?\s*<\|\/det\|>/g;
const unlimitedOCRSkipMarkdownLabels = new Set(['header', 'footer', 'number', 'page_number', 'page_num']);
const unlimitedOCRCaptionLabels = new Set(['image_caption', 'figure_caption', 'table_caption']);
const unlimitedOCRTitleLabels = new Set(['title', 'section_title']);

function normalizeMarkdownNewlines(markdown) {
    return String(markdown)
        .replace(/\r\n/g, '\n')
        .replace(/\r/g, '\n')
        .replace(/\\r\\n/g, '\n')
        .replace(/\\n/g, '\n');
}

function compactMarkdownBlock(text) {
    return normalizeMarkdownNewlines(text)
        .replace(/[ \t]+/g, ' ')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

function formatUnlimitedOCRBlock(label, content, seenTitle) {
    const normalizedLabel = String(label || '').toLowerCase().trim();
    const text = compactMarkdownBlock(content);
    if (!text || unlimitedOCRSkipMarkdownLabels.has(normalizedLabel)) {
        return { block: '', seenTitle };
    }
    if (unlimitedOCRTitleLabels.has(normalizedLabel)) {
        return { block: `${seenTitle ? '##' : '#'} ${text}`, seenTitle: true };
    }
    if (unlimitedOCRCaptionLabels.has(normalizedLabel)) {
        return { block: `*${text}*`, seenTitle };
    }
    if (normalizedLabel === 'formula' || normalizedLabel === 'display_formula') {
        return { block: `$$\n${text}\n$$`, seenTitle };
    }
    if (normalizedLabel === 'image' || normalizedLabel === 'chart') {
        return { block: `**${normalizedLabel.replace(/_/g, ' ')}:** ${text}`, seenTitle };
    }
    return { block: text, seenTitle };
}

function cleanUnlimitedOCRMarkdown(markdown) {
    const text = normalizeMarkdownNewlines(markdown);
    if (!text.includes('<|det|>')) return compactMarkdownBlock(text);

    const matches = Array.from(text.matchAll(unlimitedOCRDetPattern));
    unlimitedOCRDetPattern.lastIndex = 0;
    if (!matches.length) {
        return compactMarkdownBlock(text.replace(/<\|\/?det\|>/g, ''));
    }

    const blocks = [];
    const prefix = compactMarkdownBlock(text.slice(0, matches[0].index));
    if (prefix) blocks.push(prefix);

    let seenTitle = false;
    matches.forEach((match, index) => {
        const nextStart = matches[index + 1]?.index ?? text.length;
        const formatted = formatUnlimitedOCRBlock(match[1], text.slice(match.index + match[0].length, nextStart), seenTitle);
        seenTitle = formatted.seenTitle;
        if (formatted.block) blocks.push(formatted.block);
    });

    return compactMarkdownBlock(blocks.join('\n\n'));
}

function normalizeOCRMarkdown(markdown) {
    const normalized = normalizeMarkdownNewlines(markdown);
    return normalized.includes('<|det|>') ? cleanUnlimitedOCRMarkdown(normalized) : normalized;
}

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function renderMarkdownHtml(markdown) {
    if (!window.DOMPurify || typeof window.DOMPurify.sanitize !== 'function') {
        return `<pre class="sanitizer-unavailable">${escapeHtml(markdown)}</pre>`;
    }
    const { text, math } = stashMathSegments(markdown);
    let html = marked.parse(text);
    math.forEach((value, index) => {
        html = html.split(mathToken(index)).join(escapeHtml(value));
    });
    return DOMPurify.sanitize(html);
}

function stashMathSegments(markdown) {
    const math = [];
    const store = (value) => {
        const token = mathToken(math.length);
        math.push(value);
        return token;
    };
    let text = markdown.replace(/\$\$[\s\S]*?\$\$/g, store);
    text = text.replace(/\\\[[\s\S]*?\\\]/g, store);
    text = text.replace(/\\\([\s\S]*?\\\)/g, store);
    text = text.replace(/\$([^$\n]|\\.)+?\$/g, store);
    return { text, math };
}

function mathToken(index) {
    return `PANDOCRMATHTOKEN${index}X`;
}

function prepareMarkdownForRender(markdown) {
    return normalizeOCRMarkdown(markdown);
}

function renderMathWhenReady(container, retries = 20) {
    if (!window.renderMathInElement) {
        if (retries > 0) setTimeout(() => renderMathWhenReady(container, retries - 1), 150);
        return;
    }
    renderMathInElement(container, {
        delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '\\[', right: '\\]', display: true },
            { left: '\\(', right: '\\)', display: false },
            { left: '$', right: '$', display: false }
        ],
        ignoredTags: ['script', 'noscript', 'style', 'textarea'],
        throwOnError: false,
        strict: false
    });
}

function renderOfficialLayoutResult(task) {
    const blocks = collectOfficialRenderBlocks(task);
    if (!blocks.length) return { rendered: false, changed: false, mathRoots: [] };

    const context = officialLayoutRenderContext(task);
    const expectedKeys = blocks.map(officialLayoutBlockKey);
    const children = Array.from(els.markdownView.children);
    const existingBlocks = children.filter((element) => element.classList.contains('official-layout-block'));
    const hasOnlyOfficialBlocks = children.length === existingBlocks.length;
    const existingKeys = existingBlocks.map((element) => element.dataset.blockKey || '');
    const canAppend = hasOnlyOfficialBlocks
        && renderedOfficialLayoutContext === context
        && existingKeys.length <= expectedKeys.length
        && existingKeys.every((key, index) => key === expectedKeys[index]);

    const appendedElements = [];
    const fullRebuild = !canAppend;
    if (fullRebuild) {
        clearSourceHighlight();
        clearSourceHotspots();
        linkedLayoutEntries = [];
        els.markdownView.replaceChildren();
        renderedOfficialLayoutContext = context;
    }

    const startIndex = canAppend ? existingKeys.length : 0;
    if (startIndex === expectedKeys.length) {
        return { rendered: true, changed: false, mathRoots: [] };
    }

    const fragment = document.createDocumentFragment();
    blocks.slice(startIndex).forEach((block, offset) => {
        const blockIndex = startIndex + offset;
        const element = createOfficialLayoutBlockElement(block, expectedKeys[blockIndex], task);
        appendedElements.push(element);
        fragment.appendChild(element);
    });
    els.markdownView.appendChild(fragment);
    renderedOfficialLayoutContext = context;

    return {
        rendered: true,
        changed: true,
        mathRoots: fullRebuild ? [els.markdownView] : appendedElements
    };
}

function createOfficialLayoutBlockElement(block, blockKey, task) {
    const element = document.createElement('section');
    element.className = 'layout-linked-block official-layout-block';
    element.dataset.blockKey = blockKey;
    element.dataset.layoutLabel = layoutLabelText(block.label);
    element.dataset.page = String(block.page);
    element.dataset.blockIndex = String(block.blockIndex);

    const content = rewriteBlockImageSources(block.content || fallbackBlockContent(block), block.pageResult, task);
    element.innerHTML = renderMarkdownHtml(content);

    addSourceHotspot(block, element);
    registerLinkedLayoutBlock(element, block);
    bindLinkedBlockEvents(element, block);
    return element;
}

function officialLayoutRenderContext(task) {
    return [
        task?.id || '',
        sourceRenderToken,
        currentZoom,
        currentLanguage,
        els.ignoreNumberSwitch.checked,
        els.ignoreHeaderSwitch.checked,
        els.ignoreFooterSwitch.checked
    ].join(':');
}

function officialLayoutBlockKey(block) {
    return [
        block.page,
        block.blockIndex,
        String(block.label || '').toLowerCase(),
        Array.isArray(block.bbox) ? block.bbox.join(',') : '',
        shortHash(block.content || fallbackBlockContent(block))
    ].join(':');
}

function shortHash(value) {
    const text = String(value || '');
    let hash = 5381;
    for (let index = 0; index < text.length; index += 1) {
        hash = ((hash << 5) + hash) ^ text.charCodeAt(index);
    }
    return (hash >>> 0).toString(36);
}

function isUnlimitedOCRResult(pageResult, pruned = null) {
    return String(pageResult?.parser || pruned?.parser || '').toLowerCase() === UNLIMITED_OCR_MODEL_ID;
}

function isHPDParsingResult(pageResult, pruned = null) {
    return String(pageResult?.parser || pruned?.parser || '').toLowerCase() === HPD_PARSING_MODEL_ID;
}

function looksLikeUnlimitedOCRNormalizedBox(bbox, pageWidth, pageHeight) {
    if (!Array.isArray(bbox) || bbox.length < 4) return false;
    if (Math.round(Number(pageWidth)) !== 1024 || Math.round(Number(pageHeight)) !== 1024) return false;
    const coords = bbox.map(Number).slice(0, 4);
    if (!coords.every(Number.isFinite)) return false;
    return Math.max(...coords.map(Math.abs)) <= UNLIMITED_OCR_COORDINATE_SIZE + 0.5;
}

function looksLikeHPDParsingNormalizedBox(bbox) {
    if (!Array.isArray(bbox) || bbox.length < 4) return false;
    const coords = bbox.map(Number).slice(0, 4);
    if (!coords.every(Number.isFinite)) return false;
    return Math.max(...coords.map(Math.abs)) <= HPD_PARSING_COORDINATE_SIZE + 0.5;
}

function layoutCoordinateBoundsForBlock(pageResult, pruned, bbox) {
    const rawPageWidth = Number(pruned?.width);
    const rawPageHeight = Number(pruned?.height);
    if (!rawPageWidth || !rawPageHeight) return null;

    // HPD-Parsing reports boxes in a fixed 0-1000 space, independently of
    // the page raster dimensions used for inference.
    if (isHPDParsingResult(pageResult, pruned)
        && looksLikeHPDParsingNormalizedBox(bbox)) {
        return {
            pageWidth: HPD_PARSING_COORDINATE_SIZE,
            pageHeight: HPD_PARSING_COORDINATE_SIZE
        };
    }

    if (isUnlimitedOCRResult(pageResult, pruned)
        && looksLikeUnlimitedOCRNormalizedBox(bbox, rawPageWidth, rawPageHeight)) {
        return {
            pageWidth: UNLIMITED_OCR_COORDINATE_SIZE,
            pageHeight: UNLIMITED_OCR_COORDINATE_SIZE
        };
    }

    return { pageWidth: rawPageWidth, pageHeight: rawPageHeight };
}

function unlimitedOCRSourceBoundsForBox(bbox, pageWidth, pageHeight) {
    const rawPageWidth = Number(pageWidth || UNLIMITED_OCR_COORDINATE_SIZE);
    const rawPageHeight = Number(pageHeight || UNLIMITED_OCR_COORDINATE_SIZE);
    if (looksLikeUnlimitedOCRNormalizedBox(bbox, rawPageWidth, rawPageHeight)) {
        return {
            pageWidth: UNLIMITED_OCR_COORDINATE_SIZE,
            pageHeight: UNLIMITED_OCR_COORDINATE_SIZE
        };
    }
    return { pageWidth: rawPageWidth, pageHeight: rawPageHeight };
}

function collectOfficialRenderBlocks(task) {
    const blocks = [];
    if (!Array.isArray(task?.ocrResults)) return blocks;

    task.ocrResults.forEach((pageResult, pageIndex) => {
        const pruned = pageResult?.prunedResult || pageResult;
        const parsingBlocks = Array.isArray(pruned?.parsing_res_list) ? pruned.parsing_res_list : [];

        parsingBlocks.forEach((sourceBlock, blockIndex) => {
            const bbox = sourceBlock.block_bbox || sourceBlock.coordinate || sourceBlock.bbox;
            const label = sourceBlock.block_label || sourceBlock.label || '';
            const content = sourceBlock.block_content ?? sourceBlock.text ?? sourceBlock.content ?? '';
            if (!Array.isArray(bbox) || bbox.length < 4) return;
            const bounds = layoutCoordinateBoundsForBlock(pageResult, pruned, bbox);
            if (!bounds) return;
            if (isIgnoredLayoutLabel(label)) return;
            if (!String(content || '').trim() && !isVisualLayoutLabel(label)) return;

            blocks.push({
                page: Number(pageResult?.sourcePage || pageResult?.page_index || pageIndex + 1),
                blockIndex,
                label,
                bbox,
                pageWidth: bounds.pageWidth,
                pageHeight: bounds.pageHeight,
                content: String(content || ''),
                pageResult,
                sourceBlock
            });
        });
    });

    return blocks;
}

function isIgnoredLayoutLabel(label) {
    const normalized = String(label || '').toLowerCase();
    const ignored = new Set(['footnote', 'aside_text']);
    if (els.ignoreNumberSwitch.checked) ignored.add('number');
    if (els.ignoreHeaderSwitch.checked) {
        ignored.add('header');
        ignored.add('header_image');
    }
    if (els.ignoreFooterSwitch.checked) {
        ignored.add('footer');
        ignored.add('footer_image');
    }
    return ignored.has(normalized);
}

function isVisualLayoutLabel(label) {
    return ['image', 'chart', 'table', 'algorithm'].includes(String(label || '').toLowerCase());
}

function fallbackBlockContent(block) {
    const label = layoutLabelText(block.label);
    return label ? `<div class="layout-block-placeholder">${escapeHtml(label)}</div>` : '';
}

function rewriteBlockImageSources(content, pageResult, task) {
    let output = normalizeOCRMarkdown(String(content || ''));
    const imageMaps = [
        pageResult?.markdown?.images,
        pageResult?.prunedResult?.markdown?.images,
        task?.images
    ];

    imageMaps.forEach((images) => {
        if (!images || typeof images !== 'object') return;
        Object.entries(images).forEach(([path, value]) => {
            if (!path || value == null) return;
            output = output.split(path).join(imageValueToSrc(value));
        });
    });

    return output;
}

function imageValueToSrc(value) {
    const text = String(value || '');
    if (/^(https?:|data:|blob:)/i.test(text)) return text;
    if (/^ocr_images\//i.test(text)) return text;
    return `data:image/jpeg;base64,${text}`;
}

function bindLinkedBlockEvents(element, block) {
    const preview = () => activateLinkedBlock(element, block);
    const locate = () => activateLinkedBlock(element, block, { scrollSource: true });
    const deactivate = () => deactivateLinkedBlocks();
    element.addEventListener('mouseenter', preview);
    element.addEventListener('mouseover', preview);
    element.addEventListener('pointerenter', preview);
    element.addEventListener('focusin', preview);
    element.addEventListener('click', locate);
    element.addEventListener('mouseleave', deactivate);
    element.addEventListener('pointerleave', deactivate);
    element.addEventListener('focusout', deactivate);
}

function linkMarkdownToSourceBlocks(task) {
    clearSourceHighlight();
    clearSourceHotspots();
    linkedLayoutEntries = [];
    if (!task?.ocrResults?.length) return;

    const blocks = collectLayoutBlocks(task);
    if (!blocks.length) return;

    const elements = collectMarkdownBlockElements(els.markdownView);
    let cursor = 0;

    elements.forEach((element) => {
        const isImageBlock = isMarkdownImageBlock(element);
        const text = normalizeMatchText(element.innerText || element.textContent || '');
        if (!isImageBlock && text.length < 2) return;

        const match = isImageBlock
            ? findNextLayoutBlockByLabel(blocks, cursor, ['image', 'chart', 'table'])
            : isAlgorithmText(element.innerText || element.textContent || '')
                ? findNextLayoutBlockByLabel(blocks, cursor, ['algorithm'])
            : isFigureTitleText(element.innerText || element.textContent || '')
                ? findNextLayoutBlockByLabel(blocks, cursor, ['figure_title'])
            : findBestLayoutBlock(text, blocks, cursor);
        if (!match) return;

        cursor = match.index;
        element.classList.add('layout-linked-block');
        element.dataset.layoutLabel = layoutLabelText(match.block.label);
        registerLinkedLayoutBlock(element, match.block);
        addSourceHotspot(match.block, element);
        const preview = () => activateLinkedBlock(element, match.block);
        const locate = () => activateLinkedBlock(element, match.block, { scrollSource: true });
        const deactivate = () => deactivateLinkedBlocks();
        element.addEventListener('mouseenter', preview);
        element.addEventListener('mouseover', preview);
        element.addEventListener('pointerenter', preview);
        element.addEventListener('focusin', preview);
        element.addEventListener('click', locate);
        element.addEventListener('mouseleave', deactivate);
        element.addEventListener('pointerleave', deactivate);
        element.addEventListener('focusout', deactivate);
    });
}

function collectLayoutBlocks(task) {
    const blocks = [];
    task.ocrResults.forEach((pageResult, pageIndex) => {
        const pruned = pageResult?.prunedResult || pageResult;
        const parsingBlocks = Array.isArray(pruned?.parsing_res_list) ? pruned.parsing_res_list : [];
        const pageNumber = Number(pageResult?.sourcePage || pruned?.sourcePage || pageResult?.page_index || pageIndex + 1);

        parsingBlocks.forEach((block, blockIndex) => {
            const bbox = block.block_bbox || block.coordinate || block.bbox;
            const content = block.block_content || block.text || block.content || '';
            const label = block.block_label || block.label || '';
            if (!Array.isArray(bbox) || bbox.length < 4) return;
            const bounds = layoutCoordinateBoundsForBlock(pageResult, pruned, bbox);
            if (!bounds) return;
            if (!content && !['image', 'chart', 'table'].includes(label)) return;
            blocks.push({
                page: pageNumber,
                order: Number(block.block_order ?? blockIndex),
                label,
                bbox,
                pageWidth: bounds.pageWidth,
                pageHeight: bounds.pageHeight,
                text: normalizeMatchText(content || label)
            });
        });
    });
    return blocks.sort((a, b) => (a.page - b.page) || (a.order - b.order));
}

function registerLinkedLayoutBlock(element, block) {
    if (!element || !block) return;
    linkedLayoutBlockByElement.set(element, block);
    linkedLayoutEntries.push({ element, block });
}

function hasLinkedLayoutScrollSync(task) {
    return activeResultView === 'markdown'
        && isUnlimitedOCRTask(task)
        && !isPPOCRVisualTask(task)
        && currentPdf
        && linkedLayoutEntries.length > 0
        && Boolean(task?.ocrResults?.length);
}

function visibleLinkedLayoutEntries() {
    return linkedLayoutEntries.filter(({ element }) => element?.isConnected);
}

function findNearestLinkedEntryInResult() {
    const entries = visibleLinkedLayoutEntries();
    if (!entries.length) return null;
    const containerRect = els.markdownView.getBoundingClientRect();
    const targetY = containerRect.top + (containerRect.height * 0.36);
    let best = null;
    let bestDistance = Infinity;

    entries.forEach((entry) => {
        const rect = entry.element.getBoundingClientRect();
        if (!rect.width && !rect.height) return;
        const distance = Math.abs(rect.top - targetY);
        const visiblePenalty = rect.bottom < containerRect.top || rect.top > containerRect.bottom ? containerRect.height : 0;
        const score = distance + visiblePenalty;
        if (score < bestDistance) {
            bestDistance = score;
            best = entry;
        }
    });

    return best;
}

function findNearestLinkedEntryInSource() {
    const entries = visibleLinkedLayoutEntries();
    if (!entries.length) return null;
    const page = getActiveSourcePage();
    if (!page) return null;
    const pageNumber = Number(page.dataset.page || currentPage || 1);
    const surface = sourcePageSurface(pageNumber);
    if (!surface?.element) return null;

    const sourceHeight = surface.element.clientHeight || surface.element.height || surface.element.naturalHeight || page.offsetHeight;
    const anchorY = (els.sourceViewer.scrollTop - sourcePageTop(page)) + (els.sourceViewer.clientHeight * 0.42);
    const sourceProgress = Math.min(Math.max(anchorY / Math.max(sourceHeight, 1), 0), 1);
    let best = null;
    let bestDistance = Infinity;

    entries.forEach((entry) => {
        const block = entry.block;
        if (Number(block.page || 1) !== pageNumber || !Array.isArray(block.bbox)) return;
        const blockProgress = layoutBlockCenterProgress(block);
        const distance = Math.abs(blockProgress - sourceProgress);
        if (distance < bestDistance) {
            bestDistance = distance;
            best = entry;
        }
    });

    return best;
}

function layoutBlockCenterProgress(block) {
    if (!Array.isArray(block?.bbox) || !block.pageHeight) return 0;
    const y1 = Number(block.bbox[1]);
    const y2 = Number(block.bbox[3]);
    if (!Number.isFinite(y1) || !Number.isFinite(y2)) return 0;
    return Math.min(Math.max(((y1 + y2) / 2) / Math.max(Number(block.pageHeight), 1), 0), 1);
}

function setActiveLinkedLayoutEntry(entry) {
    els.markdownView.querySelectorAll('.layout-linked-block-active').forEach((element) => {
        if (element !== entry?.element) element.classList.remove('layout-linked-block-active');
    });
    if (entry?.element?.isConnected) {
        entry.element.classList.add('layout-linked-block-active');
    }
}

function scrollSourceToLayoutBlock(block, behavior = 'auto') {
    const surface = sourcePageSurface(block?.page);
    if (!surface?.container || !surface.element) return;
    const [x1, y1, x2, y2] = block.bbox.map(Number);
    const sourceWidth = surface.element.clientWidth || surface.element.width || surface.element.naturalWidth || surface.container.offsetWidth;
    const sourceHeight = surface.element.clientHeight || surface.element.height || surface.element.naturalHeight || surface.container.offsetHeight;
    const centerY = ((y1 + y2) / 2 / Math.max(Number(block.pageHeight), 1)) * sourceHeight;
    const centerX = ((x1 + x2) / 2 / Math.max(Number(block.pageWidth), 1)) * sourceWidth;
    const targetTop = sourcePageTop(surface.container) + centerY - (els.sourceViewer.clientHeight * 0.42);
    const targetLeft = centerX - (els.sourceViewer.clientWidth * 0.5);
    withSplitScrollLock(() => {
        els.sourceViewer.scrollTo({
            top: Math.max(targetTop, 0),
            left: Math.max(targetLeft, 0),
            behavior
        });
    });
    currentPage = Number(block.page || currentPage);
    updatePdfControls();
}

function scrollResultToLinkedEntry(entry, behavior = 'auto') {
    if (!entry?.element?.isConnected) return;
    const elementRect = entry.element.getBoundingClientRect();
    const containerRect = els.markdownView.getBoundingClientRect();
    const targetTop = els.markdownView.scrollTop + elementRect.top - containerRect.top - (els.markdownView.clientHeight * 0.28);
    withSplitScrollLock(() => {
        els.markdownView.scrollTo({
            top: Math.max(targetTop, 0),
            left: 0,
            behavior
        });
    });
}

function collectMarkdownBlockElements(container) {
    const selector = 'h1,h2,h3,h4,h5,h6,p,li,table,pre,blockquote,div,img';
    const seen = new Set();
    const elements = [];

    Array.from(container.querySelectorAll(selector)).forEach((element) => {
        if (element.closest('.empty-result')) return false;
        if (element.parentElement?.closest('li,table,pre,blockquote')) return false;
        if (element.tagName === 'DIV' && !isMarkdownImageBlock(element) && !isFigureTitleText(element.innerText || element.textContent || '')) {
            return false;
        }

        const imageHost = element.tagName === 'IMG' ? element.closest('p,div') || element : element;
        const target = ['P', 'DIV'].includes(imageHost.tagName) && imageHost.querySelector('img') ? imageHost : element;
        if (seen.has(target)) return false;

        const hasText = Boolean((target.innerText || target.textContent || '').trim());
        const hasImage = isMarkdownImageBlock(target);
        if (!hasText && !hasImage) return false;

        seen.add(target);
        elements.push(target);
        return true;
    });

    return elements;
}

function isMarkdownImageBlock(element) {
    return element?.tagName === 'IMG' || Boolean(element?.querySelector?.('img'));
}

function isFigureTitleText(value) {
    return /^Figure\s+\d+(?=\s|$|[:：])/i.test(String(value || '').trim());
}

function isAlgorithmText(value) {
    return /^Algorithm\s+\d+(?=\s|$|[:：])/i.test(String(value || '').trim());
}

function findBestLayoutBlock(text, blocks, cursor) {
    let best = null;
    const start = Math.max(0, cursor - 1);
    const end = Math.min(blocks.length, cursor + 18);

    for (let index = start; index < end; index += 1) {
        const score = matchScore(text, blocks[index].text);
        if (score < 0.55) continue;
        if (!best || score > best.score) best = { index, block: blocks[index], score };
        if (score >= 0.92) break;
    }

    return best;
}

function findNextLayoutBlockByLabel(blocks, cursor, labels) {
    const wanted = new Set(labels);
    const start = Math.max(0, cursor - 1);
    for (let index = start; index < blocks.length; index += 1) {
        if (wanted.has(String(blocks[index].label || '').toLowerCase())) {
            return { index, block: blocks[index], score: 1 };
        }
    }
    return null;
}

function matchScore(elementText, blockText) {
    if (!elementText || !blockText) return 0;
    if (elementText === blockText) return 1;
    if (blockText.includes(elementText)) return Math.min(0.98, elementText.length / Math.max(blockText.length, 1) + 0.62);
    if (elementText.includes(blockText)) return Math.min(0.96, blockText.length / Math.max(elementText.length, 1) + 0.55);

    const words = elementText.split(' ').filter((word) => word.length > 2);
    if (!words.length) return 0;
    const hitCount = words.filter((word) => blockText.includes(word)).length;
    return hitCount / words.length;
}

function normalizeMatchText(value) {
    return String(value)
        .replace(/\$+/g, ' ')
        .replace(/\\[a-zA-Z]+/g, ' ')
        .replace(/[{}^_`~|()[\]<>#*_.,:;'"!?，。；：！？、]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .toLowerCase();
}

function showSourceHighlight(block) {
    clearSourceHighlight();
    const surface = sourcePageSurface(block.page);
    if (!surface) return;

    const box = document.createElement('div');
    box.className = 'source-highlight-box';
    positionSourceOverlayBox(box, block, surface.element);
    const label = document.createElement('span');
    label.className = 'source-highlight-label';
    label.textContent = layoutLabelText(block.label);
    box.appendChild(label);
    surface.layer.appendChild(box);

}

function showStreamingSourceHighlight(position) {
    clearSourceHighlight();
    const pageNumber = Math.max(1, Number(position?.pageNumber || 1));
    const surface = sourcePageSurface(pageNumber);
    if (!surface?.element || !surface.layer) return;

    const box = document.createElement('div');
    box.className = 'source-highlight-box source-highlight-box-streaming';
    if (Array.isArray(position.bbox) && position.bbox.length >= 4) {
        positionSourceOverlayBox(box, {
            bbox: position.bbox,
            pageWidth: Number(position.pageWidth || UNLIMITED_OCR_COORDINATE_SIZE),
            pageHeight: Number(position.pageHeight || UNLIMITED_OCR_COORDINATE_SIZE)
        }, surface.element);
    } else {
        const sourceWidth = surface.element.clientWidth || surface.element.width || surface.element.naturalWidth || surface.container.offsetWidth;
        const sourceHeight = surface.element.clientHeight || surface.element.height || surface.element.naturalHeight || surface.container.offsetHeight;
        const pageProgress = Math.min(Math.max(Number(position?.pageProgress || 0), 0), 1);
        const bandHeight = Math.min(Math.max(sourceHeight * 0.08, 42), 86);
        const top = Math.min(Math.max((pageProgress * sourceHeight) - (bandHeight / 2), 0), Math.max(sourceHeight - bandHeight, 0));
        box.style.left = '0px';
        box.style.top = `${top}px`;
        box.style.width = `${sourceWidth}px`;
        box.style.height = `${bandHeight}px`;
    }

    const label = document.createElement('span');
    label.className = 'source-highlight-label';
    label.textContent = position?.label ? layoutLabelText(position.label) : t('解析中');
    box.appendChild(label);
    surface.layer.appendChild(box);
}

function showPPOCRSourceHighlight(line) {
    if (!line?.box || !line.pageWidth || !line.pageHeight) return;
    clearSourceHighlight();
    const surface = sourcePageSurface(line.sourcePage);
    if (!surface) return;

    const box = document.createElement('div');
    box.className = 'source-highlight-box source-highlight-box-ocr';
    positionSourceOverlayBox(box, {
        bbox: line.box,
        pageWidth: line.pageWidth,
        pageHeight: line.pageHeight
    }, surface.element);
    surface.layer.appendChild(box);
}

function clearSourceHighlight() {
    els.sourceViewer.querySelectorAll('.source-highlight-box').forEach((box) => box.remove());
}

function addPPOCRSourceHotspot(line, markdownElement, toolbar) {
    if (!line?.box || !line.pageWidth || !line.pageHeight) return;
    const surface = sourcePageSurface(line.sourcePage);
    if (!surface) return;

    const hotspot = document.createElement('button');
    hotspot.type = 'button';
    hotspot.className = 'source-link-hotspot source-ocr-hotspot';
    hotspot.setAttribute('aria-label', line.text || 'OCR');
    hotspot.dataset.page = String(line.sourcePage || '');
    hotspot.dataset.pageResultIndex = String(line.pageResultIndex ?? '');
    hotspot.dataset.lineIndex = String(line.index ?? '');
    positionSourceOverlayBox(hotspot, {
        bbox: line.box,
        pageWidth: line.pageWidth,
        pageHeight: line.pageHeight
    }, surface.element);

    const preview = () => {
        activatePPOCRLine(markdownElement, toolbar, line, { scrollSource: false });
    };
    const locate = () => {
        scrollElementIntoContainer(markdownElement, els.markdownView, 'smooth');
        activatePPOCRLine(markdownElement, toolbar, line, { scrollSource: false });
    };
    hotspot.addEventListener('mouseenter', preview);
    hotspot.addEventListener('mouseover', preview);
    hotspot.addEventListener('pointerenter', preview);
    hotspot.addEventListener('focusin', preview);
    hotspot.addEventListener('click', locate);
    surface.layer.appendChild(hotspot);
}

function addSourceHotspot(block, markdownElement) {
    const surface = sourcePageSurface(block.page);
    if (!surface) return;

    const hotspot = document.createElement('button');
    hotspot.type = 'button';
    hotspot.className = 'source-link-hotspot';
    hotspot.setAttribute('aria-label', layoutLabelText(block.label));
    positionSourceOverlayBox(hotspot, block, surface.element);

    const preview = () => activateLinkedBlock(markdownElement, block);
    const locate = () => activateLinkedBlock(markdownElement, block, { scrollMarkdown: true });
    const deactivate = () => deactivateLinkedBlocks();
    hotspot.addEventListener('mouseenter', preview);
    hotspot.addEventListener('mouseover', preview);
    hotspot.addEventListener('pointerenter', preview);
    hotspot.addEventListener('focusin', preview);
    hotspot.addEventListener('click', locate);
    hotspot.addEventListener('mouseleave', deactivate);
    hotspot.addEventListener('pointerleave', deactivate);
    hotspot.addEventListener('focusout', deactivate);

    surface.layer.appendChild(hotspot);
}

function sourcePageSurface(pageNumber = 1) {
    const page = String(pageNumber || 1);
    const pdfPage = els.sourceViewer.querySelector(`.pdf-page-wrap[data-page="${page}"]`);
    if (pdfPage) {
        const element = pdfPage.querySelector('canvas');
        const layer = pdfPage.querySelector('.pdf-highlight-layer');
        if (element && layer) return { container: pdfPage, element, layer };
    }

    const imagePage = els.sourceViewer.querySelector(`.source-image-wrap[data-page="${page}"]`);
    if (imagePage) {
        const element = imagePage.querySelector('.source-image');
        const layer = imagePage.querySelector('.pdf-highlight-layer');
        if (element && layer) return { container: imagePage, element, layer };
    }

    return null;
}

function positionSourceOverlayBox(element, block, sourceElement) {
    const [x1, y1, x2, y2] = block.bbox.map(Number);
    const sourceWidth = sourceElement.clientWidth || sourceElement.width || sourceElement.naturalWidth;
    const sourceHeight = sourceElement.clientHeight || sourceElement.height || sourceElement.naturalHeight;
    element.style.left = `${(x1 / block.pageWidth) * sourceWidth}px`;
    element.style.top = `${(y1 / block.pageHeight) * sourceHeight}px`;
    element.style.width = `${((x2 - x1) / block.pageWidth) * sourceWidth}px`;
    element.style.height = `${((y2 - y1) / block.pageHeight) * sourceHeight}px`;
}

function activateLinkedBlock(markdownElement, block, { scrollMarkdown = false, scrollSource = false } = {}) {
    deactivateLinkedBlocks();
    markdownElement.classList.add('layout-linked-block-active');
    showSourceHighlight(block);
    if (scrollMarkdown && !isElementMostlyVisible(markdownElement, els.markdownView)) {
        scrollElementIntoContainer(markdownElement, els.markdownView, 'smooth');
    }
    if (scrollSource) {
        const sourceSurface = sourcePageSurface(block.page);
        if (sourceSurface?.container && !isElementMostlyVisible(sourceSurface.container, els.sourceViewer)) {
            scrollPdfPageIntoView(block.page, 'smooth');
        }
    }
}

function deactivateLinkedBlocks() {
    els.markdownView.querySelectorAll('.layout-linked-block-active').forEach((element) => {
        element.classList.remove('layout-linked-block-active');
    });
    clearSourceHighlight();
}

function clearSourceHotspots() {
    els.sourceViewer.querySelectorAll('.source-link-hotspot').forEach((hotspot) => hotspot.remove());
}

function isElementMostlyVisible(element, container) {
    const elementRect = element.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    return elementRect.top >= containerRect.top - 20 && elementRect.top <= containerRect.bottom - 80;
}

function scrollElementIntoContainer(element, container, behavior = 'smooth') {
    const elementRect = element.getBoundingClientRect();
    const containerRect = container.getBoundingClientRect();
    const offset = elementRect.top - containerRect.top;
    const centeredTop = container.scrollTop + offset - (container.clientHeight / 2) + (elementRect.height / 2);
    container.scrollTo({ top: Math.max(centeredTop, 0), behavior });
}

function layoutLabelText(label) {
    const normalized = String(label || '').trim().toLowerCase();
    const labels = {
        abstract: '摘要',
        doc_title: '标题',
        title: '标题',
        paragraph_title: '段落标题',
        text: '文本',
        image: '图片',
        figure_title: '图表标题',
        table: '表格',
        formula: '公式',
        display_formula: '行间公式',
        formula_number: '公式编号',
        footer: '页脚',
        header: '页眉',
        number: '页码',
        reference: '参考文献',
        reference_content: '参考文献',
        reference_text: '参考文献',
        reference_title: '参考文献标题',
        ref_text: '参考文献',
        ref_title: '参考文献标题',
        footnote: '脚注',
        algorithm: '算法',
        chart: '图表'
    };
    const unlimitedLabels = {
        image_caption: '图片说明',
        figure_caption: '图注',
        table_caption: '表格说明',
        equation: '公式',
        page_number: '页码',
        page_num: '页码',
        header_image: '页眉图片',
        footer_image: '页脚图片',
        section_title: '章节标题',
        subtitle: '副标题',
        seal: '印章'
    };
    return labels[normalized]
        ? t(labels[normalized])
        : (unlimitedLabels[normalized] ? t(unlimitedLabels[normalized]) : (normalized || t('版面块')));
}

function prepareBatchResult(result, batchId, existingImagePathMap = {}) {
    let markdown = normalizeOCRMarkdown(result.markdown || '');
    const images = {};
    const imagePathMap = { ...(existingImagePathMap || {}) };
    Object.entries(result.images || {}).forEach(([path, base64]) => {
        const safePath = imagePathMap[path] || safeImagePath(batchId, path);
        imagePathMap[path] = safePath;
        images[safePath] = base64;
    });
    Object.entries(imagePathMap).forEach(([path, safePath]) => {
        markdown = markdown.split(path).join(safePath);
    });
    return { markdown, images, imagePathMap };
}

function safeImagePath(batchId, path) {
    const filename = String(path || '').split('/').pop() || 'image';
    return `ocr_images/${batchId}_${filename}`;
}

function compactOCRJsonResult(pageResult, batchOrId, pageIndex = 0) {
    const batch = typeof batchOrId === 'object' ? batchOrId : null;
    const batchId = batch?.id || batchOrId;
    const compact = stripLargeOCRFields(pageResult);
    if (batch && ['pp-ocrv6', 'unlimited-ocr', OVIS_OCR_MODEL_ID, HPD_PARSING_MODEL_ID].includes(compact?.parser)) {
        compact.sourcePage = Number(batch.startPage || 1) + pageIndex;
        compact.batchId = batch.id;
    }
    rewriteMarkdownImageMaps(compact, batchId);
    return compact;
}

function stripLargeOCRFields(value) {
    if (Array.isArray(value)) {
        return value.map(stripLargeOCRFields);
    }
    if (!value || typeof value !== 'object') {
        return value;
    }

    const output = {};
    Object.entries(value).forEach(([key, nestedValue]) => {
        if (key === 'inputImage' || key === 'outputImages') return;
        output[key] = stripLargeOCRFields(nestedValue);
    });
    return output;
}

function rewriteMarkdownImageMaps(value, batchId) {
    if (!value || typeof value !== 'object') return;
    if (value.images && typeof value.images === 'object' && typeof value.text === 'string') {
        value.images = Object.fromEntries(
            Object.keys(value.images).map((path) => [path, safeImagePath(batchId, path)])
        );
    }
    Object.values(value).forEach((nestedValue) => rewriteMarkdownImageMaps(nestedValue, batchId));
}

function normalizeOCRJsonResults(result) {
    if (Array.isArray(result.layoutParsingResults)) {
        return result.layoutParsingResults;
    }
    if (Array.isArray(result.pages)) {
        return result.pages;
    }
    if (Array.isArray(result.results)) {
        return result.results;
    }
    return [{
        markdown: {
            text: result.markdown || '',
            images: result.images || {}
        }
    }];
}

function toOfficialJson(task) {
    if (Array.isArray(task.ocrResults) && task.ocrResults.length > 0) {
        return task.ocrResults;
    }

    return [];
}

function readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

async function waitForImageReady(img) {
    if (!img || (img.complete && img.naturalWidth > 0)) return;
    if (typeof img.decode === 'function') {
        try {
            await img.decode();
            return;
        } catch (error) {
            if (img.complete) return;
        }
    }
    await new Promise((resolve) => {
        img.addEventListener('load', resolve, { once: true });
        img.addEventListener('error', resolve, { once: true });
    });
}

function dataUrlToUint8Array(dataUrl) {
    const base64 = dataUrl.split('base64,')[1];
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

function dataUrlToBlob(dataUrl) {
    const mimeMatch = String(dataUrl).match(/^data:([^;,]+)[;,]/i);
    return new Blob([dataUrlToUint8Array(dataUrl)], {
        type: mimeMatch?.[1] || 'application/octet-stream'
    });
}

function base64ToBytes(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

function emptyDropZoneHtml() {
    return `
        <div class="drop-zone" id="drop-zone">
            <svg viewBox="0 0 24 24"><path d="M12 3v12M7 8l5-5 5 5M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/></svg>
            <h3>${escapeHtml(t('拖拽文件到这里'))}</h3>
            <p>${escapeHtml(t('支持 PDF、图片、PPT/PPTX、DOC/DOCX；PDF 会逐页解析。'))}</p>
            <button class="primary-button" id="browse-btn">${escapeHtml(t('选择文件'))}</button>
        </div>
    `;
}

function taskIcon(task) {
    if (task.sourceKind === 'image') {
        return '<svg viewBox="0 0 24 24"><path d="M4 5h16v14H4z"/><path d="m4 16 5-5 4 4 2-2 5 5"/><circle cx="16" cy="9" r="1.5"/></svg>';
    }
    return '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h6"/></svg>';
}

function statusText(task) {
    const donePages = task.batches?.filter((batch) => batch.status === 'completed').reduce((sum, batch) => sum + batch.pageCount, 0) || task.completedPages || 0;
    if (task.status === 'completed') return t('完成');
    if (isTaskActivelyProcessing(task)) return t('{done}/{total} 解析中', { done: donePages, total: task.pageCount || 1 });
    if (shouldResumeTask(task)) return t('{done}/{total} 可继续', { done: donePages, total: task.pageCount || 1 });
    if (task.status === 'error') return t('失败');
    return t('待解析');
}

function resultPaneTitle(task) {
    if (task.status === 'completed') return t('解析结果');
    if (task.status === 'processing') return t('解析中');
    if (shouldResumeTask(task)) return t('解析中断');
    if (task.status === 'error') return t('解析失败');
    return t('待解析');
}

function emptyResultText(task) {
    if (task.status === 'processing') {
        const batch = task.batches?.find((item) => item.status === 'processing');
        if (batch?._progressStartedAt && isOvisOCR2Task(task)) {
            return t(
                'OvisOCR2 正在解析{label}，已用时 {seconds} 秒。苹果端逐页处理，完成后会自动显示结果。',
                {
                    label: batch.label || '',
                    seconds: taskProgressSeconds(task)
                }
            );
        }
        return t('正在解析，结果会实时追加到这里。');
    }
    if (shouldResumeTask(task)) return t('上次解析中断，点击“继续解析”从未完成页面恢复。');
    if (task.status === 'error') return t('解析失败：{detail}', { detail: task.error || t('未知错误') });
    return t('点击“开始解析”生成 Markdown 或 JSON 结果。');
}

function taskProgressSeconds(task) {
    const batch = task?.batches?.find((item) => item.status === 'processing');
    if (!batch?._progressStartedAt) return 0;
    return Math.max(0, Math.floor((Date.now() - Number(batch._progressStartedAt)) / 1000));
}

function sourceLabel(task) {
    if (task.sourceKind === 'office') return t('Office 已转 PDF · {name}', { name: task.originalName });
    if (task.sourceKind === 'image') return t('图片');
    return 'PDF';
}

function taskSourceMeta(task) {
    return `${sourceLabel(task)} · ${formatSize(task.size)} · ${formatPageCount(task.pageCount || 1)}`;
}

function formatPageCount(count) {
    if (currentLanguage === 'en') {
        return `${count} ${Number(count) === 1 ? 'page' : 'pages'}`;
    }
    return t('{count} 页', { count });
}

function getExtension(filename) {
    return filename.split('.').pop().toLowerCase();
}

function initPdfBatchSizeSetting() {
    if (!els.pdfBatchSizeInput) return;
    syncPdfBatchSizeSetting();
}

function applyModelBatchSizeRecommendation() {
    if (!els.pdfBatchSizeInput || selectedModelId !== UNLIMITED_OCR_MODEL_ID) return;
    const currentBatchSize = clampPdfBatchSize(els.pdfBatchSizeInput.value || localStorage.getItem(PDF_BATCH_SIZE_STORAGE_KEY));
    if (currentBatchSize <= UNLIMITED_OCR_RECOMMENDED_PDF_BATCH_SIZE) return;
    els.pdfBatchSizeInput.value = String(UNLIMITED_OCR_RECOMMENDED_PDF_BATCH_SIZE);
    localStorage.setItem(PDF_BATCH_SIZE_STORAGE_KEY, String(UNLIMITED_OCR_RECOMMENDED_PDF_BATCH_SIZE));
}

function syncPdfBatchSizeSetting() {
    if (!els.pdfBatchSizeInput) return DEFAULT_PDF_BATCH_SIZE;
    const batchSize = getConfiguredPdfBatchSize();
    els.pdfBatchSizeInput.value = String(batchSize);
    localStorage.setItem(PDF_BATCH_SIZE_STORAGE_KEY, String(batchSize));
    return batchSize;
}

function handlePdfBatchSizeInput() {
    if (!els.pdfBatchSizeInput) return;
    const rawValue = els.pdfBatchSizeInput.value;
    if (rawValue === '') return;
    const parsed = Number.parseInt(rawValue, 10);
    if (!Number.isFinite(parsed)) return;
    const batchSize = clampPdfBatchSize(parsed);
    if (String(parsed) !== String(batchSize)) {
        els.pdfBatchSizeInput.value = String(batchSize);
    }
    localStorage.setItem(PDF_BATCH_SIZE_STORAGE_KEY, String(batchSize));
}

function getConfiguredPdfBatchSize() {
    const rawValue = els.pdfBatchSizeInput?.value || localStorage.getItem(PDF_BATCH_SIZE_STORAGE_KEY);
    return clampPdfBatchSize(rawValue);
}

function clampPdfBatchSize(value) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed)) return DEFAULT_PDF_BATCH_SIZE;
    return Math.min(MAX_PDF_BATCH_SIZE, Math.max(1, parsed));
}

function formatDate(timestamp) {
    const date = new Date(timestamp);
    const now = new Date();
    const sameYear = date.getFullYear() === now.getFullYear();
    return date.toLocaleString(languageLocale(), {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        ...(sameYear ? {} : { year: 'numeric' })
    });
}

function formatSize(bytes = 0) {
    if (!bytes) return t('未知大小');
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / 1024 / 1024).toFixed(2)}MB`;
}

function formatPageLabel(startPage, endPage = startPage) {
    return startPage === endPage
        ? t('第 {start} 页', { start: startPage })
        : t('第 {start}-{end} 页', { start: startPage, end: endPage });
}

function safeDownloadName(name, ext) {
    return `${name.replace(/\.[^.]+$/, '').replace(/[\\/:*?"<>|]/g, '_')}.${ext}`;
}

function createId() {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
