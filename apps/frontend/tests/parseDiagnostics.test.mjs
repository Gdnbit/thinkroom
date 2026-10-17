import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import ts from 'typescript'

const root = process.cwd()
const sourcePath = path.join(root, 'src', 'services', 'parseDiagnostics.ts')
const source = fs.readFileSync(sourcePath, 'utf8')
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    esModuleInterop: true,
  },
}).outputText

const sandbox = {
  exports: {},
  module: { exports: {} },
}
vm.runInNewContext(transpiled, sandbox, { filename: sourcePath })
const { summarizeParseDiagnostics } = { ...sandbox.exports, ...sandbox.module.exports }

assert.equal(typeof summarizeParseDiagnostics, 'function')

const successful = summarizeParseDiagnostics({
  status: 'completed',
  method_attempts: [
    { method: 'dispatch', status: 'success', chars: 0 },
    { method: 'read_text', status: 'success', chars: 166 },
  ],
})

assert.equal(successful.method, '文本读取')
assert.equal(successful.text, '解析方式：文本读取 · 166 字符')
assert.equal(successful.attemptCount, 2)

const failed = summarizeParseDiagnostics({
  status: 'failed',
  method_attempts: [
    { method: 'pymupdf_text', status: 'failed', reason_code: 'empty_text' },
    { method: 'ocr', status: 'failed', failed_pages: 2 },
  ],
})

assert.equal(failed.method, 'OCR')
assert.equal(failed.text, '解析尝试：OCR 未完成 · 2 次尝试')
assert.equal(failed.attemptCount, 2)

const normalizedDocumentParse = summarizeParseDiagnostics({
  status: 'completed',
  method_attempts: [],
  document_parse: {
    status: 'partial',
    text_chars: 2048,
    methods: ['pdf_ocr'],
    pages_total: 5,
    pages_failed: [2, 4],
    segment_count: 1,
  },
})

assert.equal(normalizedDocumentParse.method, 'OCR')
assert.equal(normalizedDocumentParse.text, '解析方式：OCR · 2,048 字符 · 5 页 · 2 页需复核')
assert.equal(normalizedDocumentParse.attemptCount, 0)

assert.equal(summarizeParseDiagnostics(null), null)
assert.equal(summarizeParseDiagnostics({ status: 'completed', method_attempts: [] }), null)

console.log('parseDiagnostics tests passed')
