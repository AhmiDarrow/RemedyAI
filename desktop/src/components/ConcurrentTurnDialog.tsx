/** Confirm starting another turn when several are already live. */

import { concurrentTurnConfirmMessage } from '../sessions/concurrentTurns'

interface ConcurrentTurnDialogProps {
  open: boolean
  runningCount: number
  onContinue: () => void
  onCancel: () => void
}

export function ConcurrentTurnDialog({
  open,
  runningCount,
  onContinue,
  onCancel,
}: ConcurrentTurnDialogProps) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4 ui-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="concurrent-turn-title"
      onClick={onCancel}
    >
      <div
        className="ui-surface max-w-md w-full p-4 space-y-3"
        style={{ color: 'var(--text-primary)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="concurrent-turn-title" className="text-sm font-semibold tracking-tight">
          Multiple live turns
        </h2>
        <p className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
          {concurrentTurnConfirmMessage(runningCount)}
        </p>
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className="ui-btn ui-btn-secondary" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="ui-btn ui-btn-primary" onClick={onContinue}>
            Continue
          </button>
        </div>
      </div>
    </div>
  )
}
