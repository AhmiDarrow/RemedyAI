import { useState } from 'react'
import { RemedyLogo } from './RemedyLogo'
import { useI18n } from '../i18n'

interface UserNamePromptProps {
  open: boolean
  initial?: string
  onSave: (name: string) => void
  onSkip?: () => void
}

/** First-run / missing name: ask what Remedy should call the user. */
export function UserNamePrompt({ open, initial = '', onSave, onSkip }: UserNamePromptProps) {
  const { t } = useI18n()
  const [name, setName] = useState(initial)
  if (!open) return null

  const submit = () => {
    const n = name.trim()
    if (!n) return
    onSave(n)
  }

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center p-4 ui-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="user-name-title"
    >
      <div className="ui-surface w-full max-w-sm p-5" style={{ color: 'var(--text-primary)' }}>
        <div className="flex items-center gap-3 mb-4">
          <RemedyLogo size={36} framed />
          <div className="min-w-0">
            <div id="user-name-title" className="font-semibold text-sm tracking-tight">
              {t('userName.title')}
            </div>
            <div className="text-[11px] mt-0.5 leading-snug" style={{ color: 'var(--text-muted)' }}>
              {t('userName.hint')}
            </div>
          </div>
        </div>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit()
          }}
          placeholder={t('userName.placeholder')}
          className="ui-input mb-4 text-sm"
          style={{ padding: '0.55rem 0.75rem', fontSize: '0.875rem' }}
        />
        <div className="flex gap-2 justify-end">
          {onSkip && (
            <button type="button" className="ui-btn ui-btn-secondary" onClick={onSkip}>
              {t('userName.later')}
            </button>
          )}
          <button
            type="button"
            className="ui-btn ui-btn-primary"
            disabled={!name.trim()}
            onClick={submit}
          >
            {t('userName.save')}
          </button>
        </div>
      </div>
    </div>
  )
}
