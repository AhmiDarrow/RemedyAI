import { useState, useCallback, useEffect, useRef } from 'react'
import { checkUpdates, type UpdateInfo } from '../api/updates'

const CHECK_INTERVAL = 30 * 60 * 1000 // 30 minutes

export function useUpdateChecker() {
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null)
  const [checking, setChecking] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const check = useCallback(async () => {
    setChecking(true)
    try {
      const info = await checkUpdates()
      setUpdateInfo(info)
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

  return { updateInfo, checking, check }
}
