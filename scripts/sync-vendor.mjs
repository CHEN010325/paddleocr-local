import { createHash } from 'node:crypto';
import { cp, mkdir, readFile, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const vendorRoot = path.join(projectRoot, 'static', 'vendor');
const checkOnly = process.argv.includes('--check');

const mappings = [
    ['node_modules/pdfjs-dist/build/pdf.min.mjs', 'pdfjs/pdf.min.mjs'],
    ['node_modules/pdfjs-dist/build/pdf.worker.min.mjs', 'pdfjs/pdf.worker.min.mjs'],
    ['node_modules/pdfjs-dist/LICENSE', 'pdfjs/LICENSE'],
    ['node_modules/pdf-lib/dist/pdf-lib.min.js', 'pdf-lib/pdf-lib.min.js'],
    ['node_modules/pdf-lib/LICENSE.md', 'pdf-lib/LICENSE.md'],
    ['node_modules/marked/lib/marked.umd.js', 'marked/marked.min.js'],
    ['node_modules/marked/LICENSE', 'marked/LICENSE'],
    ['node_modules/dompurify/dist/purify.min.js', 'dompurify/purify.min.js'],
    ['node_modules/dompurify/LICENSE', 'dompurify/LICENSE'],
    ['node_modules/@highlightjs/cdn-assets/highlight.min.js', 'highlight/highlight.min.js'],
    ['node_modules/@highlightjs/cdn-assets/styles/github.min.css', 'highlight/github.min.css'],
    ['node_modules/@highlightjs/cdn-assets/LICENSE', 'highlight/LICENSE'],
    ['node_modules/jszip/dist/jszip.min.js', 'jszip/jszip.min.js'],
    ['node_modules/jszip/LICENSE.markdown', 'jszip/LICENSE.markdown'],
    ['node_modules/katex/dist/katex.min.js', 'katex/katex.min.js'],
    ['node_modules/katex/dist/katex.min.css', 'katex/katex.min.css'],
    ['node_modules/katex/dist/contrib/auto-render.min.js', 'katex/contrib/auto-render.min.js'],
    ['node_modules/katex/LICENSE', 'katex/LICENSE'],
];

const directoryMappings = [
    ['node_modules/katex/dist/fonts', 'katex/fonts'],
];

async function digest(filePath) {
    return createHash('sha256').update(await readFile(filePath)).digest('hex');
}

async function directoryFiles(directory, prefix = '') {
    const entries = await readdir(directory, { withFileTypes: true });
    const files = [];
    for (const entry of entries) {
        const relative = path.join(prefix, entry.name);
        if (entry.isDirectory()) files.push(...await directoryFiles(path.join(directory, entry.name), relative));
        else if (entry.isFile()) files.push(relative);
    }
    return files.sort();
}

async function assertSame(source, target) {
    try {
        if (await digest(source) !== await digest(target)) throw new Error('content differs');
    } catch (error) {
        throw new Error(`Vendored dependency is stale: ${path.relative(projectRoot, target)} (${error.message})`);
    }
}

for (const [sourceName, targetName] of mappings) {
    const source = path.join(projectRoot, sourceName);
    const target = path.join(vendorRoot, targetName);
    if (checkOnly) {
        await assertSame(source, target);
    } else {
        await mkdir(path.dirname(target), { recursive: true });
        await cp(source, target);
    }
}

for (const [sourceName, targetName] of directoryMappings) {
    const source = path.join(projectRoot, sourceName);
    const target = path.join(vendorRoot, targetName);
    if (checkOnly) {
        const sourceFiles = await directoryFiles(source);
        const targetFiles = await directoryFiles(target).catch(() => []);
        if (sourceFiles.join('\n') !== targetFiles.join('\n')) {
            throw new Error(`Vendored dependency directory is stale: ${path.relative(projectRoot, target)}`);
        }
        for (const relative of sourceFiles) await assertSame(path.join(source, relative), path.join(target, relative));
    } else {
        await rm(target, { recursive: true, force: true });
        await mkdir(path.dirname(target), { recursive: true });
        await cp(source, target, { recursive: true });
    }
}

console.log(checkOnly ? 'Vendored dependencies are current.' : 'Vendored dependencies synchronized.');
