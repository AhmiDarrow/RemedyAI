import { describe, expect, it } from 'vitest'
import { connectReasonLabel, OFFLINE_PROVIDERS } from './providers'

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
