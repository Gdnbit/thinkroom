import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

const source = fs.readFileSync('src/utils/escapeHtml.ts', 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText
const sandbox = { exports: {} }
vm.runInNewContext(compiled, sandbox)
const { escapeHtml } = sandbox.exports

for (const payload of [
  '<img src=x onerror="alert(document.cookie)">',
  '<svg onload=alert(1)>',
  '</div><script>alert(1)</script>',
  '&lt;img src=x onerror=alert(1)&gt;',
  '**<iframe srcdoc="<script>alert(1)</script>">**',
]) {
  const result = escapeHtml(payload)
  assert.equal(result.includes('<'), false)
  assert.equal(result.includes('>'), false)
  assert.equal(result.includes('"'), false)
}
assert.equal(escapeHtml('ThinkRoom 思考间\n## Evidence **text**'), 'ThinkRoom 思考间\n## Evidence **text**')
assert.equal(escapeHtml('&lt;script&gt;'), '&amp;lt;script&amp;gt;')
