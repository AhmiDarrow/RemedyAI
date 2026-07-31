/** Tiny spinner / “live” chip for a session with a running turn. */

export function SessionBusyBadge({ title = 'Turn in progress' }: { title?: string }) {
  return (
    <span
      className="session-busy-dot inline-block w-1.5 h-1.5 rounded-full shrink-0"
      style={{ background: 'var(--accent)' }}
      title={title}
      aria-label={title}
    />
  )
}
