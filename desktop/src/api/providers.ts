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
    name: 'Google AI',
    base_url: 'https://generativelanguage.googleapis.com/v1beta/openai',
    models: [{ id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash' }],
    default_model: 'gemini-2.5-flash',
    auth: ['api_key'],
    oauth: false,
    env_keys: ['GOOGLE_API_KEY'],
    show_base_url: false,
    advanced: false,
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    base_url: 'https://api.deepseek.com/v1',
    models: [
      { id: 'deepseek-chat', name: 'DeepSeek Chat' },
      { id: 'deepseek-reasoner', name: 'DeepSeek Reasoner' },
    ],
    default_model: 'deepseek-chat',
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
      { id: 'grok-3-mini', name: 'Grok 3 Mini' },
      { id: 'grok-3', name: 'Grok 3' },
      { id: 'grok-4', name: 'Grok 4' },
    ],
    default_model: 'grok-3-mini',
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
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    base_url: 'https://openrouter.ai/api/v1',
    models: [{ id: 'openrouter/auto', name: 'OpenRouter Auto' }],
    default_model: 'openrouter/auto',
    auth: ['api_key'],
    oauth: false,
    env_keys: ['OPENROUTER_API_KEY'],
    show_base_url: false,
    advanced: false,
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
