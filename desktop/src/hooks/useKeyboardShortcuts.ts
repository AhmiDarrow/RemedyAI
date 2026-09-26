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
      if (e.defaultPrevented || e.isComposing) return
      // Modals own navigation and dismissal. In particular Shift+Tab must move
      // focus backwards instead of toggling the hidden chat's work mode.
      const inDialog = document.querySelector('[role="dialog"][aria-modal="true"]')
      const fontShortcut = e.ctrlKey && ['=', '+', '-', '0'].includes(e.key)
      if (inDialog && !fontShortcut) return
      const tag = (e.target as HTMLElement)?.tagName || ''
      const isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || Boolean((e.target as HTMLElement)?.isContentEditable)

      for (const s of ref.current) {
        // Default ctrl=false so F1/Escape work; callers must set ctrl:true explicitly.
        const ctrl = s.ctrl ?? false
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
