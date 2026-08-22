/**
 * Model discovery helpers — pure merge / precedence / default-pick logic shared
 * by Settings, the status bar and the setup wizard.
 *
 * Precedence for the picker is: live endpoint list → session/connected list →
 * backend catalog. The backend (`GET /api/models`) is the single source of
 * truth for which models exist; the client never ships model lists of its own.
 */

import { apiFetch } from './client'

export type ModelSource = 'endpoint' | 'catalog'

export interface DiscoveredModel {
  id: string
  name: string
  provider: string
  default?: boolean
  source?: ModelSource
  context_window?: number | null
  vision?: boolean
  tools?: boolean
}

export interface DiscoveryStatus {
  attempted: boolean
  ok: boolean
  status: number | null
  error: string | null
  url: string
  cached: boolean
  flavour: string | null
}

export interface ModelsResponse {
  models: DiscoveredModel[]
  default: string
  provider: string
  base_url?: string
  discovery?: DiscoveryStatus | null
}

export interface ModelOption {
  id: string
  name: string
  provider?: string
  source?: ModelSource
}

/** Build the `/models` query string; unsaved form values ride along as params. */
export function modelsQuery(
  provider: string | undefined,
  opts?: { base_url?: string; api_key?: string },
): string {
  const q = new URLSearchParams()
  if (provider) q.set('provider', provider)
  const b = (opts?.base_url || '').trim()
  if (b) q.set('base_url', b)
  const k = (opts?.api_key || '').trim()
  if (k) q.set('api_key', k)
  const s = q.toString()
  return s ? `?${s}` : ''
}

export async function fetchModels(
  provider: string | undefined,
  opts?: { base_url?: string; api_key?: string },
): Promise<ModelsResponse> {
  const data = await apiFetch<ModelsResponse>(`/models${modelsQuery(provider, opts)}`)
  const prov = (data?.provider || provider || '').toLowerCase()
  return {
    ...data,
    provider: prov,
    default: data?.default || '',
    models: (data?.models || [])
      .filter((m) => m && m.id)
      .map((m) => ({ ...m, name: m.name || m.id, provider: m.provider || prov })),
  }
}

/**
 * Monotonic request generation — a late response from an older request must
 * never overwrite the result of a newer one.
 */
export function createRequestGeneration() {
  let gen = 0
  return {
    next(): number {
      gen += 1
      return gen
    },
    isCurrent(n: number): boolean {
      return n === gen
    },
    current(): number {
      return gen
    },
  }
}

/** Merge model lists by precedence (first list wins on id collisions). */
export function mergeModelOptions(
  ...lists: Array<ReadonlyArray<ModelOption> | null | undefined>
): ModelOption[] {
  const seen = new Set<string>()
  const out: ModelOption[] = []
  for (const list of lists) {
    for (const m of list || []) {
      const id = (m?.id || '').trim()
      if (!id || seen.has(id)) continue
      seen.add(id)
      out.push({ ...m, id, name: m.name || id })
    }
  }
  return out
}

/**
 * Model to select after discovery: keep the current one when the list still
 * has it, otherwise the backend default, otherwise the first row. An empty
 * list keeps whatever was selected (the user may have typed a custom id).
 */
export function pickDefaultModel(
  current: string | null | undefined,
  list: ReadonlyArray<{ id: string; default?: boolean }>,
  backendDefault?: string | null,
): string {
  const cur = (current || '').trim()
  if (list.length === 0) return cur
  if (cur && list.some((m) => m.id === cur)) return cur
  const def = (backendDefault || '').trim()
  if (def && list.some((m) => m.id === def)) return def
  const flagged = list.find((m) => m.default)
  return flagged?.id || list[0]?.id || cur
}

/** Human hint for the discovery result shown next to the model picker. */
export function discoveryHint(
  d: DiscoveryStatus | null | undefined,
  endpointCount: number,
): { kind: 'ok' | 'error' | 'none'; text: string } {
  if (!d || !d.attempted) return { kind: 'none', text: '' }
  if (d.ok) {
    const n = endpointCount
    return {
      kind: 'ok',
      text: `${n} model${n === 1 ? '' : 's'} from endpoint${d.cached ? ' (cached)' : ''}`,
    }
  }
  const why = d.error || (d.status != null ? `HTTP ${d.status}` : 'no response')
  return {
    kind: 'error',
    text: `Couldn't list models from ${d.url || 'endpoint'}: ${why} — showing catalog defaults`,
  }
}

/** Label for a dropdown row; catalog-only rows are marked. */
export function modelOptionLabel(m: ModelOption): string {
  const name = m.name || m.id
  return m.source === 'catalog' ? `${name} (catalog)` : name
}

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]'])

export function isLocalUrl(url: string | null | undefined): boolean {
  const u = (url || '').trim()
  if (!u) return false
  try {
    const host = new URL(u.includes('://') ? u : `http://${u}`).hostname.toLowerCase()
    return (
      LOCAL_HOSTS.has(host)
      || host.endsWith('.local')
      || /^10\./.test(host)
      || /^192\.168\./.test(host)
      || /^172\.(1[6-9]|2\d|3[01])\./.test(host)
    )
  } catch {
    return false
  }
}

/** Id of the "Custom endpoint" template row. */
export const CUSTOM_TEMPLATE_ID = 'custom'
const SAVED_ENDPOINT_PREFIX = 'custom-'

/** `custom-<slug>` — an endpoint the owner saved from the Custom template. */
export function isSavedEndpointId(id: string | null | undefined): boolean {
  const p = (id || '').toLowerCase()
  return p.startsWith(SAVED_ENDPOINT_PREFIX) && p.length > SAVED_ENDPOINT_PREFIX.length
}

/** The template itself or a saved endpoint — both are flexible endpoints. */
export function isCustomLikeProvider(id: string | null | undefined): boolean {
  const p = (id || '').toLowerCase()
  return p === CUSTOM_TEMPLATE_ID || isSavedEndpointId(p)
}

/**
 * Form state after `POST /providers/custom` succeeds: select the new provider,
 * its first model (backend default first); the caller blanks the template.
 */
export function afterCustomSave(
  res: {
    id: string
    provider?: { base_url?: string; default_model?: string } | null
    models?: ReadonlyArray<{ id: string; name?: string }> | null
    discovery?: { attempted: boolean; ok: boolean } | null
    note?: string | null
  },
): {
  provider: string
  model: string
  baseUrl: string
  hint: string | null
} {
  const models = res.models || []
  const model = pickDefaultModel('', models, res.provider?.default_model || '')
  const d = res.discovery
  const failed = Boolean(d?.attempted && !d.ok)
  const hint = res.note || (failed ? 'Saved, but the endpoint did not list any models yet.' : null)
  return {
    provider: res.id,
    model,
    baseUrl: res.provider?.base_url || '',
    hint,
  }
}

/**
 * Provider to land on after a saved endpoint is removed: the active chat
 * provider unless it was the removed one, then the first connected provider,
 * then the Custom template.
 */
export function afterEndpointDelete(
  removedId: string,
  activeProvider: string | null | undefined,
  connected: ReadonlyArray<{ id: string; connected?: boolean }>,
): string {
  const active = (activeProvider || '').trim()
  if (active && active !== removedId) return active
  const first = connected.find((p) => p.id !== removedId && p.connected !== false)
  return first?.id || CUSTOM_TEMPLATE_ID
}

/** Providers whose endpoint list is often partial or absent — allow a typed id. */
export const FREE_TEXT_MODEL_PROVIDERS = new Set(['ollama', 'custom', 'llamacpp', 'rmb'])

export function allowsFreeTextModel(
  provider: string | undefined,
  discovery?: DiscoveryStatus | null,
): boolean {
  const p = (provider || '').toLowerCase()
  if (FREE_TEXT_MODEL_PROVIDERS.has(p) || isSavedEndpointId(p)) return true
  return Boolean(discovery?.attempted && !discovery.ok)
}

/** Providers that may always edit the base URL regardless of catalog flag. */
export const ALWAYS_BASE_URL_PROVIDERS = new Set(['ollama', 'custom', 'llamacpp'])

export function showsBaseUrl(
  provider: string | undefined,
  meta?: { show_base_url?: boolean } | null,
): boolean {
  const p = (provider || '').toLowerCase()
  return (
    ALWAYS_BASE_URL_PROVIDERS.has(p)
    || isSavedEndpointId(p)
    || Boolean(meta?.show_base_url)
  )
}

/** Whether the key field should nag; key-less auth or a local URL never does. */
export function providerNeedsKey(
  provider: string | undefined,
  meta: { auth?: string[] } | null | undefined,
  baseUrl: string | null | undefined,
): boolean {
  const p = (provider || '').toLowerCase()
  const auth = meta?.auth || []
  if (auth.length && !auth.includes('api_key')) return false
  if (auth.includes('none')) return false
  if (isCustomLikeProvider(p) || p === 'llamacpp') return !isLocalUrl(baseUrl)
  if (p === 'demo' || p === 'ollama' || p === 'rmb') return false
  return true
}
