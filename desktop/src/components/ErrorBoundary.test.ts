import { describe, expect, it } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

describe('ErrorBoundary', () => {
  it('exports a recoverable React error boundary class', () => {
    expect(ErrorBoundary).toBeTypeOf('function')
    expect(typeof ErrorBoundary.getDerivedStateFromError).toBe('function')
    const next = ErrorBoundary.getDerivedStateFromError(new Error('boom'))
    expect(next).toMatchObject({ error: expect.any(Error), copied: false })
    expect((next as { error: Error }).error.message).toBe('boom')
  })
})
