import { describe, expect, it } from 'vitest'
import { chatMarkdownUrlTransform } from './chatMarkdownUrl'

describe('chatMarkdownUrlTransform', () => {
  it('allows https and http', () => {
    expect(chatMarkdownUrlTransform('https://cdn.example/x.png')).toBe(
      'https://cdn.example/x.png',
    )
    expect(chatMarkdownUrlTransform('http://127.0.0.1:7400/a.png')).toBe(
      'http://127.0.0.1:7400/a.png',
    )
  })

  it('allows data:image URIs (Comfy / embedded previews)', () => {
    const src = 'data:image/png;base64,iVBORw0KGgo='
    expect(chatMarkdownUrlTransform(src)).toBe(src)
    expect(
      chatMarkdownUrlTransform('data:image/jpeg;base64,/9j/4AAQ'),
    ).toMatch(/^data:image\/jpeg/)
  })

  it('allows blob and file URLs', () => {
    expect(chatMarkdownUrlTransform('blob:http://127.0.0.1/uuid')).toBe(
      'blob:http://127.0.0.1/uuid',
    )
    expect(chatMarkdownUrlTransform('file:///C:/Users/a/b.png')).toBe(
      'file:///C:/Users/a/b.png',
    )
  })

  it('allows Windows drive paths used by attachments', () => {
    const win = 'C:\\Users\\Administrator\\.remedy\\attachments\\x\\shot.png'
    expect(chatMarkdownUrlTransform(win)).toBe(win)
    expect(
      chatMarkdownUrlTransform(
        'C:/Users/Administrator/.remedy/attachments/x/shot.png',
      ),
    ).toContain('C:/Users')
  })

  it('allows relative project paths', () => {
    expect(chatMarkdownUrlTransform('assets/previews/hero.png')).toBe(
      'assets/previews/hero.png',
    )
    expect(chatMarkdownUrlTransform('./foo.webp')).toBe('./foo.webp')
    expect(chatMarkdownUrlTransform('/api/sessions/s/attachments/a.png')).toBe(
      '/api/sessions/s/attachments/a.png',
    )
  })

  it('blocks javascript and vbscript', () => {
    expect(chatMarkdownUrlTransform('javascript:alert(1)')).toBe('')
    expect(chatMarkdownUrlTransform('JAVASCRIPT:void(0)')).toBe('')
    expect(chatMarkdownUrlTransform('vbscript:msgbox(1)')).toBe('')
  })

  it('blocks non-image data URIs', () => {
    expect(chatMarkdownUrlTransform('data:text/html,<script>x</script>')).toBe(
      '',
    )
    expect(chatMarkdownUrlTransform('data:application/javascript,alert(1)')).toBe(
      '',
    )
  })

  it('blocks protocol-relative URLs and unknown schemes', () => {
    expect(chatMarkdownUrlTransform('//evil.example/x.png')).toBe('')
    expect(chatMarkdownUrlTransform('ftp://host/x.png')).toBe('')
    expect(chatMarkdownUrlTransform('')).toBe('')
  })
})
