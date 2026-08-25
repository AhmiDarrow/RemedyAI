/** React subscription to the TurnStore for one session. */

import { useEffect, useState } from 'react'
import { subscribeTurns, turnsForSession, type Turn } from './turns'

/** Live turns for `sessionId`; unsubscribes on unmount / session change. */
export function useSessionTurns(sessionId: string | null | undefined): Turn[] {
  const [turns, setTurns] = useState<Turn[]>(() =>
    sessionId ? turnsForSession(sessionId) : [],
  )

  useEffect(() => {
    if (!sessionId) {
      setTurns([])
      return
    }
    setTurns(turnsForSession(sessionId))
    return subscribeTurns(() => {
      setTurns(turnsForSession(sessionId))
    })
  }, [sessionId])

  return turns
}
