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
  /** Present after PATCH /rmb/settings — confirms live process apply */
  live_apply?: {
    live?: boolean
    restarted?: boolean
    started?: boolean
    stopped?: boolean
    process_keys_changed?: string[]
    ctx_size_config?: number
    ctx_size_live?: number | null
    live_error?: string | null
  }
  live_note?: string
  runtime_applied?: boolean
  /** GGUF stem synced to config / status bar after load */
  chat_model?: string
  llm_model?: string
  chat_sync?: { synced?: boolean; stem?: string; llm_model?: string }
  error?: string
}

/** Broadcast so Status bar + Provider form adopt the Loaded GGUF stem. */
export function notifyRmbModelChanged(detail: {
  stem: string
  path?: string
  provider?: string
}) {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent('remedy:rmb-model-changed', { detail }))
}

export async function getRmbStatus(): Promise<RmbStatus> {
  return apiFetch<RmbStatus>('/rmb/status')
}

export async function getRmbCatalog(): Promise<RmbStatus['catalog']> {
  return apiFetch('/rmb/catalog')
}

/** Large GGUF reloads often exceed the default 30s HTTP budget. */
const RMB_LONG_MS = 180_000

export async function startRmb(): Promise<Record<string, unknown>> {
  return apiFetch('/rmb/start', { method: 'POST', timeout: RMB_LONG_MS })
}

export async function stopRmb(): Promise<Record<string, unknown>> {
  return apiFetch('/rmb/stop', { method: 'POST', timeout: 60_000 })
}

export async function patchRmbSettings(
  body: Record<string, unknown>,
): Promise<RmbStatus> {
  return apiFetch('/rmb/settings', {
    method: 'POST',
    body: JSON.stringify(body),
    // Live apply may stop+start llama-server (minutes for big models)
    timeout: RMB_LONG_MS,
  })
}

/** Switch chat provider to RMB (API call — not a React hook). */
export async function applyRmbAsProvider(): Promise<Record<string, unknown>> {
  return apiFetch('/rmb/use', { method: 'POST', timeout: RMB_LONG_MS })
}

/** @deprecated Prefer applyRmbAsProvider — name looked like a React hook to linters. */
export const useRmbAsProvider = applyRmbAsProvider
