export interface ParseMethodAttempt {
  method: string
  status: string
  chars?: number | null
  pages?: number | null
  reason_code?: string | null
  file_type?: string | null
  failed_pages?: number | number[] | null
}

export interface DocumentParseDiagnostics {
  status?: string | null
  text_chars?: number | null
  methods?: string[] | null
  pages_total?: number | null
  pages_failed?: number[] | null
  segment_count?: number | null
  warnings?: string[] | null
}

export interface ParseDiagnostics {
  stage?: string | null
  status?: string | null
  reason_code?: string | null
  message?: string | null
  detail?: string | null
  source_id?: string | null
  title?: string | null
  file_type?: string | null
  file_name?: string | null
  attempted_methods?: string[] | null
  method_attempts?: ParseMethodAttempt[] | null
  document_parse?: DocumentParseDiagnostics | null
  retryable?: boolean | null
}

export interface ParseDiagnosticsSummary {
  method: string
  text: string
  attemptCount: number
}

const METHOD_LABELS: Record<string, string> = {
  dispatch: '格式分派',
  read_text: '文本读取',
  raw_text_cache: '已有文本',
  pymupdf_text: 'PDF文本层',
  pdfium_text: 'PDF文本层',
  pdfplumber_text: 'PDF文本层',
  pdf_text: 'PDF文本层',
  python_docx: 'Word解析',
  docx: 'Word解析',
  ocr: 'OCR',
  tesseract_ocr: 'OCR',
  pdf_ocr: 'OCR',
  openai_vision: '视觉解析',
  openrouter_vision: '视觉解析',
  vision: '视觉解析',
}

export function formatParseMethod(method?: string | null) {
  if (!method) return '解析'
  const normalized = method.toLowerCase()
  return METHOD_LABELS[normalized] || method
}

function formatCount(attempt: ParseMethodAttempt) {
  if (typeof attempt.chars === 'number' && attempt.chars > 0) {
    return `${attempt.chars.toLocaleString('zh-CN')} 字符`
  }
  if (typeof attempt.pages === 'number' && attempt.pages > 0) {
    return `${attempt.pages.toLocaleString('zh-CN')} 页`
  }
  return null
}

function formatDocumentParseCount(documentParse: DocumentParseDiagnostics) {
  const parts: string[] = []
  if (typeof documentParse.text_chars === 'number' && documentParse.text_chars > 0) {
    parts.push(`${documentParse.text_chars.toLocaleString('zh-CN')} 字符`)
  }
  if (typeof documentParse.pages_total === 'number' && documentParse.pages_total > 0) {
    parts.push(`${documentParse.pages_total.toLocaleString('zh-CN')} 页`)
  }
  const failedPages = documentParse.pages_failed?.filter((page) => Number.isFinite(page) && page > 0) || []
  if (failedPages.length > 0) {
    parts.push(`${failedPages.length.toLocaleString('zh-CN')} 页需复核`)
  }
  return parts.length > 0 ? parts.join(' · ') : null
}

function findLastAttempt(
  attempts: ParseMethodAttempt[],
  predicate: (attempt: ParseMethodAttempt) => boolean,
) {
  for (let idx = attempts.length - 1; idx >= 0; idx -= 1) {
    if (predicate(attempts[idx])) return attempts[idx]
  }
  return undefined
}

export function summarizeParseDiagnostics(diagnostics?: ParseDiagnostics | null): ParseDiagnosticsSummary | null {
  const attempts = diagnostics?.method_attempts?.filter(Boolean) || []
  const documentParse = diagnostics?.document_parse || null

  if (documentParse && (
    documentParse.status === 'completed' ||
    documentParse.status === 'partial' ||
    (typeof documentParse.text_chars === 'number' && documentParse.text_chars > 0)
  )) {
    const method = formatParseMethod(documentParse.methods?.[0] || attempts[0]?.method)
    const count = formatDocumentParseCount(documentParse)
    return {
      method,
      text: count ? `解析方式：${method} · ${count}` : `解析方式：${method}`,
      attemptCount: attempts.length,
    }
  }

  if (attempts.length === 0) return null

  const successfulAttempt =
    findLastAttempt(attempts, (attempt) => attempt.status === 'success' && typeof attempt.chars === 'number' && attempt.chars > 0) ||
    findLastAttempt(attempts, (attempt) => attempt.status === 'success')
  const representativeAttempt = successfulAttempt || attempts[attempts.length - 1]
  if (!representativeAttempt) return null

  const method = formatParseMethod(representativeAttempt.method)
  const count = formatCount(representativeAttempt)
  const attemptCount = attempts.length

  if (diagnostics?.status === 'completed' || successfulAttempt) {
    return {
      method,
      text: count ? `解析方式：${method} · ${count}` : `解析方式：${method}`,
      attemptCount,
    }
  }

  return {
    method,
    text: `解析尝试：${method} 未完成 · ${attemptCount} 次尝试`,
    attemptCount,
  }
}
