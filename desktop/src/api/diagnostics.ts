/** Health diagnostics — Remedy API, RMB, hardware, providers. */

import { apiFetch } from './client'

export type DiagnosticsOverall = 'ok' | 'degraded' | 'error'

export interface DiagnosticsIssue {
  severity: 'info' | 'warn' | 'error'
  area: string
  message: string
  hint?: string
}

export interface DiagnosticsSnapshot {
  ok: boolean
  overall: DiagnosticsOverall
  checked_at: string
  collect_ms?: number
  issues: DiagnosticsIssue[]
  remedy: {
    version?: string
    uptime?: string
    uptime_seconds?: number | null
    api?: {
      host?: string
      port?: number
      base_url?: string
      listening?: boolean
    }
    process?: {
      pid?: number
      rss_mb?: number
      vms_mb?: number
      cpu_pct?: number
      threads?: number
    }
    gateway?: Record<string, unknown>
    memory_entries?: number
    skills_count?: number
    sessions_count?: number
    chat_sessions_count?: number
    home_dir?: string
    home_disk?: {
      path?: string
      total_gb?: number
      used_gb?: number
      free_gb?: number
      used_pct?: number
      error?: string
    }
    log_dir?: string
    metrics_counters?: number
    active_provider?: string
    active_model?: string
    health_checks?: Record<string, { status?: string; error?: string; detail?: unknown }>
  }
  rmb: {
    ok?: boolean
    enabled?: boolean
    running?: boolean
    ready?: boolean
    starting?: boolean
    model_id?: string
    model_name?: string
    model_path?: string | null
    model_present?: boolean
    runtime_present?: boolean
    ctx_size?: number
    n_gpu_layers?: number | null
    profile?: string
    host?: string
    port?: number
    base_url?: string
    nvidia?: boolean
    latency_ms?: number | null
    not_ready_hint?: string | null
    vision_suspended?: boolean
    local_agent_mode?: boolean
    error?: string
  }
  vision: {
    ok?: boolean
    enabled?: boolean
    running?: boolean
    ready?: boolean
    model_id?: string
    port?: number
    base_url?: string
    suspended_for_rmb?: boolean
    not_ready_hint?: string | null
    error?: string
  }
  hardware: {
    platform?: string
    system?: string
    machine?: string
    python?: string
    hostname?: string
    cpu?: {
      count_logical?: number
      percent?: number | null
      brand?: string | null
    }
    memory?: {
      total_gb?: number | null
      available_gb?: number | null
      used_pct?: number | null
    }
    gpu?: {
      nvidia?: boolean
      gpus?: Array<{
        name?: string
        memory_total_mb?: number
        memory_used_mb?: number
        util_pct?: number
        temp_c?: number
      }>
      error?: string
    }
    disks?: Array<{
      path?: string
      total_gb?: number
      used_gb?: number
      free_gb?: number
      used_pct?: number
      error?: string
    }>
  }
  providers: {
    active?: { provider?: string; model?: string; base_url?: string }
    connected_count?: number
    remote_connected_count?: number
    local_connected_count?: number
    providers?: Array<{
      id: string
      label?: string
      connected?: boolean
      enabled?: boolean
      reason?: string
      local?: boolean
      last_model?: string | null
      base_url?: string
      badge?: string
    }>
    usage_7d?: Array<{
      provider?: string
      total_tokens?: number
      estimated_cost_usd?: number
      events?: number
    }>
    probes?: Array<{
      id?: string
      base_url?: string
      latency_ms?: number | null
      ok?: boolean
    }>
    ollama?: Record<string, unknown>
  }
  computer: {
    ok?: boolean
    host_connected?: boolean
    pending_jobs?: number | null
    jobs_root?: string
    error?: string
  }
}

export async function getDiagnostics(opts?: {
  probeProviders?: boolean
}): Promise<DiagnosticsSnapshot> {
  const q = opts?.probeProviders ? '?probe_providers=true' : ''
  return apiFetch<DiagnosticsSnapshot>(`/diagnostics${q}`)
}
