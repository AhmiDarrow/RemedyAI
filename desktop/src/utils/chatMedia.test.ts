import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  getApiBase: () => '/api',
  getServerUrl: () => 'http://127.0.0.1:7400',
  ensureApiToken: async () => 'test-token',
  authHeaders: () => ({ Authorization: 'Bearer test-token' }),
}))

import {
  chatMediaRequestUrl,
  clearChatMediaCache,
  isAbsoluteFsPath,
  isAuthenticatedApiUrl,
  isLocalMediaPath,
  isRemoteOrDataUrl,
  normalizeLocalMediaPath,
  peekChatMediaUrl,
  resolveChatMediaUrl,
  shouldUseCorsForImage,
} from './chatMedia'

describe('chatMedia path helpers', () => {
  // resolveChatMediaUrl needs fetch — path helpers only here
  it('detects remote and data urls', () => {
    expect(isRemoteOrDataUrl('https://x.com/a.png')).toBe(true)
    expect(isRemoteOrDataUrl('data:image/png;base64,xx')).toBe(true)
    expect(isRemoteOrDataUrl('blob:http://local/1')).toBe(true)
    expect(isRemoteOrDataUrl('assets/foo.png')).toBe(false)
  })

  it('detects local paths used by agents', () => {
    expect(isLocalMediaPath('assets/previews/hero.png')).toBe(true)
    expect(isLocalMediaPath('C:\\Users\\Administrator\\RemedyAI\\assets\\x.png')).toBe(
      true,
    )
    expect(isLocalMediaPath('file:///C:/Users/a/b.png')).toBe(true)
    expect(isLocalMediaPath('https://cdn.example/x.png')).toBe(false)
    // Loopback API attachments are not "local paths" — they use authed fetch
    expect(
      isLocalMediaPath(
        'http://127.0.0.1:7400/api/sessions/s/attachments/a.png',
      ),
    ).toBe(false)
    expect(isLocalMediaPath('/api/sessions/s/attachments/a.png')).toBe(false)
  })

  it('detects authenticated loopback API image urls', () => {
    expect(isAuthenticatedApiUrl('/api/sessions/s/attachments/a.png')).toBe(true)
    expect(
      isAuthenticatedApiUrl(
        'http://127.0.0.1:7400/api/sessions/s/attachments/a.png',
      ),
    ).toBe(true)
    expect(isAuthenticatedApiUrl('https://cdn.example/x.png')).toBe(false)
    expect(isAuthenticatedApiUrl('C:/Users/a/b.png')).toBe(false)
  })

  it('normalizes file urls and angle brackets', () => {
    expect(normalizeLocalMediaPath('file:///C:/Users/a/b.png')).toMatch(
      /^C:[/\\]Users[/\\]a[/\\]b\.png$/i,
    )
    expect(normalizeLocalMediaPath('assets/previews/x.png')).toBe(
      'assets/previews/x.png',
    )
    expect(
      normalizeLocalMediaPath(
        '<C:/Users/Administrator/.remedy/attachments/s/shot.png>',
      ),
    ).toBe('C:/Users/Administrator/.remedy/attachments/s/shot.png')
  })

  it('never sets CORS on blob/data (WebView image viewer)', () => {
    expect(shouldUseCorsForImage('blob:http://127.0.0.1/uuid')).toBe(false)
    expect(shouldUseCorsForImage('data:image/png;base64,xx')).toBe(false)
    expect(shouldUseCorsForImage('/icon.png')).toBe(false)
    expect(shouldUseCorsForImage('https://cdn.example/x.png')).toBe(true)
  })

  it('peek returns displayable urls without a network hop', () => {
    expect(peekChatMediaUrl('data:image/png;base64,xx')).toBe(
      'data:image/png;base64,xx',
    )
    expect(peekChatMediaUrl('blob:http://127.0.0.1/uuid')).toBe(
      'blob:http://127.0.0.1/uuid',
    )
    expect(peekChatMediaUrl('https://cdn.example/x.png')).toBe(
      'https://cdn.example/x.png',
    )
    expect(peekChatMediaUrl('data:text/plain,hi')).toBe(null)
    expect(peekChatMediaUrl('')).toBe(null)
    // Local / authed paths need a cache fill — peek must not throw
    expect(peekChatMediaUrl('assets/previews/hero.png')).toBe(null)
    expect(peekChatMediaUrl('/api/sessions/s/attachments/a.png')).toBe(null)
  })
})

describe('chatMedia request resolution (/api/media with Bearer)', () => {
  it('maps relative attachments paths to /api/media against the API base', () => {
    expect(chatMediaRequestUrl('attachments/sess1/remedy_comfy_00019_.png')).toBe(
      '/api/media?path=attachments%2Fsess1%2Fremedy_comfy_00019_.png',
    )
    // Angle-bracket markdown target with a space
    expect(chatMediaRequestUrl('<attachments/s/my shot.png>')).toBe(
      '/api/media?path=attachments%2Fs%2Fmy%20shot.png',
    )
  })

  it('maps absolute Windows paths (and collapses ~/.remedy/attachments)', () => {
    const win = String.raw`C:\ComfyUI\output\comfy_out\x.png`
    expect(chatMediaRequestUrl(win)).toBe(
      `/api/media?path=${encodeURIComponent(win)}`,
    )
    expect(
      chatMediaRequestUrl('C:/Users/Administrator/.remedy/attachments/s/shot.png'),
    ).toBe('/api/media?path=attachments%2Fs%2Fshot.png')
    expect(chatMediaRequestUrl('file:///C:/tmp/a.png')).toBe(
      `/api/media?path=${encodeURIComponent('C:/tmp/a.png')}`,
    )
  })

  it('does not route data URIs or http URLs through /api/media', () => {
    expect(chatMediaRequestUrl('data:image/png;base64,xx')).toBe(null)
    expect(chatMediaRequestUrl('https://cdn.example/x.png')).toBe(null)
    expect(chatMediaRequestUrl('/api/sessions/s/attachments/a.png')).toBe(null)
  })

  it('flags absolute filesystem paths for the reveal action', () => {
    expect(isAbsoluteFsPath(String.raw`C:\x\y.png`)).toBe(true)
    expect(isAbsoluteFsPath(String.raw`\\server\share\y.png`)).toBe(true)
    expect(isAbsoluteFsPath('attachments/s/y.png')).toBe(false)
    expect(isAbsoluteFsPath('https://cdn.example/x.png')).toBe(false)
  })

  it('resolveChatMediaUrl fetches local paths with Authorization and returns a blob url', async () => {
    const calls: Array<{ url: string; headers: Record<string, string> }> = []
    const origFetch = globalThis.fetch
    const origCreate = URL.createObjectURL
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), headers: (init?.headers || {}) as Record<string, string> })
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        headers: new Headers({ 'content-type': 'image/png' }),
        blob: async () => new Blob([new Uint8Array([1, 2, 3])], { type: 'image/png' }),
        text: async () => '',
      } as unknown as Response
    }) as typeof fetch
    URL.createObjectURL = () => 'blob:http://127.0.0.1/test-blob'
    try {
      const rel = await resolveChatMediaUrl('attachments/sess1/a.png')
      expect(rel).toBe('blob:http://127.0.0.1/test-blob')
      expect(calls[0]!.url).toBe('/api/media?path=attachments%2Fsess1%2Fa.png')
      expect(calls[0]!.headers.Authorization).toBe('Bearer test-token')

      const winPath = String.raw`C:\comfy_out\b.png`
      const abs = await resolveChatMediaUrl(winPath)
      expect(abs).toBe('blob:http://127.0.0.1/test-blob')
      expect(calls[1]!.url).toBe(
        `/api/media?path=${encodeURIComponent(winPath)}`,
      )

      // Cached: second resolve of the same path does not refetch
      await resolveChatMediaUrl('attachments/sess1/a.png')
      expect(calls.length).toBe(2)

      // Pass-throughs
      expect(await resolveChatMediaUrl('data:image/png;base64,xx')).toBe(
        'data:image/png;base64,xx',
      )
      expect(await resolveChatMediaUrl('https://cdn.example/x.png')).toBe(
        'https://cdn.example/x.png',
      )
      expect(await resolveChatMediaUrl('data:text/html,<b>x</b>')).toBe('')
      expect(calls.length).toBe(2)
    } finally {
      globalThis.fetch = origFetch
      URL.createObjectURL = origCreate
      clearChatMediaCache()
    }
  })
})
