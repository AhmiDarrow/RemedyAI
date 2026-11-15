/** Gap catalogs share one key set, match English placeholders, and skip Traditional Chinese. */

import { describe, expect, it } from 'vitest'
import { EN } from './en'
import { GAP, gapFor, gapLocaleId } from './gap'

const PLACEHOLDER = /\{(\w+)\}/g

function placeholders(value: string): string[] {
  return [...value.matchAll(PLACEHOLDER)].map((m) => m[1]).sort()
}

const reference = Object.keys(GAP.es).sort()

describe('desktop gap catalogs', () => {
  it('covers the ten target languages', () => {
    expect(Object.keys(GAP).sort()).toEqual(
      ['de', 'es', 'fr', 'it', 'ja', 'ko', 'pl', 'pt', 'ru', 'zh-Hans'].sort(),
    )
    expect(reference.length).toBeGreaterThan(40)
  })

  for (const id of Object.keys(GAP)) {
    it(`${id} matches the Spanish key set and English placeholders`, () => {
      const catalog = GAP[id]
      expect(Object.keys(catalog).sort()).toEqual(reference)
      for (const key of reference) {
        const value = catalog[key]
        expect(value.trim().length, key).toBeGreaterThan(0)
        expect(EN[key], key).toBeTruthy()
        expect(placeholders(value), key).toEqual(placeholders(EN[key]))
      }
      expect(catalog['starter.planPrompt'].endsWith(' ')).toBe(true)
    })
  }

  it('does not translate English, Traditional Chinese, or unknown languages', () => {
    expect(gapFor('en')).toEqual({})
    expect(gapFor('en-US')).toEqual({})
    expect(gapFor('zh-Hant')).toEqual({})
    expect(gapFor('zh-TW')).toEqual({})
    expect(gapFor('zh-HK')).toEqual({})
    expect(gapFor('sv')).toEqual({})
    expect(gapLocaleId('zh-Hant')).toBe('')
  })

  it('resolves Portuguese and Simplified Chinese aliases', () => {
    expect(gapLocaleId('pt-BR')).toBe('pt')
    expect(gapLocaleId('zh-CN')).toBe('zh-Hans')
    expect(gapFor('pt-BR')['settings.categories']).toBe(GAP.pt['settings.categories'])
    expect(gapFor('es')['settings.categories']).not.toBe(EN['settings.categories'])
    expect(EN['starter.planPrompt'].endsWith(' ')).toBe(true)
    expect(GAP['zh-Hans']['starter.planPrompt']).toBe('帮我规划： ')
  })
})
