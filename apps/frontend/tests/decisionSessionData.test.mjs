import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import ts from 'typescript'

const root = process.cwd()
const sourcePath = path.join(root, 'src', 'data', 'decisionSession.ts')
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
const { currentDecisionSession } = {
  ...sandbox.exports,
  ...sandbox.module.exports,
}

assert.equal(currentDecisionSession.id, 'empty-session')
assert.equal(Array.isArray(currentDecisionSession.files), true)
assert.equal(Array.isArray(currentDecisionSession.evidence), true)
assert.equal(Array.isArray(currentDecisionSession.risks), true)
assert.equal(currentDecisionSession.files.length, 0)
assert.equal(currentDecisionSession.evidence.length, 0)
assert.equal(currentDecisionSession.risks.length, 0)

const serialized = JSON.stringify(currentDecisionSession)
assert.equal(serialized.includes('Q1_report.pdf'), false)
assert.equal(serialized.includes('manager_call.md'), false)
assert.equal(serialized.includes('fund_position.xlsx'), false)

console.log('decisionSession data tests passed')
