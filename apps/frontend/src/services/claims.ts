import api from './api'

export interface ClaimCard {
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

export interface ClaimSearchResponse {
  claims: ClaimCard[]
  total: number
  has_more: boolean
}

export const claimsApi = {
  search: (params: {
    q?: string
    asset?: string
    theme?: string
    status?: string
    limit?: number
    offset?: number
  }) => api.get<ClaimSearchResponse>('/api/v1/claims', { params }),

  getById: (id: string) => api.get<ClaimCard>(`/api/v1/claims/${id}`),
}
