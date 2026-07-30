import { describe, expect, it } from 'vitest'
import {
  isAuthenticatedApiUrl,
  isLocalMediaPath,
  isRemoteOrDataUrl,
  normalizeLocalMediaPath,
  shouldUseCorsForImage,
} from './chatMedia'

describe('chatMedia path helpers', () => {
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
})
