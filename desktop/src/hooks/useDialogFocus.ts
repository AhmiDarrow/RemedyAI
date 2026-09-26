import { useEffect, useRef, type RefObject } from 'react'

// Only the topmost dialog handles keyboard dismissal and focus wrapping.
const activeDialogs: { token: symbol; layer: number }[] = []
const focusableSelector = 'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'

export function useDialogFocus<T extends HTMLElement>(
  open: boolean,
  onClose: () => void,
  initialFocus?: RefObject<HTMLElement | null>,
) {
  const ref = useRef<T>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    if (!open || !ref.current) return
    const root = ref.current
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const token = Symbol('dialog')
    // Keep visual stacking in the same order as keyboard ownership. Some
    // dialogs put the backdrop outside their focus root; mark that wrapper.
    const layer = (activeDialogs.at(-1)?.layer ?? 590) + 10
    const overlay = root.closest<HTMLElement>('[data-dialog-layer]') ?? root
    const previousLayer = overlay.style.zIndex
    overlay.style.zIndex = String(layer)
    activeDialogs.push({ token, layer })
    const controls = () => Array.from(root.querySelectorAll<HTMLElement>(focusableSelector))
      .filter((el) => el.tabIndex >= 0 && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden')
    ;(initialFocus?.current ?? controls()[0] ?? root).focus()
    const onKey = (event: KeyboardEvent) => {
      if (activeDialogs.at(-1)?.token !== token || event.defaultPrevented) return
      if (event.key === 'Escape') {
        if (event.isComposing) return
        event.preventDefault()
        event.stopImmediatePropagation()
        closeRef.current()
      } else if (event.key === 'Tab') {
        const items = controls()
        const first = items[0]
        const last = items.at(-1)
        const focused = document.activeElement
        if (!first || !last) {
          event.preventDefault()
          root.focus()
        } else if (!root.contains(focused) || (event.shiftKey ? focused === first : focused === last)) {
          event.preventDefault()
          ;(event.shiftKey ? last : first).focus()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      overlay.style.zIndex = previousLayer
      const wasTop = activeDialogs.at(-1)?.token === token
      const index = activeDialogs.findIndex((dialog) => dialog.token === token)
      if (index >= 0) activeDialogs.splice(index, 1)
      if (wasTop && previous?.isConnected) previous.focus()
    }
  }, [open, initialFocus])

  return ref
}
