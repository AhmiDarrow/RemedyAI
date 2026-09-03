import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import { buildDiagnostics, describeExit, formatAgo, formatUptime } from './ServerMenu'

const statusBarSrc = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'StatusBar.tsx'),
  'utf8',
)

describe('server menu helpers', () => {
  it('explains Windows native crash codes and plain exit codes', () => {
    expect(describeExit('exit code: 3221225477')).toContain('0xC0000005')
    expect(describeExit('exit code: 3221225477')).toContain('access violation')
    expect(describeExit('exit code: -1073741819')).toContain('access violation')
    expect(describeExit('exit code: 1')).toContain('error')
    expect(describeExit('exit code: 0')).toContain('cleanly')
    expect(describeExit('exit code: 42')).toBe('exit code: 42')
    expect(describeExit('signal: 9 (SIGKILL)')).toContain('killed')
    expect(describeExit('')).toBe('')
    expect(describeExit(null)).toBe('')
  })

  it('formats uptime and relative times', () => {
    const now = 1_000_000_000
    expect(formatUptime(null)).toBe('—')
    expect(formatUptime(now - 5_000, now)).toBe('5s')
    expect(formatUptime(now - 65_000, now)).toBe('1m 5s')
    expect(formatUptime(now - 3_600_000 * 3, now)).toBe('3h 0m')
    expect(formatAgo(now - 2_000, now)).toBe('just now')
    expect(formatAgo(now - 120_000, now)).toBe('2 min ago')
    expect(formatAgo(null)).toBe('')
  })

  it('builds a diagnostics dump with the exit, gateway and output tail', () => {
    const text = buildDiagnostics(
      {
        mode: 'managed',
        running: true,
        healthy: true,
        pid: 1234,
        started_at_ms: Date.now() - 10_000,
        launch_cmd: 'C:\\remedy-desktop.exe',
        api_origin: 'http://127.0.0.1:7400',
        port: 7400,
        unexpected_exits: 1,
        last_exit_status: 'exit code: 3221225477',
        last_exit_at_ms: Date.now() - 60_000,
        recovery_attempts: 1,
        recovered_at_ms: Date.now() - 50_000,
        last_error: null,
        output: ['[err] Traceback', '[err] RuntimeError: boom'],
        logs_dir: 'C:\\Users\\x\\.remedy\\logs',
        data_dir: 'C:\\Users\\x\\.remedy',
        desktop_version: '0.50.0',
      },
      null,
      { crashes: 1, last_crash: 'SystemExit: sim', last_crash_ts: 0, healing: false, serving: true, thread_alive: true, listening: ['10.0.0.2', 7401] },
      'connected',
      '0.50.0',
    )
    expect(text).toContain('access violation')
    expect(text).toContain('pid: 1234')
    expect(text).toContain('gateway: serving=true')
    expect(text).toContain('RuntimeError: boom')
  })
})

describe('status bar uses the server menu as its connection indicator', () => {
  it('renders ServerMenu for both the desktop strip and the phone portal', () => {
    expect(statusBarSrc).toContain("import { ServerMenu } from './ServerMenu'")
    expect((statusBarSrc.match(/<ServerMenu/g) || []).length).toBe(2)
    // The old static dot/label block is gone.
    expect(statusBarSrc).not.toContain("title={status === 'connected' ? `Remedy ${version || ''}`.trim() : 'Server offline'}")
  })
})
