import { useEffect, useId, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useDialogFocus } from '../../hooks/useDialogFocus'
import { browserStackHold } from '../../utils/browserStack'

/** A focused settings task, with the same keyboard and native-browser behavior as app dialogs. */
export function SettingsDialog({
  open, onClose, title, description, busy = false, saveBehavior = 'immediate', children,
}: {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  busy?: boolean
  saveBehavior?: 'draft' | 'immediate'
  children: ReactNode
}) {
  const titleId = useId()
  const [visited, setVisited] = useState(false)
  const close = () => { if (!busy) onClose() }
  const ref = useDialogFocus<HTMLDivElement>(open, close)
  useEffect(() => {
    if (!open) return
    setVisited(true)
    return browserStackHold(`settings-flow-${titleId}`)
  }, [open, titleId])
  // Retain local form drafts between visits; never render unopened setup work.
  if (!open && !visited) return null
  return createPortal(
    <div className="settings-flow-backdrop ui-overlay" data-dialog-layer
      style={{ display: open ? 'flex' : 'none' }} onClick={close}>
      <div ref={ref} className="settings-flow ui-surface" role="dialog"
        aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
        onClick={event => event.stopPropagation()}>
        <header className="settings-flow-header">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <button type="button" className="ui-btn ui-btn-ghost" onClick={close}
            disabled={busy} aria-label={`Close ${title}`}>×</button>
        </header>
        <div className="settings-flow-body">{children}</div>
        <footer className="settings-flow-footer">
          <p>{saveBehavior === 'draft' ? 'Use Save in Settings to apply your changes.' : 'Changes apply as you make them.'}</p>
          <button type="button" className="ui-btn ui-btn-primary" onClick={close} disabled={busy}>Done</button>
        </footer>
      </div>
    </div>, document.body,
  )
}

export function SettingsFlow({ title, summary, children, busy, saveBehavior }: {
  title: string
  summary: string
  children: ReactNode
  busy?: boolean
  saveBehavior?: 'draft' | 'immediate'
}) {
  const [open, setOpen] = useState(false)
  return <>
    <button type="button" className="settings-flow-link" onClick={() => setOpen(true)} aria-haspopup="dialog">
      <span><strong>{title}</strong><span>{summary}</span></span><span aria-hidden>›</span>
    </button>
    <SettingsDialog open={open} onClose={() => setOpen(false)} title={title}
      busy={busy} saveBehavior={saveBehavior}>{children}</SettingsDialog>
  </>
}
