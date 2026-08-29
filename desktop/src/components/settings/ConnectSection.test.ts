import { describe, expect, it } from 'vitest'
import { CONNECT_PANE_KEYS, CONNECT_PANE_LABELS } from '../../api/connect'
import { ConnectSection } from './ConnectSection'

describe('ConnectSection', () => {
  it('is a settings section component', () => {
    expect(ConnectSection).toBeTypeOf('function')
  })

  it('covers every spec pane with locked approvals', () => {
    expect(CONNECT_PANE_KEYS).toEqual([
      'live_ui',
      'chat',
      'approvals',
      'sessions',
      'rails',
      'computer_preview',
      'settings_write',
    ])
    expect(CONNECT_PANE_LABELS.approvals).toBe('Approvals/Stop')
  })
})
