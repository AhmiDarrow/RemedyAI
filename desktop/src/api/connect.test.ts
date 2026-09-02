import { describe, expect, it } from 'vitest'
import {
  DEFAULT_CONNECT_PANES,
  CONNECT_PANE_LABELS,
  connectListenLabel,
  connectPairedLabel,
  filterConnectAddresses,
  mergeConnectPanes,
  normalizeConnectStatus,
  parseConnectPauseResult,
  parseTailscaleStatus,
  parseTailscaleAction,
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

  it('maps the server listening tuple, even when it differs from the bind', () => {
    const st = normalizeConnectStatus({
      enabled: true,
      bind_host: '10.0.0.4',
      bind_port: 7401,
      listening: ['192.168.1.5', 7401],
    })
    expect(st.listening).toEqual(['192.168.1.5', 7401])
    expect(connectListenLabel(st)).toBe('192.168.1.5:7401')
  })

  it('keeps listening null when enabled but nothing is bound', () => {
    const st = normalizeConnectStatus({
      enabled: true,
      bind_host: '10.0.0.4',
      bind_port: 7401,
      listening: null,
    })
    expect(st.listening).toBeNull()
    expect(connectListenLabel(st)).toBe('')
  })

  it('leaves listening unknown when the payload omits it and falls back to the bind', () => {
    const st = normalizeConnectStatus({ enabled: true, bind_host: '10.0.0.4', bind_port: 7401 })
    expect(st.listening).toBeUndefined()
    expect(connectListenLabel(st)).toBe('10.0.0.4:7401')
  })

  it('keeps a float paired_at from the store as text', () => {
    const st = normalizeConnectStatus({
      devices: [{ id: 'p1', name: 'Pixel', paired_at: 1725000000.5 }],
    })
    expect(st.devices[0]?.paired_at).toBe('1725000000.5')
  })
})

describe('connectPairedLabel', () => {
  const now = 1725000000.5 * 1000 + 3 * 60 * 1000

  it('reads float epoch seconds', () => {
    expect(connectPairedLabel('1725000000.5', now)).toBe('3m ago')
    expect(connectPairedLabel(1725000000.5, now)).toBe('3m ago')
  })

  it('reads integer seconds, epoch ms, and ISO strings', () => {
    expect(connectPairedLabel('1725000000', now)).toBe('3m ago')
    expect(connectPairedLabel(1725000000500, now)).toBe('3m ago')
    expect(connectPairedLabel(new Date(1725000000500).toISOString(), now)).toBe('3m ago')
  })

  it('is empty for nothing or garbage', () => {
    expect(connectPairedLabel(undefined, now)).toBe('')
    expect(connectPairedLabel('', now)).toBe('')
    expect(connectPairedLabel('not a date', now)).toBe('')
  })
})

describe('parseConnectPauseResult', () => {
  it('treats the {ok, paused} reply from /pause and /resume as done', () => {
    expect(parseConnectPauseResult({ ok: true, paused: true }, true)).toEqual({
      ok: true,
      paused: true,
    })
    expect(parseConnectPauseResult({ ok: true, paused: false }, false)).toEqual({
      ok: true,
      paused: false,
    })
  })

  it('asks for a PUT fallback when the reply is not a pause ack', () => {
    expect(parseConnectPauseResult(null, true).ok).toBe(false)
    expect(parseConnectPauseResult({}, true).ok).toBe(false)
    expect(parseConnectPauseResult({ ok: false, paused: true }, true).ok).toBe(false)
  })

  it('accepts a full status snapshot too', () => {
    const r = parseConnectPauseResult({ enabled: true, paused: true, panes: {}, devices: [] }, true)
    expect(r.ok).toBe(true)
    expect(r.paused).toBe(true)
    expect(r.status?.enabled).toBe(true)
  })
})

describe('parseTailscaleStatus', () => {
  it('parses a connected status with a tailnet ip', () => {
    const st = parseTailscaleStatus({
      installed: true,
      running: true,
      logged_in: true,
      tailnet_ipv4: '100.101.102.103',
      version: '1.102.3',
      error: '',
    })
    expect(st.installed).toBe(true)
    expect(st.logged_in).toBe(true)
    expect(st.tailnet_ipv4).toBe('100.101.102.103')
    expect(st.version).toBe('1.102.3')
  })

  it('degrades when fields are missing', () => {
    const st = parseTailscaleStatus(null)
    expect(st.installed).toBe(false)
    expect(st.logged_in).toBe(false)
    expect(st.tailnet_ipv4).toBe('')
    expect(st.error).toBe('')
  })
})

describe('parseTailscaleAction', () => {
  it('keeps optional login url and msi path', () => {
    const a = parseTailscaleAction({
      status: 'needs_login',
      message: 'Open the sign-in link',
      login_url: 'https://login.tailscale.com/a/abc123',
    })
    expect(a.status).toBe('needs_login')
    expect(a.login_url).toBe('https://login.tailscale.com/a/abc123')
    expect(a.msi_path).toBeUndefined()
  })

  it('degrades to empty strings', () => {
    const a = parseTailscaleAction(undefined)
    expect(a.status).toBe('')
    expect(a.message).toBe('')
    expect(a.login_url).toBeUndefined()
  })
})
