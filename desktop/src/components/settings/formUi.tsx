/** Polished form controls for Settings FormSections. */

import type { ReactNode } from 'react'

export function FormHint({ children }: { children: ReactNode }) {
  return (
    <div className="text-[10px] mb-2 leading-snug" style={{ color: 'var(--text-muted)' }}>
      {children}
    </div>
  )
}

export function FormLabel({
  children,
  htmlFor,
  className = '',
}: {
  children: ReactNode
  htmlFor?: string
  className?: string
}) {
  return (
    <label
      htmlFor={htmlFor}
      className={`block mb-1 text-[0.68rem] font-semibold uppercase tracking-wide ${className}`.trim()}
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </label>
  )
}

export function FormInput({
  value,
  onChange,
  type = 'text',
  placeholder,
  className = '',
  disabled,
  title,
  id,
  spellCheck,
  mono,
}: {
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
  className?: string
  disabled?: boolean
  title?: string
  id?: string
  spellCheck?: boolean
  mono?: boolean
}) {
  return (
    <input
      id={id}
      type={type}
      value={value}
      disabled={disabled}
      title={title}
      placeholder={placeholder}
      spellCheck={spellCheck}
      onChange={(e) => onChange(e.target.value)}
      className={`ui-input ${mono ? 'font-mono' : ''} ${className}`.trim()}
    />
  )
}

export function FormSelect({
  value,
  onChange,
  children,
  className = 'mb-2',
  disabled,
  title,
  id,
}: {
  value: string
  onChange: (v: string) => void
  children: ReactNode
  className?: string
  disabled?: boolean
  title?: string
  id?: string
}) {
  return (
    <select
      id={id}
      value={value}
      disabled={disabled}
      title={title}
      onChange={(e) => onChange(e.target.value)}
      className={`ui-select w-full ${className}`.trim()}
    >
      {children}
    </select>
  )
}

export function FormToggle({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  description?: string
  disabled?: boolean
}) {
  return (
    <label
      className="flex items-start gap-2 mb-2 cursor-pointer select-none"
      style={{ opacity: disabled ? 0.55 : 1 }}
    >
      <input
        type="checkbox"
        className="mt-0.5"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        style={{ accentColor: 'var(--accent)' }}
      />
      <span className="min-w-0">
        <span className="block text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
          {label}
        </span>
        {description ? (
          <span className="block text-[10px] mt-0.5 leading-snug" style={{ color: 'var(--text-muted)' }}>
            {description}
          </span>
        ) : null}
      </span>
    </label>
  )
}

export function FormNotice({
  children,
  tone = 'muted',
}: {
  children: ReactNode
  tone?: 'muted' | 'accent' | 'warn' | 'error'
}) {
  const border =
    tone === 'accent'
      ? 'color-mix(in srgb, var(--accent) 35%, var(--border))'
      : tone === 'warn'
        ? 'color-mix(in srgb, var(--warning) 40%, var(--border))'
        : tone === 'error'
          ? 'color-mix(in srgb, var(--error) 40%, var(--border))'
          : 'color-mix(in srgb, var(--border) 90%, transparent)'
  const bg =
    tone === 'accent'
      ? 'color-mix(in srgb, var(--accent) 8%, transparent)'
      : tone === 'warn'
        ? 'color-mix(in srgb, var(--warning) 12%, var(--bg-tertiary))'
        : tone === 'error'
          ? 'color-mix(in srgb, var(--error) 10%, transparent)'
          : 'color-mix(in srgb, var(--bg-tertiary) 50%, transparent)'
  const color =
    tone === 'warn'
      ? 'var(--warning)'
      : tone === 'error'
        ? 'var(--error)'
        : 'var(--text-muted)'
  return (
    <div
      className="text-[10px] rounded-lg px-2.5 py-1.5 mb-2 leading-snug"
      style={{ color, border: `1px solid ${border}`, background: bg }}
    >
      {children}
    </div>
  )
}

export function FormLinkButton({
  children,
  onClick,
  accent,
}: {
  children: ReactNode
  onClick: () => void
  accent?: boolean
}) {
  return (
    <button
      type="button"
      className="mb-2 text-[10px] underline block bg-transparent border-0 p-0 cursor-pointer"
      style={{ color: accent ? 'var(--accent)' : 'var(--text-muted)' }}
      onClick={onClick}
    >
      {children}
    </button>
  )
}

export function FormActionButton({
  children,
  onClick,
  disabled,
  variant = 'secondary',
  className = '',
}: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  className?: string
}) {
  const v =
    variant === 'primary'
      ? 'ui-btn-primary'
      : variant === 'danger'
        ? 'ui-btn-danger'
        : variant === 'ghost'
          ? 'ui-btn-ghost'
          : 'ui-btn-secondary'
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`ui-btn ${v} ${className}`.trim()}
      style={{ fontSize: '0.7rem', padding: '0.35rem 0.65rem' }}
    >
      {children}
    </button>
  )
}

export function FormSegmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { id: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div className="flex flex-wrap gap-1 mb-2">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          className={`seg-btn${value === o.id ? ' is-active' : ''}`}
          aria-pressed={value === o.id}
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function FormRow({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-1.5 mb-2">{children}</div>
}
