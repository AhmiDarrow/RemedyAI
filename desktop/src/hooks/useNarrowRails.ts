import { useCallback, useEffect, useRef, useState } from 'react'
import type { RailMode, WorkspaceLayout } from '../workspace/layoutPrefs'

/** A narrow viewport has one temporary panel; desktop preferences stay intact. */
export function useNarrowRails(layout: WorkspaceLayout) {
  const [narrow, setNarrow] = useState(() => window.matchMedia('(max-width: 760px)').matches)
  const [side, setSide] = useState<'left' | 'right' | null>(null)
  const previous = useRef(layout)
  useEffect(() => {
    const media = window.matchMedia('(max-width: 760px)')
    const changed = () => { setNarrow(media.matches); setSide(null) }
    media.addEventListener('change', changed)
    return () => media.removeEventListener('change', changed)
  }, [])
  useEffect(() => {
    const old = previous.current
    previous.current = layout
    if (!narrow) return
    if (layout.rightRail === 'open' && (old.rightRail !== 'open' || old.right !== layout.right)) setSide('right')
    else if (layout.leftRail === 'open' && (old.leftRail !== 'open' || old.left !== layout.left)) setSide('left')
  }, [layout, narrow])
  const open = useCallback((next: 'left' | 'right' | null) => setSide(next), [])
  const mode = (which: 'left' | 'right'): RailMode => narrow
    ? side === which ? 'open' : 'icons'
    : which === 'left' ? layout.leftRail : layout.rightRail
  return { narrow, open, leftRail: mode('left'), rightRail: mode('right') }
}
