import { useCallback, useEffect, useRef } from 'react'

export function useNotifications() {
  const enabled = useRef(typeof Notification !== 'undefined')

  const request = useCallback(() => {
    if (enabled.current && Notification.permission === 'default') {
      Notification.requestPermission()
    }
  }, [])

  const notify = useCallback((title: string, options?: { body?: string; silent?: boolean }) => {
    if (enabled.current && Notification.permission === 'granted') {
      try {
        new Notification(title, { icon: '/icon.png', ...options })
      } catch {
        // ignore
      }
    }
  }, [])

  useEffect(() => {
    request()
  }, [request])

  return { notify }
}
