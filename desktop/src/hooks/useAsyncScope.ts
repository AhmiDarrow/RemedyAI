import { useEffect, useState } from 'react'
import { createAsyncScope } from '../utils/asyncScope'

/** Scope async reads and actions to the view that started them. */
export function useAsyncScope(key: string | null) {
  const [scope] = useState(() => createAsyncScope(key))
  scope.setKey(key)
  useEffect(() => () => scope.invalidate(), [key, scope])
  return scope
}
