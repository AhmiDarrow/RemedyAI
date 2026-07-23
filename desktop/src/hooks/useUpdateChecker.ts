import { useState, useCallback, useEffect, useRef } from 'react'
import { checkUpdates, type UpdateInfo } from '../api/updates'

const CHECK_INTERVAL = 30 * 60 * 1000 // 30 minutes

/** True when Tauri updater pubkey is configured (non-empty). */
function updaterEnabled(): boolean {
  // Frontend cannot read tauri.conf at runtime; opt out via env for unsigned builds.
  // When REMEDY_UPDATER_DISABLED=1 or no desktop release channel, skip noisy checks.
  try {
    if (typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_DISABLE_UPDATER === '1') {
      return false
    }
  } catch {
    // ignore
  }
  return true
}

export function useUpdateChecker() {
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const enabled = updaterEnabled()

  const check = useCallback(async () => {
    if (!enabled) return
    setChecking(true)
    try {
      const info = await checkUpdates()
      // Ignore desktop update flags when the channel has no signature metadata —
      // unsigned auto-update cannot install securely.
      if (info && (info as any).signature_required === false) {
        setUpdateInfo({ ...info, update_available: info.update_available && !!(info as any).signed })
      } else {
        setUpdateInfo(info)
      }
    } catch {
      // server not ready
    } finally {
      setChecking(false)
    }
  }, [enabled])

  useEffect(() => {
    if (!enabled) return
    check()
    intervalRef.current = setInterval(check, CHECK_INTERVAL)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [check, enabled])

  return { updateInfo, checking, check, enabled }
}
