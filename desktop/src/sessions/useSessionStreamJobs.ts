/** React subscription to the global stream job registry. */

import { useCallback, useEffect, useState } from 'react'
import {
  countRunningJobs,
  getBusySessionIds,
  listStreamJobs,
  subscribeStreamJobs,
  type StreamJob,
} from './streamJobs'

export function useSessionStreamJobs() {
  const [jobs, setJobs] = useState<StreamJob[]>(() => listStreamJobs())
  const [busyIds, setBusyIds] = useState<Set<string>>(() => getBusySessionIds())
  const [runningCount, setRunningCount] = useState(() => countRunningJobs())

  const refresh = useCallback(() => {
    setJobs(listStreamJobs())
    setBusyIds(getBusySessionIds())
    setRunningCount(countRunningJobs())
  }, [])

  useEffect(() => {
    return subscribeStreamJobs(() => {
      refresh()
    })
  }, [refresh])

  return {
    jobs,
    busyIds,
    runningCount,
    refresh,
  }
}
