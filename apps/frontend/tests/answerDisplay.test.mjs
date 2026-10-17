import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { createRequire } from 'node:module'
import ts from 'typescript'

const root = process.cwd()
const require = createRequire(import.meta.url)
const sourcePath = path.join(root, 'src', 'utils', 'answerDisplay.ts')
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
  require,
  TextDecoder,
  Uint8Array,
  Date,
}
vm.runInNewContext(transpiled, sandbox, { filename: sourcePath })
const {
  formatAssistantAnswerForDisplay,
  formatGeneratedAnswerText,
} = { ...sandbox.exports, ...sandbox.module.exports }

assert.equal(typeof formatAssistantAnswerForDisplay, 'function')
assert.equal(typeof formatGeneratedAnswerText, 'function')

const structured = formatAssistantAnswerForDisplay({
  content: JSON.stringify({
    rationale: ['库存下降提供短线支撑。', '美元走弱会抬高风险资产偏好。'],
    final_answer: '原油短期更适合观察震荡区间，不能仅凭当前材料直接追多。[1]',
    risk_warnings: ['缺少最新 EIA 库存确认。'],
    next_steps: ['等待下一轮库存和持仓数据。'],
  }),
  payload: {
    quality_report: {
      reference_time: '2026年07月06日 09:30:00 CST',
    },
  },
  generatedAt: '2026-07-06T01:30:00.000Z',
})

assert.match(structured, /^截至 2026年07月06日 09:30:00 CST/)
assert.match(structured, /## 结论[\s\S]*原油短期更适合观察震荡区间/)
assert.match(structured, /## 主要依据[\s\S]*库存下降提供短线支撑/)
assert.match(structured, /## 风险与边界[\s\S]*缺少最新 EIA 库存确认/)
assert.match(structured, /## 下一步[\s\S]*等待下一轮库存和持仓数据/)
assert.ok(structured.indexOf('## 结论') < structured.indexOf('## 主要依据'))
assert.doesNotMatch(structured, /"final_answer"|"rationale"|^\{/m)
assert.doesNotMatch(structured, /^## 主要依据/)

const rawOutput = formatAssistantAnswerForDisplay({
  content: '{"status":"ok"}',
  payload: {
    raw_output: JSON.stringify({
      synthesis_narrative: '委员会认为当前证据不足，建议维持观察。',
      reasoning: '可用来源缺少方向性证据。',
    }),
    reference_time: '2026年07月06日 10:00:00 CST',
  },
})

assert.match(rawOutput, /^截至 2026年07月06日 10:00:00 CST/)
assert.match(rawOutput, /## 结论[\s\S]*委员会认为当前证据不足/)
assert.match(rawOutput, /## 主要依据[\s\S]*可用来源缺少方向性证据/)
assert.doesNotMatch(rawOutput, /"synthesis_narrative"|"reasoning"/)

const alreadyTimestamped = formatAssistantAnswerForDisplay({
  content: '截至 2026年07月06日 11:00:00 CST，已有时间戳的回答不应重复加头。',
  payload: {},
})

assert.equal(
  (alreadyTimestamped.match(/截至 2026年07月06日/g) ?? []).length,
  1,
)

const generatedSummary = formatGeneratedAnswerText(
  JSON.stringify({
    synthesis_narrative: '委员会结论应显示为自然语言，而不是 JSON 字段。',
    reasoning: '内部理由可进入正文结构，但不应污染一句话摘要。',
  }),
  '后端未返回综合裁决。',
)

assert.equal(generatedSummary, '委员会结论应显示为自然语言，而不是 JSON 字段。')
assert.doesNotMatch(generatedSummary, /synthesis_narrative|reasoning|^\{/)

const generatedList = formatGeneratedAnswerText(['第一条风险', '第二条风险'])
assert.equal(generatedList, '第一条风险；第二条风险')

console.log('answerDisplay tests passed')
