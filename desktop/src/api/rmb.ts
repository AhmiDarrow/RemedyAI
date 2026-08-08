/** RMB — Remedy Muscle Bridge (local llama.cpp chat host for agents). */

import { apiFetch } from './client'

export interface RmbModelInfo {
  id: string
  name: string
  filename?: string
  hf_repo?: string | null
  approx_gb?: number
  n_ctx_recommend?: number
  notes?: string
  size_label?: string
}

export interface RmbStatus {
  ok?: boolean
  brand?: string
  brand_full?: string
  engine?: string
  enabled?: boolean
  auto_start?: boolean
  installed?: boolean
  running?: boolean
  ready?: boolean
  base_url?: string
  host?: string
  port?: number
  model_id?: string
  model?: RmbModelInfo
  model_path?: string | null
  model_present?: boolean
  runtime_binary?: string | null
  runtime_present?: boolean
  ctx_size?: number
  n_gpu_layers?: number | null
  profile?: string
  nvidia?: boolean
  not_ready_hint?: string | null
  catalog?: {
    default_model_id?: string
    models?: RmbModelInfo[]
    profiles?: Record<string, { id?: string; label?: string; blurb?: string; ctx_size?: number }>
    note?: string
  }
  local_agent_mode?: boolean
  skips_vision_stack?: boolean
  vision_suspended?: boolean
  discovered_ggufs?: Array<{ path?: string; name?: string; size_gb?: number }>
  endless_session?: {
    harness_min_pct?: number
    harness_max_pct?: number
    ctx_size?: number
    silent_context?: boolean
    note?: string
  }
  error?: string
}

export async function getRmbStatus(): Promise<RmbStatus> {
  return apiFetch<RmbStatus>('/rmb/status')
}

export async function getRmbCatalog(): Promise<RmbStatus['catalog']> {
  return apiFetch('/rmb/catalog')
}

export async function startRmb(): Promise<Record<string, unknown>> {
  return apiFetch('/rmb/start', { method: 'POST' })
}

export async function stopRmb(): Promise<Record<string, unknown>> {
  return apiFetch('/rmb/stop', { method: 'POST' })
}

export async function patchRmbSettings(
  body: Record<string, unknown>,
): Promise<RmbStatus> {
  return apiFetch('/rmb/settings', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** Switch chat provider to RMB (API call — not a React hook). */
export async function applyRmbAsProvider(): Promise<Record<string, unknown>> {
  return apiFetch('/rmb/use', { method: 'POST' })
}

/** @deprecated Prefer applyRmbAsProvider — name looked like a React hook to linters. */
export const useRmbAsProvider = applyRmbAsProvider
