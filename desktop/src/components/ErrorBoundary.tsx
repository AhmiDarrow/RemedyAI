import { Component, type ErrorInfo, type ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { error: Error | null }

/** Catch render crashes so the shell is not a silent black screen. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Remedy UI crash:', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      const msg = this.state.error.message || String(this.state.error)
      return (
        <div
          style={{
            height: '100%',
            width: '100%',
            boxSizing: 'border-box',
            padding: 24,
            background: '#0a0e0b',
            color: '#e6ebe7',
            fontFamily: 'system-ui, sans-serif',
            overflow: 'auto',
          }}
        >
          <h1 style={{ fontSize: 18, margin: '0 0 12px' }}>Remedy UI crashed</h1>
          <p style={{ opacity: 0.8, marginBottom: 16 }}>
            The window went blank because React hit an error. Copy the message below
            or click Reload.
          </p>
          <pre
            style={{
              background: '#121816',
              border: '1px solid #2a3530',
              borderRadius: 8,
              padding: 12,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: 12,
            }}
          >
            {msg}
          </pre>
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{
              marginTop: 16,
              padding: '8px 16px',
              borderRadius: 8,
              border: 'none',
              background: '#4d7a5a',
              color: '#fff',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            Reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
