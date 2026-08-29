import { describe, expect, it } from 'vitest'
import {
  DEFAULT_CONNECT_PANES,
  CONNECT_PANE_LABELS,
  filterConnectAddresses,
  mergeConnectPanes,
  normalizeConnectStatus,
} from './connect'

describe('mergeConnectPanes', () => {
  it('defaults live_ui chat approvals sessions rails on; preview and settings write off', () => {
    expect(mergeConnectPanes(null)).toEqual(DEFAULT_CONNECT_PANES)
    expect(DEFAULT_CONNECT_PANES.live_ui).toBe(true)
    expect(DEFAULT_CONNECT_PANES.chat).toBe(true)
    expect(DEFAULT_CONNECT_PANES.approvals).toBe(true)
    expect(DEFAULT_CONNECT_PANES.sessions).toBe(true)
    expect(DEFAULT_CONNECT_PANES.rails).toBe(true)
    expect(DEFAULT_CONNECT_PANES.computer_preview).toBe(false)
    expect(DEFAULT_CONNECT_PANES.settings_write).toBe(false)
  })

  it('keeps approvals on even when the payload turns them off', () => {
    expect(mergeConnectPanes({ approvals: false }).approvals).toBe(true)
    expect(mergeConnectPanes({ approvals: false, live_ui: false }).live_ui).toBe(false)
  })

  it('fills missing keys from defaults', () => {
    const next = mergeConnectPanes({ chat: false })
    expect(next.chat).toBe(false)
    expect(next.live_ui).toBe(true)
    expect(next.computer_preview).toBe(false)
  })
})

describe('CONNECT_PANE_LABELS', () => {
  it('uses the spec labels', () => {
    expect(CONNECT_PANE_LABELS.live_ui).toBe('Live host UI')
    expect(CONNECT_PANE_LABELS.chat).toBe('Chat/stream')
    expect(CONNECT_PANE_LABELS.approvals).toBe('Approvals/Stop')
    expect(CONNECT_PANE_LABELS.sessions).toBe('Sessions')
    expect(CONNECT_PANE_LABELS.rails).toBe('Files/Terminal/Browser rails')
    expect(CONNECT_PANE_LABELS.computer_preview).toBe('Computer preview')
    expect(CONNECT_PANE_LABELS.settings_write).toBe('Settings write')
  })
})

describe('filterConnectAddresses', () => {
  it('keeps IPv4 candidates and drops 0.0.0.0 and wildcards', () => {
    expect(
      filterConnectAddresses([
        '192.168.1.10',
        '0.0.0.0',
        '*',
        '10.0.0.4/24',
        '172.16.0.9:7400',
        '::',
        'fe80::1',
        '  192.168.1.10  ',
      ]),
    ).toEqual(['192.168.1.10', '10.0.0.4', '172.16.0.9'])
  })

  it('treats missing, empty, and non-arrays as no addresses', () => {
    expect(filterConnectAddresses(undefined)).toEqual([])
    expect(filterConnectAddresses(null)).toEqual([])
    expect(filterConnectAddresses([])).toEqual([])
    expect(filterConnectAddresses('192.168.1.10')).toEqual([])
    expect(filterConnectAddresses([0, { ip: '1.2.3.4' }, ''])).toEqual([])
  })

  it('drops 0.0.0.0 with port or CIDR and starred hosts', () => {
    expect(filterConnectAddresses(['0.0.0.0:7400', '0.0.0.0/0', '*.*.*.*', '192.168.0.*'])).toEqual(
      [],
    )
  })

  it('keeps loopback last so Enable Connect prefers a LAN address', () => {
    expect(filterConnectAddresses(['127.0.0.1', '10.0.0.4', '192.168.1.10'])).toEqual([
      '10.0.0.4',
      '192.168.1.10',
      '127.0.0.1',
    ])
  })
})

describe('normalizeConnectStatus', () => {
  it('degrades when fields are missing', () => {
    const st = normalizeConnectStatus({})
    expect(st.enabled).toBe(false)
    expect(st.paused).toBe(false)
    expect(st.bind_host).toBe('')
    expect(st.bind_port).toBe(0)
    expect(st.relay_url).toBe('')
    expect(st.devices).toEqual([])
    expect(st.panes).toEqual(DEFAULT_CONNECT_PANES)
  })

  it('hides a 0.0.0.0 bind host and forces approvals', () => {
    const st = normalizeConnectStatus({
      enabled: true,
      bind_host: '0.0.0.0',
      panes: { approvals: false, chat: false },
      devices: [{ id: 'p1', name: 'Pixel' }, { name: 'no-id' }],
    })
    expect(st.bind_host).toBe('')
    expect(st.panes.approvals).toBe(true)
    expect(st.panes.chat).toBe(false)
    expect(st.devices).toEqual([{ id: 'p1', name: 'Pixel' }])
  })
})
