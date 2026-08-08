/** Shared Settings UI primitives and constants. */
/* oxlint-disable react/only-export-components -- shared constants + helpers for form sections */

import { FormInput, FormLabel } from './formUi'

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

/** Standard labeled text field — uses shared FormLabel + FormInput. */
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
    <div className="mb-2.5">
      <FormLabel>{label}</FormLabel>
      <FormInput
        type={password ? 'password' : 'text'}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
      />
    </div>
  )
}
