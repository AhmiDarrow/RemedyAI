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

/** Fallback when server is offline — keep aligned with backend PROVIDER_CATALOG. */
export const FALLBACK_PROVIDERS: ProviderInfo[] = [
  {
    id: 'demo',
    name: 'Demo (Free)',
    base_url: 'https://api.llm7.io/v1',
    // Curated guest chat only — never mirror the full llm7 /models dump.
    models: [
      { id: 'codestral-latest', name: 'Codestral demo' },
      { id: 'gemini-3.1-flash-lite', name: 'Gemini Flash Lite demo' },
      { id: 'gpt-oss:20b', name: 'GPT-OSS 20B demo' },
    ],
    default_model: 'codestral-latest',
    auth: ['none'],
    oauth: false,
    env_keys: [],
    show_base_url: false,
    advanced: false,
    free_tier: 'instant',
    badge: 'No signup',
    limits_blurb:
      'Rate-limited guest chat (curated models only). Add a real provider for agents / vision.',
    privacy_note: 'Chat goes to a third-party free API (not Remedy cloud).',
  },
  {
    id: 'openai',
    name: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    models: [
      { id: 'gpt-4o-mini', name: 'GPT-4o Mini' },
      { id: 'gpt-4o', name: 'GPT-4o' },
    ],
    default_model: 'gpt-4o-mini',
    auth: ['api_key'],
    oauth: false,
    env_keys: ['OPENAI_API_KEY'],
    show_base_url: false,
    advanced: false,
    free_tier: 'none',
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    base_url: 'https://api.anthropic.com/v1',
    models: [
      { id: 'claude-opus-4-1', name: 'Claude Opus 4.1' },
      { id: 'claude-sonnet-4-0', name: 'Claude Sonnet 4' },
      { id: 'claude-3-7-sonnet-latest', name: 'Claude 3.7 Sonnet' },
      { id: 'claude-3-5-haiku-latest', name: 'Claude 3.5 Haiku' },
    ],
    default_model: 'claude-sonnet-4-0',
    auth: ['api_key'],
    oauth: false,
    env_keys: ['ANTHROPIC_API_KEY'],
    show_base_url: false,
    advanced: false,
  },
  {
    id: 'google',
    name: 'Google AI (Gemini)',
    base_url: 'https://generativelanguage.googleapis.com/v1beta/openai',
    models: [{ id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash' }],
    default_model: 'gemini-2.5-flash',
    auth: ['api_key'],
    oauth: false,
    env_keys: ['GOOGLE_API_KEY'],
    show_base_url: false,
    advanced: false,
    free_tier: 'free_key',
    badge: 'Free key',
    key_docs_url: 'https://aistudio.google.com/app/apikey',
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    base_url: 'https://api.deepseek.com/v1',
    models: [
      { id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash' },
      { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro' },
    ],
    default_model: 'deepseek-v4-flash',
    auth: ['api_key'],
    oauth: false,
    env_keys: ['DEEPSEEK_API_KEY'],
    show_base_url: false,
    advanced: false,
  },
  {
    id: 'xai',
    name: 'xAI (Grok)',
    base_url: 'https://api.x.ai/v1',
    models: [
      { id: 'grok-4.5', name: 'Grok 4.5' },
      { id: 'grok-4.3', name: 'Grok 4.3' },
      { id: 'grok-4', name: 'Grok 4' },
    ],
    default_model: 'grok-4.5',
    auth: ['oauth', 'api_key'],
    oauth: true,
    env_keys: ['XAI_API_KEY'],
    show_base_url: false,
    advanced: false,
  },
  {
    id: 'groq',
    name: 'Groq',
    base_url: 'https://api.groq.com/openai/v1',
    models: [{ id: 'llama-3.3-70b-versatile', name: 'Llama 3.3 70B' }],
    default_model: 'llama-3.3-70b-versatile',
    auth: ['api_key'],
    oauth: false,
    env_keys: ['GROQ_API_KEY'],
    show_base_url: false,
    advanced: false,
    free_tier: 'free_key',
    badge: 'Free key',
    key_docs_url: 'https://console.groq.com/keys',
  },
  {
    id: 'mistral',
    name: 'Mistral',
    base_url: 'https://api.mistral.ai/v1',
    models: [{ id: 'mistral-small-latest', name: 'Mistral Small' }],
    default_model: 'mistral-small-latest',
    auth: ['api_key'],
    oauth: false,
    env_keys: ['MISTRAL_API_KEY'],
    show_base_url: false,
    advanced: false,
    free_tier: 'free_key',
    badge: 'Free key',
    key_docs_url: 'https://console.mistral.ai/api-keys',
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    base_url: 'https://openrouter.ai/api/v1',
    models: [
      { id: 'openrouter/auto', name: 'OpenRouter Auto' },
      { id: 'openai/gpt-oss-20b:free', name: 'GPT-OSS 20B (free)' },
    ],
    default_model: 'openrouter/auto',
    auth: ['api_key'],
    oauth: false,
    env_keys: ['OPENROUTER_API_KEY'],
    show_base_url: false,
    advanced: false,
    free_tier: 'free_key',
    badge: 'Free key',
    key_docs_url: 'https://openrouter.ai/keys',
  },
  {
    id: 'poe',
    name: 'Poe',
    base_url: 'https://api.poe.com/v1',
    models: [
      { id: 'Claude-Sonnet-4.6', name: 'Claude Sonnet 4.6' },
      { id: 'Claude-Opus-4.7', name: 'Claude Opus 4.7' },
      { id: 'GPT-5.4', name: 'GPT-5.4' },
      { id: 'Gemini-3.1-Pro', name: 'Gemini 3.1 Pro' },
      { id: 'Grok-4', name: 'Grok 4' },
    ],
    default_model: 'Claude-Sonnet-4.6',
    auth: ['api_key'],
    oauth: false,
    env_keys: ['POE_API_KEY'],
    show_base_url: false,
    advanced: false,
    free_tier: 'none',
    badge: 'Multi-model',
    limits_blurb:
      'One key for many frontier bots via Poe. Uses subscription points / add-on balance.',
    key_docs_url: 'https://poe.com/api_key',
  },
  {
    id: 'ollama',
    name: 'Ollama (local)',
    base_url: 'http://127.0.0.1:11434/v1',
    // No curated models — the local server's pulled models are fetched live
    // (detectOllama / GET /api/models?provider=ollama). Guessing model names
    // produced dead sessions for users who never pulled them.
    models: [],
    default_model: '',
    auth: ['none'],
    oauth: false,
    env_keys: [],
    show_base_url: false,
    advanced: false,
    free_tier: 'local',
    badge: 'Local',
    key_docs_url: 'https://ollama.com/download',
  },
  {
    id: 'rmb',
    name: 'RMB (local agent)',
    base_url: 'http://127.0.0.1:8787/v1',
    models: [
      { id: 'Qwen2.5-Coder-7B-Instruct-Q4_K_M', name: 'Qwen2.5 Coder 7B' },
      { id: 'Qwen2.5-Coder-14B-Instruct-Q4_K_M', name: 'Qwen2.5 Coder 14B' },
      { id: 'default', name: 'Default (loaded model)' },
    ],
    default_model: 'Qwen2.5-Coder-7B-Instruct-Q4_K_M',
    auth: ['none'],
    oauth: false,
    env_keys: [],
    show_base_url: true,
    advanced: false,
    free_tier: 'local',
    badge: 'Local · Agent',
    limits_blurb:
      'Built-in local agent host (llama.cpp) for coding + tools. Manage in Settings → RMB.',
  },
  {
    id: 'custom',
    name: 'Custom / OpenAI-compatible',
    base_url: 'http://127.0.0.1:5001/v1',
    models: [{ id: 'default', name: 'Default' }],
    default_model: 'default',
    auth: ['api_key'],
    oauth: false,
    env_keys: [],
    show_base_url: true,
    // Always primary (not behind Advanced) — LM Studio / llama.cpp / KoboldCpp
    // local hosts. KoboldCpp serves the OpenAI API at /v1 (not /api/v1).
    advanced: false,
  },
]

export async function listProviders(): Promise<ProviderInfo[]> {
  try {
    const res = await apiFetch<{ providers: ProviderInfo[] }>('/providers')
    if (res?.providers?.length) return res.providers
  } catch {
    // offline
  }
  return FALLBACK_PROVIDERS
}

export async function listFreeProviders(): Promise<FreeProviderOption[]> {
  try {
    const res = await apiFetch<{ options: FreeProviderOption[] }>('/providers/free')
    if (res?.options?.length) return res.options
  } catch {
    // offline — derive from fallback catalog
  }
  return FALLBACK_PROVIDERS.filter(
    (p) => p.free_tier && p.free_tier !== 'none',
  ).map((p) => ({
    id: p.id,
    tier: p.free_tier || 'free_key',
    title: p.name,
    blurb: p.limits_blurb || '',
    badge: p.badge,
    name: p.name,
    base_url: p.base_url,
    auth: p.auth,
    key_docs_url: p.key_docs_url,
    limits_blurb: p.limits_blurb,
    privacy_note: p.privacy_note,
    default_model: p.default_model,
    free_tier: p.free_tier,
  }))
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
