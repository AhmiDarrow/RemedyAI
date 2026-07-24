import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  HELP_ARTICLES,
  articlesByCategory,
  getArticle,
  getDefaultArticleId,
  resolveWikiHref,
  searchArticles,
  type HelpArticle,
} from '../help/catalog'
import { openExternalUrl } from '../api/auth'

export interface HelpPanelProps {
  open: boolean
  onClose: () => void
  /** Article id to open (e.g. "09-troubleshooting"). */
  initialArticleId?: string | null
}

/**
 * Full-screen wiki-style Help: searchable TOC + offline owner's manual.
 */
export function HelpPanel({ open, onClose, initialArticleId }: HelpPanelProps) {
  const [query, setQuery] = useState('')
  const [articleId, setArticleId] = useState(getDefaultArticleId())
  const [history, setHistory] = useState<string[]>([getDefaultArticleId()])
  const searchRef = useRef<HTMLInputElement>(null)
  const articleTopRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const id = getArticle(initialArticleId)?.id || getDefaultArticleId()
    setArticleId(id)
    setHistory([id])
    setQuery('')
    const t = window.setTimeout(() => searchRef.current?.focus(), 50)
    return () => window.clearTimeout(t)
  }, [open, initialArticleId])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open, onClose])

  const navigate = useCallback((id: string, push = true) => {
    const art = getArticle(id)
    if (!art) return
    setArticleId(art.id)
    if (push) {
      setHistory((h) => (h[h.length - 1] === art.id ? h : [...h, art.id]))
    }
    articleTopRef.current?.scrollTo?.({ top: 0 })
  }, [])

  const goBack = useCallback(() => {
    setHistory((h) => {
      if (h.length <= 1) return h
      const next = h.slice(0, -1)
      const id = next[next.length - 1]!
      setArticleId(id)
      return next
    })
  }, [])

  const filtered = useMemo(() => searchArticles(query), [query])
  const grouped = useMemo(() => articlesByCategory(), [])
  const article: HelpArticle | undefined = getArticle(articleId) || HELP_ARTICLES[0]

  const showSearchResults = query.trim().length > 0

  if (!open) return null

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Help — Owner's Manual"
      className="fixed inset-0 z-[200] flex items-stretch justify-center p-3 sm:p-6"
      style={{ background: 'rgba(0,0,0,0.55)' }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className="flex w-full max-w-5xl overflow-hidden rounded-xl shadow-2xl"
        style={{
          background: 'var(--bg-primary)',
          border: '1px solid var(--border)',
          maxHeight: 'min(920px, calc(100vh - 2rem))',
          minHeight: 'min(640px, calc(100vh - 2rem))',
        }}
      >
        {/* Sidebar */}
        <aside
          className="flex w-[240px] shrink-0 flex-col border-r"
          style={{
            background: 'var(--bg-secondary)',
            borderColor: 'var(--border)',
          }}
        >
          <div className="border-b px-3 py-2.5" style={{ borderColor: 'var(--border)' }}>
            <div className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>
              Owner&apos;s Manual
            </div>
            <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
              Offline wiki · {HELP_ARTICLES.length} chapters
            </div>
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search help…"
              className="mt-2 w-full rounded px-2 py-1.5 text-xs outline-none"
              style={{
                background: 'var(--bg-primary)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
              aria-label="Search help articles"
            />
          </div>

          <nav className="flex-1 overflow-y-auto px-1.5 py-2 text-xs">
            {showSearchResults ? (
              <div className="space-y-0.5">
                <div className="px-2 py-1 text-[10px] uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                  {filtered.length} result{filtered.length === 1 ? '' : 's'}
                </div>
                {filtered.map((a) => (
                  <TocButton
                    key={a.id}
                    active={a.id === articleId}
                    title={a.title}
                    subtitle={a.summary}
                    onClick={() => navigate(a.id)}
                  />
                ))}
                {filtered.length === 0 && (
                  <div className="px-2 py-3" style={{ color: 'var(--text-muted)' }}>
                    No matches. Try “oauth”, “compact”, or “uninstall”.
                  </div>
                )}
              </div>
            ) : (
              grouped.map((g) => (
                <div key={g.category} className="mb-2">
                  <div
                    className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    {g.category}
                  </div>
                  <div className="space-y-0.5">
                    {g.articles.map((a) => (
                      <TocButton
                        key={a.id}
                        active={a.id === articleId}
                        title={a.title}
                        onClick={() => navigate(a.id)}
                      />
                    ))}
                  </div>
                </div>
              ))
            )}
          </nav>

          <div
            className="border-t px-2 py-2 flex gap-1"
            style={{ borderColor: 'var(--border)' }}
          >
            <button
              type="button"
              className="flex-1 rounded px-2 py-1.5 text-[11px]"
              style={{
                background: 'var(--bg-tertiary)',
                color: history.length > 1 ? 'var(--text-primary)' : 'var(--text-muted)',
                border: '1px solid var(--border)',
                cursor: history.length > 1 ? 'pointer' : 'default',
              }}
              disabled={history.length <= 1}
              onClick={goBack}
            >
              ← Back
            </button>
            <button
              type="button"
              className="flex-1 rounded px-2 py-1.5 text-[11px]"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
              }}
              onClick={() => navigate(getDefaultArticleId())}
            >
              Home
            </button>
          </div>
        </aside>

        {/* Article */}
        <section className="flex min-w-0 flex-1 flex-col">
          <header
            className="flex items-start justify-between gap-3 border-b px-4 py-2.5"
            style={{ borderColor: 'var(--border)', background: 'var(--bg-secondary)' }}
          >
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                {article?.category}
              </div>
              <h1 className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
                {article?.title || 'Help'}
              </h1>
              {article?.summary && (
                <p className="text-[11px] mt-0.5 line-clamp-2" style={{ color: 'var(--text-muted)' }}>
                  {article.summary}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={onClose}
              className="shrink-0 rounded px-2 py-1 text-sm"
              style={{ color: 'var(--text-muted)', border: '1px solid var(--border)' }}
              aria-label="Close help"
              title="Close (Esc)"
            >
              ×
            </button>
          </header>

          <div
            ref={articleTopRef}
            className="help-article flex-1 overflow-y-auto px-5 py-4 text-sm leading-relaxed"
            style={{ color: 'var(--text-primary)' }}
          >
            {article && (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ href, children }) => {
                    const wikiId = href ? resolveWikiHref(href) : null
                    if (wikiId) {
                      return (
                        <button
                          type="button"
                          className="underline font-medium"
                          style={{ color: 'var(--accent)' }}
                          onClick={() => navigate(wikiId)}
                        >
                          {children}
                        </button>
                      )
                    }
                    return (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline"
                        style={{ color: 'var(--accent)' }}
                      >
                        {children}
                      </a>
                    )
                  },
                  h1: ({ children }) => (
                    <h1 className="text-xl font-bold mb-3 mt-1" style={{ color: 'var(--text-primary)' }}>
                      {children}
                    </h1>
                  ),
                  h2: ({ children }) => (
                    <h2
                      className="text-base font-semibold mt-5 mb-2 pb-1 border-b"
                      style={{ color: 'var(--text-primary)', borderColor: 'var(--border)' }}
                    >
                      {children}
                    </h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className="text-sm font-semibold mt-4 mb-1.5" style={{ color: 'var(--text-primary)' }}>
                      {children}
                    </h3>
                  ),
                  p: ({ children }) => (
                    <p className="mb-3" style={{ color: 'var(--text-secondary)' }}>
                      {children}
                    </p>
                  ),
                  ul: ({ children }) => (
                    <ul className="mb-3 list-disc pl-5 space-y-1" style={{ color: 'var(--text-secondary)' }}>
                      {children}
                    </ul>
                  ),
                  ol: ({ children }) => (
                    <ol className="mb-3 list-decimal pl-5 space-y-1" style={{ color: 'var(--text-secondary)' }}>
                      {children}
                    </ol>
                  ),
                  li: ({ children }) => <li className="leading-snug">{children}</li>,
                  table: ({ children }) => (
                    <div className="mb-4 overflow-x-auto rounded-md" style={{ border: '1px solid var(--border)' }}>
                      <table className="w-full text-xs border-collapse">{children}</table>
                    </div>
                  ),
                  thead: ({ children }) => (
                    <thead style={{ background: 'var(--bg-tertiary)' }}>{children}</thead>
                  ),
                  th: ({ children }) => (
                    <th
                      className="text-left px-2.5 py-1.5 font-semibold"
                      style={{
                        color: 'var(--text-primary)',
                        borderBottom: '1px solid var(--border)',
                      }}
                    >
                      {children}
                    </th>
                  ),
                  td: ({ children }) => (
                    <td
                      className="px-2.5 py-1.5 align-top"
                      style={{
                        color: 'var(--text-secondary)',
                        borderBottom: '1px solid var(--border)',
                      }}
                    >
                      {children}
                    </td>
                  ),
                  code: ({ className, children }) => {
                    const isBlock = Boolean(className?.includes('language-') || String(children).includes('\n'))
                    if (isBlock) {
                      return (
                        <code
                          className="block text-[11px] font-mono whitespace-pre overflow-x-auto rounded-md p-3 mb-3"
                          style={{
                            background: 'var(--bg-tertiary)',
                            color: 'var(--text-primary)',
                            border: '1px solid var(--border)',
                          }}
                        >
                          {children}
                        </code>
                      )
                    }
                    return (
                      <code
                        className="px-1 py-0.5 rounded text-[11px] font-mono"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--accent)',
                        }}
                      >
                        {children}
                      </code>
                    )
                  },
                  pre: ({ children }) => <div className="mb-3">{children}</div>,
                  blockquote: ({ children }) => (
                    <blockquote
                      className="mb-3 pl-3 py-1 text-xs"
                      style={{
                        borderLeft: '3px solid var(--accent)',
                        color: 'var(--text-muted)',
                        background: 'var(--bg-secondary)',
                      }}
                    >
                      {children}
                    </blockquote>
                  ),
                  hr: () => (
                    <hr className="my-4" style={{ borderColor: 'var(--border)' }} />
                  ),
                  strong: ({ children }) => (
                    <strong style={{ color: 'var(--text-primary)' }}>{children}</strong>
                  ),
                }}
              >
                {article.body}
              </ReactMarkdown>
            )}
          </div>

          <footer
            className="flex flex-wrap items-center justify-between gap-2 border-t px-4 py-2 text-[10px]"
            style={{ borderColor: 'var(--border)', color: 'var(--text-muted)', background: 'var(--bg-secondary)' }}
          >
            <span>
              Tip: wiki links navigate in-app · external links open in browser · Esc closes
            </span>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="underline"
                style={{ color: 'var(--accent)' }}
                onClick={() => navigate('11-reference-commands')}
              >
                Commands
              </button>
              <button
                type="button"
                className="underline"
                style={{ color: 'var(--accent)' }}
                onClick={() => navigate('12-reference-shortcuts')}
              >
                Shortcuts
              </button>
              <button
                type="button"
                className="underline"
                style={{ color: 'var(--accent)' }}
                onClick={() => navigate('09-troubleshooting')}
              >
                Fix a problem
              </button>
              <button
                type="button"
                className="underline"
                style={{ color: 'var(--accent)' }}
                onClick={() =>
                  void openExternalUrl('https://github.com/AhmiDarrow/RemedyAI/issues/new')
                }
              >
                Report an issue
              </button>
            </div>
          </footer>
        </section>
      </div>
    </div>
  )
}

function TocButton({
  active,
  title,
  subtitle,
  onClick,
}: {
  active: boolean
  title: string
  subtitle?: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full text-left rounded px-2 py-1.5 transition-colors"
      style={{
        background: active ? 'var(--accent-subtle, rgba(108,140,255,0.15))' : 'transparent',
        color: active ? 'var(--accent)' : 'var(--text-secondary)',
        border: active ? '1px solid var(--accent)' : '1px solid transparent',
      }}
    >
      <div className="font-medium leading-snug">{title}</div>
      {subtitle && (
        <div className="text-[10px] mt-0.5 line-clamp-2" style={{ color: 'var(--text-muted)' }}>
          {subtitle}
        </div>
      )}
    </button>
  )
}
