/** Cohesive form controls for Settings — pairs with ui-* / seg-btn design tokens. */

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type SelectHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'

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

export type FormSelectOption = { value: string; label: string; disabled?: boolean }

/**
 * Settings select that opens a fixed portal menu so options are never clipped
 * by the rail panel's overflow (native &lt;select&gt; lists go off-window on WebView2).
 *
 * Prefer the `options` prop (reliable). Children `&lt;option&gt;` still work as a
 * fallback via tree walk, but React 19 / production builds can hide props.
 */
export function FormSelect({
  value,
  onChange,
  children,
  options: optionsProp,
  className = 'mb-2',
  disabled,
  title,
  id,
  size = 'md',
}: {
  value: string
  onChange: (v: string) => void
  children?: ReactNode
  /** Preferred — explicit list so portal menu always has real values. */
  options?: FormSelectOption[]
  className?: string
  disabled?: boolean
  title?: string
  id?: string
  size?: 'md' | 'sm'
}) {
  return (
    <PortalSelect
      id={id}
      value={value}
      disabled={disabled}
      title={title}
      onChange={onChange}
      size={size}
      className={className}
      optionsProp={optionsProp}
    >
      {children}
    </PortalSelect>
  )
}

type Opt = FormSelectOption

function childText(node: unknown): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(childText).join('')
  if (typeof node === 'object' && node !== null && 'props' in node) {
    const p = (node as { props?: { children?: unknown } }).props
    return childText(p?.children)
  }
  return ''
}

function collectOptions(node: ReactNode, out: Opt[] = []): Opt[] {
  if (node == null || typeof node === 'boolean') return out
  if (Array.isArray(node)) {
    for (const n of node) collectOptions(n, out)
    return out
  }
  if (typeof node === 'object' && node !== null && 'props' in node) {
    const el = node as {
      type?: unknown
      props?: Record<string, unknown> & { value?: unknown; children?: unknown }
    }
    const t = el.type
    const typeName =
      typeof t === 'string'
        ? t
        : typeof t === 'function'
          ? (t as { name?: string; displayName?: string }).displayName
            || (t as { name?: string }).name
            || ''
          : ''
    const isOption =
      t === 'option'
      || (typeof t === 'string' && t.toLowerCase() === 'option')
      || typeName === 'option'
    if (isOption && el.props) {
      // React may put value on props.value; also accept defaultValue
      const rawVal =
        el.props.value !== undefined && el.props.value !== null
          ? el.props.value
          : el.props.defaultValue
      const val = rawVal != null ? String(rawVal) : ''
      const label = childText(el.props.children).trim() || val
      out.push({
        value: val,
        label,
        disabled: Boolean(el.props.disabled),
      })
      return out
    }
    if (el.props?.children != null) collectOptions(el.props.children as ReactNode, out)
  }
  return out
}

function pathsEqual(a: string, b: string): boolean {
  if (a === b) return true
  const na = a.replace(/\//g, '\\').toLowerCase()
  const nb = b.replace(/\//g, '\\').toLowerCase()
  return na === nb
}

function PortalSelect({
  value,
  onChange,
  children,
  optionsProp,
  className = '',
  disabled,
  title,
  id,
  size = 'md',
}: {
  value: string
  onChange: (v: string) => void
  children?: ReactNode
  optionsProp?: Opt[]
  className?: string
  disabled?: boolean
  title?: string
  id?: string
  size?: 'md' | 'sm'
}) {
  const [open, setOpen] = useState(false)
  const btnRef = useRef<HTMLButtonElement | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const [pos, setPos] = useState<{
    top: number
    left: number
    width: number
    maxH: number
    openUp: boolean
  } | null>(null)

  const fromChildren = collectOptions(children)
  const options =
    optionsProp && optionsProp.length > 0 ? optionsProp : fromChildren
  const selected =
    options.find((o) => pathsEqual(o.value, value))
    || (value
      ? { value, label: value.replace(/^.*[\\/]/, '') || value }
      : options.find((o) => o.value === '') || options[0])
  const label = selected?.label || value || '—'

  const place = () => {
    const el = btnRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const spaceBelow = window.innerHeight - r.bottom - 8
    const spaceAbove = r.top - 8
    const itemH = size === 'sm' ? 26 : 28
    const needed = Math.min(320, Math.max(120, options.length * itemH + 16))
    const inStatusBar = Boolean(el.closest('[data-remedy-status-bar]'))
    const inBottomBand = r.bottom > window.innerHeight * 0.58
    // Status-bar / fullscreen: native <select> lists fall under the OS taskbar.
    // Always flip up when the trigger sits in the lower band or will not fit.
    const openUp =
      inStatusBar
      || inBottomBand
      || (spaceBelow < needed && spaceAbove >= spaceBelow)
    const maxH = Math.max(120, Math.min(320, openUp ? spaceAbove : spaceBelow))
    const width = Math.max(r.width, 160)
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8))
    setPos({
      top: openUp ? r.top : r.bottom + 4,
      left,
      width,
      maxH,
      openUp,
    })
  }

  useLayoutEffect(() => {
    if (!open) return
    place()
    const onWin = () => place()
    window.addEventListener('resize', onWin)
    window.addEventListener('scroll', onWin, true)
    return () => {
      window.removeEventListener('resize', onWin)
      window.removeEventListener('scroll', onWin, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (btnRef.current?.contains(t) || menuRef.current?.contains(t)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  const menu =
    open && pos
      ? createPortal(
          <div
            ref={menuRef}
            role="listbox"
            className="settings-portal-select-menu"
            style={{
              position: 'fixed',
              zIndex: 800,
              left: pos.left,
              width: pos.width,
              maxHeight: pos.maxH,
              ...(pos.openUp
                ? { bottom: window.innerHeight - pos.top + 4, top: 'auto' }
                : { top: pos.top }),
              overflowY: 'auto',
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border)',
              borderRadius: 8,
              boxShadow: '0 12px 36px rgba(0,0,0,0.35)',
              padding: '4px 0',
            }}
          >
            {options.length === 0 ? (
              <div className="px-3 py-2 text-[11px]" style={{ color: 'var(--text-muted)' }}>
                No options
              </div>
            ) : (
              options.map((o, idx) => {
                const on = pathsEqual(o.value, value)
                return (
                  <button
                    key={`${idx}:${o.value}`}
                    type="button"
                    role="option"
                    aria-selected={on}
                    disabled={o.disabled}
                    data-value={o.value}
                    className="w-full text-left px-3 py-1.5 text-[11px] truncate"
                    style={{
                      background: on
                        ? 'color-mix(in srgb, var(--accent) 18%, transparent)'
                        : 'transparent',
                      color: o.disabled ? 'var(--text-muted)' : 'var(--text-primary)',
                      border: 'none',
                      cursor: o.disabled ? 'not-allowed' : 'pointer',
                      fontWeight: on ? 600 : 500,
                    }}
                    onMouseEnter={(e) => {
                      if (!on && !o.disabled) {
                        e.currentTarget.style.background =
                          'color-mix(in srgb, var(--bg-tertiary) 90%, transparent)'
                      }
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.background = on
                        ? 'color-mix(in srgb, var(--accent) 18%, transparent)'
                        : 'transparent'
                    }}
                    onClick={() => {
                      if (o.disabled) return
                      // Always pass explicit value from the option object
                      onChange(o.value)
                      setOpen(false)
                    }}
                    title={o.label}
                  >
                    {o.label}
                  </button>
                )
              })
            )}
          </div>,
          document.body,
        )
      : null

  return (
    <div
      className={`relative ${
        /\bw-|\bmax-w-|\bmin-w-/.test(className) ? '' : 'w-full'
      } ${className}`.trim()}
    >
      <button
        ref={btnRef}
        id={id}
        type="button"
        disabled={disabled}
        title={title}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`ui-select w-full text-left flex items-center gap-1 ${
          size === 'sm' ? 'ui-select-sm' : ''
        }`}
        style={{ cursor: disabled ? 'not-allowed' : 'pointer' }}
        onClick={() => {
          if (disabled) return
          setOpen((o) => !o)
        }}
      >
        <span className="truncate flex-1 min-w-0">{label}</span>
        <span className="shrink-0 opacity-60 text-[10px]" aria-hidden>
          ▾
        </span>
      </button>
      {/* Keep a hidden native select for form semantics / tests */}
      <select
        value={value}
        disabled={disabled}
        tabIndex={-1}
        aria-hidden
        className="sr-only"
        style={{ position: 'absolute', width: 1, height: 1, opacity: 0, pointerEvents: 'none' }}
        onChange={(e) => onChange(e.target.value)}
      >
        {children}
      </select>
      {menu}
    </div>
  )
}

/** Native select escape hatch when portal is not wanted. */
export function FormSelectNative({
  value,
  onChange,
  children,
  className = 'mb-2',
  disabled,
  title,
  id,
  size = 'md',
  ...rest
}: {
  value: string
  onChange: (v: string) => void
  children: ReactNode
  className?: string
  disabled?: boolean
  title?: string
  id?: string
  size?: 'md' | 'sm'
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, 'value' | 'onChange' | 'size'>) {
  return (
    <select
      id={id}
      value={value}
      disabled={disabled}
      title={title}
      onChange={(e) => onChange(e.target.value)}
      className={`ui-select w-full ${size === 'sm' ? 'ui-select-sm' : ''} ${className}`.trim()}
      {...rest}
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
      className={`flex gap-2.5 mb-2 cursor-pointer select-none ${
        description ? 'items-start' : 'items-center'
      }`}
      style={{ opacity: disabled ? 0.55 : 1 }}
    >
      <input
        type="checkbox"
        className={`settings-switch${description ? ' mt-0.5' : ''}`}
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        aria-checked={checked}
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

export function FormDownloadProgress({
  label,
  percent,
  className = '',
}: {
  label: string
  percent?: number | null
  className?: string
}) {
  const known = typeof percent === 'number' && Number.isFinite(percent) && percent >= 3
  const pct = known ? Math.max(0, Math.min(100, Math.round(percent as number))) : null
  return (
    <div
      className={`mb-2 min-w-[12rem] ${className}`.trim()}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct ?? undefined}
    >
      <div className="remedy-shell-progress-track" style={{ height: '0.45rem' }}>
        <div
          className={`remedy-shell-progress-fill${known ? '' : ' is-unknown'}`}
          style={{ width: known ? `${pct}%` : '32%' }}
        />
      </div>
      <FormHint>
        {label}
        {pct != null ? ` · ${pct}%` : ''}
      </FormHint>
    </div>
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
    <div className={`form-seg-track mb-2 ${className}`.trim()} role="group">
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
