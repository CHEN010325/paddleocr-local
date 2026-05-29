const API_BASE = '/api';
const PDF_BATCH_SIZE = 1;

let availableModel = 'PaddleOCR-VL-1.6-0.9B';
let tasks = [];
let activeTaskId = null;
let activeFilter = 'all';
let activeResultView = 'markdown';
let isProcessing = false;
let currentPdf = null;
let currentPage = 1;
let currentZoom = 1.15;
let sourceRenderToken = 0;

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
    statusDot: document.getElementById('model-status-dot'),
    statusText: document.getElementById('model-status-text'),
    activeModelName: document.getElementById('active-model-name'),
    sourceTitle: document.getElementById('source-title'),
    sourceMeta: document.getElementById('source-meta'),
    sourceViewer: document.getElementById('source-viewer'),
    pdfControls: document.getElementById('pdf-controls'),
    pageIndicator: document.getElementById('page-indicator'),
    prevPageBtn: document.getElementById('prev-page-btn'),
    nextPageBtn: document.getElementById('next-page-btn'),
    zoomInBtn: document.getElementById('zoom-in-btn'),
    zoomOutBtn: document.getElementById('zoom-out-btn'),
    resultTitle: document.getElementById('result-title'),
    startBtn: document.getElementById('start-btn'),
    copyBtn: document.getElementById('copy-btn'),
    downloadBtn: document.getElementById('download-btn'),
    markdownView: document.getElementById('markdown-view'),
    jsonView: document.getElementById('json-view'),
    chartRecognitionSwitch: document.getElementById('chart-recognition-switch'),
    docUnwarpingSwitch: document.getElementById('doc-unwarping-switch'),
    docOrientationSwitch: document.getElementById('doc-orientation-switch'),
    sealRecognitionSwitch: document.getElementById('seal-recognition-switch'),
    formulaNumberSwitch: document.getElementById('formula-number-switch'),
    ignoreHeaderSwitch: document.getElementById('ignore-header-switch'),
    ignoreFooterSwitch: document.getElementById('ignore-footer-switch'),
    ignoreNumberSwitch: document.getElementById('ignore-number-switch'),
    taskTemplate: document.getElementById('task-item-template')
};

document.addEventListener('DOMContentLoaded', async () => {
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
    setupEventListeners();
    await checkBackendConnection();
    await loadTasks();
    renderTaskList();
    if (tasks.length > 0) {
        selectTask(tasks[0].id);
    }
});

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
            els.dropZone?.classList.add('drag-over');
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
    els.copyBtn.addEventListener('click', copyActiveMarkdown);
    els.downloadBtn.addEventListener('click', downloadActiveTask);
    els.prevPageBtn.addEventListener('click', () => changePdfPage(-1));
    els.nextPageBtn.addEventListener('click', () => changePdfPage(1));
    els.zoomInBtn.addEventListener('click', () => changeZoom(0.15));
    els.zoomOutBtn.addEventListener('click', () => changeZoom(-0.15));
    els.sourceViewer.addEventListener('scroll', updateCurrentPageFromScroll);

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
            document.querySelectorAll('.view-tab').forEach((tab) => tab.classList.remove('active'));
            button.classList.add('active');
            activeResultView = button.dataset.view;
            renderResultPane(getActiveTask());
        });
    });
}

async function checkBackendConnection() {
    try {
        const response = await fetch(`${API_BASE}/models`);
        if (!response.ok) throw new Error('API Error');
        const data = await response.json();
        availableModel = data.data?.[0]?.id || availableModel;
        els.statusDot.className = 'dot connected';
        els.statusText.textContent = availableModel;
        els.activeModelName.textContent = availableModel.replace('PaddleOCR-', '');
    } catch (error) {
        els.statusDot.className = 'dot error';
        els.statusText.textContent = '模型未连接';
        setTimeout(checkBackendConnection, 5000);
    }
}

async function saveTask(task) {
    await saveTaskToServer(task);
}

async function saveTaskToServer(task) {
    const response = await fetch(`${API_BASE}/tasks/${encodeURIComponent(task.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(task)
    });
    if (!response.ok) {
        throw new Error(`保存本地任务失败：${await response.text()}`);
    }
}

async function loadTasks() {
    const localTasks = await loadServerTasks();
    tasks = dedupeTasks(localTasks.map(reconcileTaskStatus));
}

function reconcileTaskStatus(task) {
    const batches = Array.isArray(task.batches) ? task.batches : [];
    const allBatchesCompleted = batches.length > 0 && batches.every((batch) => batch.status === 'completed');
    const hasAllOcrResults = Array.isArray(task.ocrResults) && task.ocrResults.length >= batches.length;
    if (task.status === 'processing' && allBatchesCompleted && hasAllOcrResults) {
        return { ...task, status: 'completed', updatedAt: task.updatedAt || Date.now() };
    }
    return task;
}

function dedupeTasks(taskItems) {
    const byFingerprint = new Map();
    taskItems.forEach((task) => {
        const fingerprint = [
            task.name,
            task.originalName || '',
            task.sourceKind || '',
            task.size || 0,
            task.pageCount || 0
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
        const response = await fetch(`${API_BASE}/tasks`);
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        return data.tasks || [];
    } catch (error) {
        console.warn('读取本地任务目录失败', error);
        return [];
    }
}

async function deleteAllTasks() {
    const response = await fetch(`${API_BASE}/tasks`, { method: 'DELETE' });
    if (!response.ok) {
        throw new Error(`清空本地任务失败：${await response.text()}`);
    }
}

async function deleteTaskById(taskId) {
    const response = await fetch(`${API_BASE}/tasks/${encodeURIComponent(taskId)}`, { method: 'DELETE' });
    if (!response.ok) {
        throw new Error(`删除本地任务失败：${await response.text()}`);
    }
}

async function handleFiles(files) {
    if (!files || files.length === 0) return;

    const fileList = Array.from(files);
    const results = await Promise.allSettled(fileList.map((file) => createTaskFromFile(file)));
    const newTasks = results
        .filter((result) => result.status === 'fulfilled')
        .map((result) => result.value);
    const failed = results.filter((result) => result.status === 'rejected');

    if (failed.length > 0) {
        console.warn('Some files could not be added', failed.map((result) => result.reason));
    }
    if (newTasks.length === 0) return;

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

async function createTaskFromFile(file) {
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

    alert(`不支持的文件格式：${file.name}`);
    throw new Error(`Unsupported file type: ${file.name}`);
}

async function createImageTask(file) {
    const dataUrl = await readAsDataUrl(file);
    const now = Date.now();
    return {
        id: createId(),
        name: file.name,
        sourceKind: 'image',
        mimeType: file.type || 'image/*',
        size: file.size,
        createdAt: now,
        updatedAt: now,
        status: 'pending',
        pageCount: 1,
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
}

async function createPdfTask(fileOrBlob, name, extra = {}) {
    const arrayBuffer = await fileOrBlob.arrayBuffer();
    const sourceDataUrl = arrayBufferToDataUrl(arrayBuffer, 'application/pdf');
    const pdf = await pdfjsLib.getDocument({ data: arrayBuffer.slice(0) }).promise;
    const pageCount = pdf.numPages;
    const thumbnail = await renderPDFPageDataUrl(pdf, 1, 0.35);
    const sourcePdf = pageCount > 1
        ? await PDFLib.PDFDocument.load(arrayBuffer.slice(0))
        : null;
    const batches = [];

    for (let startPage = 1; startPage <= pageCount; startPage += PDF_BATCH_SIZE) {
        const endPage = Math.min(startPage + PDF_BATCH_SIZE - 1, pageCount);
        const batchPageCount = endPage - startPage + 1;
        const payloadDataUrl = pageCount === 1
            ? sourceDataUrl
            : await createPDFBatchDataUrl(sourcePdf, startPage, endPage);
        batches.push({
            id: createId(),
            label: formatPageLabel(startPage, endPage),
            fileType: 0,
            startPage,
            endPage,
            pageCount: batchPageCount,
            payloadDataUrl,
            status: 'pending'
        });
    }

    const now = Date.now();
    return {
        id: createId(),
        name,
        sourceKind: extra.sourceKind || 'pdf',
        originalName: extra.originalName || name,
        mimeType: 'application/pdf',
        size: fileOrBlob.size || arrayBuffer.byteLength,
        createdAt: now,
        updatedAt: now,
        status: 'pending',
        pageCount,
        sourceDataUrl,
        thumbnail,
        batches,
        markdown: '',
        images: {},
        ocrResults: []
    };
}

async function convertOfficeToPdf(file) {
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${API_BASE}/convert/to-pdf`, {
        method: 'POST',
        body: formData
    });
    if (!response.ok) {
        const detail = await response.text();
        throw new Error(`Office 转 PDF 失败：${detail}`);
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
        els.taskList.innerHTML = '<div class="task-empty">暂无任务</div>';
        return;
    }

    for (const task of visibleTasks) {
        const clone = els.taskTemplate.content.cloneNode(true);
        const item = clone.querySelector('.task-item');
        item.dataset.taskId = task.id;
        item.classList.toggle('active', task.id === activeTaskId);
        item.classList.add(`status-${task.status}`);
        item.querySelector('.task-icon').innerHTML = taskIcon(task);
        item.querySelector('.task-name').textContent = task.name;
        item.querySelector('.task-meta').textContent = `${formatDate(task.updatedAt)} · ${task.pageCount || 1} 页`;
        item.querySelector('.task-state').textContent = statusText(task);
        item.addEventListener('click', () => selectTask(task.id));
        item.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectTask(task.id);
            }
        });
        item.querySelector('.task-delete').addEventListener('click', async (event) => {
            event.stopPropagation();
            await deleteTask(task.id);
        });
        els.taskList.appendChild(item);
    }
}

async function deleteTask(taskId) {
    const task = tasks.find((item) => item.id === taskId);
    if (!task) return;
    if (task.status === 'processing') {
        alert('当前文件正在解析中，完成后再删除。');
        return;
    }
    if (!confirm('确定要删除“' + task.name + '”吗？当前操作不可回撤。')) return;

    const wasActive = activeTaskId === taskId;
    try {
        await deleteTaskById(taskId);
    } catch (error) {
        console.error(error);
        alert(error.message || '删除失败，请稍后重试。');
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
    const task = getActiveTask();
    if (!task) return;
    els.sourceTitle.textContent = task.name;
    els.sourceMeta.textContent = `${sourceLabel(task)} · ${formatSize(task.size)} · ${task.pageCount || 1} 页`;
    els.resultTitle.textContent = task.status === 'completed' ? '解析结果' : statusText(task);
    await renderSource(task);
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

    if (task.sourceKind === 'image') {
        const img = document.createElement('img');
        img.className = 'source-image';
        img.src = task.sourceDataUrl;
        els.sourceViewer.appendChild(img);
        return;
    }

    currentPage = Math.min(Math.max(currentPage, 1), task.pageCount || 1);
    els.pdfControls.classList.remove('hidden');
    const bytes = dataUrlToUint8Array(task.sourceDataUrl);
    const pdf = await pdfjsLib.getDocument({ data: bytes }).promise;
    if (renderToken !== sourceRenderToken) return;
    currentPdf = pdf;
    await renderPdfDocument(renderToken);
}

async function renderPdfDocument(renderToken = sourceRenderToken) {
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

    scrollPdfPageIntoView(currentPage, 'auto');
    updateCurrentPageFromScroll();
}

function renderResultPane(task) {
    if (!task) {
        els.resultTitle.textContent = '解析结果';
        els.markdownView.innerHTML = '<div class="empty-result">选择左侧任务，或上传一个新文件开始解析。</div>';
        els.jsonView.textContent = '';
        return;
    }

    els.resultTitle.textContent = resultPaneTitle(task);

    if (activeResultView === 'json') {
        els.markdownView.classList.add('hidden');
        els.jsonView.classList.remove('hidden');
        els.jsonView.textContent = JSON.stringify(toOfficialJson(task), null, 2);
        return;
    }

    els.jsonView.classList.add('hidden');
    els.markdownView.classList.remove('hidden');

    if (renderOfficialLayoutResult(task)) {
        renderMathWhenReady(els.markdownView);
        return;
    }

    const markdown = prepareMarkdownForRender(task.markdown || '');
    if (!markdown) {
        clearSourceHighlight();
        clearSourceHotspots();
        els.markdownView.innerHTML = `<div class="empty-result">${emptyResultText(task)}</div>`;
        return;
    }

    let renderMarkdown = markdown;
    Object.entries(task.images || {}).forEach(([path, base64]) => {
        renderMarkdown = renderMarkdown.split(path).join(`data:image/jpeg;base64,${base64}`);
    });
    const html = renderMarkdownHtml(renderMarkdown);
    els.markdownView.innerHTML = html;
    linkMarkdownToSourceBlocks(task);
    renderMathWhenReady(els.markdownView);
}

async function processActiveTask() {
    const task = getActiveTask();
    await processTask(task, { confirmCompleted: true });
}

async function processTask(task, { confirmCompleted = true } = {}) {
    if (!task || isProcessing) return;
    if (confirmCompleted && task.status === 'completed' && !confirm('这个任务已经解析完成，要重新解析吗？')) return;

    isProcessing = true;
    try {
        task.status = 'processing';
        task.markdown = '';
        task.images = {};
        task.ocrResults = [];
        task.batches.forEach((batch) => {
            batch.status = 'pending';
            batch.markdown = '';
        });
        task.updatedAt = Date.now();
        await saveTask(task);
        refreshTaskUi(task);

        for (const batch of task.batches) {
            batch.status = 'processing';
            task.updatedAt = Date.now();
            refreshTaskUi(task);

            const result = await callVLLM(batch);
            const prepared = prepareBatchResult(result, batch.id);
            batch.status = 'completed';
            batch.markdown = prepared.markdown;
            task.markdown += `${prepared.markdown}\n\n`;
            Object.assign(task.images, prepared.images);
            task.ocrResults.push(...normalizeOCRJsonResults(result));
            task.updatedAt = Date.now();
            await saveTask(task);
            refreshTaskUi(task);
        }
        task.status = 'completed';
    } catch (error) {
        console.error(error);
        task.status = 'error';
        task.error = error.message;
    } finally {
        isProcessing = false;
        task.updatedAt = Date.now();
        await saveTask(task);
        refreshTaskUi(task);
    }
}

function refreshTaskUi(task) {
    renderTaskList();
    const activeTask = getActiveTask();
    if (task?.id === activeTaskId) {
        renderResultPane(task);
    }
    updateActionState(activeTask);
}

async function callVLLM(batch) {
    const ignoreLabels = [];
    if (els.ignoreNumberSwitch.checked) ignoreLabels.push('number');
    ignoreLabels.push('footnote');
    if (els.ignoreHeaderSwitch.checked) ignoreLabels.push('header', 'header_image');
    if (els.ignoreFooterSwitch.checked) ignoreLabels.push('footer', 'footer_image');
    ignoreLabels.push('aside_text');

    const payload = {
        image: batch.payloadDataUrl,
        fileType: batch.fileType,
        useLayoutDetection: true,
        useChartRecognition: els.chartRecognitionSwitch.checked,
        useDocUnwarping: els.docUnwarpingSwitch.checked,
        useDocOrientationClassify: els.docOrientationSwitch.checked,
        useSealRecognition: els.sealRecognitionSwitch.checked,
        formatBlockContent: true,
        showFormulaNumber: els.formulaNumberSwitch.checked,
        markdownIgnoreLabels: ignoreLabels
    };

    const response = await fetch(`${API_BASE}/paddleocr-vl-1.6`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    if (!response.ok) {
        throw new Error(await response.text());
    }
    return response.json();
}

function updateActionState(task) {
    const hasResult = Boolean(task?.markdown) || Boolean(task?.ocrResults?.length);
    els.startBtn.disabled = !task || isProcessing || task.status === 'processing';
    els.copyBtn.disabled = !task?.markdown;
    els.downloadBtn.disabled = !hasResult;
    const startLabel = task?.status === 'completed'
        ? '重新解析'
        : task?.status === 'error'
            ? '重试解析'
            : '开始解析';
    els.startBtn.innerHTML = task?.status === 'processing'
        ? '<span class="spinner"></span>解析中'
        : `<svg viewBox="0 0 24 24"><path d="m8 5 11 7-11 7V5Z"/></svg>${startLabel}`;
}

async function copyActiveMarkdown() {
    const task = getActiveTask();
    if (!task?.markdown) return;
    await navigator.clipboard.writeText(normalizeOCRMarkdown(task.markdown));
    els.copyBtn.classList.add('success');
    setTimeout(() => els.copyBtn.classList.remove('success'), 900);
}

async function downloadActiveTask() {
    const task = getActiveTask();
    if (!task?.markdown && !task?.ocrResults?.length) return;

    if (activeResultView === 'json') {
        const json = JSON.stringify(toOfficialJson(task), null, 2);
        downloadBlob(new Blob([json], { type: 'application/json' }), safeDownloadName(task.name, 'json'));
        return;
    }

    const markdown = normalizeOCRMarkdown(task.markdown);
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
    if (!confirm('确认清空所有本地任务历史吗？')) return;
    try {
        await deleteAllTasks();
    } catch (error) {
        console.error(error);
        alert(error.message || '清空失败，请稍后重试。');
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
    els.sourceTitle.textContent = '等待上传文件';
    els.sourceMeta.textContent = 'PDF、图片、Office 文档';
    els.pdfControls.classList.add('hidden');
    els.sourceViewer.innerHTML = emptyDropZoneHtml();
    els.dropZone = document.getElementById('drop-zone');
    els.browseBtn = document.getElementById('browse-btn');
    els.browseBtn.addEventListener('click', () => els.fileInput.click());
    renderResultPane(null);
    updateActionState(null);
}

function changePdfPage(delta) {
    if (!currentPdf) return;
    currentPage = Math.min(Math.max(currentPage + delta, 1), currentPdf.numPages);
    scrollPdfPageIntoView(currentPage, 'smooth');
    updatePdfControls();
}

async function changeZoom(delta) {
    if (!currentPdf) return;
    currentZoom = Math.min(2.2, Math.max(0.55, currentZoom + delta));
    await renderPdfDocument(++sourceRenderToken);
    const task = getActiveTask();
    if (task && activeResultView === 'markdown') {
        renderResultPane(task);
    }
}

function scrollPdfPageIntoView(pageNumber, behavior = 'smooth') {
    const page = els.sourceViewer.querySelector(`.pdf-page-wrap[data-page="${pageNumber}"]`);
    if (!page) return;
    const top = page.offsetTop - els.sourceViewer.offsetTop - 12;
    els.sourceViewer.scrollTo({ top: Math.max(top, 0), behavior });
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

async function createPDFBatchDataUrl(sourcePdf, startPage, endPage) {
    const batchPdf = await PDFLib.PDFDocument.create();
    const pageIndices = [];
    for (let i = startPage - 1; i <= endPage - 1; i++) {
        pageIndices.push(i);
    }
    const copiedPages = await batchPdf.copyPages(sourcePdf, pageIndices);
    copiedPages.forEach((page) => batchPdf.addPage(page));
    const bytes = await batchPdf.save();
    return uint8ArrayToDataUrl(bytes, 'application/pdf');
}

function normalizeOCRMarkdown(markdown) {
    return String(markdown)
        .replace(/\r\n/g, '\n')
        .replace(/\r/g, '\n')
        .replace(/\\r\\n/g, '\n')
        .replace(/\\n/g, '\n');
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
    const { text, math } = stashMathSegments(markdown);
    let html = marked.parse(text);
    math.forEach((value, index) => {
        html = html.split(mathToken(index)).join(escapeHtml(value));
    });
    return window.DOMPurify ? DOMPurify.sanitize(html) : html;
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
    if (!blocks.length) return false;

    clearSourceHighlight();
    clearSourceHotspots();
    els.markdownView.innerHTML = '';

    blocks.forEach((block) => {
        const element = document.createElement('section');
        element.className = 'layout-linked-block official-layout-block';
        element.dataset.layoutLabel = layoutLabelText(block.label);
        element.dataset.page = String(block.page);
        element.dataset.blockIndex = String(block.blockIndex);

        const content = rewriteBlockImageSources(block.content || fallbackBlockContent(block), block.pageResult, task);
        element.innerHTML = renderMarkdownHtml(content);
        els.markdownView.appendChild(element);

        addSourceHotspot(block, element);
        bindLinkedBlockEvents(element, block);
    });

    return true;
}

function collectOfficialRenderBlocks(task) {
    const blocks = [];
    if (!Array.isArray(task?.ocrResults)) return blocks;

    task.ocrResults.forEach((pageResult, pageIndex) => {
        const pruned = pageResult?.prunedResult || pageResult;
        const pageWidth = Number(pruned?.width);
        const pageHeight = Number(pruned?.height);
        const parsingBlocks = Array.isArray(pruned?.parsing_res_list) ? pruned.parsing_res_list : [];

        parsingBlocks.forEach((sourceBlock, blockIndex) => {
            const bbox = sourceBlock.block_bbox || sourceBlock.coordinate || sourceBlock.bbox;
            const label = sourceBlock.block_label || sourceBlock.label || '';
            const content = sourceBlock.block_content ?? sourceBlock.text ?? sourceBlock.content ?? '';
            if (!Array.isArray(bbox) || bbox.length < 4 || !pageWidth || !pageHeight) return;
            if (isIgnoredLayoutLabel(label)) return;
            if (!String(content || '').trim() && !isVisualLayoutLabel(label)) return;

            blocks.push({
                page: pageIndex + 1,
                blockIndex,
                label,
                bbox,
                pageWidth,
                pageHeight,
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
        const pageWidth = Number(pruned?.width);
        const pageHeight = Number(pruned?.height);
        const parsingBlocks = Array.isArray(pruned?.parsing_res_list) ? pruned.parsing_res_list : [];

        parsingBlocks.forEach((block, blockIndex) => {
            const bbox = block.block_bbox || block.coordinate || block.bbox;
            const content = block.block_content || block.text || block.content || '';
            const label = block.block_label || block.label || '';
            if (!Array.isArray(bbox) || bbox.length < 4 || !pageWidth || !pageHeight) return;
            if (!content && !['image', 'chart', 'table'].includes(label)) return;
            blocks.push({
                page: pageIndex + 1,
                order: Number(block.block_order ?? blockIndex),
                label,
                bbox,
                pageWidth,
                pageHeight,
                text: normalizeMatchText(content || label)
            });
        });
    });
    return blocks.sort((a, b) => (a.page - b.page) || (a.order - b.order));
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
    return /^Figure\s+\d+\s*[:：]/i.test(String(value || '').trim());
}

function isAlgorithmText(value) {
    return /^Algorithm\s+\d+\s*[:：]/i.test(String(value || '').trim());
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
    const pageWrap = els.sourceViewer.querySelector(`.pdf-page-wrap[data-page="${block.page}"]`);
    const canvas = pageWrap?.querySelector('canvas');
    const layer = pageWrap?.querySelector('.pdf-highlight-layer');
    if (!pageWrap || !canvas || !layer) return;

    const box = document.createElement('div');
    box.className = 'source-highlight-box';
    positionSourceOverlayBox(box, block, canvas);
    const label = document.createElement('span');
    label.className = 'source-highlight-label';
    label.textContent = layoutLabelText(block.label);
    box.appendChild(label);
    layer.appendChild(box);

}

function clearSourceHighlight() {
    els.sourceViewer.querySelectorAll('.source-highlight-box').forEach((box) => box.remove());
}

function addSourceHotspot(block, markdownElement) {
    const pageWrap = els.sourceViewer.querySelector(`.pdf-page-wrap[data-page="${block.page}"]`);
    const canvas = pageWrap?.querySelector('canvas');
    const layer = pageWrap?.querySelector('.pdf-highlight-layer');
    if (!pageWrap || !canvas || !layer) return;

    const hotspot = document.createElement('button');
    hotspot.type = 'button';
    hotspot.className = 'source-link-hotspot';
    hotspot.setAttribute('aria-label', layoutLabelText(block.label));
    positionSourceOverlayBox(hotspot, block, canvas);

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

    layer.appendChild(hotspot);
}

function positionSourceOverlayBox(element, block, canvas) {
    const [x1, y1, x2, y2] = block.bbox.map(Number);
    const canvasWidth = canvas.clientWidth || canvas.width;
    const canvasHeight = canvas.clientHeight || canvas.height;
    element.style.left = `${(x1 / block.pageWidth) * canvasWidth}px`;
    element.style.top = `${(y1 / block.pageHeight) * canvasHeight}px`;
    element.style.width = `${((x2 - x1) / block.pageWidth) * canvasWidth}px`;
    element.style.height = `${((y2 - y1) / block.pageHeight) * canvasHeight}px`;
}

function activateLinkedBlock(markdownElement, block, { scrollMarkdown = false, scrollSource = false } = {}) {
    deactivateLinkedBlocks();
    markdownElement.classList.add('layout-linked-block-active');
    showSourceHighlight(block);
    if (scrollMarkdown && !isElementMostlyVisible(markdownElement, els.markdownView)) {
        scrollElementIntoContainer(markdownElement, els.markdownView, 'smooth');
    }
    if (scrollSource) {
        const pageWrap = els.sourceViewer.querySelector(`.pdf-page-wrap[data-page="${block.page}"]`);
        if (pageWrap && !isElementMostlyVisible(pageWrap, els.sourceViewer)) {
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
        footnote: '脚注',
        algorithm: '算法',
        chart: '图表'
    };
    return labels[normalized] || normalized || '版面块';
}

function prepareBatchResult(result, batchId) {
    let markdown = normalizeOCRMarkdown(result.markdown || '');
    const images = {};
    Object.entries(result.images || {}).forEach(([path, base64]) => {
        const filename = path.split('/').pop();
        const safePath = `ocr_images/${batchId}_${filename}`;
        markdown = markdown.split(path).join(safePath);
        images[safePath] = base64;
    });
    return { markdown, images };
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

function arrayBufferToDataUrl(buffer, mimeType) {
    return uint8ArrayToDataUrl(new Uint8Array(buffer), mimeType);
}

function uint8ArrayToDataUrl(bytes, mimeType) {
    let binary = '';
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
    }
    return `data:${mimeType};base64,${btoa(binary)}`;
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
            <h3>拖拽文件到这里</h3>
            <p>支持 PDF、图片、PPT/PPTX、DOC/DOCX；PDF 会逐页解析。</p>
            <button class="primary-button" id="browse-btn">选择文件</button>
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
    const donePages = task.batches?.filter((batch) => batch.status === 'completed').reduce((sum, batch) => sum + batch.pageCount, 0) || 0;
    if (task.status === 'completed') return '完成';
    if (task.status === 'processing') return `${donePages}/${task.pageCount || 1}`;
    if (task.status === 'error') return '失败';
    return '待解析';
}

function resultPaneTitle(task) {
    if (task.status === 'completed') return '解析结果';
    if (task.status === 'processing') return '解析中';
    if (task.status === 'error') return '解析失败';
    return '待解析';
}

function emptyResultText(task) {
    if (task.status === 'processing') return '正在解析，结果会实时追加到这里。';
    if (task.status === 'error') return `解析失败：${task.error || '未知错误'}`;
    return '点击“开始解析”生成 Markdown 或 JSON 结果。';
}

function sourceLabel(task) {
    if (task.sourceKind === 'office') return `Office 已转 PDF · ${task.originalName}`;
    if (task.sourceKind === 'image') return '图片';
    return 'PDF';
}

function getExtension(filename) {
    return filename.split('.').pop().toLowerCase();
}

function formatDate(timestamp) {
    const date = new Date(timestamp);
    const now = new Date();
    const sameYear = date.getFullYear() === now.getFullYear();
    return date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        ...(sameYear ? {} : { year: 'numeric' })
    });
}

function formatSize(bytes = 0) {
    if (!bytes) return '未知大小';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
    return `${(bytes / 1024 / 1024).toFixed(2)}MB`;
}

function formatPageLabel(startPage, endPage = startPage) {
    const prefix = '\u7b2c';
    const suffix = '\u9875';
    return startPage === endPage
        ? `${prefix} ${startPage} ${suffix}`
        : `${prefix} ${startPage}-${endPage} ${suffix}`;
}

function safeDownloadName(name, ext) {
    return `${name.replace(/\.[^.]+$/, '').replace(/[\\/:*?"<>|]/g, '_')}.${ext}`;
}

function createId() {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
