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
          style={{
            height: '100%',
            width: '100%',
            boxSizing: 'border-box',
            padding: 24,
            background: 'var(--bg, #0a0e0b)',
            color: 'var(--text, #e6ebe7)',
            fontFamily: 'system-ui, sans-serif',
            overflow: 'auto',
          }}
        >
          <h1 style={{ fontSize: 18, margin: '0 0 12px' }}>Remedy UI crashed</h1>
          <p style={{ opacity: 0.85, marginBottom: 16, lineHeight: 1.45, maxWidth: 560 }}>
            React hit a render error. Try <strong>Continue</strong> to re-mount the UI
            without a full reload, or <strong>Reload</strong> if the shell stays broken.
          </p>
          <pre
            style={{
              background: 'var(--surface, #121816)',
              border: '1px solid var(--border, #2a3530)',
              borderRadius: 8,
              padding: 12,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 12,
              maxHeight: '40vh',
              overflow: 'auto',
            }}
          >
            {detail.slice(0, 6000)}
          </pre>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 16 }}>
            <button
              type="button"
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
              onClick={this.reload}
              style={{
                padding: '8px 16px',
                borderRadius: 8,
                border: '1px solid var(--border, #2a3530)',
                background: 'transparent',
                color: 'var(--text, #e6ebe7)',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Reload
            </button>
            <button
              type="button"
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
              {this.state.copied ? 'Copied' : 'Copy error'}
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
