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
  engine_brand?: string
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
  autofit?: {
    enabled?: boolean
    locked?: boolean
    profile?: string
    summary?: string
    target?: string
    ctx_size?: number
    n_gpu_layers?: number
    cache_type?: string
    vram_total_mb?: number
    last?: Record<string, unknown> | null
    last_good?: Record<string, unknown> | null
    planned?: Record<string, unknown>
  }
  /** Live inference-engine knobs (llama-server argv). */
  engine?: {
    threads?: number
    parallel?: number
    flash_attn?: boolean
    temperature?: number | null
    top_p?: number | null
    top_k?: number | null
    min_p?: number | null
    repeat_penalty?: number | null
    repeat_last_n?: number | null
    seed?: number | null
    batch_size?: number | null
    ubatch_size?: number | null
    mmproj?: string
    chat_template?: string
    use_jinja?: boolean
    /** false = GGUF detection drives use_jinja; true = owner pinned it. */
    use_jinja_owner?: boolean
    rope_freq_scale?: number | null
    rope_freq_base?: number | null
    mlock?: boolean
    no_mmap?: boolean
    cache_type?: string
    typical_p?: number | null
    tfs_z?: number | null
    mirostat?: number | null
    mirostat_tau?: number | null
    mirostat_eta?: number | null
    presence_penalty?: number | null
    frequency_penalty?: number | null
    main_gpu?: number | null
    threads_batch?: number | null
    tensor_split?: string
    samplers?: string
    rope_scaling?: string
    yarn_orig_ctx?: number | null
    yarn_factor?: number | null
    yarn_beta_fast?: number | null
    yarn_beta_slow?: number | null
    no_kv_offload?: boolean
    dry_multiplier?: number | null
    dry_base?: number | null
    dry_allowed_length?: number | null
    dry_penalty_last_n?: number | null
    xtc_probability?: number | null
    xtc_threshold?: number | null
    cache_reuse?: number | null
    thinking?: 'on' | 'off' | string
    reasoning_budget?: number | null
    enable_mtp?: boolean
    spec_draft_n_max?: number | null
    n_cpu_moe?: number | null
    n_gpu_layers_draft?: number | null
    model_draft?: string
  }
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
  /** Auto-load knobs Remedy inferred from the GGUF; owner can override each. */
  host_auto?: {
    summary?: string
    thinking?: boolean
    thinking_mode?: string
    coder?: boolean
    qwen3_family?: boolean
    mtp?: boolean
    vision?: boolean
    base_model?: boolean
    use_jinja?: boolean
    unfit?: boolean
    chat_style?: string
    warnings?: string[]
    reasons?: string[]
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

export interface HfRepoOption {
  id: string
  downloads?: number
  likes?: number
  tags?: string[]
  pipeline_tag?: string | null
  private?: boolean
}

export interface HfFileOption {
  path: string
  name: string
  size?: number
  size_gb?: number
  role?: string
  recommended?: boolean
  url?: string
}

export interface HfSearchResult {
  ok?: boolean
  error?: string
  need?: string
  hint?: {
    kind?: string
    query?: string
    repo?: string | null
    revision?: string | null
    filename?: string | null
    url?: string | null
  }
  repos?: HfRepoOption[]
  files?: HfFileOption[]
}

export interface HfProgress {
  phase?: string
  query?: string
  repo?: string
  filename?: string
  bytes_done?: number
  bytes_total?: number
  pct?: number
  error?: string | null
  path?: string | null
  message?: string
}

export async function searchHfModels(query: string): Promise<HfSearchResult> {
  return apiFetch('/rmb/hf/search', {
    method: 'POST',
    body: JSON.stringify({ query }),
    timeout: 45_000,
  })
}

export async function listHfFiles(
  repo: string,
  revision?: string,
): Promise<{ ok?: boolean; error?: string; repo?: string; files?: HfFileOption[] }> {
  return apiFetch('/rmb/hf/files', {
    method: 'POST',
    body: JSON.stringify({ repo, revision: revision || undefined }),
    timeout: 45_000,
  })
}

export async function pullHfModel(body: {
  query?: string
  repo?: string
  filename?: string
  revision?: string
  url?: string
  expected_size?: number
  load?: boolean
}): Promise<{ ok?: boolean; error?: string; started?: boolean; progress?: HfProgress }> {
  return apiFetch('/rmb/hf/pull', {
    method: 'POST',
    body: JSON.stringify(body),
    timeout: 30_000,
  })
}

export async function getHfProgress(): Promise<{ ok?: boolean; progress?: HfProgress }> {
  return apiFetch('/rmb/hf/progress', { timeout: 4000 })
}

export async function cancelHfPull(): Promise<{ ok?: boolean; error?: string; progress?: HfProgress }> {
  return apiFetch('/rmb/hf/cancel', { method: 'POST' })
}
