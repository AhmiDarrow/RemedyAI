import { describe, expect, it } from 'vitest'
import { EN, formatMsg } from './en'
import { getAgentIdentity, normalizeAgentGender, pronounVars, setAgentIdentity } from './pronouns'

const fmt = (key: string, gender: string, name = 'Remedy') =>
  formatMsg(EN[key], pronounVars(gender, name) as unknown as Record<string, string>)

describe('pronounVars', () => {
  it('female → she/her/hers', () => {
    const p = pronounVars('female')
    expect([p.they, p.them, p.their, p.theirs, p.themself]).toEqual(['she', 'her', 'her', 'hers', 'herself'])
    expect([p.They, p.s, p.is]).toEqual(['She', 's', 'is'])
  })
  it('male → he/him/his', () => {
    const p = pronounVars('male')
    expect([p.they, p.them, p.their, p.theirs, p.themself]).toEqual(['he', 'him', 'his', 'his', 'himself'])
    expect([p.They, p.s, p.has]).toEqual(['He', 's', 'has'])
  })
  it('neutral, unset, or unknown → they/them/theirs with plural verbs', () => {
    for (const g of ['neutral', '', undefined, null, 'robot']) {
      const p = pronounVars(g)
      expect([p.they, p.them, p.their, p.theirs]).toEqual(['they', 'them', 'their', 'theirs'])
      expect([p.s, p.is, p.has, p.was]).toEqual(['', 'are', 'have', 'were'])
    }
  })
  it('normalizes loose spellings', () => {
    expect(normalizeAgentGender('SHE')).toBe('female')
    expect(normalizeAgentGender(' m ')).toBe('male')
    expect(normalizeAgentGender('nb')).toBe('neutral')
  })
  it('carries the agent name, defaulting to Remedy', () => {
    expect(pronounVars('male', 'Atlas').agent).toBe('Atlas')
    expect(pronounVars('male', '  ').agent).toBe('Remedy')
  })
})

describe('English strings follow the configured gender', () => {
  it('composer queue placeholder', () => {
    expect(fmt('composer.placeholderQueue', 'female')).toMatch(/^Steer while she works \(Enter\)/)
    expect(fmt('composer.placeholderQueue', 'male')).toMatch(/^Steer while he works \(Enter\)/)
    expect(fmt('composer.placeholderQueue', 'neutral')).toMatch(/^Steer while they work \(Enter\)/)
  })
  it('plan strings use the name and agreement', () => {
    expect(fmt('plan.researching', 'male', 'Atlas')).toBe(
      'Atlas is researching — the plan appears here once he saves it.',
    )
    expect(fmt('plan.hint', 'neutral')).toMatch(/before they build\.$/)
  })
  it('no English string hardcodes a third-person pronoun for the agent', () => {
    const gendered = /\b(she|her|hers|herself|he|him|his|himself)\b/i
    const offenders = Object.entries(EN).filter(([, v]) => gendered.test(v))
    expect(offenders).toEqual([])
  })
})

describe('identity store', () => {
  it('updates live and ignores no-op writes', () => {
    setAgentIdentity({ gender: 'male', name: 'Atlas' })
    const a = getAgentIdentity()
    expect(a).toEqual({ gender: 'male', name: 'Atlas' })
    setAgentIdentity({ gender: 'male' })
    expect(getAgentIdentity()).toBe(a)
    setAgentIdentity({ gender: 'neutral' })
    expect(getAgentIdentity()).toEqual({ gender: 'neutral', name: 'Atlas' })
    setAgentIdentity({ gender: 'female', name: 'Remedy' })
  })
})
