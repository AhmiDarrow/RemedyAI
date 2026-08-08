/**
 * Owner's manual catalog for the in-app Help wiki.
 * Bodies are bundled offline via Vite raw imports from ./articles.
 */

export interface HelpArticleMeta {
  id: string
  title: string
  category: string
  order: number
  tags: string[]
  summary: string
}

export interface HelpArticle extends HelpArticleMeta {
  body: string
}

/** Static metadata — keep in sync with docs/manual/*.md filenames. */
const META: HelpArticleMeta[] = [
  {
    id: '00-overview',
    title: 'Overview',
    category: 'Start here',
    order: 0,
    tags: ['intro', 'quick start', 'map'],
    summary: 'What Remedy is, architecture sketch, and manual map.',
  },
  {
    id: '16-continuity-philosophy',
    title: 'How Remedy works (continuity)',
    category: 'Start here',
    order: 0.5,
    tags: ['continuity', 'philosophy', 'memory', 'partner', 'vision'],
    summary:
      'Why Remedy feels like one partner on any model — silent continuity, not a bot farm.',
  },
  {
    id: '17-nanoswarm',
    title: 'Continuity workers (nano swarm)',
    category: 'Reference',
    order: 16.5,
    tags: ['nanoswarm', 'continuity', 'token', 'router', 'pattern', 'operator'],
    summary:
      'Operator guide: continuity workers, NanoToken BPE packs (v2 default), calibration — not chat agents.',
  },
  {
    id: '18-agency',
    title: 'Coding agency (Build-class power)',
    category: 'Build',
    order: 7.5,
    tags: ['agency', 'file_edit', 'repo_search', 'mission', 'work alone', 'tools'],
    summary:
      'file_edit, repo_search, missions, silent jobs — multi-hour coding with one partner.',
  },
  {
    id: '19-metabolism',
    title: 'Partner Metabolism (Advanced)',
    category: 'How it works',
    order: 7.6,
    tags: [
      'metabolism',
      'evidence',
      'tier',
      'governor',
      'time crystal',
      'shadow',
      'identity export',
    ],
    summary:
      '0.20.0+ silent partner OS: L0–L3 tiers, evidence ledger, shadow, IR, governor, portable identity.',
  },
  {
    id: 'computer-use-soak',
    title: 'Computer-use soak notes',
    category: 'Reference',
    order: 18,
    tags: ['computer-use', 'browser', 'rail', 'soak', 'navigate'],
    summary:
      'Maintainer QA checklist for computer-use (soak test). Not end-user how-to; agent can help_read it.',
  },
  {
    id: '01-install-windows',
    title: 'Install (Windows)',
    category: 'Start here',
    order: 1,
    tags: ['install', 'smartscreen', 'paths'],
    summary: 'Download, install paths, first launch, always-ready.',
  },
  {
    id: '02-first-run',
    title: 'First run & setup',
    category: 'Start here',
    order: 2,
    tags: ['wizard', 'setup', 'skip'],
    summary: 'Setup wizard steps, skip, Open setup, corrupt config.',
  },
  {
    id: '03-providers-and-auth',
    title: 'Providers & auth',
    category: 'Configuration',
    order: 3,
    tags: ['xai', 'oauth', 'api key', 'ollama', 'provider'],
    summary: 'Providers, keys, xAI OAuth, Ollama, local API token.',
  },
  {
    id: '04-security-and-data',
    title: 'Security & data',
    category: 'Configuration',
    order: 4,
    tags: ['security', 'privacy', 'scope', 'approvals', 'dpapi'],
    summary: 'Data map, what leaves the machine, scope, approvals.',
  },
  {
    id: '05-chat-and-sessions',
    title: 'Chat & sessions',
    category: 'Daily use',
    order: 5,
    tags: ['chat', 'plan', 'build', 'proc', 'sessions'],
    summary: 'UI map, Plan/Build, tool process, sessions export.',
  },
  {
    id: '06-memory-and-harness',
    title: 'Memory & harness',
    category: 'Daily use',
    order: 6,
    tags: ['memory', 'compact', 'goals', 'profile'],
    summary: 'Durable memory, Session Brief, harness modes.',
  },
  {
    id: '07-skills',
    title: 'Skills',
    category: 'Daily use',
    order: 7,
    tags: ['skills', 'quarantine', 'trust', 'lifecycle'],
    summary: 'Skills panel, lifecycle, import safety.',
  },
  {
    id: '08-updates-and-uninstall',
    title: 'Updates & uninstall',
    category: 'Maintenance',
    order: 8,
    tags: ['update', 'uninstall', 'wipe'],
    summary: 'Check/install updates, uninstall data options.',
  },
  {
    id: '09-troubleshooting',
    title: 'Troubleshooting',
    category: 'Maintenance',
    order: 9,
    tags: ['error', 'server', 'oauth', 'defender', 'fix'],
    summary: 'Server, setup save, OAuth, Defender, provider errors.',
  },
  {
    id: '10-cli-and-api',
    title: 'CLI & API',
    category: 'Reference',
    order: 10,
    tags: ['cli', 'api', 'sse', 'openapi'],
    summary: 'Power-user CLI and local HTTP API.',
  },
  {
    id: '11-reference-commands',
    title: 'Slash commands',
    category: 'Reference',
    order: 11,
    tags: ['slash', 'commands', 'help'],
    summary: 'Full slash command reference table.',
  },
  {
    id: '12-reference-shortcuts',
    title: 'Keyboard shortcuts',
    category: 'Reference',
    order: 12,
    tags: ['hotkeys', 'keyboard', 'f1'],
    summary: 'Composer and app-wide shortcuts.',
  },
  {
    id: '13-whats-new',
    title: "What's new",
    category: 'Reference',
    order: 13,
    tags: ['changelog', 'release', 'version'],
    summary: 'Recent product changes for owners.',
  },
  {
    id: '14-visual-decoder',
    title: 'Visual decoder',
    category: 'Configuration',
    order: 14,
    tags: ['vision', 'image', 'ocr', 'llama', 'qwen', 'screenshot'],
    summary: 'Local image→text for text-only models (SmolVLM2 2.2B).',
  },
  {
    id: '20-rmb-local-agent',
    title: 'RMB local agent',
    category: 'Configuration',
    order: 14.5,
    tags: ['rmb', 'local', 'llama', 'coding', 'tools', 'gguf', 'agent', 'offline'],
    summary: 'Built-in local agent host (llama.cpp) for coding and tool use.',
  },
  {
    id: '15-free-providers',
    title: 'Free providers & demo',
    category: 'Configuration',
    order: 15,
    tags: ['free', 'demo', 'gemini', 'groq', 'ollama', 'openrouter', 'no signup', 'rmb'],
    summary: 'Demo mode, free API keys, and local Ollama — use Remedy without paying.',
  },
]

const rawModules = import.meta.glob('./articles/*.md', {
  query: 'raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function bodyForId(id: string): string {
  const key = Object.keys(rawModules).find((k) => k.includes(`${id}.md`))
  if (key && typeof rawModules[key] === 'string') return rawModules[key]
  return `# ${id}\n\n_Article body missing from bundle. Reinstall or rebuild the desktop app._\n`
}

/** All articles with markdown bodies (eagerly bundled). */
export const HELP_ARTICLES: HelpArticle[] = META.map((m) => ({
  ...m,
  body: bodyForId(m.id),
}))

export function getArticle(id: string | null | undefined): HelpArticle | undefined {
  if (!id) return undefined
  const normalized = id.replace(/\.md$/i, '').replace(/^\//, '')
  return (
    HELP_ARTICLES.find((a) => a.id === normalized)
    || HELP_ARTICLES.find((a) => a.id.endsWith(normalized))
    || HELP_ARTICLES.find((a) => normalized.endsWith(a.id))
  )
}

export function getDefaultArticleId(): string {
  return '00-overview'
}

/** Resolve wiki hrefs like "02-first-run", "./09-troubleshooting", "article:04-security-and-data". */
export function resolveWikiHref(href: string): string | null {
  if (!href) return null
  let h = href.trim()
  if (h.startsWith('article:')) h = h.slice('article:'.length)
  if (h.startsWith('http://') || h.startsWith('https://') || h.startsWith('mailto:')) {
    return null // external — let browser handle
  }
  if (h.startsWith('#')) return null // in-page anchor only
  h = h.replace(/^\.\//, '').replace(/\.md$/i, '')
  // strip query/hash
  h = h.split('?')[0]?.split('#')[0] || h
  const art = getArticle(h)
  return art ? art.id : null
}

export function searchArticles(query: string): HelpArticle[] {
  const q = query.trim().toLowerCase()
  if (!q) return HELP_ARTICLES
  const terms = q.split(/\s+/).filter(Boolean)
  return HELP_ARTICLES
    .map((a) => {
      const hay = [
        a.title,
        a.summary,
        a.category,
        a.tags.join(' '),
        a.body.slice(0, 4000),
      ]
        .join('\n')
        .toLowerCase()
      let score = 0
      for (const t of terms) {
        if (a.title.toLowerCase().includes(t)) score += 8
        if (a.tags.some((tag) => tag.includes(t))) score += 5
        if (a.summary.toLowerCase().includes(t)) score += 3
        if (hay.includes(t)) score += 1
      }
      return { a, score }
    })
    .filter((x) => x.score > 0)
    .sort((x, y) => y.score - x.score || x.a.order - y.a.order)
    .map((x) => x.a)
}

export function articlesByCategory(): { category: string; articles: HelpArticle[] }[] {
  const map = new Map<string, HelpArticle[]>()
  for (const a of HELP_ARTICLES) {
    const list = map.get(a.category) || []
    list.push(a)
    map.set(a.category, list)
  }
  return [...map.entries()].map(([category, articles]) => ({
    category,
    articles: articles.sort((x, y) => x.order - y.order),
  }))
}
