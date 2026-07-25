import { describe, expect, it } from 'vitest'
import type { SettingsUpdate } from './settings'

describe('SettingsUpdate payload shape', () => {
  it('accepts polish-pass advanced fields', () => {
    const updates: SettingsUpdate = {
      harness_min_context_pct: 0.75,
      harness_max_context_pct: 0.92,
      thinking_level: 'high',
      approval_mode: 'auto',
      web_tools_enabled: false,
      http_bootstrap: true,
      allow_skill_creation: true,
      auto_approve_threshold: 0.8,
      log_level: 'INFO',
      sarcasm_mode: false,
    }
    expect(updates.approval_mode).toBe('auto')
    expect(updates.harness_min_context_pct).toBeLessThan(updates.harness_max_context_pct!)
  })
})
