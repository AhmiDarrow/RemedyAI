import { useState, useCallback, useEffect, useRef } from 'react'
import {
  checkDesktopUpdate,
  checkUpdates,
  type DesktopUpdateInfo,
  type UpdateInfo,
} from '../api/updates'
import { isTauri } from '../api/tauri'

const CHECK_INTERVAL = 30 * 60 * 1000 // 30 minutes

function deskToUpdateInfo(desk: DesktopUpdateInfo): UpdateInfo {
  return {
    current_version: desk.current_version,
    latest_python: null,
    latest_desktop: desk.latest_version,
    release_url: desk.download_url,
    installer_url: desk.download_url,
    update_available: desk.update_available,
    error: desk.error,
  }
}

export function useUpdateChecker() {
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null)
  const [desktopInfo, setDesktopInfo] = useState<DesktopUpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)
  const [lastCheckedAt, setLastCheckedAt] = useState<number | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const inFlightRef = useRef(false)

  const check = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    setChecking(true)
    try {
      // Desktop shell: prefer Rust GitHub release check (installer URL).
      if (isTauri()) {
        const desk = await checkDesktopUpdate()
        setDesktopInfo(desk)
        setUpdateInfo(deskToUpdateInfo(desk))
        // If Rust path failed, fall back to the Python sidecar API.
        if (desk.error) {
          try {
            const info = await checkUpdates()
            setUpdateInfo((prev) => ({
              ...info,
              // Prefer desktop installer URL when the API has one.
              error: info.error || desk.error,
              current_version:
                desk.current_version !== 'unknown'
                  ? desk.current_version
                  : info.current_version,
            }))
            if (info.update_available && info.installer_url) {
              setDesktopInfo({
                current_version: info.current_version,
                latest_version:
                  info.latest_desktop || info.latest_python || info.current_version,
                update_available: true,
                download_url: info.installer_url,
                release_notes: null,
                error: info.error,
              })
            }
          } catch (apiErr) {
            // Keep the Rust error already set on updateInfo.
            const msg =
              apiErr instanceof Error ? apiErr.message : String(apiErr)
            setUpdateInfo((prev) =>
              prev
                ? {
                    ...prev,
                    error: prev.error
                      ? `${prev.error} · API fallback: ${msg}`
                      : msg,
                  }
                : {
                    current_version: desk.current_version,
                    latest_python: null,
                    latest_desktop: null,
                    release_url: null,
                    installer_url: null,
                    update_available: false,
                    error: msg,
                  },
            )
          }
        }
        setLastCheckedAt(Date.now())
        return
      }

      const info = await checkUpdates()
      setUpdateInfo(info)
      if (info?.update_available && info.installer_url) {
        setDesktopInfo({
          current_version: info.current_version,
          latest_version:
            info.latest_desktop || info.latest_python || info.current_version,
          update_available: true,
          download_url: info.installer_url,
          release_notes: null,
          error: info.error,
        })
      } else {
        setDesktopInfo(null)
      }
      setLastCheckedAt(Date.now())
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setUpdateInfo({
        current_version: 'unknown',
        latest_python: null,
        latest_desktop: null,
        release_url: null,
        installer_url: null,
        update_available: false,
        error: `Update check failed: ${msg}`,
      })
      setLastCheckedAt(Date.now())
    } finally {
      inFlightRef.current = false
      setChecking(false)
    }
  }, [])

  useEffect(() => {
    void check()
    intervalRef.current = setInterval(() => {
      void check()
    }, CHECK_INTERVAL)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [check])

  return {
    updateInfo,
    desktopInfo,
    checking,
    lastCheckedAt,
    check,
    updateAvailable: Boolean(
      desktopInfo?.update_available || updateInfo?.update_available,
    ),
  }
}
