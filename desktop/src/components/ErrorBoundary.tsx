import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = {
  error: Error | null
  componentStack: string
  copied: boolean
}

/** Catch render crashes so the shell is not a silent black screen. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, componentStack: '', copied: false }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error, copied: false }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    const stack = info.componentStack || ''
    console.error('Remedy UI crash:', error, stack)
    this.setState({ componentStack: stack })
  }

  private reset = (): void => {
    this.setState({ error: null, componentStack: '', copied: false })
  }

  private reload = (): void => {
    window.location.reload()
  }

  private copyError = async (): Promise<void> => {
    const { error, componentStack } = this.state
    const msg = error?.message || String(error || '')
    const stack = error?.stack || ''
    const blob = [msg, stack, componentStack].filter(Boolean).join('\n\n')
    try {
      await navigator.clipboard.writeText(blob)
      this.setState({ copied: true })
      window.setTimeout(() => this.setState({ copied: false }), 2000)
    } catch {
      // Clipboard may be denied in some webviews — ignore.
    }
  }

  render() {
    if (this.state.error) {
      const msg = this.state.error.message || String(this.state.error)
      const stack = this.state.componentStack.trim()
      const detail = [msg, this.state.error.stack, stack].filter(Boolean).join('\n\n')
      return (
        <div
          role="alert"
          className="error-boundary-fallback"
          style={{
            height: '100%',
            width: '100%',
            boxSizing: 'border-box',
            padding: 24,
            background: 'var(--bg-primary, #0a0e0b)',
            color: 'var(--text-primary, #e6ebe7)',
            fontFamily: 'Inter, system-ui, sans-serif',
            overflow: 'auto',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
          }}
        >
          <div
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 12,
              padding: '4px 10px',
              borderRadius: 999,
              background: 'color-mix(in srgb, var(--error, #b87a7a) 16%, transparent)',
              color: 'var(--error, #b87a7a)',
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.04em',
              textTransform: 'uppercase',
            }}
          >
            UI crash
          </div>
          <h1 style={{ fontSize: 18, margin: '0 0 12px', fontWeight: 700 }}>
            Remedy hit a render error
          </h1>
          <p
            style={{
              opacity: 0.9,
              marginBottom: 16,
              lineHeight: 1.5,
              maxWidth: 560,
              color: 'var(--text-secondary, #9aa89e)',
              fontSize: 14,
            }}
          >
            Try <strong style={{ color: 'var(--text-primary, #e6ebe7)' }}>Continue</strong> to
            re-mount the UI without a full reload, or{' '}
            <strong style={{ color: 'var(--text-primary, #e6ebe7)' }}>Reload</strong> if the shell
            stays broken.
          </p>
          <pre
            style={{
              background: 'var(--bg-secondary, #121816)',
              border: '1px solid var(--border, #2a3530)',
              borderRadius: 10,
              padding: 14,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 12,
              maxHeight: '42vh',
              overflow: 'auto',
              width: '100%',
              maxWidth: 720,
              margin: 0,
              color: 'var(--text-secondary, #9aa89e)',
              boxSizing: 'border-box',
            }}
          >
            {detail.slice(0, 6000)}
          </pre>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 18 }}>
            <button
              type="button"
              className="ui-btn ui-btn-primary"
              onClick={this.reset}
              style={{
                padding: '8px 16px',
                borderRadius: 8,
                border: 'none',
                background: 'var(--accent, #4d7a5a)',
                color: '#fff',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Continue
            </button>
            <button
              type="button"
              className="ui-btn ui-btn-secondary"
              onClick={this.reload}
              style={{
                padding: '8px 16px',
                borderRadius: 8,
                border: '1px solid var(--border, #2a3530)',
                background: 'var(--bg-secondary, transparent)',
                color: 'var(--text-primary, #e6ebe7)',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Reload
            </button>
            <button
              type="button"
              className="ui-btn ui-btn-secondary"
              onClick={() => void this.copyError()}
              style={{
                padding: '8px 16px',
                borderRadius: 8,
                border: '1px solid var(--border, #2a3530)',
                background: 'transparent',
                color: 'var(--text-muted, #a8b5ad)',
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              {this.state.copied ? 'Copied ✓' : 'Copy error'}
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
