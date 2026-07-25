import { apiFetch } from './client'

export interface UsageTotals {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  estimated_cost_usd: number
  events: number
}

export interface UsageByGroup extends UsageTotals {
  provider?: string
  model?: string
}

export interface UsageSummary {
  range_days: number
  session_id?: string | null
  totals: UsageTotals
  by_provider: UsageByGroup[]
  by_model: UsageByGroup[]
}

export interface UsageSeriesPoint {
  day: string
  group: string
  total_tokens: number
  estimated_cost_usd: number
  events: number
}

export interface ContinuityDashboard {
  session_id: string
  session_quality: Record<string, unknown>
  pattern: {
    step_count: number
    success_rate: number | null
    recent: string[]
  }
  goal?: {
    open?: string[]
    stale?: boolean
    tool_steps_since_sync?: number
  }
  health?: {
    error_rate?: number
    rate_limit_hits?: number
    avg_latency_ms?: number | null
    flaky?: boolean
    samples?: number
  }
  scout?: {
    last_tools?: string[]
    last_active?: boolean
  }
  token: {
    last_method: string
    last_estimate: number
    active_provider?: string | null
    active_model?: string | null
    last_remeasure?: Record<string, unknown> | null
    status?: Record<string, unknown>
  }
  context_snapshot?: Record<string, unknown> | null
  harness_mode: string
  swarm: { event_count?: number; last_event?: string | null }
}

export async function getUsageSummary(rangeDays = 7, sessionId?: string | null) {
  const q = new URLSearchParams({ range_days: String(rangeDays) })
  if (sessionId) q.set('session_id', sessionId)
  return apiFetch<UsageSummary>(`/usage/summary?${q}`)
}

export async function getUsageSeries(rangeDays = 30, group: 'provider' | 'model' = 'provider') {
  const q = new URLSearchParams({
    range_days: String(rangeDays),
    group,
  })
  return apiFetch<{ range_days: number; group: string; points: UsageSeriesPoint[] }>(
    `/usage/series?${q}`,
  )
}

export async function getContinuityDashboard(sessionId?: string | null) {
  const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return apiFetch<ContinuityDashboard>(`/continuity/dashboard${q}`)
}

export async function getTokenNanobotStatus() {
  return apiFetch<Record<string, unknown>>('/nanoswarm/token/status')
}
