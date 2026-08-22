import { describe, expect, it } from 'vitest'
import {
  allowsFreeTextModel,
  createRequestGeneration,
  discoveryHint,
  isLocalUrl,
  mergeModelOptions,
  modelOptionLabel,
  modelsQuery,
  pickDefaultModel,
  providerNeedsKey,
  showsBaseUrl,
  type DiscoveryStatus,
} from './modelDiscovery'

const okDiscovery: DiscoveryStatus = {
  attempted: true,
  ok: true,
  status: 200,
  error: null,
  url: 'http://127.0.0.1:11434/v1/models',
  cached: false,
  flavour: 'openai',
}

describe('modelsQuery', () => {
  it('passes unsaved base_url and api_key as query params', () => {
    const q = modelsQuery('custom', { base_url: 'http://x:1/v1', api_key: 'sk-1' })
    expect(q).toBe('?provider=custom&base_url=http%3A%2F%2Fx%3A1%2Fv1&api_key=sk-1')
  })
  it('omits blank values', () => {
    expect(modelsQuery('openai', { base_url: '  ', api_key: '' })).toBe('?provider=openai')
    expect(modelsQuery(undefined)).toBe('')
  })
})

describe('mergeModelOptions', () => {
  it('prefers earlier lists and dedupes by id', () => {
    const out = mergeModelOptions(
      [{ id: 'a', name: 'Live A', source: 'endpoint' }],
      [{ id: 'a', name: 'Session A' }, { id: 'b', name: 'Session B' }],
      [{ id: 'b', name: 'Catalog B', source: 'catalog' }, { id: 'c', name: '', source: 'catalog' }],
    )
    expect(out).toEqual([
      { id: 'a', name: 'Live A', source: 'endpoint' },
      { id: 'b', name: 'Session B' },
      { id: 'c', name: 'c', source: 'catalog' },
    ])
  })
  it('tolerates null lists and blank ids', () => {
    expect(mergeModelOptions(null, [{ id: ' ', name: 'x' }], undefined)).toEqual([])
  })
})

describe('pickDefaultModel', () => {
  const list = [{ id: 'm1' }, { id: 'm2', default: true }, { id: 'm3' }]
  it('keeps the current model when the list still has it', () => {
    expect(pickDefaultModel('m3', list, 'm1')).toBe('m3')
  })
  it('falls back to the backend default when current is empty or stale', () => {
    expect(pickDefaultModel('', list, 'm1')).toBe('m1')
    expect(pickDefaultModel('gone', list, 'm1')).toBe('m1')
  })
  it('uses the flagged default, then the first row, when the backend default is missing', () => {
    expect(pickDefaultModel('gone', list, '')).toBe('m2')
    expect(pickDefaultModel('gone', [{ id: 'only' }], 'nope')).toBe('only')
  })
  it('keeps a typed id when discovery returned nothing', () => {
    expect(pickDefaultModel('my-local-model', [], 'x')).toBe('my-local-model')
  })
})

describe('createRequestGeneration', () => {
  it('rejects a stale response after a newer request was issued', () => {
    const gen = createRequestGeneration()
    const first = gen.next()
    const second = gen.next()
    expect(gen.isCurrent(first)).toBe(false)
    expect(gen.isCurrent(second)).toBe(true)
    expect(gen.current()).toBe(second)
  })
  it('simulates out-of-order async resolution', async () => {
    const gen = createRequestGeneration()
    let selected = ''
    const run = (result: string, delayMs: number) => {
      const g = gen.next()
      return new Promise<void>((resolve) =>
        setTimeout(() => {
          if (gen.isCurrent(g)) selected = result
          resolve()
        }, delayMs),
      )
    }
    await Promise.all([run('old', 20), run('new', 1)])
    expect(selected).toBe('new')
  })
})

describe('discoveryHint', () => {
  it('is silent when discovery was not attempted', () => {
    expect(discoveryHint(null, 0).kind).toBe('none')
    expect(discoveryHint({ ...okDiscovery, attempted: false }, 0).kind).toBe('none')
  })
  it('reports endpoint count on success', () => {
    expect(discoveryHint(okDiscovery, 3)).toEqual({ kind: 'ok', text: '3 models from endpoint' })
    expect(discoveryHint({ ...okDiscovery, cached: true }, 1).text).toBe(
      '1 model from endpoint (cached)',
    )
  })
  it('explains the failure and that catalog defaults are shown', () => {
    const h = discoveryHint(
      { ...okDiscovery, ok: false, status: 401, error: 'unauthorized' },
      0,
    )
    expect(h.kind).toBe('error')
    expect(h.text).toBe(
      "Couldn't list models from http://127.0.0.1:11434/v1/models: unauthorized — showing catalog defaults",
    )
    expect(discoveryHint({ ...okDiscovery, ok: false, status: 503, error: null }, 0).text).toContain(
      'HTTP 503',
    )
  })
})

describe('modelOptionLabel', () => {
  it('marks catalog-only rows', () => {
    expect(modelOptionLabel({ id: 'a', name: 'A', source: 'catalog' })).toBe('A (catalog)')
    expect(modelOptionLabel({ id: 'a', name: 'A', source: 'endpoint' })).toBe('A')
    expect(modelOptionLabel({ id: 'a', name: '' })).toBe('a')
  })
})

describe('local endpoints', () => {
  it('detects loopback and LAN hosts', () => {
    expect(isLocalUrl('http://127.0.0.1:5001/v1')).toBe(true)
    expect(isLocalUrl('localhost:11434')).toBe(true)
    expect(isLocalUrl('http://192.168.1.20:8080/v1')).toBe(true)
    expect(isLocalUrl('https://api.openai.com/v1')).toBe(false)
    expect(isLocalUrl('')).toBe(false)
  })
  it('treats custom as key-optional on a local URL', () => {
    expect(providerNeedsKey('custom', { auth: ['api_key'] }, 'http://127.0.0.1:5001/v1')).toBe(false)
    expect(providerNeedsKey('custom', { auth: ['api_key'] }, 'https://my-host.example/v1')).toBe(true)
    expect(providerNeedsKey('openai', { auth: ['api_key'] }, '')).toBe(true)
    expect(providerNeedsKey('ollama', { auth: ['none'] }, '')).toBe(false)
    expect(providerNeedsKey('xai', { auth: ['oauth'] }, '')).toBe(false)
  })
  it('allows a typed model id for local providers or when discovery failed', () => {
    expect(allowsFreeTextModel('ollama', null)).toBe(true)
    expect(allowsFreeTextModel('llamacpp', null)).toBe(true)
    expect(allowsFreeTextModel('openai', okDiscovery)).toBe(false)
    expect(allowsFreeTextModel('openai', { ...okDiscovery, ok: false })).toBe(true)
  })
  it('always shows the base URL for ollama/custom/llamacpp and respects the catalog flag', () => {
    expect(showsBaseUrl('ollama', { show_base_url: false })).toBe(true)
    expect(showsBaseUrl('rmb', { show_base_url: true })).toBe(true)
    expect(showsBaseUrl('openai', { show_base_url: false })).toBe(false)
  })
})
