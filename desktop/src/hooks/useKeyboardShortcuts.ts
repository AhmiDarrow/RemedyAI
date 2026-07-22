import { useEffect, useRef } from 'react'

export interface ShortcutDef {
  key: string
  ctrl?: boolean
  shift?: boolean
  handler: () => void
}

export function useKeyboardShortcuts(shortcuts: ShortcutDef[]) {
  const ref = useRef(shortcuts)
  ref.current = shortcuts

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(
        (e.target as HTMLElement)?.tagName || '',
      )

      for (const s of ref.current) {
        const ctrl = s.ctrl ?? true
        const shift = s.shift ?? false
        if (e.key.toLowerCase() === s.key.toLowerCase() && e.ctrlKey === ctrl && e.shiftKey === shift && !e.metaKey && !e.altKey) {
          if (isInput && s.key !== 'Escape') continue
          e.preventDefault()
          s.handler()
          return
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])
}
