/**
 * Dev-only visual harness for the current Studio chrome.
 *   npx vite --config harness/vite.config.ts --port 5201 --strictPort
 *   http://127.0.0.1:5201/harness/studio/?theme=forest&screen=chat
 *
 * screen=chat (default) — sessions sidebar, a finished turn, composer, status bar
 * screen=empty — selected empty session, no messages
 * screen=rails-left | rails-right | rails-dual — real WorkspaceSide chrome
 * screen=palette | help | about | diagnostics | menu — chrome overlays
 * screen=usage | usage-harness — Usage dashboard on a fixture, no runtime
 * screen=diagnostics — Diagnostics panel and hive inspector on a fixture, no runtime
 */
import { StrictMode, useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ChatSessionHeader } from '../../src/components/ChatSessionHeader'
import { Composer } from '../../src/components/Composer'
import { MessageFeed } from '../../src/components/MessageFeed'
import { Sidebar } from '../../src/components/Sidebar'
import { StatusBar } from '../../src/components/StatusBar'
import { AboutDialog } from '../../src/components/AboutDialog'
import { CommandPalette, type CommandItem } from '../../src/components/CommandPalette'
import { DiagnosticsPanel } from '../../src/components/DiagnosticsPanel'
import { HelpPanel } from '../../src/components/HelpPanel'
import { TitleBar } from '../../src/components/TitleBar'
import { UsageDashboard } from '../../src/components/UsageDashboard'
import { WorkspaceSide } from '../../src/components/slides/WorkspaceSide'
import { useNarrowRails } from '../../src/hooks/useNarrowRails'
import type { WorkspaceLayout } from '../../src/workspace/layoutPrefs'
import type { SlideId } from '../../src/workspace/types'
import { I18nProvider } from '../../src/i18n'
import { applyTheme, getResolvedTheme, type ThemeId, THEMES } from '../../src/themes'
import { applyFonts, DEFAULT_FONTS } from '../../src/utils/fontPrefs'
import type { ChatSession } from '../../src/types'
import { SCENARIO_END, stateAt } from '../live-turn/replay'
import '../../src/index.css'

const params = new URLSearchParams(location.search)
const themeParam = (params.get('theme') || 'forest') as ThemeId
const screen = params.get('screen') || 'chat'
const themeId: ThemeId = themeParam in THEMES || themeParam === 'system' ? themeParam : 'forest'

applyFonts(DEFAULT_FONTS)
applyTheme(getResolvedTheme(themeId))
if (screen === 'usage' || screen === 'usage-harness') installUsageFixture()
if (screen === 'diagnostics') installDiagnosticsFixture()
document.documentElement.setAttribute('data-density', 'cozy')
document.documentElement.classList.add('app-ready')
document.body.style.background = 'var(--bg-primary)'

const NOW = '2026-11-22T21:00:00.000Z'
const SESSIONS: ChatSession[] = [
  {
    id: 's1',
    title: 'Port to RemedyUI',
    model: 'remedy',
    agent: null,
    message_count: 6,
    created_at: '2026-11-21T18:12:00.000Z',
    updated_at: '2026-11-22T20:48:00.000Z',
  },
  {
    id: 's2',
    title: 'House rules',
    model: 'remedy',
    agent: null,
    message_count: 2,
    created_at: '2026-11-20T11:00:00.000Z',
    updated_at: '2026-11-22T09:15:00.000Z',
  },
  {
    id: 's3',
    title: 'New chat',
    model: null,
    agent: null,
    message_count: 0,
    created_at: NOW,
    updated_at: NOW,
  },
]

const RAIL_BLURB: Record<SlideId, string> = {
  sessions: 'Chats and projects for this studio.',
  settings: 'Provider, the local mind, messengers, appearance.',
  files: 'In-app file browser for the session or project.',
  terminal: 'In-app terminal on this PC.',
  browser: 'Research pane.',
  scratch: 'Session notepad.',
  automations: 'Background tasks that run once or repeat.',
}

function railPreset(which: string): WorkspaceLayout {
  const base: WorkspaceLayout = {
    left: 'sessions',
    right: 'settings',
    leftWidth: 280,
    rightWidth: 300,
    leftOpen: true,
    rightOpen: false,
    leftRail: 'open',
    rightRail: 'thin',
  }
  if (which === 'rails-right') {
    return { ...base, leftOpen: false, leftRail: 'thin', rightOpen: true, rightRail: 'open' }
  }
  if (which === 'rails-dual') {
    return { ...base, rightOpen: true, rightRail: 'open' }
  }
  return base
}

function RailsShell({
  which,
  theme,
  setTheme,
  resolved,
}: {
  which: string
  theme: ThemeId
  setTheme: (id: ThemeId) => void
  resolved: ReturnType<typeof getResolvedTheme>
}) {
  const [layout, setLayout] = useState<WorkspaceLayout>(() => railPreset(which))
  const [activeId, setActiveId] = useState('s3')
  const narrow = useNarrowRails(layout)
  const slide = (id: SlideId) => {
    if (id === 'sessions') {
      return (
        <Sidebar
          embedded
          sessions={SESSIONS}
          activeId={activeId}
          onSelect={setActiveId}
          onNew={() => setActiveId('s3')}
          onDelete={() => {}}
        />
      )
    }
    return (
      <p style={{ margin: 16, color: 'var(--text-secondary)', fontSize: 13, lineHeight: 1.45 }}>
        {RAIL_BLURB[id]}
      </p>
    )
  }
  const side = (whichSide: 'left' | 'right') => (
    <WorkspaceSide
      side={whichSide}
      active={whichSide === 'left' ? layout.left : layout.right}
      width={whichSide === 'left' ? layout.leftWidth : layout.rightWidth}
      railMode={whichSide === 'left' ? narrow.leftRail : narrow.rightRail}
      onSelect={(id) => {
        setLayout((prev) => ({ ...prev, [whichSide]: id }))
      }}
      onWidth={(w) => {
        setLayout((prev) => ({ ...prev, [whichSide === 'left' ? 'leftWidth' : 'rightWidth']: w }))
      }}
      onRailMode={(mode) => {
        if (narrow.narrow) {
          narrow.open(mode === 'open' ? whichSide : null)
          return
        }
        setLayout((prev) => ({
          ...prev,
          [whichSide === 'left' ? 'leftRail' : 'rightRail']: mode,
          [whichSide === 'left' ? 'leftOpen' : 'rightOpen']: mode === 'open',
        }))
      }}
      onSwap={() => {
        setLayout((prev) => ({
          ...prev,
          left: prev.right,
          right: prev.left,
          leftWidth: prev.rightWidth,
          rightWidth: prev.leftWidth,
          leftOpen: prev.rightOpen,
          rightOpen: prev.leftOpen,
          leftRail: prev.rightRail,
          rightRail: prev.leftRail,
        }))
      }}
    >
      {slide(whichSide === 'left' ? layout.left : layout.right)}
    </WorkspaceSide>
  )
  return (
    <div
      className="flex flex-col h-full min-h-0"
      style={{ background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
      data-harness-ready="1"
    >
      <div className="studio-shell flex flex-1 min-h-0 h-full items-stretch">
        {side('left')}
        <div className="chat-middle" style={{ minWidth: 240 }}>
          <ChatSessionHeader
            title="New chat"
            partnerName="Remedy"
            modelLabel={null}
            providerLabel="local"
            messageCount={0}
          />
          <div className="chat-middle-feed flex-1 min-h-0">
            <MessageFeed
              messages={[]}
              partialText=""
              textBreaks={[]}
              streaming={false}
              loading={false}
              processSteps={[]}
              activeTools={[]}
              taskProgress={null}
              toolProcessMode="off"
              userName="Ahmi"
              partnerName="Remedy"
              sessionId={activeId}
              stickNonce={0}
            />
          </div>
          <div className="chat-middle-composer flex flex-col">
            <Composer
              onSend={() => {}}
              onStop={() => {}}
              onCommand={() => {}}
              streaming={false}
              disabled={false}
              slashCommands={[{ name: '/help', description: 'List commands', aliases: [], arguments: null }]}
              sessionId={activeId}
              acceptNativeDrops={false}
            />
          </div>
        </div>
        {side('right')}
      </div>
      <StatusBar
        sessionId={activeId}
        streaming={false}
        model="remedy"
        models={[{ id: 'remedy', name: 'Remedy', provider: 'local', default: true }]}
        provider="local"
        connectedProviders={[]}
        thinkingLevel="medium"
        approvalMode="ask"
        themeId={theme}
        theme={resolved}
        onThemeChange={(id) => {
          setTheme(id)
          applyTheme(getResolvedTheme(id))
        }}
        planMode={false}
        onTogglePlanMode={() => {}}
        onTogglePanel={() => {
          setLayout((prev) => {
            if (prev.right === 'settings' && prev.rightRail === 'open') {
              return { ...prev, rightRail: 'thin', rightOpen: false }
            }
            return { ...prev, right: 'settings', rightRail: 'open', rightOpen: true }
          })
        }}
        updateAvailable={false}
        onCheckUpdates={() => {}}
        surface="studio"
      />
    </div>
  )
}

const PALETTE: CommandItem[] = [
  ['new', 'New Session', 'Start a new chat session', 'session'],
  ['time-travel', 'Time Travel', 'Timeline: restore chat & files to an earlier step', 'session'],
  ['export', 'Export Session', 'Download active session as .txt', 'session'],
  ['import', 'Import Session', 'Import a session from .txt / .md', 'session'],
  ['palette', 'Command palette', 'Open this palette', 'general'],
  ['plan', 'Cycle Chat / Plan / Build', 'Pin conversation, planning, or building', 'general'],
  ['memory', 'Memory', 'Toggle memory panel', 'panel'],
  ['skills', 'Skills', 'Toggle skills panel', 'panel'],
  ['settings', 'Settings', 'Open Settings', 'panel'],
  ['help', 'Help', 'Searchable offline wiki (F1 / Ctrl+/)', 'general'],
  ['diagnostics', 'Diagnostics', 'Server, local host, hardware, hive', 'panel'],
  ['help-troubleshoot', 'Troubleshooting', 'Open Help → Troubleshooting', 'general'],
  ['help-commands', 'Slash commands reference', 'Open Help → Commands', 'general'],
  ['session-s3', 'New chat', '0 messages', 'session'],
  ['session-s1', 'Port to RemedyUI', '2 messages', 'session'],
  ['session-s2', 'House rules', '2 messages', 'session'],
  ['agent-remedy', '@remedy', 'House partner', 'agent'],
  ['model-remedy', 'remedy', 'local', 'model'],
].map(([id, label, description, category]) => ({
  id, label, description, category, action: () => {},
}))

function ChromeLayer({ which }: { which: string }) {
  const noop = () => {}
  if (which === 'palette') return <CommandPalette open onClose={noop} commands={PALETTE} />
  if (which === 'help') return <HelpPanel open onClose={noop} initialArticleId="00-overview" version="1.0.0" />
  if (which === 'about') {
    return (
      <AboutDialog
        open
        onClose={noop}
        version="1.0.0"
        userName="Ahmi"
        onOpenHelp={noop}
        onOpenSettings={noop}
        onOpenDiagnostics={noop}
      />
    )
  }
  if (which === 'diagnostics') return <DiagnosticsPanel open onClose={noop} />
  return null
}

function MenuBar() {
  useEffect(() => {
    const id = window.setTimeout(() => {
      document.querySelector<HTMLButtonElement>('[aria-label="Open Remedy menu"]')?.click()
    }, 200)
    return () => window.clearTimeout(id)
  }, [])
  return <TitleBar version="1.0.0" />
}

/** Same figures as the RemedyUI usage golden. Answers `/api` without a runtime. */
function installUsageFixture() {
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  const summary = {
    range_days: 7,
    totals: {
      prompt_tokens: 5894,
      completion_tokens: 2526,
      total_tokens: 8420,
      estimated_cost_usd: 1.28,
      events: 37,
    },
    by_provider: [
      { provider: 'local', model: '', total_tokens: 5200, estimated_cost_usd: 1.28, events: 22, prompt_tokens: 3640, completion_tokens: 1560 },
      { provider: 'openai', model: '', total_tokens: 3220, estimated_cost_usd: 2.4, events: 15, prompt_tokens: 2254, completion_tokens: 966 },
    ],
    by_model: [
      { provider: 'local', model: 'remedy', total_tokens: 5200, estimated_cost_usd: 1.28, events: 22, prompt_tokens: 3640, completion_tokens: 1560 },
      { provider: 'openai', model: 'gpt', total_tokens: 3220, estimated_cost_usd: 2.4, events: 15, prompt_tokens: 2254, completion_tokens: 966 },
    ],
  }
  const series = {
    range_days: 7,
    group: 'provider',
    points: [
      ['2026-11-16', 1200],
      ['2026-11-17', 800],
      ['2026-11-18', 2400],
      ['2026-11-19', 400],
      ['2026-11-20', 6400],
      ['2026-11-21', 1600],
      ['2026-11-22', 2100],
    ].map(([day, tokens]) => ({
      day,
      group: 'local',
      total_tokens: tokens,
      estimated_cost_usd: 0,
      events: 1,
    })),
  }
  const continuity = {
    session_id: 'preview',
    harness_mode: 'auto',
    session_quality: {
      turns: 14,
      tokens_saved_by_compress: 3200,
      soft_nudge_count: 2,
      strong_nudge_count: 1,
      compress_count: 3,
      stuck_rate: 0.125,
      re_explain_rate: 0.4,
      max_tool_fail_streak: 2,
    },
    pattern: { step_count: 8, success_rate: 0.75, recent: ['read', 'edit', 'test'] },
    goal: { open: ['ship the usage panel', 'keep the ledger on the runtime'], stale: false },
    health: { samples: 20, error_rate: 0.05, avg_latency_ms: 840, flaky: false, rate_limit_hits: 0 },
    token: { last_method: 'bpe', last_estimate: 18420, last_remeasure: { fill_pct: 0.62 } },
    context_snapshot: { intent: 'port the usage dashboard', policy_id: 'house', nudge: 'stay on the usage panel', fill_pct: 0.4 },
    swarm: { event_count: 0 },
  }
  const previous = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (url.includes('local-bootstrap')) return Promise.resolve(json({ token: 'harness-token-ok!!' }))
    if (url.includes('/usage/summary')) return Promise.resolve(json(summary))
    if (url.includes('/usage/series')) return Promise.resolve(json(series))
    if (url.includes('/continuity/dashboard')) return Promise.resolve(json(continuity))
    if (url.includes('/nanoswarm/token/status')) {
      return Promise.resolve(json({
        label: 'NanoToken',
        last_method: 'bpe',
        last_estimate: 4096,
        active_provider: 'local',
        active_model: 'remedy',
        worker: 'attached',
        ip_note: 'Remedy-owned BBPE tokenizer.',
      }))
    }
    return previous(input, init)
  }
}

function UsageScreen({ harness }: { harness: boolean }) {
  useEffect(() => {
    if (!harness) return
    const btn = Array.from(document.querySelectorAll('button')).find((b) => b.textContent === 'Harness')
    btn?.click()
  }, [harness])
  return (
    <div className="h-full" style={{ background: 'var(--bg-primary)', color: 'var(--text-primary)' }} data-harness-ready="1">
      <UsageDashboard open onClose={() => {}} sessionId="preview" provider="local" model="remedy" />
    </div>
  )
}

/**
 * Same payload as the RemedyUI diagnostics golden.
 * Answers GET /diagnostics, /self-inject/rounds, and /hive/roster without a runtime.
 */
function installDiagnosticsFixture() {
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  const snapshot = {
    ok: false,
    overall: 'degraded',
    checked_at: '2026-11-22T21:04:05Z',
    collect_ms: 128,
    issues: [{
      severity: 'warn',
      area: 'rmb',
      message: 'RMB is still starting.',
      hint: 'Give the weights a moment.',
    }],
    remedy: {
      version: '1.0.0',
      uptime: '2h 14m',
      api: { host: '127.0.0.1', port: 7400, base_url: 'http://127.0.0.1:7400', listening: true },
      process: { pid: 4242, rss_mb: 180.5, cpu_pct: 3.2, threads: 12 },
      memory_entries: 40,
      chat_sessions_count: 3,
      skills_count: 7,
      active_provider: 'grok',
      active_model: 'grok-4',
      home_dir: 'C:\\Users\\Ahmi\\.remedy',
      home_disk: { free_gb: 120.5, used_pct: 62 },
    },
    rmb: {
      enabled: true,
      running: true,
      ready: false,
      model_name: 'Qwen2.5',
      model_path: 'C:\\models\\qwen2.5-7b.gguf',
      model_present: true,
      runtime_present: true,
      ctx_size: 8192,
      n_gpu_layers: 20,
      profile: 'balanced',
      base_url: 'http://127.0.0.1:8787',
      nvidia: true,
      latency_ms: 40,
      not_ready_hint: 'Loading weights.',
      vision_suspended: true,
    },
    hardware: {
      platform: 'Windows-11',
      system: 'Windows',
      machine: 'AMD64',
      python: '3.12.6',
      hostname: 'hub',
      cpu: { count_logical: 16, percent: 22, brand: 'Ryzen' },
      memory: { total_gb: 64, available_gb: 12, used_pct: 82 },
      gpu: {
        nvidia: true,
        gpus: [{
          name: 'RTX 4090',
          memory_total_mb: 24576,
          memory_used_mb: 23000,
          util_pct: 12,
          temp_c: 48,
        }],
      },
    },
    providers: {
      active: { provider: 'grok', model: 'grok-4' },
      connected_count: 2,
      remote_connected_count: 1,
      local_connected_count: 1,
      providers: [
        { id: 'grok', label: 'Grok', connected: true, local: false, last_model: 'grok-4' },
        { id: 'ollama', label: 'Ollama', connected: true, local: true, last_model: 'qwen2.5' },
        { id: 'openai', label: 'OpenAI', connected: false, local: false, reason: 'no key' },
      ],
      usage_7d: [{ provider: 'grok', total_tokens: 12345, estimated_cost_usd: 1.25 }],
      probes: [
        { id: 'ollama', ok: true, latency_ms: 18 },
        { id: 'lmstudio', ok: false, latency_ms: null },
      ],
    },
    vision: {
      enabled: true,
      running: false,
      ready: false,
      model_id: 'SmolVLM',
      port: 8790,
      base_url: 'http://127.0.0.1:8790',
      suspended_for_rmb: true,
      not_ready_hint: 'Paused while RMB loads.',
    },
    computer: {
      host_connected: true,
      pending_jobs: 1,
      jobs_root: 'C:\\Users\\Ahmi\\.remedy\\jobs',
    },
  }
  const rounds = {
    serve_started_utc: '2026-11-22T20:00:00Z',
    rounds: [
      {
        round_id: 'r-live',
        status: 'applied',
        tree: 'desktop',
        summary: 'Diagnostics panel wired to the runtime.',
        finished_utc: '2026-11-22T21:01:00Z',
        live_state: 'live',
      },
      {
        round_id: 'r-back',
        status: 'rolled_back',
        tree: 'python',
        summary: 'Gate failed, serve kept the previous tree.',
        finished_utc: '2026-11-22T18:12:00Z',
        live_state: '',
      },
      {
        round_id: 'r-red',
        status: 'red',
        tree: 'both',
        summary: 'Typecheck failed.',
        finished_utc: '2026-11-22T16:00:00Z',
      },
    ],
  }
  const roster = {
    daughters: [
      {
        id: 'post-1',
        cadence: 'post',
        status: 'running',
        goal: 'Watch the build queue and report when a job stalls overnight',
        outcome: 'Last check was clean.',
        blockers: [],
      },
      {
        id: 'forage-1',
        cadence: 'forager',
        status: 'pending',
        goal: 'Collect overnight logs',
        outcome: '',
        blockers: ['disk full', 'retry later', 'ignored third'],
      },
      {
        id: 'old-1',
        cadence: 'forager',
        status: 'retired',
        goal: 'Old sweep',
        outcome: 'finished',
        blockers: [],
      },
    ],
    live_posts: 1,
    live_foragers: 1,
    count: 3,
  }
  const previous = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (url.includes('local-bootstrap')) return Promise.resolve(json({ token: 'harness-token-ok!!' }))
    if (url.includes('/diagnostics')) return Promise.resolve(json(snapshot))
    if (url.includes('/self-inject/rounds')) return Promise.resolve(json(rounds))
    if (url.includes('/hive/roster')) return Promise.resolve(json(roster))
    return previous(input, init)
  }
}

function DiagnosticsScreen() {
  return (
    <div
      className="h-full"
      style={{ background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
      data-harness-ready="1"
    >
      <DiagnosticsPanel open onClose={() => {}} />
    </div>
  )
}

function Harness() {
  if (screen === 'diagnostics') {
    return <DiagnosticsScreen />
  }
  if (screen === 'usage' || screen === 'usage-harness') {
    return <UsageScreen harness={screen === 'usage-harness'} />
  }
  const empty = screen === 'empty'
  const [activeId, setActiveId] = useState(empty ? 's3' : 's1')
  const [theme, setTheme] = useState<ThemeId>(themeId)
  const resolved = useMemo(() => getResolvedTheme(theme), [theme])
  const feed = empty ? { messages: [], partialText: '', textBreaks: [] as number[], processSteps: [], activeTools: [], taskProgress: null, streaming: false } : stateAt(SCENARIO_END + 1, Date.now(), 0)
  const active = SESSIONS.find((s) => s.id === activeId) ?? SESSIONS[0]
  if (screen === 'rails-left' || screen === 'rails-right' || screen === 'rails-dual') {
    return <RailsShell which={screen} theme={theme} setTheme={setTheme} resolved={resolved} />
  }

  return (
    <div
      className="flex flex-col h-full min-h-0"
      style={{ background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
      data-harness-ready="1"
    >
      {screen === 'menu' ? <MenuBar /> : null}
      <div className="studio-shell flex flex-1 min-h-0 h-full items-stretch">
        <Sidebar
          sessions={SESSIONS}
          activeId={activeId}
          onSelect={setActiveId}
          onNew={() => setActiveId('s3')}
          onDelete={() => {}}
        />
        <div className="chat-middle flex flex-col flex-1 min-h-0 min-w-0">
          <ChatSessionHeader
            title={active.title}
            partnerName="Remedy"
            modelLabel={active.model}
            providerLabel="local"
            messageCount={empty ? 0 : feed.messages.length}
          />
          <div className="chat-middle-feed flex-1 min-h-0">
            <MessageFeed
              messages={feed.messages}
              partialText={empty ? '' : feed.partialText}
              textBreaks={empty ? [] : feed.textBreaks}
              streaming={false}
              loading={false}
              processSteps={empty ? [] : feed.processSteps}
              activeTools={empty ? [] : feed.activeTools}
              taskProgress={empty ? [] : feed.taskProgress}
              toolProcessMode="off"
              userName="Ahmi"
              partnerName="Remedy"
              sessionId={activeId}
              stickNonce={0}
            />
          </div>
          <div className="chat-middle-composer flex flex-col">
            <Composer
              onSend={() => {}}
              onStop={() => {}}
              onCommand={() => {}}
              streaming={false}
              disabled={false}
              slashCommands={[
                { name: '/help', description: 'List commands', aliases: [], arguments: null },
                { name: '/new', description: 'New session', aliases: [], arguments: null },
              ]}
              sessionId={activeId}
              acceptNativeDrops={false}
            />
          </div>
        </div>
      </div>
      <StatusBar
        sessionId={activeId}
        streaming={false}
        model="remedy"
        models={[{ id: 'remedy', name: 'Remedy', provider: 'local', default: true }]}
        provider="local"
        connectedProviders={[{
          id: 'local',
          name: 'Local',
          base_url: 'http://127.0.0.1:7400',
          models: [],
          default_model: 'remedy',
          auth: [],
          oauth: false,
          env_keys: [],
          show_base_url: false,
          advanced: false,
          connected: true,
          connect_reason: 'ok',
          enabled: true,
          picker_eligible: true,
          last_model: 'remedy',
        }]}
        thinkingLevel="medium"
        approvalMode="ask"
        themeId={theme}
        theme={resolved}
        onThemeChange={(id) => {
          setTheme(id)
          applyTheme(getResolvedTheme(id))
        }}
        planMode={false}
        onTogglePlanMode={() => {}}
        onTogglePanel={() => {}}
        updateAvailable={false}
        onCheckUpdates={() => {}}
        surface="studio"
      />
      <ChromeLayer which={screen} />
    </div>
  )
}

document.fonts.ready.then(() => {
  document.documentElement.dataset.fontsReady = '1'
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider>
      <Harness />
    </I18nProvider>
  </StrictMode>,
)
