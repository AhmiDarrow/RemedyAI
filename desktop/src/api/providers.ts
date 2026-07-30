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
    models: [{ id: 'claude-3-5-sonnet-latest', name: 'Claude 3.5 Sonnet' }],
    default_model: 'claude-3-5-sonnet-latest',
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
    models: [
      { id: 'llama3.2', name: 'Llama 3.2' },
      { id: 'qwen2.5', name: 'Qwen 2.5' },
    ],
    default_model: 'llama3.2',
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
    id: 'custom',
    name: 'Custom / OpenAI-compatible',
    base_url: 'http://127.0.0.1:5001/api/v1',
    models: [{ id: 'default', name: 'Default' }],
    default_model: 'default',
    auth: ['api_key'],
    oauth: false,
    env_keys: [],
    show_base_url: true,
    advanced: true,
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

export async function setSessionLlm(
  sessionId: string,
  provider: string,
  model?: string,
  makeDefault = true,
): Promise<{
  status: string
  provider: string
  model: string
  remeasure?: Record<string, unknown> | null
  context_window?: number | null
  toast?: string
}> {
  return apiFetch(`/sessions/${sessionId}/llm`, {
    method: 'PUT',
    body: JSON.stringify({
      provider,
      model: model || null,
      make_default: makeDefault,
    }),
  })
}
