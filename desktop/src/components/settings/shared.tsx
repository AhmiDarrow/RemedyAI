/** Shared Settings UI primitives and constants. */

export const PERSONAS = [
  { id: 'balanced', name: 'Balanced', description: 'Helpful and adaptable to the task' },
  { id: 'efficient', name: 'Efficient', description: 'Concise, code-first, minimal explanation' },
  { id: 'detailed', name: 'Detailed', description: 'Thorough explanations with context' },
  { id: 'playful', name: 'Playful', description: 'Casual tone with light humor' },
] as const

export async function pickProjectFolder(): Promise<string | null> {
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    const path = await invoke<string | null>('pick_folder')
    return path && path.trim() ? path.trim() : null
  } catch {
    return null
  }
}

export function Field({
  label,
  value,
  onChange,
  placeholder,
  password,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  password?: boolean
}) {
  return (
    <div className="mb-2">
      <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>{label}</label>
      <input
        type={password ? 'password' : 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded px-2 py-1 text-xs outline-none"
        style={{
          background: 'var(--bg-tertiary)',
          color: 'var(--text-primary)',
          border: '1px solid var(--border)',
        }}
        onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
        onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
      />
    </div>
  )
}
