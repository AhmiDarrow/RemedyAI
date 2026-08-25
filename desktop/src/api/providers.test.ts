import { describe, expect, it } from 'vitest'
import {
  connectReasonLabel,
  customProviderBody,
  OFFLINE_PROVIDERS,
  pickerFromConnectedResponse,
  type ConnectedProvider,
} from './providers'

function stubConnected(id: string): ConnectedProvider {
  return {
    id,
    name: id,
    base_url: 'http://127.0.0.1/v1',
    models: [],
    default_model: '',
    auth: ['none'],
    oauth: false,
    env_keys: [],
    show_base_url: false,
    advanced: false,
    connected: true,
    connect_reason: 'demo',
    enabled: true,
    picker_eligible: true,
  }
}

describe('pickerFromConnectedResponse', () => {
  it('prefers picker rows when the backend sent them', () => {
    const picker = [stubConnected('xai')]
    const connected = [stubConnected('xai'), stubConnected('demo')]
    expect(pickerFromConnectedResponse({ picker, connected })).toEqual(picker)
  })

  it('falls back to connected when picker is empty', () => {
    const connected = [stubConnected('demo')]
    expect(pickerFromConnectedResponse({ picker: [], connected })).toEqual(connected)
  })

  it('is empty when the connected payload never loaded', () => {
    expect(pickerFromConnectedResponse(null)).toEqual([])
    expect(pickerFromConnectedResponse(undefined)).toEqual([])
  })
})

describe('connectReasonLabel', () => {
  it('explains why a provider is or is not ready', () => {
    expect(connectReasonLabel('demo', true)).toBe('Ready (no key)')
    expect(connectReasonLabel('api_key', true)).toBe('API key stored')
    expect(connectReasonLabel('ollama_down', false)).toBe('Ollama not running')
    expect(connectReasonLabel('no_credentials', false)).toBe('Needs API key')
    expect(connectReasonLabel('active_local', true)).toBe('Local endpoint')
  })
})

describe('OFFLINE_PROVIDERS', () => {
  it('is a minimal stub: ids, names, base URLs and auth only — no model lists', () => {
    expect(OFFLINE_PROVIDERS.length).toBeGreaterThan(0)
    for (const p of OFFLINE_PROVIDERS) {
      expect(p.id).toBeTruthy()
      expect(p.name).toBeTruthy()
      expect(p.base_url).toMatch(/^https?:\/\//)
      expect(p.auth.length).toBeGreaterThan(0)
      expect(p.models).toEqual([])
      expect(p.default_model).toBe('')
    }
  })

  it('has unique ids and includes the local endpoints', () => {
    const ids = OFFLINE_PROVIDERS.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const id of ['ollama', 'llamacpp', 'custom', 'rmb']) {
      expect(ids).toContain(id)
      expect(OFFLINE_PROVIDERS.find((p) => p.id === id)?.show_base_url).toBe(true)
    }
  })
})

describe('customProviderBody', () => {
  it('sends name + base_url and drops empty optionals', () => {
    expect(
      customProviderBody({ name: ' LM Studio ', base_url: ' http://127.0.0.1:1234/v1 ', api_key: '  ' }),
    ).toEqual({ name: 'LM Studio', base_url: 'http://127.0.0.1:1234/v1' })
  })

  it('passes key, requires_key, flavour and id through when set', () => {
    expect(
      customProviderBody({
        name: 'Remote',
        base_url: 'https://host.example/v1',
        api_key: 'sk-1',
        requires_key: true,
        flavour: 'anthropic',
        id: 'custom-remote',
      }),
    ).toEqual({
      name: 'Remote',
      base_url: 'https://host.example/v1',
      api_key: 'sk-1',
      flavour: 'anthropic',
      requires_key: true,
      id: 'custom-remote',
    })
  })

  it('keeps an explicit requires_key=false for keyless local servers', () => {
    const body = customProviderBody({ name: 'x', base_url: 'http://127.0.0.1:1/v1', requires_key: false })
    expect(body.requires_key).toBe(false)
    expect('api_key' in body).toBe(false)
  })
})
