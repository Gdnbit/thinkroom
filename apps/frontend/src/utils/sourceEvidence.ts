export type EvidenceCitation = {
  index: number
  claim_id: string
  detail_available?: boolean
  source_id?: string
  evidence_span_id?: string
  span_type?: string
  char_start?: number | null
  char_end?: number | null
  page_or_timestamp?: string
  extraction_method?: string
  stance: string
  summary: string
  short_quote: string
  trust_weight: number
  confidence: number
  time_horizon?: string | null
  speaker_id?: string | null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readString(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback
}

function readNumber(value: unknown, fallback = 0) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function readSourceIds(payload: Record<string, unknown>) {
  const scope = isRecord(payload.scope) ? payload.scope : {}
  const raw = Array.isArray(scope.source_ids) ? scope.source_ids : []
  return raw.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

function normalizeStructuredCitation(item: Record<string, unknown>, idx: number): EvidenceCitation {
  const claimId = readString(item.claim_id)
  return {
    index: readNumber(item.index, idx + 1),
    claim_id: claimId || `citation-${idx + 1}`,
    detail_available: Boolean(claimId && !claimId.startsWith('source:')),
    source_id: readString(item.source_id),
    evidence_span_id: readString(item.evidence_span_id),
    span_type: readString(item.span_type),
    char_start: typeof item.char_start === 'number' ? item.char_start : null,
    char_end: typeof item.char_end === 'number' ? item.char_end : null,
    page_or_timestamp: readString(item.page_or_timestamp),
    extraction_method: readString(item.extraction_method),
    stance: readString(item.stance, 'neutral'),
    summary: readString(item.summary, readString(item.short_quote, '未提供摘要')),
    short_quote: readString(item.short_quote),
    trust_weight: readNumber(item.trust_weight),
    confidence: readNumber(item.confidence),
    time_horizon: readString(item.time_horizon) || null,
    speaker_id: readString(item.speaker_id) || null,
  }
}

export function buildEvidenceCitations(payload: Record<string, unknown>): EvidenceCitation[] {
  const structured = Array.isArray(payload.citations) ? payload.citations.filter(isRecord) : []
  if (structured.length > 0) {
    return structured.map(normalizeStructuredCitation)
  }

  const dataSources = isRecord(payload.data_sources) ? payload.data_sources : {}
  const claims = isRecord(dataSources.claims) && Array.isArray(dataSources.claims.items)
    ? dataSources.claims.items.filter(isRecord)
    : []
  const events = isRecord(dataSources.events) && Array.isArray(dataSources.events.items)
    ? dataSources.events.items.filter(isRecord)
    : []
  const mode = readString(payload.mode, 'evidence')
  const sourceIds = readSourceIds(payload)
  const defaultSourceId = sourceIds[0]
  const citations: EvidenceCitation[] = []

  claims.forEach((claim, idx) => {
    const summary = readString(claim.summary, '未提供摘要')
    citations.push({
      index: citations.length + 1,
      claim_id: `${mode}-claim-${idx + 1}`,
      detail_available: false,
      source_id: defaultSourceId,
      stance: readString(claim.stance, 'neutral'),
      summary,
      short_quote: summary,
      trust_weight: readNumber(claim.trust_weight, readNumber(claim.confidence)),
      confidence: readNumber(claim.confidence),
      time_horizon: readString(claim.time_horizon) || null,
      speaker_id: null,
    })
  })

  events.forEach((event, idx) => {
    const title = readString(event.title, readString(event.summary, '未提供事件摘要'))
    const source = readString(event.source)
    citations.push({
      index: citations.length + 1,
      claim_id: `${mode}-event-${idx + 1}`,
      detail_available: false,
      source_id: source || defaultSourceId,
      stance: readString(event.impact_direction, 'neutral'),
      summary: title,
      short_quote: title,
      trust_weight: readNumber(event.importance, 0.5),
      confidence: readNumber(event.confidence, 0.5),
      time_horizon: null,
      speaker_id: null,
    })
  })

  return citations
}
