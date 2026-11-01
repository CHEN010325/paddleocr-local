import * as pdfjsLib from './vendor/pdfjs/pdf.min.mjs?v=legacy1';

pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.mjs?v=legacy1';
globalThis.pdfjsLib = pdfjsLib;

await import('./app.js?v=111');
