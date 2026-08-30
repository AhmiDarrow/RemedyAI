import { describe, expect, it } from 'vitest'
import {
  CONNECT_LOOPBACK_BIND_WARNING,
  isConnectCompact,
  isConnectLoopbackHost,
  preferredConnectBindHost,
} from './connectMode'

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

describe('preferredConnectBindHost', () => {
  it('skips loopback when a LAN unicast exists and bind is empty', () => {
    expect(preferredConnectBindHost(['127.0.0.1', '10.0.0.5', '192.168.1.20'], '')).toBe(
      '10.0.0.5',
    )
    expect(preferredConnectBindHost(['127.0.0.1', '10.0.0.5'])).toBe('10.0.0.5')
  })

  it('keeps an explicit current host, including loopback', () => {
    expect(preferredConnectBindHost(['10.0.0.5', '127.0.0.1'], '127.0.0.1')).toBe(
      '127.0.0.1',
    )
    expect(preferredConnectBindHost(['10.0.0.5', '127.0.0.1'], '10.0.0.5')).toBe(
      '10.0.0.5',
    )
  })

  it('falls back to loopback only when it is the only candidate', () => {
    expect(preferredConnectBindHost(['127.0.0.1'], '')).toBe('127.0.0.1')
    expect(preferredConnectBindHost([], '')).toBe('')
  })

  it('treats blank and whitespace current as unset', () => {
    expect(preferredConnectBindHost(['127.0.0.1', '192.168.0.4'], '   ')).toBe(
      '192.168.0.4',
    )
  })
})

describe('isConnectLoopbackHost', () => {
  it('flags 127/8 and not LAN', () => {
    expect(isConnectLoopbackHost('127.0.0.1')).toBe(true)
    expect(isConnectLoopbackHost(' 127.0.0.2 ')).toBe(true)
    expect(isConnectLoopbackHost('10.0.0.5')).toBe(false)
    expect(isConnectLoopbackHost('')).toBe(false)
  })

  it('has a plain warning for an explicit loopback pick', () => {
    expect(CONNECT_LOOPBACK_BIND_WARNING).toMatch(/this computer/i)
    expect(CONNECT_LOOPBACK_BIND_WARNING).toMatch(/LAN/i)
  })
})
