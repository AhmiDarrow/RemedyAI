/** Remedy's own confirm dialog — never the browser's "localhost:5173 says…". */

import { useEffect, useId, useRef } from 'react'
import { browserStackHold } from '../utils/browserStack'
import { useDialogFocus } from '../hooks/useDialogFocus'

export interface ConfirmRequest {
  /** Short title, e.g. "Delete this chat?" */
  title: string
  /** Body text — plain sentences; \n\n separates paragraphs. */
  body?: string
  /** Label for the confirming action (default "Delete"). */
  confirmLabel?: string
  /** Label for the dismissing action (default "Cancel"). */
  cancelLabel?: string
  /** Style the confirm button as destructive (default true for deletes). */
  danger?: boolean
}

interface ConfirmDialogProps extends ConfirmRequest {
  open: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  open,
  title,
  body = '',
  confirmLabel = 'Delete',
  cancelLabel = 'Cancel',
  danger = true,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement | null>(null)
  const dialogRef = useDialogFocus<HTMLDivElement>(open, onCancel, cancelRef)
  const titleId = useId()

  useEffect(() => {
    if (!open) return
    return browserStackHold('confirm-dialog')
  }, [open])

  if (!open) return null
  const paragraphs = String(body || '')
    .split('\n\n')
    .map((p) => p.trim())
    .filter(Boolean)

  return (
    <div
      className="fixed inset-0 z-[120] flex items-center justify-center p-4 ui-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      ref={dialogRef}
      tabIndex={-1}
      onClick={onCancel}
    >
      <div
        className="ui-surface max-w-md w-full p-4 space-y-3"
        style={{ color: 'var(--text-primary)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id={titleId} className="text-base font-semibold tracking-tight">
          {title}
        </h2>
        {paragraphs.map((p, i) => (
          <p
            key={i}
            className="text-xs leading-relaxed"
            style={{ color: 'var(--text-secondary)' }}
          >
            {p}
          </p>
        ))}
        <div className="flex justify-end gap-2 pt-1">
          <button ref={cancelRef} type="button" className="ui-btn ui-btn-secondary" onClick={onCancel}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className="ui-btn ui-btn-primary"
            style={
              danger
                ? { background: 'var(--error)', borderColor: 'var(--error)', color: 'var(--error-foreground)' }
                : undefined
            }
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
