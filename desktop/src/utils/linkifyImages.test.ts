import { describe, expect, it } from 'vitest'
import { linkifyBareImagePaths } from './linkifyImages'

describe('linkifyBareImagePaths', () => {
  it('wraps windows absolute png paths', () => {
    const t =
      'Here: C:\\Users\\Administrator\\.remedy\\attachments\\x\\Screenshot 2026.png end'
    const out = linkifyBareImagePaths(t)
    expect(out).toContain('![Screenshot 2026.png](')
    expect(out).toMatch(/C:\/Users\/Administrator/)
  })

  it('does not double-wrap markdown images', () => {
    const t = '![hero](assets/previews/hero.png)'
    expect(linkifyBareImagePaths(t)).toBe(t)
  })

  it('wraps file urls and relative assets', () => {
    expect(linkifyBareImagePaths('file:///C:/a/b.png')).toContain('](file:///')
    expect(linkifyBareImagePaths('see assets/foo.webp')).toContain('![foo.webp](')
  })

  it('wraps .remedy attachment paths with spaces', () => {
    const t =
      'C:\\Users\\Administrator\\.remedy\\attachments\\3ceca0f6\\Screenshot 2026-07-26 004244.png'
    const out = linkifyBareImagePaths(t)
    expect(out).toMatch(/!\[Screenshot 2026-07-26 004244\.png\]\(/)
    expect(out).toContain('.remedy/attachments/')
  })

  it('leaves non-image paths alone', () => {
    const t = 'Open C:\\Users\\x\\file.txt and README.md please'
    expect(linkifyBareImagePaths(t)).toBe(t)
  })
})
