import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import api, { apiLong } from './api'

// ── Briefings ────────────────────────────────────────────
export interface BriefingRequest {
  question: string
  asset_scope?: string[]
  source_ids?: string[]
  time_horizon?: string
  committee_level?: 'single' | 'double' | 'triple'
  history?: Array<{ role: 'user' | 'assistant'; content: string }>
}

export interface DecisionDataSources {
  claims: {
    is_mock: boolean
    count: number
    items: { summary: string; stance?: string; confidence?: number; trust_weight?: number }[]
  }
  events: {
    is_mock: boolean
    count: number
    items: { title?: string; summary?: string; source?: string; importance?: number; impact_direction?: string }[]
  }
  market: {
    is_mock: boolean
    asset: string
    snapshot: Record<string, unknown>
  }
  flow: {
    is_mock: boolean
    signals_count: number
    snapshot: Record<string, unknown>
  }
  agent_retrieval?: {
    counts?: Record<string, number>
  }
}

export interface Briefing {
  id: string
  title: string
  topic: string
  direction: string
  setup_quality: string
  executive_summary: string
  supporting_points: string[]
  contrary_points: string[]
  key_risks: string[]
  strategy_fit_summary?: string
  recommended_action: string
  invalidation_conditions: string[]
  is_mock?: boolean
  source_mode?: string
  scope?: Record<string, unknown>
  model_used?: string
  duration_ms?: number
  cost_usd?: number
  llm_call_count?: number
  decision_guardrail?: Record<string, unknown>
  quality_report?: Record<string, unknown>
  quality_filters?: Record<string, unknown>
  llm_trace?: Record<string, unknown>
  timing_trace?: Record<string, number>
  total_input_tokens?: number
  total_output_tokens?: number
  conviction?: number
  created_at: string
  data_sources?: DecisionDataSources
}

export function useBriefings(limit = 20) {
  return useQuery({
    queryKey: ['briefings', limit],
    queryFn: () => api.get<Briefing[]>('/api/v1/briefings', { params: { limit } }).then(r => r.data),
  })
}

export interface ResearchPlan {
  asset: string
  is_open_ended?: boolean
  secondary_assets: string[]
  preliminary_direction: string
  preliminary_conviction: number
  key_hypotheses: string[]
  open_questions: string[]
  reasoning: string
  model_used: string
}

export interface PlanAndResearchResult {
  plan: ResearchPlan
  data_fetched: {
    market_data: boolean
    flow_signals: number
    events: number
    claims: number
    web_results: number
    assets_fetched: string[]
    sources: {
      private_claims: Array<{ summary: string; stance: string; trust_score: number; confidence: number }>
      market_snapshot: Record<string, unknown> | null
      flow_signals: Array<{ signal_type: string; raw_value: string; description: string }>
      news_events: Array<{ title: string; source: string; importance: number }>
      web_search: Array<{ title: string; url: string; snippet: string; source: string; score: number }>
    }
  }
  briefing: Briefing
}

export function useGenerateBriefing() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (req: BriefingRequest) =>
      api.post<Briefing>('/api/v1/briefings', req).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['briefings'] }),
  })
}

export function usePlanAndResearch() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (req: BriefingRequest) =>
      api.post<PlanAndResearchResult>('/api/v1/briefings/plan-and-research', req).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['briefings'] }),
  })
}

// ── Claims ───────────────────────────────────────────────
export interface Claim {
  id: string
  summary: string
  claim_type: string
  stance: string
  time_horizon: string
  status: string
  confidence: number
  asset_scope: string[]
  theme_scope: string[]
  speaker_name?: string
  created_at: string
}

export function useClaims(params?: {
  q?: string
  asset?: string
  theme?: string
  status?: string
  limit?: number
}) {
  return useQuery({
    queryKey: ['claims', params],
    queryFn: () =>
      api.get<{ claims: Claim[]; total: number; has_more: boolean }>('/api/v1/claims', { params })
        .then(r => r.data),
  })
}

// ── Events ───────────────────────────────────────────────
export interface MarketEvent {
  id: string
  event_type: string
  title: string
  summary?: string
  asset_scope: string[]
  theme_scope: string[]
  importance: number
  impact_direction?: string
  event_timestamp: string
  truth_status?: string
  outcome_status?: string
  source_tier?: string
}

export function useEvents(params?: {
  asset?: string
  theme?: string
  importance_min?: number
  limit?: number
}) {
  return useQuery({
    queryKey: ['events', params],
    queryFn: () =>
      api.get<MarketEvent[]>('/api/v1/events', { params }).then(r => r.data),
    refetchInterval: 600_000, // 10分钟（后端事件源有1小时冷却）
    staleTime: 300_000,
  })
}

// ── Market State ─────────────────────────────────────────
export function useMarketState(asset: string, timeframe = '1d') {
  return useQuery({
    queryKey: ['market', asset, timeframe],
    queryFn: () =>
      api.get<Record<string, unknown>>(`/api/v1/signals/market/${asset}`, { params: { timeframe } })
        .then(r => r.data),
    enabled: !!asset,
    refetchInterval: 300_000, // 5分钟（匹配后端 FAST 冷却）
    staleTime: 120_000,
  })
}

export interface IndicatorResult {
  asset: string
  overall_signal: string
  signal_strength: number
  summary: string
  interpretations: string[]
  warnings: string[]
  suggested_bias: string
}

export function useIndicator(asset: string, timeframe = '1d') {
  return useQuery({
    queryKey: ['indicator', asset, timeframe],
    queryFn: () =>
      api.get<IndicatorResult>(`/api/v1/signals/indicator/${asset}`, { params: { timeframe } })
        .then(r => r.data),
    enabled: !!asset,
    refetchInterval: 300_000,
    staleTime: 120_000,
  })
}

// ── Watchlists ───────────────────────────────────────────
export interface Watchlist {
  id: string
  name: string
  description?: string
  list_type: string
  status: string
  created_at: string
  item_count?: number  // optional: 后端未返回时为 undefined
}

export function useWatchlists() {
  return useQuery({
    queryKey: ['watchlists'],
    queryFn: () => api.get<Watchlist[]>('/api/v1/watchlists').then(r => r.data),
  })
}

export function useCreateWatchlist() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: { name: string; list_type: string; description?: string }) =>
      api.post<Watchlist>('/api/v1/watchlists', data).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watchlists'] }),
  })
}

// ── Strategies ──────────────────────────────────────────
export interface Strategy {
  id: string
  strategy_name: string
  strategy_type: string
  summary: string
  core_logic?: string
  applicable_assets: string[]
  applicable_horizons: string[]
  applicable_regimes?: string[]
  entry_conditions: string[]
  disqualifying_conditions?: string[]
  exit_rules?: string[]
  risk_factors?: string[]
  confidence: number
  historical_win_rate?: number
  distilled_by?: string
  status: string
  created_at: string
}

export interface SetupResult {
  setup_id: string
  strategy_id: string
  strategy_name: string
  asset: string
  direction: string
  strategy_fit_score: number
  evidence_strength_score: number
  overall_score: number
  matched_conditions: string[]
  unmatched_conditions: string[]
  disqualifying_flags: string[]
  recommended_action: string
  action_notes: string
  invalidation_conditions: string[]
  is_mock?: boolean
  source_mode?: string
}

export function useStrategies(params?: { asset?: string; strategy_type?: string; limit?: number }) {
  return useQuery({
    queryKey: ['strategies', params],
    queryFn: () =>
      api.get<Strategy[]>('/api/v1/strategies', { params }).then(r => r.data),
  })
}

export function useStrategy(id: string) {
  return useQuery({
    queryKey: ['strategy', id],
    queryFn: () => api.get<Strategy>(`/api/v1/strategies/${id}`).then(r => r.data),
    enabled: !!id,
  })
}

export function useDistillStrategy() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (sourceId: string) =>
      api.post<{ success: boolean; strategy_id?: string; strategy_name?: string; error?: string }>(
        `/api/v1/strategies/distill/${sourceId}`
      ).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['strategies'] }),
  })
}

export function useEvaluateStrategy() {
  return useMutation({
    mutationFn: ({ strategyId, asset }: { strategyId: string; asset: string }) =>
      api.post<SetupResult>(`/api/v1/strategies/${strategyId}/evaluate`, asset, {
        headers: { 'Content-Type': 'application/json' },
      }).then(r => r.data),
  })
}

// ── Macro Data (FRED) ──────────────────────────────────
export interface FredSeries {
  label: string
  value: number | null
  date: string
  history: { date: string; value: number }[]
}

export type MacroData = Record<string, FredSeries>

export function useMacroData() {
  return useQuery({
    queryKey: ['macro'],
    queryFn: () =>
      api.get<MacroData>('/api/v1/signals/macro').then(r => r.data),
    refetchInterval: 3_600_000, // 1小时（FRED 每日配额）
    staleTime: 1_800_000,
  })
}

// ── Flow Signals ─────────────────────────────────────────
export interface FlowSignal {
  signal_type: string
  asset: string
  direction: string
  magnitude: number
  raw_value: number
  normalized_value?: number
  description: string
  data_source: string
  observed_at: string
}

export function useFlowSummary(asset: string) {
  return useQuery({
    queryKey: ['flow-summary', asset],
    queryFn: () =>
      api.get<{ asset: string; overall_direction: string; signal_count: number; bullish_signals: number; bearish_signals: number; avg_magnitude: number; signals: FlowSignal[] }>(
        `/api/v1/signals/flow/${asset}/summary`
      ).then(r => r.data),
    enabled: !!asset,
    refetchInterval: 300_000, // 5分钟（匹配后端 MEDIUM 冷却）
    staleTime: 120_000,
  })
}

// ── Alerts / Notifications ───────────────────────────────
export interface Alert {
  id: string
  type: string
  title: string
  message: string
  severity: 'info' | 'warning' | 'success' | 'error'
  related_id?: string
  related_type?: string
  created_at: string
}

export function useAlerts() {
  return useQuery({
    queryKey: ['alerts'],
    queryFn: () =>
      api.get<{ alerts: Alert[]; total: number }>('/api/v1/alerts').then(r => r.data),
    refetchInterval: 60000,
  })
}

// ── G13: 社媒登录态聚合健康（侧栏红点 + Dashboard banner 共用） ────
export interface SocialPlatformHealth {
  platform: string
  label: string
  has_cookie: boolean
  remaining_days: number | null
  is_expired: boolean
  needs_refresh: boolean
  source: string | null
}

export interface SocialHealth {
  needs_attention: boolean
  expired_count: number
  expiring_soon_count: number
  total_configured: number
  total_platforms: number
  platforms: SocialPlatformHealth[]
}

export function useSocialHealth() {
  return useQuery({
    queryKey: ['social-health'],
    queryFn: () =>
      api.get<{ success: boolean; data: SocialHealth }>('/api/v1/social-media/health')
        .then(r => r.data.data),
    // 5 分钟刷新一次（cookie 状态变化慢）
    refetchInterval: 5 * 60 * 1000,
    // 网络错误别让红点常亮（fail silently）
    retry: 1,
    staleTime: 60_000,
  })
}

// ── Committee / Debate ──────────────────────────────────
export interface DebateRound {
  id: string
  round_number: number
  role: string  // baseline | probability | risk | structure | audit | pnl (V1: bull | bear | neutral)
  model_used?: string
  position: string
  key_arguments: string[]
  challenges: string[]
  concessions: string[]
  confidence?: number
  direction?: string
  latency_ms?: number
  is_mock: boolean
}

export interface DebateAdjudication {
  id: string
  direction: string
  conviction: number
  recommended_action: string
  consensus_level: string
  synthesis: string
  bull_score?: number
  bear_score?: number
  baseline_score?: number
  probability_score?: number
  risk_score?: number
  structure_score?: number
  structure_warnings?: string[]
  risk_warnings?: string[]
  hedging_advice?: string
  action_economics?: Record<string, unknown>
  evidence_quality_warnings?: string[]
  key_agreements: string[]
  key_disagreements: string[]
  minority_view?: string
  invalidation_conditions: string[]
  adjudicated_by?: string
  is_mock: boolean
}

export interface DebateRecord {
  id: string
  question: string
  asset: string
  committee_level: string
  model_bull?: string
  model_bear?: string
  model_neutral?: string
  status: string
  final_direction?: string
  final_conviction?: number
  final_action?: string
  consensus_level?: string
  started_at?: string
  completed_at?: string
  duration_ms?: number
  error_message?: string
  created_at: string
  rounds?: DebateRound[]
  adjudication?: DebateAdjudication
  decision_guardrail?: Record<string, unknown>
  quality_report?: Record<string, unknown>
  quality_filters?: Record<string, unknown>
  claims_used?: number
  source_claim_ids?: string[]
  scope?: Record<string, unknown>
  llm_trace?: Record<string, unknown> | null
  timing_trace?: Record<string, number> | null
  cost_usd?: number | null
  llm_call_count?: number | null
}

export function useDebates(params?: { asset?: string; status?: string; limit?: number }) {
  return useQuery({
    queryKey: ['debates', params],
    queryFn: () =>
      api.get<{ debates: DebateRecord[]; total: number; has_more: boolean }>(
        '/api/v1/committee', { params }
      ).then(r => r.data),
  })
}

export function useDebate(id: string) {
  return useQuery({
    queryKey: ['debate', id],
    queryFn: () => api.get<DebateRecord>(`/api/v1/committee/${id}`).then(r => r.data),
    enabled: !!id,
  })
}

export interface CommitteeRunResult {
  debate_id: string
  direction: string
  conviction: number
  synthesis: string
  is_mock: boolean
  recommended_action?: string
  consensus_level?: string
  cost_usd?: number
  duration_ms?: number
  activated_seats?: string[]
  risk_warnings?: string[]
  structure_warnings?: string[]
  evidence_quality_warnings?: string[]
  action_economics?: Record<string, unknown>
  decision_guardrail?: Record<string, unknown>
  quality_report?: Record<string, unknown>
  quality_filters?: Record<string, unknown>
  llm_trace?: Record<string, unknown>
  timing_trace?: Record<string, number>
  scope?: Record<string, unknown>
  data_sources?: DecisionDataSources
  hedging_advice?: string
}

export function useRunCommittee() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: { question: string; asset: string; num_rounds?: number; source_ids?: string[] }) =>
      api.post<CommitteeRunResult>(
        '/api/v1/committee/run', data
      ).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['debates'] }),
  })
}

// ── Outcomes ─────────────────────────────────────────────
export interface OutcomeRecord {
  id: string
  briefing_id?: string
  claim_id?: string
  setup_id?: string
  outcome_type: string  // hit | miss | partial_hit | invalidated | not_triggered
  is_correct?: boolean
  actual_direction?: string
  predicted_direction?: string
  entry_price?: number
  exit_price?: number
  pnl_pct?: number
  max_drawdown_pct?: number
  prediction_made_at?: string
  outcome_observed_at?: string
  horizon_days?: number
  review_notes?: string
  what_went_right?: string
  what_went_wrong?: string
  lesson_learned?: string
  logged_by: string
  created_at: string
  updated_at: string
}

export interface OutcomeStats {
  total: number
  hit_count: number
  miss_count: number
  partial_hit_count: number
  not_triggered_count: number
  invalidated_count: number
  hit_rate: number
  correct_rate: number
  avg_pnl_pct?: number
}

export interface OutcomeCreateRequest {
  briefing_id?: string
  claim_id?: string
  setup_id?: string
  outcome_type: string
  is_correct?: boolean
  actual_direction?: string
  predicted_direction?: string
  entry_price?: number
  exit_price?: number
  pnl_pct?: number
  max_drawdown_pct?: number
  horizon_days?: number
  review_notes?: string
  what_went_right?: string
  what_went_wrong?: string
  lesson_learned?: string
  logged_by?: string
}

export function useOutcomeStats() {
  return useQuery({
    queryKey: ['outcome-stats'],
    queryFn: () => api.get<OutcomeStats>('/api/v1/outcomes/stats').then(r => r.data),
  })
}

export function useOutcomes(params?: {
  outcome_type?: string
  is_correct?: boolean
  limit?: number
  offset?: number
}) {
  return useQuery({
    queryKey: ['outcomes', params],
    queryFn: () =>
      api.get<{ outcomes: OutcomeRecord[]; total: number; has_more: boolean }>(
        '/api/v1/outcomes', { params }
      ).then(r => r.data),
  })
}

export function useCreateOutcome() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: OutcomeCreateRequest) =>
      api.post<OutcomeRecord>('/api/v1/outcomes', data).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['outcomes'] })
      qc.invalidateQueries({ queryKey: ['outcome-stats'] })
    },
  })
}

export function useDeleteOutcome() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/outcomes/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['outcomes'] })
      qc.invalidateQueries({ queryKey: ['outcome-stats'] })
    },
  })
}

// ── Upload ───────────────────────────────────────────────
export function useUpload() {
  return useMutation({
    mutationFn: ({
      files,
      speakerHint,
      themeHint,
      mergeImages,
    }: {
      files: File[]
      speakerHint?: string
      themeHint?: string
      mergeImages?: boolean
    }) => {
      const form = new FormData()
      files.forEach(f => form.append('files', f))
      if (speakerHint) form.append('speaker_hint', speakerHint)
      if (themeHint) form.append('theme_hint', themeHint)
      if (mergeImages) form.append('merge_images', 'true')
      return api
        .post<{ batch_id: string; file_count: number; source_ids: string[]; status: string; message: string }>(
          '/api/v1/upload',
          form,
          { headers: { 'Content-Type': 'multipart/form-data' } }
        )
        .then(r => r.data)
    },
  })
}


// ── Chat Sessions ────────────────────────────────────────
export interface ChatSession {
  id: string
  title: string
  mode: string
  asset: string
  updated_at: string
  message_count: number
  is_system_test?: boolean
}
export interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  payload: any
  created_at: string
}
export function useChatSessions(requestedSessionId?: string | null) {
  return useQuery({
    queryKey: ['chat-sessions', { requestedSessionId: requestedSessionId ?? null }],
    queryFn: () => api.get<ChatSession[]>('/api/v1/chat/sessions', {
      params: requestedSessionId ? { requested_session_id: requestedSessionId } : undefined,
    }).then(r => r.data),
    refetchOnWindowFocus: false,
  })
}
export function useChatMessages(sessionId: string | null) {
  return useQuery({ queryKey: ['chat-messages', sessionId], queryFn: () => sessionId ? api.get<ChatMsg[]>(`/api/v1/chat/sessions/${sessionId}/messages`).then(r => r.data) : Promise.resolve([]), enabled: !!sessionId })
}
export function useCreateSession() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (body: { title?: string; mode?: string; asset?: string }) => api.post<{id:string;title:string;mode:string;asset:string}>('/api/v1/chat/sessions', body).then(r => r.data), onSuccess: () => qc.invalidateQueries({ queryKey: ['chat-sessions'] }) })
}
export function useDeleteSession() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: (id: string) => api.delete(`/api/v1/chat/sessions/${id}`).then(r => r.data), onSuccess: () => qc.invalidateQueries({ queryKey: ['chat-sessions'] }) })
}
export function useAddMessage() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: ({ sessionId, role, content, payload }: { sessionId: string; role: string; content: string; payload?: any }) => api.post(`/api/v1/chat/sessions/${sessionId}/messages`, { role, content, payload: payload ?? {} }).then(r => r.data), onSuccess: (_,v) => qc.invalidateQueries({ queryKey: ['chat-messages', v.sessionId] }) })
}
export function useUpdateSession() {
  const qc = useQueryClient()
  return useMutation({ mutationFn: ({ id, title }: { id: string; title: string }) => api.patch(`/api/v1/chat/sessions/${id}`, { title }).then(r => r.data), onSuccess: () => qc.invalidateQueries({ queryKey: ['chat-sessions'] }) })
}
export function useSendChat() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ sessionId, message, history, scopeMode, sourceIds }: { sessionId: string; message: string; history?: Array<{role:string;content:string}>; scopeMode?: string; sourceIds?: string[] }) =>
      api.post<{id:string;content:string;claims_used:number;asset_detected:string|null}>(
        `/api/v1/chat/sessions/${sessionId}/chat`,
        { message, history: history ?? [], scope_mode: scopeMode, source_ids: sourceIds ?? [] }
      ).then(r => r.data),
    onSuccess: (_,v) => qc.invalidateQueries({ queryKey: ['chat-messages', v.sessionId] }),
  })
}

export interface DecisionTraceEvent {
  id: string
  related_object_type: string
  related_object_id: string
  run_id?: string | null
  session_id?: string | null
  job_id?: string | null
  event_type: 'scope' | 'quality' | 'guardrail' | 'llm_call' | string
  step_name?: string | null
  status: string
  model?: string | null
  skill_role?: string | null
  error_type?: string | null
  duration_ms?: number | null
  cost_usd?: number | null
  source_ids: string[]
  payload: Record<string, unknown>
  created_at?: string | null
  updated_at?: string | null
}

export interface DecisionTraceList {
  count: number
  items: DecisionTraceEvent[]
}

export interface DecisionTraceTrustReceipt {
  related_object_type: string
  related_object_id: string
  event_count: number
  event_types: string[]
  trace_source_ids: string[]
  expected_source_ids: string[]
  covered_expected_source_ids: string[]
  missing_expected_source_ids: string[]
  source_coverage_ratio: number | null
  coverage_status: 'no_trace' | 'no_expected_sources' | 'missing_sources' | 'covered' | string
  has_llm_trace: boolean
  llm_call_count: number
  llm_source_ids: string[]
  models: string[]
  requested_models: string[]
  configured_fallback_models: string[]
  warning_count: number
  warning_event_types: string[]
  total_duration_ms?: number | null
  total_cost_usd?: number | null
}

export function useDecisionTraces(params: {
  relatedObjectType?: string
  relatedObjectId?: string
  eventType?: string
  sourceId?: string
  limit?: number
  enabled?: boolean
}) {
  return useQuery({
    queryKey: ['decision-traces', params],
    queryFn: () => api.get<DecisionTraceList>('/api/v1/decision-traces', {
      params: {
        related_object_type: params.relatedObjectType,
        related_object_id: params.relatedObjectId,
        event_type: params.eventType,
        source_id: params.sourceId,
        limit: params.limit ?? 50,
      },
    }).then(r => r.data),
    enabled: params.enabled !== false,
    staleTime: 30_000,
  })
}

export function useDecisionTraceTrustReceipt(params: {
  relatedObjectType?: string
  relatedObjectId?: string
  expectedSourceIds?: string[]
  limit?: number
  enabled?: boolean
}) {
  return useQuery({
    queryKey: ['decision-trace-trust-receipt', params],
    queryFn: () => {
      const query = new URLSearchParams()
      query.set('related_object_type', params.relatedObjectType ?? '')
      query.set('related_object_id', params.relatedObjectId ?? '')
      query.set('limit', String(params.limit ?? 100))
      for (const sourceId of params.expectedSourceIds ?? []) {
        const value = sourceId.trim()
        if (value) query.append('expected_source_ids', value)
      }
      return api.get<DecisionTraceTrustReceipt>(`/api/v1/decision-traces/trust-receipt?${query.toString()}`).then(r => r.data)
    },
    enabled: params.enabled !== false && Boolean(params.relatedObjectType && params.relatedObjectId),
    staleTime: 30_000,
  })
}

// ── Mode Router (三模式融合) ──────────────────────────────
export interface RoutingDecision {
  route: 'chat' | 'briefing' | 'debate'
  reason: string
  confidence: number
  intent_mode: string
  intent_confidence: number
  assets: string[]
  force_source: 'slash' | 'api' | null
  cost_estimate_usd: number
  can_upgrade_to: string[]
  can_downgrade_to: string | null
}
export interface AutoRouteResponse {
  routing: RoutingDecision
  cleaned_message: string
  original_message: string
}
export function useAutoRoute() {
  return useMutation({
    mutationFn: ({ sessionId, message, forceRoute }: { sessionId: string; message: string; forceRoute?: string }) =>
      api.post<AutoRouteResponse>(
        `/api/v1/chat/sessions/${sessionId}/auto`,
        { message, force_route: forceRoute ?? null }
      ).then(r => r.data),
  })
}

// ── B6: Routing Feedback ─────────────────────────────────────
export interface RoutingFeedbackPayload {
  message_id: string
  auto_route: string
  chosen_route?: string
  is_helpful?: boolean | null
  message?: string
  intent_mode?: string
  intent_confidence?: number
  reason?: string
  assets?: string[]
}

export function useRoutingFeedback() {
  return useMutation({
    mutationFn: (body: RoutingFeedbackPayload) =>
      api.post('/api/v1/routing-feedback', body).then(r => r.data),
  })
}

export interface RoutingFeedbackStats {
  total_feedback: number
  buckets: Array<{
    auto_route: string
    total: number
    helpful: number
    unhelpful: number
    no_feedback: number
    switched: number
    helpful_rate: number
    accept_rate: number
  }>
  top_switches: Array<{ from: string; to: string; count: number }>
}

export function useRoutingFeedbackStats(limit = 500) {
  return useQuery({
    queryKey: ['routing-feedback-stats', limit],
    queryFn: () =>
      api.get<RoutingFeedbackStats>(`/api/v1/routing-feedback/stats?limit=${limit}`).then(r => r.data),
    staleTime: 60_000,
  })
}

// ── Decision Replay ──────────────────────────────────────

export interface ReplaySnapshot {
  id: string
  trigger_type: string
  trigger_id: string
  asset: string
  question: string
  snapshot_at: string | null
  final_direction: string | null
  final_conviction: number | null
  final_action: string | null
  activated_seats: string[]
  claims_count: number
  replay_status: string
  outcome_direction: string | null
  outcome_pnl_pct: number | null
  record_origin?: string
  visibility_scope?: 'user_primary' | 'workflow_history' | 'system_test' | string
  is_system_test?: boolean
}

export interface ReplayDetail extends ReplaySnapshot {
  claims_snapshot: Array<Record<string, unknown>>
  data_coverage: Record<string, unknown>
  market_snapshot: Record<string, unknown>
  flow_snapshot: Record<string, unknown>
  events_snapshot: Array<Record<string, unknown>>
  macro_snapshot: Record<string, unknown>
  signal_scorecard: Record<string, unknown>
  evidence_budget: Record<string, unknown>
  synthesis_summary: string | null
  outcome_observed_at: string | null
  replay_notes: string | null
  misleading_evidence: Array<{ type: string; description: string }>
  key_missed_signals: string[]
}

export function useReplaySnapshots(
  asset?: string,
  options: { includeSystem?: boolean; limit?: number } = {},
) {
  const includeSystem = options.includeSystem ?? false
  const limit = options.limit ?? 100
  return useQuery<ReplaySnapshot[]>({
    queryKey: ['replay-snapshots', asset, includeSystem, limit],
    queryFn: () =>
      api.get('/api/v1/replay', {
        params: {
          ...(asset ? { asset } : {}),
          include_system: includeSystem,
          limit,
        },
      }).then(r => {
        const snapshots = r.data.snapshots ?? []
        return includeSystem ? snapshots : snapshots.filter((snapshot: ReplaySnapshot) => !snapshot.is_system_test)
      }),
    staleTime: 30_000,
  })
}

export function useReplayDetail(snapshotId: string | null) {
  return useQuery<ReplayDetail>({
    queryKey: ['replay-detail', snapshotId],
    queryFn: () => api.get(`/api/v1/replay/${snapshotId}`).then(r => r.data),
    enabled: !!snapshotId,
  })
}

export function useAnnotateOutcome() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ snapshotId, outcome_direction, outcome_pnl_pct, notes }: {
      snapshotId: string; outcome_direction: string; outcome_pnl_pct?: number; notes?: string
    }) => api.post(`/api/v1/replay/${snapshotId}/outcome`, { outcome_direction, outcome_pnl_pct, notes }).then(r => r.data),
    onSuccess: (_, v) => {
      qc.invalidateQueries({ queryKey: ['replay-snapshots'] })
      qc.invalidateQueries({ queryKey: ['replay-detail', v.snapshotId] })
    },
  })
}

export function useReplayAnalysis() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ snapshotId }: { snapshotId: string }) =>
      api.post(`/api/v1/replay/${snapshotId}/replay`).then(r => r.data),
    onSuccess: (_, v) => {
      qc.invalidateQueries({ queryKey: ['replay-snapshots'] })
      qc.invalidateQueries({ queryKey: ['replay-detail', v.snapshotId] })
    },
  })
}

export function useSaveChatSnapshot() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ messageId, asset }: { messageId: string; asset?: string }) =>
      api.post<{ status: string; snapshot_id: string; trigger_type: string; claims_count: number }>(
        `/api/v1/replay/from-chat/${messageId}`,
        { asset, final_action: 'watch' },
      ).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['decision-snapshots'] })
      qc.invalidateQueries({ queryKey: ['replay-snapshots'] })
      qc.invalidateQueries({ queryKey: ['snapshot-status'] })
    },
  })
}

// ── Daily Radar ──────────────────────────────────────────

export interface OpportunityCard {
  asset: string
  title: string
  opportunity_type: string
  urgency: string
  direction_hint: string
  confidence: number
  evidence_summary: string
  key_factors: string[]
  risks: string[]
  suggested_action: string
  data_sources_used: string[]
  detected_at: string
}

export function useRadar() {
  return useQuery<{ cards: OpportunityCard[]; scanned_at: string | null; assets_scanned: string[] }>({
    queryKey: ['radar'],
    queryFn: () => api.get('/api/v1/radar').then(r => r.data),
    staleTime: 5 * 60_000,
  })
}

export function useRadarScan() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (assets?: string[]) =>
      api.post('/api/v1/radar/scan', null, { params: assets ? { assets } : {} }).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['radar'] }),
  })
}

// ── Market Snapshots (manual trigger) ────────────────────

export interface SnapshotStatus {
  requested?: number
  saved?: number
  failed?: number
  funds?: number
  indicators?: {
    saved?: number
    failed?: number
    details?: { pizza?: number | null; fear_greed?: number | null; polymarket?: number }
  }
  triggered_by?: string | null
  ts?: string | null
}

export function useSnapshotStatus() {
  return useQuery<SnapshotStatus>({
    queryKey: ['snapshot-status'],
    queryFn: () => api.get('/api/v1/snapshots/status').then(r => r.data),
    staleTime: 60_000,
  })
}

export function useTriggerSnapshot() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (opts?: { symbols?: string[]; include_indicators?: boolean }) => {
      const params: Record<string, any> = {}
      if (opts?.symbols?.length) params.symbols = opts.symbols
      if (opts?.include_indicators != null) params.include_indicators = opts.include_indicators
      // apiLong 用于长耗时 API（市场抓取可能 30-60 秒）
      return apiLong.post('/api/v1/snapshots/trigger', null, { params }).then(r => r.data)
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['snapshot-status'] })
      qc.invalidateQueries({ queryKey: ['market-history'] })
      qc.invalidateQueries({ queryKey: ['radar'] })
    },
  })
}

// ── Fund Valuation ──────────────────────────────────────
export interface FundNavPoint { t: number; nav: number }
export interface FundCompareSeries { name: string; data: [number, number][] }
export interface FundHistory {
  code: string; name: string
  nav: FundNavPoint[]; compare: FundCompareSeries[]
  returns: Record<string, number>; total_points: number
}

export function useFundHistory(code: string, period = '1y') {
  return useQuery<FundHistory>({
    queryKey: ['fund-history', code, period],
    queryFn: () =>
      api.get<FundHistory>(`/api/v1/fund/history/${code}`, { params: { period } }).then(r => r.data),
    enabled: !!code,
    staleTime: 600_000,
    refetchInterval: 3_600_000,
  })
}
