type AnswerDisplayInput = {
  content: string
  payload?: Record<string, unknown>
  generatedAt?: string
}

type FormattedStructuredAnswer = {
  markdown: string
  score: number
}

const CONCLUSION_KEYS = [
  'final_answer',
  'answer',
  'content',
  'synthesis_narrative',
  'analysis',
  'executive_summary',
  'summary',
  'conclusion',
  'decision',
  'recommendation',
]

const SECTION_KEYS: Array<[string, string[]]> = [
  ['结论', CONCLUSION_KEYS],
  ['主要依据', ['supporting_points', 'key_points', 'rationale', 'reasoning', 'analysis', 'reasons']],
  ['反向证据', ['contrary_points', 'counterarguments', 'key_disagreements']],
  ['风险与边界', ['key_risks', 'risk_warnings', 'risks', 'warnings', 'invalidation_conditions']],
  ['下一步', ['next_steps', 'actions', 'suggestions', 'recommended_action']],
]

const TECHNICAL_KEYS = new Set([
  'id',
  'run_id',
  'mode',
  'status',
  'cost_usd',
  'duration_ms',
  'model',
  'model_used',
  'llm_trace',
  'quality_report',
  'quality_filters',
  'decision_guardrail',
  'data_sources',
  'scope',
  'citations',
  'claims_used',
  'raw_output',
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function countCjk(value: string) {
  return (value.match(/[\u3400-\u9fff]/g) ?? []).length
}

export function repairTextEncoding(value: string) {
  if (!/[ÃÂâåæçèéäãï\u0080-\u009f]/.test(value)) return value
  try {
    const bytes = Uint8Array.from(Array.from(value, (char) => char.charCodeAt(0) & 0xff))
    const decoded = new TextDecoder('utf-8', { fatal: false }).decode(bytes)
    const replacementCount = (decoded.match(/\uFFFD/g) ?? []).length
    if (countCjk(decoded) > countCjk(value) && replacementCount < Math.max(3, decoded.length * 0.03)) {
      return decoded
    }
  } catch {
    return value
  }
  return value
}

export function displayReadableText(value: string) {
  const repaired = repairTextEncoding(value)
  const questionMarks = (repaired.match(/\?/g) ?? []).length
  if (questionMarks >= 6 && countCjk(repaired) === 0) {
    return '历史文本不可读（旧编码记录）'
  }
  return repaired
}

export function readDisplayString(value: unknown, fallback = '') {
  return typeof value === 'string' ? repairTextEncoding(value) : fallback
}

function formatReferenceTime(date = new Date()) {
  return date.toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }) + ' CST'
}

function formatReferenceTimeFromValue(value?: string) {
  if (!value) return formatReferenceTime()
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return formatReferenceTime(date)
}

function referenceTimeFromPayload(payload: Record<string, unknown> | undefined, generatedAt?: string) {
  const qualityReport = isRecord(payload?.quality_report) ? payload.quality_report : null
  return readDisplayString(qualityReport?.reference_time)
    || readDisplayString(payload?.reference_time)
    || readDisplayString(qualityReport?.generated_at)
    || readDisplayString(payload?.generated_at)
    || formatReferenceTimeFromValue(generatedAt)
}

function hasReferenceTime(content: string) {
  return /截至\s*\d{4}|参考时间|生成时间|更新[时日]间/.test(content)
}

function ensureReferenceTime(content: string, payload?: Record<string, unknown>, generatedAt?: string) {
  if (hasReferenceTime(content)) return content
  return `截至 ${referenceTimeFromPayload(payload, generatedAt)}，以下回答基于本次会话可检索到的资料、引用证据与行情快照。\n\n${content}`
}

function tryParseJson(text: string): unknown | null {
  const trimmed = text.trim()
  if (!trimmed) return null
  const fenced = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i)
  const candidate = fenced?.[1]?.trim() || trimmed
  if (!candidate.startsWith('{') && !candidate.startsWith('[')) return null
  try {
    return JSON.parse(candidate)
  } catch {
    return null
  }
}

function firstPresentValue(value: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const item = value[key]
    if (item === undefined || item === null) continue
    if (typeof item === 'string' && !item.trim()) continue
    if (Array.isArray(item) && item.length === 0) continue
    return item
  }
  return undefined
}

function formatListItem(item: unknown) {
  if (typeof item === 'string') return repairTextEncoding(item)
  if (isRecord(item)) {
    const title = readDisplayString(item.title)
      || readDisplayString(item.name)
      || readDisplayString(item.claim)
      || readDisplayString(item.summary)
    const body = readDisplayString(item.description)
      || readDisplayString(item.rationale)
      || readDisplayString(item.detail)
      || readDisplayString(item.content)
    const joined = [title, body].filter(Boolean).join('：')
    return joined || compactRecord(item)
  }
  return String(item ?? '')
}

export function formatGeneratedAnswerText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') {
    const repaired = repairTextEncoding(value).trim()
    const parsed = tryParseJson(repaired)
    if (parsed !== null) return formatGeneratedAnswerText(parsed, fallback)
    return repaired || fallback
  }

  if (Array.isArray(value)) {
    const items = value
      .map((item) => formatGeneratedAnswerText(item))
      .map((item) => item.trim())
      .filter(Boolean)
    return items.join('；') || fallback
  }

  if (isRecord(value)) {
    const direct = firstPresentValue(value, CONCLUSION_KEYS)
      ?? firstPresentValue(value, ['synthesis', 'title', 'name'])
    if (direct !== undefined) return formatGeneratedAnswerText(direct, fallback)

    const compact = compactRecord(value).trim()
    return compact || fallback
  }

  if (value === undefined || value === null) return fallback
  return String(value).trim() || fallback
}

function formatValue(value: unknown): string {
  if (typeof value === 'string') return repairTextEncoding(value)
  if (Array.isArray(value)) {
    return value
      .map((item) => formatListItem(item).trim())
      .filter(Boolean)
      .map((item) => `- ${item}`)
      .join('\n')
  }
  if (isRecord(value)) return compactRecord(value)
  if (value === undefined || value === null) return ''
  return String(value)
}

function compactRecord(value: Record<string, unknown>) {
  const pairs = Object.entries(value)
    .filter(([key, item]) => !TECHNICAL_KEYS.has(key) && item !== undefined && item !== null && item !== '')
    .slice(0, 6)
    .map(([key, item]) => `${key}: ${formatValue(item)}`)
  return pairs.join('；')
}

function formatStructuredAnswer(value: unknown): FormattedStructuredAnswer | null {
  if (typeof value === 'string') {
    const repaired = repairTextEncoding(value).trim()
    return repaired ? { markdown: repaired, score: 1 } : null
  }

  if (Array.isArray(value)) {
    const markdown = formatValue(value).trim()
    return markdown ? { markdown, score: 2 } : null
  }

  if (!isRecord(value)) {
    const text = String(value ?? '').trim()
    return text ? { markdown: text, score: 1 } : null
  }

  const sections: string[] = []
  let matchedSections = 0
  for (const [label, keys] of SECTION_KEYS) {
    const found = firstPresentValue(value, keys)
    if (found === undefined) continue
    const formatted = formatValue(found).trim()
    if (!formatted) continue
    matchedSections += 1
    sections.push(`## ${label}\n${formatted}`)
  }

  if (sections.length) {
    return {
      markdown: sections.join('\n\n'),
      score: 10 + matchedSections,
    }
  }

  const compact = compactRecord(value).trim()
  if (!compact) return null
  return { markdown: compact, score: 1 }
}

export function formatAssistantAnswerForDisplay({
  content,
  payload = {},
  generatedAt,
}: AnswerDisplayInput) {
  const candidates: FormattedStructuredAnswer[] = []
  const parsedContent = tryParseJson(content)
  const rawOutput = typeof payload.raw_output === 'string' ? payload.raw_output : ''
  const parsedRawOutput = rawOutput ? tryParseJson(rawOutput) : null

  if (parsedContent !== null) {
    const formatted = formatStructuredAnswer(parsedContent)
    if (formatted) candidates.push(formatted)
  }
  if (parsedRawOutput !== null) {
    const formatted = formatStructuredAnswer(parsedRawOutput)
    if (formatted) candidates.push(formatted)
  }

  const best = candidates.sort((a, b) => b.score - a.score || b.markdown.length - a.markdown.length)[0]
  if (best?.markdown) {
    return ensureReferenceTime(best.markdown, payload, generatedAt)
  }

  const repaired = displayReadableText(content || '后端没有返回有效内容。')
  return ensureReferenceTime(repaired, payload, generatedAt)
}
