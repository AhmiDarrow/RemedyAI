import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./client', () => ({
  apiFetch: vi.fn(),
}))

import { apiFetch } from './client'
import {
  clearToolEvidenceCache,
  fetchToolEvidence,
  peekToolEvidence,
  toolEvidenceCacheSize,
} from './turnEvidence'

const RECORDED = {
  session_id: 's1',
  request_id: 'req-1',
  call_id: 'call-a',
  name: 'bash',
  input: { command: 'npm test' },
  ok: true,
  is_error: false,
  preview: '> vitest run…',
  output: 'x'.repeat(4096),
  bytes: 4096,
  images: [
    {
      media_type: 'image/png',
      sha256: 'abc123',
      bytes: 900,
      path: 'req-1/abc123.png',
      url: '/api/sessions/s1/turns/req-1/tools/call-a?image=abc123',
    },
    // A malformed row must not break the panel.
    { media_type: 'image/png', sha256: 'no-url' },
  ],
}

describe('turnEvidence', () => {
  beforeEach(() => {
    clearToolEvidenceCache()
    vi.mocked(apiFetch).mockReset()
    vi.mocked(apiFetch).mockResolvedValue(RECORDED as never)
  })

  afterEach(() => {
    clearToolEvidenceCache()
  })

  it('fetches nothing until a row is opened', () => {
    expect(peekToolEvidence('s1', 'req-1', 'call-a')).toBeNull()
    expect(apiFetch).not.toHaveBeenCalled()
  })

  it('returns the full recorded output and the image list on demand', async () => {
    const ev = await fetchToolEvidence('s1', 'req-1', 'call-a')
    expect(apiFetch).toHaveBeenCalledWith(
      '/sessions/s1/turns/req-1/tools/call-a',
    )
    expect(ev.output).toHaveLength(4096)
    expect(ev.output.length).toBeGreaterThan(ev.preview.length)
    expect(ev.ok).toBe(true)
    expect(ev.images).toEqual([
      {
        media_type: 'image/png',
        sha256: 'abc123',
        bytes: 900,
        path: 'req-1/abc123.png',
        url: '/api/sessions/s1/turns/req-1/tools/call-a?image=abc123',
      },
    ])
  })

  it('serves a re-opened row from the cache without another request', async () => {
    await fetchToolEvidence('s1', 'req-1', 'call-a')
    expect(peekToolEvidence('s1', 'req-1', 'call-a')?.output).toHaveLength(4096)
    await fetchToolEvidence('s1', 'req-1', 'call-a')
    expect(vi.mocked(apiFetch)).toHaveBeenCalledTimes(1)
  })

  it('shares one request when the same row is opened twice in a tick', async () => {
    const [a, b] = await Promise.all([
      fetchToolEvidence('s1', 'req-1', 'call-a'),
      fetchToolEvidence('s1', 'req-1', 'call-a'),
    ])
    expect(vi.mocked(apiFetch)).toHaveBeenCalledTimes(1)
    expect(a).toBe(b)
  })

  it('never holds every output — the cache is bounded and evicts the oldest', async () => {
    for (let i = 0; i < 20; i++) {
      vi.mocked(apiFetch).mockResolvedValueOnce({
        ...RECORDED,
        call_id: `call-${i}`,
      } as never)
      await fetchToolEvidence('s1', 'req-1', `call-${i}`)
    }
    expect(toolEvidenceCacheSize()).toBeLessThanOrEqual(6)
    expect(peekToolEvidence('s1', 'req-1', 'call-0')).toBeNull()
    expect(peekToolEvidence('s1', 'req-1', 'call-19')).not.toBeNull()
  })

  it('marks an errored tool result so the panel can colour it', async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({
      ...RECORDED,
      ok: false,
      is_error: true,
      output: 'exit 1',
      images: [],
    } as never)
    const ev = await fetchToolEvidence('s1', 'req-1', 'call-err')
    expect(ev.ok).toBe(false)
    expect(ev.is_error).toBe(true)
    expect(ev.output).toBe('exit 1')
  })
})
