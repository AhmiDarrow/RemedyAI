import { useState, useCallback, useEffect, useRef } from 'react'
import {
  checkDesktopUpdate,
  checkUpdates,
  type DesktopUpdateInfo,
  type UpdateInfo,
} from '../api/updates'
import { isTauri } from '../api/tauri'

const CHECK_INTERVAL = 30 * 60 * 1000 // 30 minutes

export function useUpdateChecker() {
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null)
  const [desktopInfo, setDesktopInfo] = useState<DesktopUpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const check = useCallback(async () => {
    setChecking(true)
    try {
      // Desktop shell: prefer Rust GitHub release check (installer URL).
      if (isTauri()) {
        const desk = await checkDesktopUpdate()
        if (desk) {
          setDesktopInfo(desk)
          setUpdateInfo({
            current_version: desk.current_version,
            latest_python: null,
            latest_desktop: desk.latest_version,
            release_url: desk.download_url,
            installer_url: desk.download_url,
            update_available: desk.update_available,
            error: desk.error,
          })
          return
        }
      }

      const info = await checkUpdates()
      setUpdateInfo(info)
      if (info?.update_available && info.installer_url) {
        setDesktopInfo({
          current_version: info.current_version,
          latest_version: info.latest_desktop || info.latest_python || info.current_version,
          update_available: true,
          download_url: info.installer_url,
          release_notes: null,
          error: info.error,
        })
      }
    } catch {
      // server not ready
    } finally {
      setChecking(false)
    }
  }, [])

  useEffect(() => {
    check()
    intervalRef.current = setInterval(check, CHECK_INTERVAL)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [check])

  return {
    updateInfo,
    desktopInfo,
    checking,
    check,
    updateAvailable: Boolean(
      desktopInfo?.update_available || updateInfo?.update_available,
    ),
  }
}
