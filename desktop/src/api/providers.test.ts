import { describe, expect, it } from 'vitest'
import { connectReasonLabel } from './providers'

describe('connectReasonLabel', () => {
  it('explains why a provider is or is not ready', () => {
    expect(connectReasonLabel('demo', true)).toBe('Ready (no key)')
    expect(connectReasonLabel('api_key', true)).toBe('API key stored')
    expect(connectReasonLabel('ollama_down', false)).toBe('Ollama not running')
    expect(connectReasonLabel('no_credentials', false)).toBe('Needs API key')
    expect(connectReasonLabel('active_local', true)).toBe('Local endpoint')
  })
})
