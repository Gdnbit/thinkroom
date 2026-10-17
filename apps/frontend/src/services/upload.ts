import api from './api'
import type { ParseDiagnostics } from './parseDiagnostics'

export interface UploadResponse {
  batch_id: string
  file_count: number
  source_ids: string[]
  job_ids?: string[]
  session_id?: string | null
  receipt_message_id?: string | null
  status: string
  message: string
  statuses?: IngestionStatus[]
  timed_out?: boolean
}

export interface IngestionStatus {
  source_id: string
  title: string
  status: string
  error?: string | null
  word_count?: number
  char_count?: number
  word_count_est?: number | null
  token_count_est?: number
  long_document_flag?: boolean
  processing_stage?: string
  claim_extraction_status?: string
  claims_count?: number
  parse_diagnostics?: ParseDiagnostics | null
}

export interface IngestionProgressStage {
  key: string
  label: string
  status: string
  elapsed_ms?: number | null
  detail?: string | null
}

export interface IngestionProgress {
  job_id: string
  source_id?: string | null
  session_id?: string | null
  workflow: string
  status: string
  current_stage?: string | null
  current_stage_label?: string | null
  current_stage_elapsed_ms?: number | null
  stages: IngestionProgressStage[]
  progress_pct: number
  elapsed_ms: number
  message?: string
  error?: string | null
  result_summary?: {
    parse_diagnostics?: ParseDiagnostics | null
    [key: string]: unknown
  } | null
}

export interface ProcessSourceResponse {
  queued?: boolean
  source_id?: string
  job_id?: string
  progress_url?: string
  message?: string
  parse_diagnostics?: ParseDiagnostics | null
}

export interface ProcessSelectedSourcesResponse {
  queued: boolean
  limit?: number
  queued_count: number
  source_ids: string[]
  job_ids: string[]
  jobs: Array<ProcessSourceResponse & { dispatch_status?: Record<string, unknown> }>
  session_id?: string | null
  message?: string
}

export interface CancelIngestionJobResponse {
  job_id: string
  status: string
  source_id?: string | null
  already_cancelled?: boolean
  message?: string
}

export const uploadApi = {
  upload: (
    files: File[],
    speakerHint?: string,
    themeHint?: string,
    mergeImages?: boolean,
    sessionId?: string | null,
    signal?: AbortSignal,
  ) => {
    const form = new FormData()
    files.forEach(f => form.append('files', f))
    if (speakerHint) form.append('speaker_hint', speakerHint)
    if (themeHint) form.append('theme_hint', themeHint)
    if (mergeImages) form.append('merge_images', 'true')
    if (sessionId) form.append('session_id', sessionId)
    return api.post<UploadResponse>('/api/v1/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      signal,
    })
  },
  processSource: (
    sourceId: string,
    asyncMode = true,
    sessionIdOrSignal?: string | null | AbortSignal,
    signal?: AbortSignal,
  ) => {
    const sessionId = typeof sessionIdOrSignal === 'string' ? sessionIdOrSignal : null
    const requestSignal = typeof sessionIdOrSignal === 'string' || sessionIdOrSignal == null
      ? signal
      : sessionIdOrSignal
    const params: Record<string, string | boolean> = { async_mode: asyncMode }
    if (sessionId) params.session_id = sessionId
    return api.post<ProcessSourceResponse>(
      `/api/v1/ingestion/process/${sourceId}`,
      null,
      { params, signal: requestSignal },
    )
  },
  processSelectedSources: (
    sourceIds: string[],
    asyncMode = true,
    sessionIdOrSignal?: string | null | AbortSignal,
    signal?: AbortSignal,
  ) => {
    const sessionId = typeof sessionIdOrSignal === 'string' ? sessionIdOrSignal : null
    const requestSignal = typeof sessionIdOrSignal === 'string' || sessionIdOrSignal == null
      ? signal
      : sessionIdOrSignal
    const body: Record<string, unknown> = {
      source_ids: sourceIds,
      async_mode: asyncMode,
    }
    if (sessionId) body.session_id = sessionId
    return api.post<ProcessSelectedSourcesResponse>(
      '/api/v1/ingestion/process-selected',
      body,
      { signal: requestSignal },
    )
  },
  cancelJob: (jobId: string, reason = 'user_cancelled_from_chat_upload') =>
    api.post<CancelIngestionJobResponse>(`/api/v1/ingestion/jobs/${jobId}/cancel`, { reason }).then(r => r.data),
  getProgress: (jobId: string) =>
    api.get<IngestionProgress>(`/api/v1/progress/${jobId}`).then(r => r.data),
  getStatus: (sourceId: string) =>
    api.get<IngestionStatus>(`/api/v1/ingestion/status/${sourceId}`).then(r => r.data),
  getBatchStatus: (sourceIds: string[]) =>
    api.post<IngestionStatus[]>('/api/v1/ingestion/status/batch', sourceIds).then(r => r.data),
}
