import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { applyStoredUiPrefs } from './utils/chatPrefs'
import { isTauri, tauriInvoke } from './api/tauri'
import './index.css'

applyStoredUiPrefs()

async function boot(): Promise<void> {
  if (isTauri()) {
    try {
      const origin = await tauriInvoke<string>('get_api_origin')
      if (origin && typeof window !== 'undefined') {
        window.__REMEDY_API_ORIGIN__ = origin
      }
    } catch {
      /* default 7400 in client.ts */
    }
  }
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </StrictMode>,
  )
}

void boot()
