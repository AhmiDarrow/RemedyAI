/**
 * Browser rail vs status-bar menus.
 *
 * The embedded browser is a native WebView2 child HWND in Tauri: it paints
 * above every DOM node, so CSS z-index cannot put a status-bar select or the
 * Theme menu over it. Those menus always flip *up* out of the status bar and
 * land on one corner of the Browser host — the old corner-sampling probe
 * needed a majority of covered points and so left the embed on top of them.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { BROWSER_STACK_OVERLAY_SELECTOR, overlayCoversHost } from './browserStack'

const here = dirname(fileURLToPath(import.meta.url))
const slideSrc = readFileSync(
  join(here, '..', 'components', 'slides', 'BrowserSlide.tsx'),
  'utf8',
)
const stackSrc = readFileSync(join(here, 'browserStack.ts'), 'utf8')
const formUiSrc = readFileSync(
  join(here, '..', 'components', 'settings', 'formUi.tsx'),
  'utf8',
)

const host = { left: 600, top: 80, right: 1200, bottom: 700 }

describe('overlayCoversHost', () => {
  it('hides the embed when a status-bar menu opens up over one host corner', () => {
    // Menu anchored to a status-bar select at the bottom-right of the window,
    // opening upward: overlaps only the host's bottom-right corner.
    const menu = { left: 1000, top: 520, right: 1180, bottom: 700 }
    expect(overlayCoversHost(host, [menu])).toBe(true)
  })

  it('leaves the embed alone when the menu sits beside the host', () => {
    const menuLeftOfHost = { left: 100, top: 500, right: 400, bottom: 700 }
    const menuBelowHost = { left: 700, top: 700, right: 900, bottom: 760 }
    expect(overlayCoversHost(host, [menuLeftOfHost, menuBelowHost])).toBe(false)
  })

  it('ignores collapsed (zero-size) overlay rects', () => {
    expect(overlayCoversHost(host, [{ left: 700, top: 100, right: 700, bottom: 100 }])).toBe(
      false,
    )
  })
})

describe('overlay selector covers the status-bar menus', () => {
  it('matches the FormSelect portal and the Theme menu', () => {
    expect(BROWSER_STACK_OVERLAY_SELECTOR).toContain('.settings-portal-select-menu')
    expect(BROWSER_STACK_OVERLAY_SELECTOR).toContain('.remedy-theme-menu')
    expect(BROWSER_STACK_OVERLAY_SELECTOR).toContain('[role="listbox"]')
    // Classes must still exist on the real menus
    expect(formUiSrc).toContain('className="settings-portal-select-menu"')
  })

  it('status-bar selects still open upward (that is what lands on the host)', () => {
    const openUp = formUiSrc.indexOf('const openUp =')
    expect(openUp).toBeGreaterThan(-1)
    expect(formUiSrc.slice(openUp, openUp + 200)).toContain('inStatusBar')
  })

  it('probe checks overlay intersection before the majority corner sample', () => {
    const probe = stackSrc.indexOf('export function browserStackProbeHostCoverage')
    const overlay = stackSrc.indexOf('overlayCoversHost(r, overlayRectsOutside(host))', probe)
    const majority = stackSrc.indexOf("browserStackSet('host-covered', foreign >= 3)", probe)
    expect(overlay).toBeGreaterThan(probe)
    expect(majority).toBeGreaterThan(overlay)
  })
})

describe('BrowserSlide re-probes when a portal mounts', () => {
  it('observes <body> childList so the embed hides before the menu paints', () => {
    const mo = slideSrc.indexOf('new MutationObserver(() => browserStackProbeHostCoverage(el))')
    expect(mo).toBeGreaterThan(-1)
    expect(slideSrc.slice(mo, mo + 200)).toContain(
      "observe(document.body, { childList: true })",
    )
    expect(slideSrc).toContain('portalMo.disconnect()')
  })

  it('still clamps the native bounds above the status bar', () => {
    const q = slideSrc.indexOf("document.querySelector('[data-remedy-status-bar]')")
    expect(q).toBeGreaterThan(-1)
    expect(slideSrc.slice(q, q + 200)).toContain(
      'maxBottom = Math.min(maxBottom, status.getBoundingClientRect().top)',
    )
  })
})
