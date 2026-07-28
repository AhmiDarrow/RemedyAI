/** React hook: sidebar project + session ↑/↓ order. */

import { useCallback, useState } from 'react'
import {
  getProjectOrder,
  moveProject as moveProjectRaw,
} from './projectOrder'
import {
  getSessionOrderMap,
  moveSession as moveSessionRaw,
  rehomeSessionOrder,
} from './sessionOrder'

export function useSidebarOrder() {
  const [projectOrder, setProjectOrderState] = useState<string[]>(() => getProjectOrder())
  const [sessionOrderMap, setSessionOrderMap] = useState<Record<string, string[]>>(
    () => getSessionOrderMap(),
  )
  const [tick, setTick] = useState(0)

  const refresh = useCallback(() => {
    setProjectOrderState(getProjectOrder())
    setSessionOrderMap(getSessionOrderMap())
    setTick((t) => t + 1)
  }, [])

  const moveProject = useCallback(
    (key: string, dir: 'up' | 'down', activeKeys: string[]) => {
      const next = moveProjectRaw(key, dir, activeKeys)
      setProjectOrderState(next)
      setTick((t) => t + 1)
      return next
    },
    [],
  )

  const moveSession = useCallback(
    (
      sessionId: string,
      projectPath: string | null | undefined,
      dir: 'up' | 'down',
      currentIds: string[],
    ) => {
      const next = moveSessionRaw(sessionId, projectPath, dir, currentIds)
      setSessionOrderMap(getSessionOrderMap())
      setTick((t) => t + 1)
      return next
    },
    [],
  )

  const onSessionRehomed = useCallback(
    (
      sessionId: string,
      fromProject: string | null | undefined,
      toProject: string | null | undefined,
    ) => {
      rehomeSessionOrder(sessionId, fromProject, toProject)
      setSessionOrderMap(getSessionOrderMap())
      setTick((t) => t + 1)
    },
    [],
  )

  return {
    projectOrder,
    sessionOrderMap,
    tick,
    refresh,
    moveProject,
    moveSession,
    onSessionRehomed,
  }
}
