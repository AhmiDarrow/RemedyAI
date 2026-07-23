import { useEffect, useRef } from 'react'

export interface ShortcutDef {
  key: string
  ctrl?: boolean
  shift?: boolean
  alt?: boolean
  /** When true, fire even if focus is in an input (e.g. Escape, F1). */
  allowInInput?: boolean
  handler: () => void
}

export function useKeyboardShortcuts(shortcuts: ShortcutDef[]) {
  const ref = useRef(shortcuts)
  ref.current = shortcuts

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName || ''
      const isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)

      for (const s of ref.current) {
        const ctrl = s.ctrl ?? true
        const shift = s.shift ?? false
        const alt = s.alt ?? false
        const keyMatch =
          e.key === s.key || e.key.toLowerCase() === s.key.toLowerCase()
        if (
          keyMatch &&
          e.ctrlKey === ctrl &&
          e.shiftKey === shift &&
          e.altKey === alt &&
          !e.metaKey
        ) {
          if (isInput && !s.allowInInput && s.key !== 'Escape') continue
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
