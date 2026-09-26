/** Ignore async results from a previous view or an older request in the same lane. */
export function createAsyncScope(initialKey: string | null) {
  let key = initialKey
  let epoch = 0
  const requests = new Map<string, number>()
  const invalidate = () => { epoch += 1; requests.clear() }
  return {
    setKey(next: string | null) {
      if (next === key) return
      key = next
      invalidate()
    },
    invalidate,
    capture() {
      const started = epoch
      return () => started === epoch
    },
    latest(lane = 'load') {
      const started = epoch
      const request = (requests.get(lane) ?? 0) + 1
      requests.set(lane, request)
      return () => started === epoch && requests.get(lane) === request
    },
  }
}
