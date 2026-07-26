import { useState } from 'react'
import { isTauri, tauriInvoke } from '../../api/tauri'

/**
 * Terminal slide MVP: themed console surface + open system terminal.
 * Full in-app PTY can follow; avoids heavy native deps for this ship.
 */
export function TerminalSlide({ projectPath }: { projectPath?: string | null }) {
  const [log, setLog] = useState(
    'Remedy Terminal (MVP)\nOpen an external terminal in the project folder, or use bash tools in chat.\n',
  )

  const openExternal = async () => {
    const cwd = projectPath || undefined
    setLog((l) => l + `\n> open terminal${cwd ? ` @ ${cwd}` : ''}…\n`)
    try {
      if (isTauri()) {
        // Prefer shell open of cmd/powershell in cwd if available later; fallback message
        await tauriInvoke('plugin:shell|open', {
          path: 'cmd.exe',
        }).catch(async () => {
          // Best-effort: tell user to use chat bash
          setLog((l) => l + 'Could not spawn host shell. Use chat bash_exec or install a PTY build.\n')
        })
      } else {
        setLog((l) => l + 'External terminal is desktop-only.\n')
      }
    } catch (e: unknown) {
      setLog((l) => l + `Error: ${e instanceof Error ? e.message : String(e)}\n`)
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 text-xs font-mono">
      <div
        className="px-2 py-1.5 border-b flex gap-1 shrink-0"
        style={{ borderColor: 'var(--border)' }}
      >
        <button
          type="button"
          className="px-2 py-0.5 rounded text-[11px] font-sans"
          style={{ background: 'var(--accent)', color: '#fff' }}
          onClick={() => void openExternal()}
        >
          Open system terminal
        </button>
      </div>
      <pre
        className="flex-1 min-h-0 overflow-auto p-2 m-0 whitespace-pre-wrap"
        style={{
          background: 'var(--bg-primary)',
          color: 'var(--text-secondary)',
          fontSize: 11,
          lineHeight: 1.45,
        }}
      >
        {log}
      </pre>
    </div>
  )
}
