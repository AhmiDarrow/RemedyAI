import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { AHMI_SIGNATURE, REMEDY_SOURCE_URL } from './aboutLine'
import { EN } from './i18n/en'
import { GAP } from './i18n/gap'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..', '..')

describe('Ahmi about line', () => {
  it('keeps the signature exact', () => {
    expect(AHMI_SIGNATURE).toBe('"Hope this helps" - Ahmi')
    expect(REMEDY_SOURCE_URL).toBe('https://github.com/AhmiDarrow/RemedyAI')
  })

  it('shows the signature and source link in About and at the end of the README', () => {
    const dialog = readFileSync(join(here, 'components/AboutDialog.tsx'), 'utf8')
    const settings = readFileSync(join(here, 'components/settings/sections_rest.tsx'), 'utf8')
    const readme = readFileSync(join(root, 'README.md'), 'utf8')
    expect(dialog).toContain('AhmiSignature')
    expect(dialog).toContain('AboutSourceLink')
    expect(settings).toContain('AhmiSignature')
    expect(settings).toContain('AboutSourceLink')
    expect(readme.trimEnd().endsWith(AHMI_SIGNATURE)).toBe(true)
  })

  it('translates the Source code label and never translates the signature', () => {
    expect(EN['about.source']).toBe('Source code')
    for (const [id, catalog] of Object.entries(GAP)) {
      expect(catalog['about.source']?.trim().length, id).toBeGreaterThan(0)
      expect(catalog['about.source'], id).not.toBe(EN['about.source'])
      expect(Object.values(catalog).includes(AHMI_SIGNATURE), id).toBe(false)
    }
    expect(Object.values(EN).includes(AHMI_SIGNATURE)).toBe(false)
  })
})
