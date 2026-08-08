/** About Remedy dialog — extracted from App.tsx. */

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
  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center p-4 ui-overlay"
      role="dialog"
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
          About Remedy
        </div>
        <div className="text-xs mb-2.5 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          Your personal AI partner on this PC — knowledge, design, code, computer use,
          and get-it-done. One continuous voice; continuity stays under{' '}
          <code
            className="px-1 py-0.5 rounded text-[0.65rem]"
            style={{
              background: 'color-mix(in srgb, var(--accent) 10%, var(--bg-tertiary))',
              color: 'var(--accent)',
            }}
          >
            ~/.remedy
          </code>
          . Not a medical product.
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
            From the creator
          </div>
          My name is Ahmi, I hope you enjoy my Remedy.
        </div>
        <div className="text-xs space-y-1 mb-4" style={{ color: 'var(--text-secondary)' }}>
          <div>
            Version{' '}
            <span className="font-semibold" style={{ color: 'var(--accent)' }}>
              {version || '—'}
            </span>
            <span style={{ color: 'var(--text-muted)' }}> · Windows desktop + local API</span>
          </div>
          {userName ? <div>Signed in as {userName}</div> : null}
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            className="ui-btn ui-btn-secondary"
            onClick={() => {
              onClose()
              onOpenHelp('13-whats-new')
            }}
          >
            What&apos;s new
          </button>
          <button
            type="button"
            className="ui-btn ui-btn-secondary"
            onClick={() => {
              onClose()
              onOpenHelp('00-overview')
            }}
          >
            Help
          </button>
          <button
            type="button"
            className="ui-btn ui-btn-secondary"
            onClick={() => {
              onClose()
              onOpenSettings()
            }}
          >
            Settings
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
              Diagnostics
            </button>
          ) : null}
          <button type="button" className="ui-btn ui-btn-primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
