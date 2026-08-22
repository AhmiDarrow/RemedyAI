import { apiFetch } from './client'

export interface ProviderModel {
  id: string
  name: string
}

export interface ProviderInfo {
  id: string
  name: string
  base_url: string
  models: ProviderModel[]
  default_model: string
  auth: string[]
  oauth: boolean
  env_keys: string[]
  show_base_url: boolean
  advanced: boolean
  key_docs_url?: string | null
  free_tier?: string
  badge?: string | null
  limits_blurb?: string | null
  privacy_note?: string | null
}

export interface FreeProviderOption {
  id: string
  tier: string
  title: string
  blurb: string
  badge?: string | null
  name: string
  base_url?: string
  auth: string[]
  key_docs_url?: string | null
  limits_blurb?: string | null
  privacy_note?: string | null
  default_model: string
  free_tier?: string
}

export interface OllamaDetect {
  available: boolean
  base_url: string
  models: string[]
  tags_url?: string
}

/**
 * Minimal offline stub so the Settings UI can render while the server is down.
 * Ids, names, base URLs and auth flags only — NO model lists and NO default
 * model live in the client. The backend catalog (`GET /api/providers`) is the
 * single source of truth; `GET /api/models` discovers models.
 */
function offlineStub(
  id: string,
  name: string,
  base_url: string,
  auth: string[],
  extra: Partial<ProviderInfo> = {},
): ProviderInfo {
  return {
    id,
    name,
    base_url,
    models: [],
    default_model: '',
    auth,
    oauth: auth.includes('oauth'),
    env_keys: [],
    show_base_url: false,
    advanced: false,
    ...extra,
  }
}

export const OFFLINE_PROVIDERS: ProviderInfo[] = [
  offlineStub('demo', 'Demo (Free)', 'https://api.llm7.io/v1', ['none']),
  offlineStub('openai', 'OpenAI', 'https://api.openai.com/v1', ['api_key']),
  offlineStub('anthropic', 'Anthropic', 'https://api.anthropic.com/v1', ['api_key']),
  offlineStub(
    'google',
    'Google AI (Gemini)',
    'https://generativelanguage.googleapis.com/v1beta/openai',
    ['api_key'],
  ),
  offlineStub('deepseek', 'DeepSeek', 'https://api.deepseek.com/v1', ['api_key']),
  offlineStub('xai', 'xAI (Grok)', 'https://api.x.ai/v1', ['oauth', 'api_key']),
  offlineStub('groq', 'Groq', 'https://api.groq.com/openai/v1', ['api_key']),
  offlineStub('mistral', 'Mistral', 'https://api.mistral.ai/v1', ['api_key']),
  offlineStub('openrouter', 'OpenRouter', 'https://openrouter.ai/api/v1', ['api_key']),
  offlineStub('poe', 'Poe', 'https://api.poe.com/v1', ['api_key']),
  offlineStub('ollama', 'Ollama (local)', 'http://127.0.0.1:11434/v1', ['none'], {
    show_base_url: true,
  }),
  offlineStub('llamacpp', 'llama.cpp (local)', 'http://127.0.0.1:8080/v1', ['none'], {
    show_base_url: true,
  }),
  offlineStub('rmb', 'RMB (local agent)', 'http://127.0.0.1:8787/v1', ['none'], {
    show_base_url: true,
  }),
  offlineStub('custom', 'Custom / OpenAI-compatible', 'http://127.0.0.1:5001/v1', ['api_key'], {
    show_base_url: true,
  }),
]

export async function listProviders(): Promise<ProviderInfo[]> {
  try {
    const res = await apiFetch<{ providers: ProviderInfo[] }>('/providers')
    if (res?.providers?.length) return res.providers
  } catch {
    // offline
  }
  return OFFLINE_PROVIDERS
}

export async function listFreeProviders(): Promise<FreeProviderOption[]> {
  try {
    const res = await apiFetch<{ options: FreeProviderOption[] }>('/providers/free')
    if (res?.options?.length) return res.options
  } catch {
    // offline — the stub carries no free-tier metadata; let the caller cope.
  }
  return []
}

export async function detectOllama(): Promise<OllamaDetect> {
  try {
    return await apiFetch<OllamaDetect>('/providers/ollama/detect')
  } catch {
    return {
      available: false,
      base_url: 'http://127.0.0.1:11434/v1',
      models: [],
    }
  }
}

export interface ConnectedProvider extends ProviderInfo {
  connected: boolean
  connect_reason: string
  enabled: boolean
  picker_eligible: boolean
  last_model?: string | null
  catalog_models?: ProviderModel[]
}

export interface ConnectedProvidersResponse {
  providers: ConnectedProvider[]
  connected: ConnectedProvider[]
  picker: ConnectedProvider[]
  active_provider: string
  active_model?: string
  enabled_providers: string[] | null
}

export async function listConnectedProviders(): Promise<ConnectedProvidersResponse> {
  return apiFetch<ConnectedProvidersResponse>('/providers/connected')
}

export interface ProviderProbeResult {
  ok: boolean
  provider: string
  base_url?: string
  status?: number | null
  latency_ms?: number | null
  models?: number
  /** Models the probe saw at the endpoint — populates pickers immediately. */
  model_list?: ProviderModel[]
  error?: string | null
}

export async function probeProvider(input: {
  provider: string
  api_key?: string
  base_url?: string
}): Promise<ProviderProbeResult> {
  return apiFetch<ProviderProbeResult>('/providers/probe', {
    method: 'POST',
    body: JSON.stringify(input),
    timeout: 12_000,
  })
}

export function connectReasonLabel(reason: string | undefined, connected: boolean): string {
  switch (reason) {
    case 'demo':
      return 'Ready (no key)'
    case 'ollama_up':
      return 'Ollama running'
    case 'ollama_down':
      return 'Ollama not running'
    case 'oauth_or_key':
      return 'Signed in'
    case 'api_key':
    case 'resolved_key':
      return 'API key stored'
    case 'rmb_local':
    case 'active_local':
      return 'Local endpoint'
    case 'local_url':
      return 'Local URL'
    case 'max_token_not_api':
      return 'Max token is not an API key'
    case 'no_credentials':
      return connected ? 'Connected' : 'Needs API key'
    default:
      return connected ? 'Connected' : 'Not connected'
  }
}

export async function setSessionLlm(
  sessionId: string,
  provider: string,
  model?: string,
  /** When true, also updates global Settings default + live runtime. Default false = this session only. */
  makeDefault = false,
): Promise<{
  status: string
  provider: string
  model: string
  remeasure?: Record<string, unknown> | null
  context_window?: number | null
  toast?: string
}> {
  const isRmb = (provider || '').toLowerCase() === 'rmb'
  return apiFetch(`/sessions/${sessionId}/llm`, {
    method: 'PUT',
    body: JSON.stringify({
      provider,
      model: model || null,
      make_default: makeDefault,
    }),
    // RMB may restart llama-server with a new GGUF
    timeout: isRmb ? 180_000 : 30_000,
  })
}
