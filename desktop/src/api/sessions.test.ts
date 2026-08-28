import { describe, expect, it } from 'vitest'
import { abortSessionPath } from './sessions'

describe('abortSessionPath', () => {
  it('sends a positive epoch so a stale Stop cannot kill a newer turn', () => {
    expect(abortSessionPath('s1', 'stop', 4)).toBe(
      '/sessions/s1/abort?reason=stop&epoch=4',
    )
  })

  it('omits epoch for CLI-style abort-current', () => {
    expect(abortSessionPath('s1')).toBe('/sessions/s1/abort?reason=stop')
    expect(abortSessionPath('s1', 'supersede')).toBe(
      '/sessions/s1/abort?reason=supersede',
    )
  })

  it('drops zero, negative, and non-finite epochs', () => {
    expect(abortSessionPath('s1', 'stop', 0)).toBe(
      '/sessions/s1/abort?reason=stop',
    )
    expect(abortSessionPath('s1', 'stop', -1)).toBe(
      '/sessions/s1/abort?reason=stop',
    )
    expect(abortSessionPath('s1', 'stop', Number.NaN)).toBe(
      '/sessions/s1/abort?reason=stop',
    )
  })
})
