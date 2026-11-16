/** About Remedy dialog — extracted from App.tsx. */

import { useEffect } from 'react'
import { AboutSourceLink, AhmiSignature } from '../aboutLine'
import { browserStackHold } from '../utils/browserStack'
import { useDialogFocus } from '../hooks/useDialogFocus'
import { useI18n } from '../i18n'

export function AboutDialog({
  open,
  onClose,
  version,
  userName,
  onOpenHelp,
  onOpenSettings,
  onOpenDiagnostics,
}: {
  open: boolean
  onClose: () => void
  version?: string
  userName?: string
  onOpenHelp: (articleId?: string) => void
  onOpenSettings: () => void
  onOpenDiagnostics?: () => void
}) {
  const { t, catalog } = useI18n()
  const dialogRef = useDialogFocus<HTMLDivElement>(open, onClose)
  const blurb = catalog['about.blurb'] || t('about.blurb', { home: '~/.remedy' })
  const blurbParts = blurb.split('{home}')
  useEffect(() => {
    if (!open) return
    return browserStackHold('about-dialog')
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center p-4 ui-overlay"
      role="dialog"
      ref={dialogRef}
      tabIndex={-1}
      aria-modal="true"
      aria-labelledby="about-title"
      onClick={onClose}
    >
      <div
        className="ui-surface w-full max-w-sm p-5"
        style={{ color: 'var(--text-primary)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 mb-3.5">
          <img
            src="/icon.png"
            alt=""
            draggable={false}
            style={{
              height: 44,
              width: 44,
              objectFit: 'contain',
              borderRadius: 10,
              flexShrink: 0,
              boxShadow: '0 2px 10px rgba(0,0,0,0.2)',
            }}
          />
          <img
            src="/logo.png"
            alt="Remedy"
            draggable={false}
            style={{
              height: 34,
              width: 'auto',
              maxWidth: 200,
              objectFit: 'contain',
              display: 'block',
            }}
          />
        </div>
        <div id="about-title" className="text-sm font-semibold mb-1 tracking-tight">
          {t('menu.about')}
        </div>
        <div className="text-xs mb-2.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          {blurbParts.length === 2 ? (
            <>
              {blurbParts[0]}
              <code
                className="px-1 py-0.5 rounded text-[0.65rem]"
                style={{
                  background: 'color-mix(in srgb, var(--accent) 10%, var(--bg-tertiary))',
                  color: 'var(--accent)',
                }}
              >
                ~/.remedy
              </code>
              {blurbParts[1]}
            </>
          ) : (
            blurb
          )}
        </div>
        <div
          className="text-xs mb-3 leading-relaxed rounded-xl px-3 py-2.5"
          style={{
            background: 'color-mix(in srgb, var(--accent) 8%, var(--bg-tertiary))',
            color: 'var(--text-secondary)',
            border: '1px solid color-mix(in srgb, var(--accent) 22%, var(--border))',
          }}
        >
          <div
            className="font-semibold mb-0.5 text-[0.7rem] uppercase tracking-wide"
            style={{ color: 'var(--accent)' }}
          >
            {t('about.fromCreator')}
          </div>
          {t('about.creator')}
          <AhmiSignature className="mt-1.5" />
        </div>
        <div className="text-xs space-y-1 mb-3" style={{ color: 'var(--text-secondary)' }}>
          <div>
            {t('about.version')}{' '}
            <span className="font-semibold" style={{ color: 'var(--accent)' }}>
              {version || '—'}
            </span>
            <span style={{ color: 'var(--text-muted)' }}> · {t('about.platform')}</span>
          </div>
          {userName ? <div>{t('about.signedIn', { userName })}</div> : null}
        </div>
        <AboutSourceLink className="text-xs mb-4" />
        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            className="ui-btn ui-btn-secondary"
            onClick={() => {
              onClose()
              onOpenHelp('13-whats-new')
            }}
          >
            {t('about.whatsNew')}
          </button>
          <button
            type="button"
            className="ui-btn ui-btn-secondary"
            onClick={() => {
              onClose()
              onOpenHelp('00-overview')
            }}
          >
            {t('menu.help')}
          </button>
          <button
            type="button"
            className="ui-btn ui-btn-secondary"
            onClick={() => {
              onClose()
              onOpenSettings()
            }}
          >
            {t('settings.title')}
          </button>
          {onOpenDiagnostics ? (
            <button
              type="button"
              className="ui-btn ui-btn-secondary"
              onClick={() => {
                onClose()
                onOpenDiagnostics()
              }}
            >
              {t('menu.diagnostics')}
            </button>
          ) : null}
          <button type="button" className="ui-btn ui-btn-primary" onClick={onClose}>
            {t('win.close')}
          </button>
        </div>
      </div>
    </div>
  )
}
