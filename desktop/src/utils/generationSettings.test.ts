import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { buildGenerationPatch, formFromValues, modelKey, tokenError, EMPTY_FORM } from './generationSettings'

vi.mock('../api/settings', () => ({
  getSettings: () => new Promise(() => {}),
  updateSettings: vi.fn(),
}))

describe('generation settings helpers', () => {
  it('reads stored values and treats zero as unset', () => {
    expect(formFromValues({ reasoning_effort: 'HIGH', max_output_tokens: 4096, context_window_tokens: 0 })).toEqual({
      reasoning_effort: 'high',
      thinking_budget_tokens: '',
      max_output_tokens: '4096',
      context_window_tokens: '',
    })
    expect(formFromValues({ reasoning_effort: 'warp' }).reasoning_effort).toBe('')
    expect(formFromValues(undefined)).toEqual(EMPTY_FORM)
  })

  it('validates token ranges', () => {
    expect(tokenError('max_output_tokens', '')).toBeNull()
    expect(tokenError('max_output_tokens', '8,192')).toBeNull()
    expect(tokenError('max_output_tokens', '12')).toMatch(/256/)
    expect(tokenError('thinking_budget_tokens', '2.5')).not.toBeNull()
    expect(tokenError('context_window_tokens', '3000000')).not.toBeNull()
  })

  it('builds a global patch where empty fields clear the setting', () => {
    const patch = buildGenerationPatch(
      { ...EMPTY_FORM, reasoning_effort: 'low', context_window_tokens: '64000' },
      'all',
      'openai',
      'o3',
    )
    expect(patch).toEqual({
      reasoning_effort: 'low',
      thinking_budget_tokens: 0,
      max_output_tokens: 0,
      context_window_tokens: 64000,
    })
  })

  it('builds a per-model patch keyed by provider/model', () => {
    expect(modelKey('anthropic', 'claude-sonnet-4-5')).toBe('anthropic/claude-sonnet-4-5')
    const patch = buildGenerationPatch({ ...EMPTY_FORM, thinking_budget_tokens: '6000' }, 'model', 'anthropic', 'claude-sonnet-4-5')
    expect(patch).toEqual({
      model_settings: {
        'anthropic/claude-sonnet-4-5': {
          reasoning_effort: '',
          thinking_budget_tokens: 6000,
          max_output_tokens: 0,
          context_window_tokens: 0,
        },
      },
    })
    expect(buildGenerationPatch(EMPTY_FORM, 'model', '', '')).toEqual({})
  })

  it('renders the settings section with every control', async () => {
    const { ReasoningContextSection } = await import('../components/settings/ReasoningContextSection')
    const html = renderToStaticMarkup(createElement(ReasoningContextSection, { provider: 'openai', model: 'o3' }))
    expect(html).toContain('Reasoning effort')
    expect(html).toContain('Extra high')
    expect(html).toContain('Thinking budget')
    expect(html).toContain('Max output tokens')
    expect(html).toContain('Context window')
    expect(html).toContain('This model (o3)')
  })
})
