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
      className="fixed inset-0 z-[100] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.45)' }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="concurrent-turn-title"
    >
      <div
        className="max-w-md w-full rounded-xl border p-4 shadow-xl space-y-3"
        style={{
          background: 'var(--bg-secondary)',
          borderColor: 'var(--border)',
          color: 'var(--text-primary)',
        }}
      >
        <h2 id="concurrent-turn-title" className="text-sm font-semibold">
          Multiple live turns
        </h2>
        <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          {concurrentTurnConfirmMessage(runningCount)}
        </p>
        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            className="px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
            }}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            className="px-3 py-1.5 rounded-lg text-xs font-semibold"
            style={{ background: 'var(--accent)', color: '#fff', border: 'none' }}
            onClick={onContinue}
          >
            Continue
          </button>
        </div>
      </div>
    </div>
  )
}
