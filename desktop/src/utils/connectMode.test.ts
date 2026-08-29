import { describe, expect, it } from 'vitest'
import { isConnectCompact } from './connectMode'

describe('isConnectCompact', () => {
  it('is true for ?connect=1', () => {
    expect(isConnectCompact('?connect=1')).toBe(true)
  })

  it('is true when connect=1 is among other query params', () => {
    expect(isConnectCompact('?foo=1&connect=1')).toBe(true)
    expect(isConnectCompact('?connect=1&bar=2')).toBe(true)
  })

  it('is true for connect=1 without a leading ?', () => {
    expect(isConnectCompact('connect=1')).toBe(true)
    expect(isConnectCompact('foo=1&connect=1')).toBe(true)
  })

  it('is false when connect is missing', () => {
    expect(isConnectCompact('')).toBe(false)
    expect(isConnectCompact('?')).toBe(false)
    expect(isConnectCompact('?foo=1')).toBe(false)
  })

  it('is false for connect=0 and other non-1 values', () => {
    expect(isConnectCompact('?connect=0')).toBe(false)
    expect(isConnectCompact('connect=0')).toBe(false)
    expect(isConnectCompact('?connect=true')).toBe(false)
    expect(isConnectCompact('?connect=01')).toBe(false)
    expect(isConnectCompact('?connect=')).toBe(false)
    expect(isConnectCompact('?Connect=1')).toBe(false)
  })

  it('ignores a hash-only connect flag', () => {
    expect(isConnectCompact('')).toBe(false)
  })
})
