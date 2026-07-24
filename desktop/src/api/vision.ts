/** Local visual decoder API (llama.cpp + Qwen2.5-VL 3B). */

import { apiFetch } from './client'

export interface VisionModelInfo {
  id: string
  name: string
  hf_repo?: string
  model_file?: string
  mmproj_file?: string
  approx_download_bytes?: number
  approx_download_gb?: number
  min_ram_gb?: number
  notes?: string
  is_default?: boolean
}

export interface VisionProgress {
  phase?: string
  message?: string
  bytes_done?: number
  bytes_total?: number
  current_file?: string
  error?: string | null
  model_id?: string | null
  runtime_id?: string | null
  cancellable?: boolean
  resumed?: boolean
}

export interface VisionHealth {
  ram_gb?: number | null
  disk_free_gb?: number | null
  install_need_gb?: number
  min_ram_gb?: number
  nvidia_detected?: boolean
  runtime_id?: string
  cpu_runtime?: boolean
  warnings?: string[]
}

export interface VisionStatus {
  enabled: boolean
  installed: boolean
  running: boolean
  ready: boolean
  model_id: string
  model?: VisionModelInfo
  backend?: string
  base_url?: string
  port?: number
  host?: string
  runtime_version?: string
  runtime_id?: string
  progress?: VisionProgress
  health?: VisionHealth
  warnings?: string[]
  not_ready_hint?: string | null
  /** Prefer local decode even for vision-capable chat models */
  force_decode?: boolean
  catalog?: {
    default_model_id?: string
    models?: VisionModelInfo[]
    llama_cpp_tag?: string
  }
}

export async function getVisionStatus(): Promise<VisionStatus> {
  return apiFetch<VisionStatus>('/vision/status')
}

export async function getVisionCatalog(): Promise<{
  default_model_id: string
  models: VisionModelInfo[]
  llama_cpp_tag?: string
}> {
  return apiFetch('/vision/catalog')
}

export async function installVision(opts?: {
  model_id?: string
  runtime_id?: string
  prefer_cuda?: boolean
}): Promise<VisionStatus & { ok?: boolean; error?: string; warnings?: string[] }> {
  return apiFetch('/vision/install', {
    method: 'POST',
    body: JSON.stringify(opts || {}),
  })
}

export async function cancelVisionInstall(): Promise<{
  ok?: boolean
  error?: string
  message?: string
}> {
  return apiFetch('/vision/install/cancel', {
    method: 'POST',
    body: '{}',
  })
}

export async function reinstallVisionRuntime(preferCuda = true): Promise<{
  ok?: boolean
  error?: string
  warnings?: string[]
}> {
  return apiFetch('/vision/reinstall-runtime', {
    method: 'POST',
    body: JSON.stringify({ prefer_cuda: preferCuda }),
  })
}

export async function uninstallVision(keepModels = false): Promise<{ ok?: boolean }> {
  return apiFetch('/vision/uninstall', {
    method: 'POST',
    body: JSON.stringify({ keep_models: keepModels }),
  })
}

export async function startVisionServer(): Promise<{ ok?: boolean; error?: string }> {
  return apiFetch('/vision/start', { method: 'POST', body: '{}' })
}

export async function stopVisionServer(): Promise<{ ok?: boolean }> {
  return apiFetch('/vision/stop', { method: 'POST', body: '{}' })
}

/** Rough client-side mirror of backend supports_vision for UI banners. */
export function chatModelSupportsVision(provider: string, model: string): boolean {
  const mid = (model || '').toLowerCase()
  const prov = (provider || '').toLowerCase()
  if (!mid) return false
  const non = new Set([
    'deepseek-chat',
    'deepseek-reasoner',
    'codestral-latest',
    'codestral',
    'o1',
    'o1-mini',
    'o1-preview',
    'o3-mini',
    'o4-mini',
  ])
  if (non.has(mid)) return false
  const hints = [
    'vision',
    'gpt-4o',
    'gpt-4.1',
    'gpt-4-turbo',
    'gpt-5',
    'claude-3',
    'claude-4',
    'claude-sonnet',
    'claude-opus',
    'claude-haiku',
    'gemini',
    'llava',
    'qwen2-vl',
    'qwen2.5-vl',
    'qwen-vl',
    'pixtral',
    'moondream',
    'grok-2-vision',
  ]
  if (hints.some((h) => mid.includes(h))) return true
  if (prov === 'openai' && mid.startsWith('gpt-4') && !mid.includes('o1')) return true
  if (prov === 'anthropic' || prov === 'google') return true
  if (prov === 'deepseek') return false
  return false
}

export function formatDownloadGb(bytes?: number): string {
  if (!bytes || bytes <= 0) return '~3 GB'
  return `~${(bytes / (1024 ** 3)).toFixed(1)} GB`
}
