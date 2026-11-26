import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

describe('tauri.conf.json', () => {
  it('does not put the Tauri API on window.__TAURI__ for every script in the webview', () => {
    const conf = JSON.parse(readFileSync(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'))
    // The app reaches Tauri through @tauri-apps/api (__TAURI_INTERNALS__);
    // the global only widens what injected or third-party script can call.
    expect(conf.app.withGlobalTauri).toBe(false)
  })
})
