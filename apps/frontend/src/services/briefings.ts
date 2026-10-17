import api from './api'

export interface BriefingRequest {
  question: string
  topic?: string
  asset_scope?: string[]
  time_horizon?: string
  committee_level?: 'single' | 'double' | 'triple'
}

export interface BriefingCard {
  id: string
  title: string
  topic: string
  direction: string
  setup_quality: string
  executive_summary: string
  supporting_points: string[]
  contrary_points: string[]
  recommended_action: string
  invalidation_conditions: string[]
  created_at: string
}

export const briefingsApi = {
  generate: (request: BriefingRequest) =>
    api.post<BriefingCard>('/api/v1/briefings', request),

  getById: (id: string) => api.get<BriefingCard>(`/api/v1/briefings/${id}`),
}
