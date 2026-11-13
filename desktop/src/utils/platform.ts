/** Desktop OS hints from the Tauri webview user-agent. */

export function isLinuxDesktop(): boolean {
  if (typeof navigator === 'undefined') return false
  const ua = navigator.userAgent || ''
  return /Linux|X11|Wayland/i.test(ua) && !/Android/i.test(ua)
}

export function isMacDesktop(): boolean {
  if (typeof navigator === 'undefined') return false
  const ua = navigator.userAgent || ''
  return /Macintosh|Mac OS X|Mac OS X;/i.test(ua) && !/iPhone|iPad|iPod/i.test(ua)
}
