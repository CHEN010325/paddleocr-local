// Configuration
const API_BASE = '/api';
let availableModel = 'PaddleOCR-VL-1.5-0.9B'; // Default model name for UI

// State
let processQueue = [];
let isProcessing = false;
let processedResults = [];

// DOM Elements
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const browseBtn = document.getElementById('browse-btn');
const queueSection = document.getElementById('processing-queue');
const queueList = document.getElementById('queue-list');
const resultsContainer = document.getElementById('results-container');
const statusDot = document.getElementById('model-status-dot');
const statusText = document.getElementById('model-status-text');
const progressText = document.getElementById('progress-text');
const clearBtn = document.getElementById('clear-all-btn');
const downloadAllBtn = document.getElementById('download-all-btn');
const startBtn = document.getElementById('start-btn');
const chartRecognitionSwitch = document.getElementById('chart-recognition-switch');
const docUnwarpingSwitch = document.getElementById('doc-unwarping-switch');
const docOrientationSwitch = document.getElementById('doc-orientation-switch');
const sealRecognitionSwitch = document.getElementById('seal-recognition-switch');
const formatContentSwitch = document.getElementById('format-content-switch');
const formulaNumberSwitch = document.getElementById('formula-number-switch');
const ignoreHeaderSwitch = document.getElementById('ignore-header-switch');
const ignoreFooterSwitch = document.getElementById('ignore-footer-switch');
const ignoreNumberSwitch = document.getElementById('ignore-number-switch');

// Templates
const queueItemTemplate = document.getElementById('queue-item-template');
const resultCardTemplate = document.getElementById('result-card-template');

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    // Set PDF.js worker
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
    
    await checkBackendConnection();
    setupEventListeners();
});

async function checkBackendConnection() {
    try {
        const response = await fetch(`${API_BASE}/models`);
        if (response.ok) {
            const data = await response.json();
            if (data.data && data.data.length > 0) {
                availableModel = data.data[0].id;
                console.log('Connected. Using model:', availableModel);
            }
            statusDot.className = 'dot connected';
            statusText.textContent = '已连接到 ' + availableModel;
        } else {
            throw new Error('API Error');
        }
    } catch (err) {
        console.error('Connection failed:', err);
        statusDot.className = 'dot error';
        statusText.textContent = '连接失败 (请检查 VLLM 是否运行)';
        // Retry after 5s
        setTimeout(checkBackendConnection, 5000);
    }
}

function setupEventListeners() {
    // Drag & Drop
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('drag-over');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        handleFiles(e.dataTransfer.files);
    });

    // File Input
    browseBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (e) => handleFiles(e.target.files));

    // Actions
    clearBtn.addEventListener('click', clearQueue);
    downloadAllBtn.addEventListener('click', downloadAllMarkdown);
    startBtn.addEventListener('click', startProcessing);
}

async function handleFiles(files) {
    if (!files || files.length === 0) return;

    queueSection.classList.remove('hidden');
    const unsupportedFiles = [];
    
    for (const file of files) {
        const ext = file.name.split('.').pop().toLowerCase();
        const allowedImageExts = ['png', 'jpg', 'jpeg', 'bmp', 'webp', 'tiff', 'tif', 'gif'];
        const allowedOfficeExts = ['ppt', 'pptx', 'doc', 'docx'];
        
        if (ext === 'pdf' || file.type === 'application/pdf') {
            await processPDF(file, file.name);
        } else if (allowedOfficeExts.includes(ext)) {
            await processOfficeFile(file);
        } else if (allowedImageExts.includes(ext)) {
            await processImage(file);
        } else {
            console.warn(`Unsupported file format: ${file.name}`);
            unsupportedFiles.push(file.name);
        }
    }

    if (unsupportedFiles.length > 0) {
        alert(`以下文件格式不支持：\n${unsupportedFiles.join('\n')}`);
    }

    updateQueueProgress();
    // REMOVED: processNextInQueue(); - Now waits for user to click start
}

function startProcessing() {
    if (processQueue.length === 0) {
        alert('请先添加文件');
        return;
    }
    if (!isProcessing) {
        processNextInQueue();
    }
}

async function processImage(file) {
    const reader = new FileReader();
    return new Promise((resolve) => {
        reader.onload = (e) => {
            addToQueue({
                id: Math.random().toString(36).substr(2, 9),
                file: file,
                type: 'image',
                dataUrl: e.target.result,
                name: file.name,
                status: 'pending'
            });
            resolve();
        };
        reader.readAsDataURL(file);
    });
}

async function processOfficeFile(file) {
    const tempId = Math.random().toString(36).substr(2, 9);
    const ext = file.name.split('.').pop().toLowerCase();
    
    // Icons
    const pptIcon = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjZTkxZTYzIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTE0IDJINmEyIDIgMCAwIDAtMiAydjE2YTIgMiAwIDAgMCAyIDJoMTJhMiAyIDAgMCAwIDItMlY4eiI+PC9wYXRoPjxwb2x5bGluZSBwb2ludHM9IjE0IDIgMTQgOCAyMCA4Ij48L3BvbHlsaW5lPjx0ZXh0IHg9IjEyIiB5PSIxOCIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1zaXplPSI2IiBmaWxsPSIjZTkxZTYzIiBzdHJva2U9Im5vbmUiPlBQVDwvdGV4dD48L3N2Zz4=';
    const docIcon = 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjMjE5NmYzIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCI+PHBhdGggZD0iTTE0IDJINmEyIDIgMCAwIDAtMiAydjE2YTIgMiAwIDAgMCAyIDJoMTJhMiAyIDAgMCAwIDItMlY4eiI+PC9wYXRoPjxwb2x5bGluZSBwb2ludHM9IjE0IDIgMTQgOCAyMCA4Ij48L3BvbHlsaW5lPjx0ZXh0IHg9IjEyIiB5PSIxOCIgdGV4dC1hbmNob3I9Im1pZGRsZSIgZm9udC1zaXplPSI2IiBmaWxsPSIjMjE5NmYzIiBzdHJva2U9Im5vbmUiPkRPQzwvdGV4dD48L3N2Zz4=';
    
    const icon = ['doc', 'docx'].includes(ext) ? docIcon : pptIcon;
    const statusColor = ['doc', 'docx'].includes(ext) ? '#2196f3' : '#e91e63';

    const item = {
        id: tempId,
        file: file,
        type: 'office_convert',
        dataUrl: icon,
        name: file.name,
        status: 'converting'
    };
    
    addToQueue(item);
    
    const el = document.getElementById(`queue-${tempId}`);
    if (el) {
        el.querySelector('.file-status').textContent = '转换格式中...';
        el.querySelector('.file-status').style.color = statusColor;
        el.querySelector('.progress-bar').style.width = '100%';
        el.querySelector('.progress-bar').classList.add('loading');
    }

    try {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch(`${API_BASE}/convert/to-pdf`, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            let errorMsg = '转换失败';
            try {
                const err = await response.json();
                errorMsg = err.detail || errorMsg;
            } catch (e) {
                errorMsg = await response.text();
            }
            throw new Error(errorMsg);
        }

        const blob = await response.blob();
        
        // Remove temp item
        const idx = processQueue.findIndex(i => i.id === tempId);
        if (idx !== -1) processQueue.splice(idx, 1);
        if (el) el.remove();
        
        // Process as PDF
        await processPDF(blob, file.name);

    } catch (error) {
        console.error(error);
        if (el) {
            el.classList.add('error');
            el.querySelector('.file-status').textContent = '转换失败';
            el.querySelector('.file-status').style.color = 'var(--error-color)';
            el.querySelector('.progress-bar').classList.remove('loading');
            el.querySelector('.progress-bar').style.backgroundColor = 'var(--error-color)';
            item.status = 'error';
        }
        alert(`文件 ${file.name} 转换失败: ${error.message}`);
    }
}

async function processPDF(file, fileName) {
    const arrayBuffer = await file.arrayBuffer();
    const pdf = await pdfjsLib.getDocument(arrayBuffer).promise;
    
    for (let i = 1; i <= pdf.numPages; i++) {
        const page = await pdf.getPage(i);
        const viewport = page.getViewport({ scale: 2.0 }); // Scale up for better OCR
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        canvas.height = viewport.height;
        canvas.width = viewport.width;

        await page.render({ canvasContext: context, viewport: viewport }).promise;
        
        addToQueue({
            id: Math.random().toString(36).substr(2, 9),
            file: file,
            type: 'pdf_page',
            dataUrl: canvas.toDataURL('image/jpeg', 0.9),
            name: `${fileName || file.name} (第 ${i} 页)`,
            pageNum: i,
            status: 'pending'
        });
    }
}

function addToQueue(item) {
    processQueue.push(item);
    renderQueueItem(item);
}

function renderQueueItem(item) {
    const clone = queueItemTemplate.content.cloneNode(true);
    const el = clone.querySelector('.queue-item');
    el.id = `queue-${item.id}`;
    
    // Truncate long names for vertical cards (more space available)
    let displayName = item.name;
    if (displayName.length > 25) {
        displayName = displayName.substring(0, 22) + '...';
    }
    
    el.querySelector('.file-name').textContent = displayName;
    el.querySelector('.file-name').title = item.name; // Full name on hover
    el.querySelector('.thumbnail').src = item.dataUrl;
    el.querySelector('.file-status').textContent = '待处理';
    queueList.appendChild(el);
    
    // Auto-scroll to the newest item (vertical scroll now)
    setTimeout(() => {
        queueList.scrollTop = queueList.scrollHeight;
    }, 100);
}

function updateQueueProgress() {
    progressText.textContent = `已完成 ${processedResults.length} / 剩余 ${processQueue.length}`;
}

async function processNextInQueue() {
    if (isProcessing || processQueue.length === 0) return;

    const itemIndex = processQueue.findIndex(i => i.status === 'pending');
    if (itemIndex === -1) {
        // All done - remove processing state from queue section
        queueSection.classList.remove('processing');
        return;
    }

    isProcessing = true;
    const item = processQueue[itemIndex];
    item.status = 'processing';
    
    // Add processing state to queue section
    queueSection.classList.add('processing');
    
    // Update UI with animations
    const el = document.getElementById(`queue-${item.id}`);
    if (el) {
        el.classList.add('processing');
        el.querySelector('.file-status').textContent = '处理中...';
        el.querySelector('.file-status').style.color = 'var(--accent-color)';
        
        const progressBar = el.querySelector('.progress-bar');
        progressBar.classList.add('loading');
    }

    try {
        const data = await callVLLM(item.dataUrl);
        
        // Success
        item.status = 'completed';
        item.markdown = data.markdown;
        item.images = data.images; // Store base64 images
        
        if (el) {
            el.classList.remove('processing');
            el.classList.add('completed');
            el.querySelector('.file-status').textContent = '完成';
            el.querySelector('.file-status').style.color = 'var(--success-color)';
            
            const progressBar = el.querySelector('.progress-bar');
            progressBar.classList.remove('loading');
            progressBar.style.width = '100%';
            progressBar.style.background = 'var(--success-color)';
        }

        processedResults.push(item);
        renderResult(item);
        
        // Play a subtle success animation
        if (el) {
            setTimeout(() => {
                el.style.transition = 'all 0.3s ease';
                el.style.transform = 'scale(0.98)';
                setTimeout(() => {
                    el.style.transform = 'scale(1)';
                }, 150);
            }, 100);
        }

    } catch (error) {
        console.error(error);
        item.status = 'error';
        if (el) {
            el.classList.remove('processing');
            el.classList.add('error');
            el.querySelector('.file-status').textContent = '失败';
            el.querySelector('.file-status').style.color = 'var(--error-color)';
            
            const progressBar = el.querySelector('.progress-bar');
            progressBar.classList.remove('loading');
            progressBar.style.backgroundColor = 'var(--error-color)';
            progressBar.style.width = '100%';
        }
    } finally {
        isProcessing = false;
        updateQueueProgress();
        // Slight delay - Check if we should continue
        setTimeout(processNextInQueue, 500);
    }
}

async function callVLLM(base64Image) {
    // Collect ignore labels
    const ignoreLabels = [];
    if (ignoreHeaderSwitch.checked) ignoreLabels.push('header', 'header_image');
    if (ignoreFooterSwitch.checked) ignoreLabels.push('footer', 'footer_image');
    if (ignoreNumberSwitch.checked) ignoreLabels.push('number');

    const payload = {
        image: base64Image,
        useLayoutDetection: true,
        useChartRecognition: chartRecognitionSwitch.checked,
        useDocUnwarping: docUnwarpingSwitch.checked,
        useDocOrientationClassify: docOrientationSwitch.checked,
        useSealRecognition: sealRecognitionSwitch.checked,
        formatBlockContent: formatContentSwitch.checked,
        showFormulaNumber: formulaNumberSwitch.checked,
        markdownIgnoreLabels: ignoreLabels
    };

    const response = await fetch(`${API_BASE}/paddleocr-vl-1.5`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    if (!response.ok) {
        const err = await response.text();
        throw new Error(`API Error: ${err}`);
    }

    const data = await response.json();
    return data;
}

function renderResult(item) {
    // Remove empty state if it exists
    const emptyState = resultsContainer.querySelector('.empty-state');
    if (emptyState) {
        emptyState.remove();
    }
    
    const clone = resultCardTemplate.content.cloneNode(true);
    const card = clone.querySelector('.result-card');
    
    card.querySelector('.page-number').textContent = item.name;
    card.querySelector('.image-preview img').src = item.dataUrl;
    
    // Replace image paths in markdown with data URLs from item.images
    let markdown = item.markdown || '(无内容)';
    if (item.images) {
        Object.entries(item.images).forEach(([path, base64]) => {
            // Replace the path in markdown with data URL
            const dataUrl = `data:image/jpeg;base64,${base64}`;
            markdown = markdown.split(path).join(dataUrl);
        });
    }

    // Render markdown using marked.js
    const mdHtml = marked.parse(markdown);
    const safeHtml = window.DOMPurify ? DOMPurify.sanitize(mdHtml) : mdHtml;
    card.querySelector('.markdown-preview').innerHTML = safeHtml;
    
    // Setup copy button
    const copyBtn = card.querySelector('.copy-btn');
    copyBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(item.markdown).then(() => {
            copyBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" fill="none" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>';
            setTimeout(() => {
                copyBtn.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" fill="none" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
            }, 2000);
        });
    });
    
    resultsContainer.appendChild(card);
    updateQueueProgress();
}

function clearQueue() {
    processQueue = [];
    processedResults = [];
    queueList.innerHTML = '';
    resultsContainer.innerHTML = `
        <div class="empty-state">
            <svg viewBox="0 0 24 24" width="48" height="48" stroke="currentColor" stroke-width="1.5" fill="none">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                <polyline points="14 2 14 8 20 8"></polyline>
                <line x1="16" y1="13" x2="8" y2="13"></line>
                <line x1="16" y1="17" x2="8" y2="17"></line>
                <polyline points="10 9 9 9 8 9"></polyline>
            </svg>
            <p>上传文件后，解析结果将在此显示</p>
        </div>
    `;
    queueSection.classList.add('hidden');
    updateQueueProgress();
}

async function downloadAllMarkdown() {
    if (processedResults.length === 0) {
        alert('没有可下载的结果');
        return;
    }
    
    const totalPages = processedResults.length;
    let combinedMarkdown = '';
    let allImages = {}; // Store all unique images
    
    processedResults.forEach((item, idx) => {
        let markdown = item.markdown;
        
        // Collect images and use relative paths
        if (item.images) {
            Object.entries(item.images).forEach(([path, base64]) => {
                const filename = path.split('/').pop();
                allImages[filename] = base64;
                markdown = markdown.split(path).join(`ocr_images/${filename}`);
            });
        }
        
        combinedMarkdown += markdown + '\n\n';
    });
    
    const imageCount = Object.keys(allImages).length;
    
    // If there are images, create a ZIP package
    if (imageCount > 0) {
        console.log(`准备打包：${totalPages} 个页面，${imageCount} 张图片`);
        
        downloadAllBtn.textContent = '准备打包...';
        downloadAllBtn.disabled = true;
        
        try {
            const zip = new JSZip();
            zip.file('README.md', combinedMarkdown);
            
            const imgFolder = zip.folder('ocr_images');
            
            Object.entries(allImages).forEach(([filename, base64]) => {
                const binary = atob(base64);
                const array = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {
                    array[i] = binary.charCodeAt(i);
                }
                imgFolder.file(filename, array.buffer);
            });
            
            // Generate and download zip
            downloadAllBtn.textContent = '生成压缩包...';
            const content = await zip.generateAsync({ 
                type: 'blob',
                compression: 'DEFLATE',
                compressionOptions: { level: 6 }
            });
            
            const url = URL.createObjectURL(content);
            const a = document.createElement('a');
            a.href = url;
            a.download = `ocr_results_${totalPages}pages_${imageCount}imgs_${new Date().getTime()}.zip`;
            a.click();
            URL.revokeObjectURL(url);
            
            console.log(`ZIP 文件已生成并下载`);
            
            // Reset button
            downloadAllBtn.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2" fill="none">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
            下载`;
            downloadAllBtn.disabled = false;

            
        } catch (error) {
            console.error('打包错误:', error);
            alert('打包失败：' + error.message);
            downloadAllBtn.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2" fill="none">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
            下载`;
            downloadAllBtn.disabled = false;
        }
    } else {
        // No images, just download markdown
        console.log(`下载纯文本 Markdown：${totalPages} 个页面，无图片`);
        const blob = new Blob([combinedMarkdown], { type: 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `ocr_results_${totalPages}pages_${new Date().getTime()}.md`;
        a.click();
        URL.revokeObjectURL(url);
    }
}
