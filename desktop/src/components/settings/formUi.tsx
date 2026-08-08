/** Cohesive form controls for Settings — pairs with ui-* / seg-btn design tokens. */

import type { CSSProperties, ReactNode } from 'react'

export function FormHint({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`text-[10px] mb-2 leading-snug ${className}`.trim()}
      style={{ color: 'var(--text-muted)' }}
    >
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
  size = 'md',
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
  size?: 'md' | 'sm'
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
      className={`ui-input ${size === 'sm' ? 'ui-input-sm' : ''} ${mono ? 'font-mono' : ''} ${className}`.trim()}
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
  size = 'md',
}: {
  value: string
  onChange: (v: string) => void
  children: ReactNode
  className?: string
  disabled?: boolean
  title?: string
  id?: string
  size?: 'md' | 'sm'
}) {
  return (
    <select
      id={id}
      value={value}
      disabled={disabled}
      title={title}
      onChange={(e) => onChange(e.target.value)}
      className={`ui-select w-full ${size === 'sm' ? 'ui-select-sm' : ''} ${className}`.trim()}
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
  label: ReactNode
  description?: ReactNode
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
  className = '',
}: {
  children: ReactNode
  tone?: 'muted' | 'accent' | 'warn' | 'error'
  className?: string
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
      className={`text-[10px] rounded-lg px-2.5 py-1.5 mb-2 leading-snug ${className}`.trim()}
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
  className = '',
}: {
  children: ReactNode
  onClick: () => void
  accent?: boolean
  className?: string
}) {
  return (
    <button
      type="button"
      className={`mb-2 text-[10px] underline block bg-transparent border-0 p-0 cursor-pointer ${className}`.trim()}
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
  title,
}: {
  children: ReactNode
  onClick?: () => void | Promise<void>
  disabled?: boolean
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  className?: string
  title?: string
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
      title={title}
      onClick={() => {
        if (!onClick) return
        void onClick()
      }}
      className={`ui-btn ui-btn-sm ${v} ${className}`.trim()}
    >
      {children}
    </button>
  )
}

export function FormSegmented<T extends string>({
  value,
  options,
  onChange,
  disabled,
  className = '',
}: {
  value: T
  options: { id: T; label: string; title?: string }[]
  onChange: (v: T) => void
  disabled?: boolean
  className?: string
}) {
  return (
    <div className={`flex flex-wrap gap-1 mb-2 ${className}`.trim()} role="group">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          disabled={disabled}
          title={o.title}
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

export function FormRow({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`flex flex-wrap items-center gap-1.5 mb-2 ${className}`.trim()}>{children}</div>
}

export function FormRange({
  value,
  onChange,
  min,
  max,
  step = 1,
  className = '',
  disabled,
  id,
}: {
  value: number
  onChange: (v: number) => void
  min: number
  max: number
  step?: number
  className?: string
  disabled?: boolean
  id?: string
}) {
  return (
    <input
      id={id}
      type="range"
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
      className={`form-range ${className}`.trim()}
    />
  )
}

/** Compact key/value status surface for RMB / vision panels. */
export function FormStatusCard({ children }: { children: ReactNode }) {
  return (
    <div
      className="rounded-md px-2 py-1.5 mb-2 text-[10px] space-y-0.5 ui-surface"
      style={
        {
          borderRadius: '0.5rem',
          boxShadow: 'none',
          background: 'var(--bg-tertiary)',
          color: 'var(--text-secondary)',
        } as CSSProperties
      }
    >
      {children}
    </div>
  )
}

export function FormStatusRow({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="flex justify-between gap-2">
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span className="text-right min-w-0 truncate max-w-[65%]">{children}</span>
    </div>
  )
}
