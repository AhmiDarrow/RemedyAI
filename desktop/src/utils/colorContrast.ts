/** Relative luminance of a CSS hex color (theme palettes use RGB hex). */
export function luminance(hex: string): number {
  const raw = hex.replace('#', '')
  const rgb = raw.length === 3 ? [...raw].map((c) => c + c).join('') : raw
  if (!/^[0-9a-f]{6}$/i.test(rgb)) return 0
  const channels = [0, 2, 4].map((i) => {
    const value = parseInt(rgb.slice(i, i + 2), 16) / 255
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  })
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
}

export function contrastRatio(a: string, b: string): number {
  const x = luminance(a)
  const y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

/** Choose readable text for both built-in and owner-selected accent colors. */
export function contrastingText(background: string): string {
  return contrastRatio(background, '#ffffff') >= contrastRatio(background, '#000000') ? '#ffffff' : '#000000'
}
