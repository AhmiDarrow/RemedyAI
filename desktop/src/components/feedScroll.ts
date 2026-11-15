import { createContext, useCallback, useContext } from 'react'

/**
 * Lets deep children (Working strip, thinking toggles) expand or collapse
 * without yanking the feed: the feed's scroll controller keeps the toggled
 * element at the same viewport offset. Without a provider the toggle just runs.
 */
export type AnchorToggle = (anchor: HTMLElement | null, mutate: () => void) => void

export const FeedScrollContext = createContext<AnchorToggle | null>(null)

export function useAnchorToggle(): AnchorToggle {
  const ctx = useContext(FeedScrollContext)
  return useCallback<AnchorToggle>(
    (anchor, mutate) => (ctx ? ctx(anchor, mutate) : mutate()),
    [ctx],
  )
}
