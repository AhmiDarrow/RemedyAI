/** Settings form sections — localModels. */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { SettingsFormProps } from './formTypes'
import { SettingsSection } from '../SettingsSection'
import {
  FormActionButton,
  FormDownloadProgress,
  FormHint,
  FormLabel,
  FormNotice,
  FormSegmented,
  FormSelect,
  FormStatusCard,
  FormStatusRow,
  FormToggle,
} from './formUi'
import {
  activateVisionBundle,
  installVision,
  cancelVisionInstall,
  reinstallVisionRuntime,
  startVisionServer,
  stopVisionServer,
  formatDownloadGb,
} from '../../api/vision'
import {
  startRmb,
  stopRmb,
  patchRmbSettings,
  applyRmbAsProvider,
  getRmbStatus,
  notifyRmbModelChanged,
  searchHfModels,
  listHfFiles,
  pullHfModel,
  getHfProgress,
  cancelHfPull,
  type HfFileOption,
  type HfProgress,
  type HfRepoOption,
  type RmbStatus,
} from '../../api/rmb'

function emitRmbChatModel(r: RmbStatus | null | undefined, fallbackPath?: string) {
  const path = r?.model_path || fallbackPath || ''
  const stem =
    (r?.chat_model || r?.llm_model || r?.chat_sync?.stem || '').trim()
    || (path ? path.replace(/^.*[\\/]/, '').replace(/\.gguf$/i, '') : '')
  if (!stem) return
  notifyRmbModelChanged({ stem, path: path || undefined, provider: 'rmb' })
}
import { updateSettings } from '../../api/settings'

/** Small numeric engine-knob input — free entry, no curated caps. */
function RmbEngineNumber({
  label,
  value,
  min,
  max,
  step = 'any',
  placeholder = 'default',
  disabled,
  onApply,
}: {
  label: string
  value: number | string | null | undefined
  min?: number
  max?: number
  step?: string | number
  placeholder?: string
  disabled?: boolean
  onApply: (v: number) => void
}) {
  return (
    <div className="min-w-0">
      <FormLabel>{label}</FormLabel>
      <input
        type="number"
        className="ui-input ui-input-sm mb-1 w-full"
        disabled={disabled}
        defaultValue={value != null && value !== '' ? String(value) : ''}
        key={`rmb-knob-${label}-${value ?? ''}`}
        min={min}
        max={max}
        step={step}
        placeholder={placeholder}
        onBlur={(e) => {
          const raw = e.target.value.trim()
          if (!raw) return
          const n = Number(raw)
          if (Number.isFinite(n)) onApply(n)
        }}
      />
    </div>
  )
}

function formatDownloads(n?: number): string {
  if (n == null || n <= 0) return ''
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M dl`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k dl`
  return `${n} dl`
}

function HfPullPanel({
  disabled,
  onBusy,
  onMsg,
  onLoaded,
}: {
  disabled: boolean
  onBusy: (v: boolean) => void
  onMsg: (m: string) => void
  onLoaded: (path?: string) => void
}): ReactNode {
  const [query, setQuery] = useState('')
  const [repos, setRepos] = useState<HfRepoOption[]>([])
  const [files, setFiles] = useState<HfFileOption[]>([])
  const [repo, setRepo] = useState('')
  const [filePath, setFilePath] = useState('')
  const [progress, setProgress] = useState<HfProgress | null>(null)
  const [searching, setSearching] = useState(false)
  const onMsgRef = useRef(onMsg)
  const onLoadedRef = useRef(onLoaded)
  const onBusyRef = useRef(onBusy)
  onMsgRef.current = onMsg
  onLoadedRef.current = onLoaded
  onBusyRef.current = onBusy

  const pulling =
    progress?.phase === 'downloading' || progress?.phase === 'loading'
  const pct = Math.max(0, Math.min(100, Number(progress?.pct || 0)))
  const selected = files.find((f) => f.path === filePath)

  useEffect(() => {
    let stop = false
    void getHfProgress()
      .then((r) => {
        if (stop) return
        const p = r.progress
        if (p?.phase === 'downloading' || p?.phase === 'loading') {
          setProgress(p)
          onBusyRef.current(true)
          onMsgRef.current(p.message || `Downloading ${p.filename || 'GGUF'}…`)
        }
      })
      .catch(() => {})
    return () => {
      stop = true
    }
  }, [])

  useEffect(() => {
    if (!pulling) return
    let stop = false
    const tick = async () => {
      try {
        const r = await getHfProgress()
        if (stop) return
        const p = r.progress || null
        setProgress(p)
        if (p?.phase === 'ready') {
          onMsgRef.current(p.message || `Saved ${p.filename || 'GGUF'}`)
          onBusyRef.current(false)
          onLoadedRef.current(p.path || undefined)
        } else if (p?.phase === 'error') {
          onBusyRef.current(false)
          onMsgRef.current(p.error || p.message || 'Download failed')
        } else if (p?.phase === 'cancelled') {
          onBusyRef.current(false)
          onMsgRef.current('Download cancelled')
        }
      } catch (err) {
        if (!stop) onMsgRef.current(String(err))
      }
    }
    void tick()
    const id = window.setInterval(() => void tick(), 800)
    return () => {
      stop = true
      window.clearInterval(id)
    }
  }, [pulling])

  const runSearch = async () => {
    const q = query.trim()
    if (!q || disabled || searching || pulling) return
    setSearching(true)
    onBusy(true)
    setRepos([])
    setFiles([])
    setRepo('')
    setFilePath('')
    onMsg(`Searching Hugging Face for ${q}…`)
    try {
      const r = await searchHfModels(q)
      if (!r.ok && r.error) {
        onMsg(r.error)
        return
      }
      const nextRepos = r.repos || []
      const nextFiles = r.files || []
      setRepos(nextRepos)
      if (nextRepos.length > 1) {
        onMsg(`${nextRepos.length} Hugging Face repos — pick a host`)
      } else if (nextRepos.length === 1) {
        const only = nextRepos[0].id
        setRepo(only)
        onMsg(`Repo ${only} — pick a GGUF`)
        const listed = await listHfFiles(only, r.hint?.revision || undefined)
        const got = listed.files || []
        setFiles(got)
        const rec = got.find((f) => f.recommended) || got[0]
        if (rec) setFilePath(rec.path)
        onMsg(
          got.length
            ? `${got.length} GGUF file${got.length === 1 ? '' : 's'} in ${only}`
            : listed.error || `No GGUF files in ${only}`,
        )
      } else if (nextFiles.length) {
        setFiles(nextFiles)
        if (r.hint?.repo) setRepo(r.hint.repo)
        const rec = nextFiles.find((f) => f.recommended) || nextFiles[0]
        if (rec) setFilePath(rec.path)
        onMsg(
          r.hint?.filename
            ? `Ready to pull ${r.hint.filename}`
            : `${nextFiles.length} GGUF file${nextFiles.length === 1 ? '' : 's'}`,
        )
      } else {
        onMsg(r.error || 'No GGUF repos matched')
      }
    } catch (err) {
      onMsg(String(err))
    } finally {
      setSearching(false)
      onBusy(false)
    }
  }

  const pickRepo = async (id: string) => {
    setRepo(id)
    setFiles([])
    setFilePath('')
    if (!id || disabled || pulling) return
    setSearching(true)
    onBusy(true)
    onMsg(`Listing GGUF files in ${id}…`)
    try {
      const listed = await listHfFiles(id)
      const got = listed.files || []
      setFiles(got)
      const rec = got.find((f) => f.recommended) || got[0]
      if (rec) setFilePath(rec.path)
      onMsg(
        got.length
          ? `${got.length} GGUF file${got.length === 1 ? '' : 's'} in ${id}`
          : listed.error || `No GGUF files in ${id}`,
      )
    } catch (err) {
      onMsg(String(err))
    } finally {
      setSearching(false)
      onBusy(false)
    }
  }

  const runPull = async () => {
    if (disabled || pulling || searching) return
    const filename = selected?.path || filePath
    if (!filename || !repo) {
      onMsg('Pick a Hugging Face repo and GGUF first')
      return
    }
    onBusy(true)
    onMsg(`Pulling ${selected?.name || filename}…`)
    try {
      const r = await pullHfModel({
        repo,
        filename,
        url: selected?.url,
        expected_size: selected?.size,
        load: true,
      })
      if (!r.ok) {
        onMsg(r.error || 'Pull failed')
        onBusy(false)
        return
      }
      setProgress(r.progress || { phase: 'downloading' })
    } catch (err) {
      onMsg(String(err))
      onBusy(false)
    }
  }

  return (
    <div className="mt-3 mb-2 space-y-1.5">
      <FormLabel>Pull from Hugging Face</FormLabel>
      <FormHint>
        Name, <code className="text-[9px]">owner/repo</code>, or a file URL.
        A name can match more than one account — pick the repo, then the GGUF.
      </FormHint>
      <div className="flex gap-1.5">
        <input
          type="text"
          className="ui-input ui-input-sm mb-1 font-mono w-full"
          disabled={disabled || searching || pulling}
          value={query}
          placeholder="qwen2.5-coder-7b  or  Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              void runSearch()
            }
          }}
        />
        <FormActionButton
          disabled={disabled || searching || pulling || !query.trim()}
          onClick={() => void runSearch()}
        >
          Search
        </FormActionButton>
      </div>
      {repos.length > 0 ? (
        <>
          <FormLabel>
            Hugging Face repo
            {repos.length > 1 ? ` (${repos.length} hosts)` : ''}
          </FormLabel>
          <FormSelect
            size="sm"
            disabled={disabled || searching || pulling}
            value={repo}
            options={[
              { value: '', label: '— pick a repo —' },
              ...repos.map((r) => ({
                value: r.id,
                label: `${r.id}${formatDownloads(r.downloads) ? ` · ${formatDownloads(r.downloads)}` : ''}`,
              })),
            ]}
            onChange={(id) => {
              void pickRepo(id)
            }}
          />
        </>
      ) : null}
      {files.length > 0 ? (
        <>
          <FormLabel>GGUF file</FormLabel>
          <FormSelect
            size="sm"
            disabled={disabled || searching || pulling}
            value={filePath}
            options={[
              { value: '', label: '— pick a GGUF —' },
              ...files.map((f) => ({
                value: f.path,
                label: `${f.recommended ? '★ ' : ''}${f.name}${
                  f.size_gb ? ` (${f.size_gb} GB)` : ''
                }${f.role === 'mmproj' ? ' · mmproj' : ''}`,
              })),
            ]}
            onChange={setFilePath}
          />
          <FormActionButton
            variant="primary"
            disabled={disabled || searching || pulling || !repo || !filePath}
            onClick={() => void runPull()}
          >
            Pull and load
          </FormActionButton>
        </>
      ) : null}
      {pulling ? (
        <div className="mt-1">
          <FormDownloadProgress
            label={progress?.message || `Downloading ${progress?.filename || 'GGUF'}`}
            percent={progress?.bytes_total ? pct : (typeof progress?.pct === 'number' ? pct : null)}
          />
          {progress?.phase === 'downloading' ? (
            <FormActionButton
              variant="ghost"
              disabled={false}
              onClick={() => {
                void cancelHfPull().then((r) => {
                  if (r.error) onMsg(r.error)
                  else onMsg('Cancelling download…')
                })
              }}
            >
              Cancel
            </FormActionButton>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

export function SettingsSections_localModels(p: SettingsFormProps): ReactNode {
  const {
    sectionProps,
    vision,
    visionBusy,
    setVisionBusy,
    visionMsg,
    setVisionMsg,
    refreshVision,
    startVisionInstallPoll,
    rmb,
    rmbBusy,
    setRmbBusy,
    rmbMsg,
    setRmbMsg,
    refreshRmb,
    onSettingsSaved,
    onOpenHelp,
  } = p

  /** One-shot knob save: patch, refresh, message. Shared by advanced knobs. */
  const patchKnob = async (
    patch: Record<string, unknown>,
    msg: string,
  ): Promise<void> => {
    if (rmbBusy) return
    setRmbBusy(true)
    try {
      await patchRmbSettings({ enabled: true, ...patch })
      setRmbMsg(msg)
      await refreshRmb()
    } catch (err) {
      setRmbMsg(String(err))
    } finally {
      setRmbBusy(false)
    }
  }

  return (
    <>
      <SettingsSection {...sectionProps('rmb')}>
        <FormHint>
          <strong style={{ color: 'var(--text-secondary)' }}>Local models (RMB)</strong>
          {' '}— pick a GGUF. Remedy <strong>loads it, sets chat to RMB, and keeps the
          status bar in sync</strong> automatically. No other provider settings needed.
          Files in Downloads or{' '}
          <code className="text-[9px]">~/.remedy/rmb/models/</code>.
        </FormHint>
        <FormStatusCard>
          <FormStatusRow label="Status">
            {!rmb
              ? '…'
              : rmb.ready
                ? '● Ready'
                : rmb.running
                  ? '● Starting…'
                  : rmb.model_present && rmb.runtime_present
                    ? '○ Stopped'
                    : rmb.not_ready_hint || 'Not ready'}
          </FormStatusRow>
          <FormStatusRow label="Loaded GGUF">
            <span className="font-mono text-[9px]" title={rmb?.model_path || undefined}>
              {rmb?.model_path
                ? rmb.model_path.replace(/^.*[\\/]/, '')
                : rmb?.model?.name || rmb?.model_id || '— none —'}
            </span>
          </FormStatusRow>
          <FormStatusRow label="Endpoint">
            <span className="font-mono text-[9px]">
              {rmb?.base_url || 'http://127.0.0.1:8787/v1'}
            </span>
          </FormStatusRow>
          <FormStatusRow label="Context">
            {rmb?.autofit?.summary
              ? rmb.autofit.summary
              : `${rmb?.ctx_size ?? 8192} tok · ${rmb?.profile || 'autofit'}`}
            {rmb?.nvidia ? ' · NVIDIA' : ' · CPU'}
          </FormStatusRow>
          <FormStatusRow label="Auto-load">
            {rmb?.host_auto?.summary || 'Remedy sets Jinja, thinking, and slots from the GGUF'}
          </FormStatusRow>
          <FormStatusRow label="Runtime">
            {rmb?.runtime_present
              ? (rmb.runtime_binary || 'llama-server').replace(/^.*[\\/]/, '')
              : 'Missing — install Local vision once'}
          </FormStatusRow>
          <FormStatusRow label="SmolVLM">
            {rmb?.vision_suspended || rmb?.running
              ? 'Suspended (RMB exclusive)'
              : 'Available when RMB stops'}
          </FormStatusRow>
        </FormStatusCard>
        {rmbMsg ? (
          <FormNotice tone={/fail|error|not found|missing/i.test(rmbMsg) ? 'warn' : undefined}>
            {rmbMsg}
          </FormNotice>
        ) : null}
        {(rmb?.host_auto?.warnings || []).length > 0 ? (
          <FormNotice tone="warn">
            {(rmb?.host_auto?.warnings || []).join(' ')}
          </FormNotice>
        ) : null}

        {/* Primary: pick GGUF from disk scan — options prop so values always stick */}
        <div className="mt-2 mb-2 space-y-1.5">
          <FormLabel>GGUF model (select to load)</FormLabel>
          <FormSelect
            size="sm"
            disabled={rmbBusy}
            value={rmb?.model_path || ''}
            title="Discovered GGUF files — selecting restarts RMB with that file"
            options={[
              {
                value: '',
                label:
                  (rmb?.discovered_ggufs?.length ?? 0) > 0
                    ? '— pick a GGUF —'
                    : '— no GGUFs found (Downloads or ~/.remedy/rmb/models) —',
              },
              ...(rmb?.discovered_ggufs || [])
                .filter((g) => Boolean(g.path))
                .map((g) => ({
                  value: String(g.path),
                  label: `${g.name || g.path}${
                    g.size_gb != null ? ` (${g.size_gb} GB)` : ''
                  }`,
                })),
            ]}
            onChange={(model_path) => {
              if (!model_path || rmbBusy) return
              const cur = (rmb?.model_path || '').replace(/\//g, '\\').toLowerCase()
              const next = model_path.replace(/\//g, '\\').toLowerCase()
              if (cur === next && (rmb?.ready || rmb?.running)) {
                setRmbMsg('Already loaded — pick a different GGUF')
                return
              }
              void (async () => {
                setRmbBusy(true)
                const file = model_path.replace(/^.*[\\/]/, '')
                setRmbMsg(`Loading ${file}… (restart may take 30–90s)`)
                try {
                  const r = await patchRmbSettings({
                    model_path,
                    enabled: true,
                    // Always make this the active chat model — no extra clicks
                    use_as_chat_provider: true,
                  })
                  const liveErr = r?.live_apply?.live_error
                  const note = r?.live_note
                  const loaded = (r?.model_path || model_path).replace(/^.*[\\/]/, '')
                  // Optimistic UI: show selected path immediately
                  await refreshRmb()
                  emitRmbChatModel(r, model_path)
                  if (liveErr) {
                    setRmbMsg(`${loaded}: ${liveErr}`)
                  } else if (r?.live_apply?.restarted || r?.ready || r?.running) {
                    setRmbMsg(note || `Loaded ${loaded}`)
                  } else if (note) {
                    setRmbMsg(note)
                  } else {
                    // Disk saved but host not up — force start
                    setRmbMsg(`Selected ${loaded} — starting host…`)
                    const start = (await startRmb()) as { ok?: boolean; error?: string }
                    setRmbMsg(
                      start?.ok
                        ? `Loaded ${loaded}`
                        : start?.error || `Selected ${loaded} — Start RMB if needed`,
                    )
                    await refreshRmb()
                    emitRmbChatModel(r, model_path)
                  }
                  onSettingsSaved?.()
                } catch (err) {
                  setRmbMsg(String(err))
                  await refreshRmb()
                } finally {
                  setRmbBusy(false)
                }
              })()
            }}
          />
          {(rmb?.catalog?.models?.length ?? 0) > 0 ? (
            <>
              <FormLabel>Catalog shortcut</FormLabel>
              <FormSelect
                size="sm"
                disabled={rmbBusy}
                value={rmb?.model_id || rmb?.catalog?.default_model_id || ''}
                options={(rmb?.catalog?.models || []).map((m) => ({
                  value: m.id,
                  label: `${m.name}${m.approx_gb != null ? ` (~${m.approx_gb} GB)` : ''}`,
                }))}
                onChange={(model_id) => {
                  if (rmbBusy || !model_id) return
                  void (async () => {
                    setRmbBusy(true)
                    setRmbMsg(`Switching catalog → ${model_id}…`)
                    try {
                      const r = await patchRmbSettings({
                        model_id,
                        model_path: '',
                        enabled: true,
                        use_as_chat_provider: true,
                      })
                      const liveErr = r?.live_apply?.live_error
                      const file = (r?.model_path || '').replace(/^.*[\\/]/, '')
                      await refreshRmb()
                      emitRmbChatModel(r)
                      if (liveErr) setRmbMsg(`Model ${model_id}: ${liveErr}`)
                      else if (r?.live_note) setRmbMsg(r.live_note)
                      else {
                        setRmbMsg(
                          file
                            ? `Catalog ${model_id} → ${file}`
                            : `Catalog ${model_id}: no matching GGUF on disk`,
                        )
                      }
                      onSettingsSaved?.()
                    } catch (err) {
                      setRmbMsg(String(err))
                    } finally {
                      setRmbBusy(false)
                    }
                  })()
                }}
              />
            </>
          ) : null}
          <FormLabel>Or paste full path</FormLabel>
          <input
            type="text"
            className="ui-input ui-input-sm mb-1 font-mono w-full"
            disabled={rmbBusy}
            defaultValue={rmb?.model_path || ''}
            key={rmb?.model_path || 'rmb-path'}
            placeholder="C:\Users\…\model.gguf"
            onBlur={async (e) => {
              const model_path = e.target.value.trim()
              if (model_path === (rmb?.model_path || '')) return
              setRmbBusy(true)
              try {
                const r = await patchRmbSettings({
                  model_path: model_path || '',
                  enabled: true,
                  use_as_chat_provider: Boolean(model_path),
                })
                setRmbMsg(
                  model_path
                    ? r?.live_apply?.live_error ||
                        r?.live_note ||
                        `Path: ${model_path.replace(/^.*[\\/]/, '')}`
                    : 'Path cleared',
                )
                await refreshRmb()
                if (model_path) emitRmbChatModel(r, model_path)
                onSettingsSaved?.()
              } catch (err) {
                setRmbMsg(String(err))
              } finally {
                setRmbBusy(false)
              }
            }}
          />
        </div>

        <HfPullPanel
          disabled={rmbBusy}
          onBusy={setRmbBusy}
          onMsg={setRmbMsg}
          onLoaded={(path) => {
            void (async () => {
              const st = await refreshRmb()
              emitRmbChatModel(st, path)
              onSettingsSaved?.()
            })()
          }}
        />

        <FormSegmented
          value={((rmb?.profile || 'autofit') as 'autofit' | 'agent' | 'turbo' | 'quality')}
          options={[
            { id: 'autofit', label: 'Autofit' },
            { id: 'agent', label: 'Agent' },
            { id: 'turbo', label: 'Turbo' },
            { id: 'quality', label: 'Quality' },
          ]}
          onChange={(pid) => {
            if (rmbBusy) return
            void (async () => {
              setRmbBusy(true)
              try {
                await patchRmbSettings({ profile: pid, enabled: true })
                setRmbMsg(
                  pid === 'autofit'
                    ? 'Autofit: largest stable context for this GPU/RAM'
                    : `Profile: ${pid}`,
                )
                await refreshRmb()
              } catch (e) {
                setRmbMsg(String(e))
              } finally {
                setRmbBusy(false)
              }
            })()
          }}
        />
        <FormHint>
          Autofit is the default — it sizes context, GPU layers, and KV cache
          from VRAM/RAM so the GGUF actually loads. Edit context or GPU layers
          below to lock a manual fit.
        </FormHint>
        <div className="flex gap-2 mt-2">
          <div className="flex-1 min-w-0">
            <FormLabel>Context size (no cap)</FormLabel>
            <input
              type="number"
              className="ui-input ui-input-sm mb-1 w-full"
              disabled={rmbBusy}
              defaultValue={String(rmb?.ctx_size ?? 8192)}
              key={`ctx-${rmb?.ctx_size ?? 8192}`}
              min={2048}
              step={1024}
              onBlur={(e) => {
                const ctx_size = parseInt(e.target.value, 10)
                if (Number.isNaN(ctx_size) || rmbBusy) return
                void (async () => {
                  setRmbBusy(true)
                  try {
                    const res = (await patchRmbSettings({
                      ctx_size,
                      enabled: true,
                    })) as RmbStatus
                    const live =
                      res.live_apply?.ctx_size_live ?? res.ctx_size ?? ctx_size
                    setRmbMsg(
                      res.live_note ||
                        (res.live_apply?.restarted
                          ? `Context live: ${live} (restarted)`
                          : `Context: ${live}`),
                    )
                    await refreshRmb()
                  } catch (err) {
                    setRmbMsg(String(err))
                  } finally {
                    setRmbBusy(false)
                  }
                })()
              }}
            />
          </div>
          <div className="flex-1 min-w-0">
            <FormLabel>GPU layers (−1 = all)</FormLabel>
            <input
              type="number"
              className="ui-input ui-input-sm mb-2 w-full"
              disabled={rmbBusy}
              defaultValue={
                rmb?.n_gpu_layers != null ? String(rmb.n_gpu_layers) : '-1'
              }
              key={`ngl-${rmb?.n_gpu_layers ?? -1}`}
              onBlur={async (e) => {
                const n = parseInt(e.target.value, 10)
                if (Number.isNaN(n)) return
                setRmbBusy(true)
                try {
                  await patchRmbSettings({ n_gpu_layers: n, enabled: true })
                  setRmbMsg(`GPU layers: ${n}`)
                  await refreshRmb()
                } catch (err) {
                  setRmbMsg(String(err))
                } finally {
                  setRmbBusy(false)
                }
              }}
            />
          </div>
        </div>

        <details className="mt-2 mb-1 rounded-md border border-[color:var(--border)] p-2">
          <summary
            className="cursor-pointer select-none text-[11px] font-medium"
            style={{ color: 'var(--text-secondary)' }}
          >
            Advanced engine settings (llama-server)
          </summary>
          <div className="mt-2 space-y-1.5">
            <div className="grid grid-cols-2 gap-x-2 gap-y-1">
              <RmbEngineNumber
                label="Threads (0 = auto)"
                value={rmb?.engine?.threads ?? 0}
                min={0}
                step={1}
                disabled={rmbBusy}
                onApply={(threads) =>
                  void patchKnob(
                    { threads: Math.max(0, Math.round(threads)) },
                    `Threads: ${threads}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Parallel slots"
                value={rmb?.engine?.parallel ?? 1}
                min={1}
                step={1}
                disabled={rmbBusy}
                onApply={(parallel) =>
                  void patchKnob(
                    { parallel: Math.max(1, Math.round(parallel)) },
                    `Parallel: ${parallel}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Batch size (0 = auto)"
                value={rmb?.engine?.batch_size ?? 0}
                min={0}
                step={1}
                disabled={rmbBusy}
                onApply={(batch_size) =>
                  void patchKnob(
                    { batch_size: Math.max(0, Math.round(batch_size)) },
                    `Batch size: ${batch_size}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Ubatch size (0 = auto)"
                value={rmb?.engine?.ubatch_size ?? 0}
                min={0}
                step={1}
                disabled={rmbBusy}
                onApply={(ubatch_size) =>
                  void patchKnob(
                    { ubatch_size: Math.max(0, Math.round(ubatch_size)) },
                    `Ubatch size: ${ubatch_size}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Temperature"
                value={rmb?.engine?.temperature ?? ''}
                min={0}
                step={0.05}
                disabled={rmbBusy}
                onApply={(temperature) =>
                  void patchKnob({ temperature }, `Temperature: ${temperature}`)
                }
              />
              <RmbEngineNumber
                label="Top-P"
                value={rmb?.engine?.top_p ?? ''}
                min={0}
                max={1}
                step={0.05}
                disabled={rmbBusy}
                onApply={(top_p) =>
                  void patchKnob({ top_p: Math.min(1, Math.max(0, top_p)) }, `Top-P: ${top_p}`)
                }
              />
              <RmbEngineNumber
                label="Top-K (0 = off)"
                value={rmb?.engine?.top_k ?? ''}
                min={0}
                step={1}
                disabled={rmbBusy}
                onApply={(top_k) =>
                  void patchKnob(
                    { top_k: Math.max(0, Math.round(top_k)) },
                    `Top-K: ${top_k}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Min-P"
                value={rmb?.engine?.min_p ?? ''}
                min={0}
                max={1}
                step={0.05}
                disabled={rmbBusy}
                onApply={(min_p) =>
                  void patchKnob({ min_p: Math.min(1, Math.max(0, min_p)) }, `Min-P: ${min_p}`)
                }
              />
              <RmbEngineNumber
                label="Repeat penalty"
                value={rmb?.engine?.repeat_penalty ?? ''}
                min={0}
                step={0.05}
                disabled={rmbBusy}
                onApply={(repeat_penalty) =>
                  void patchKnob(
                    { repeat_penalty: Math.max(0, repeat_penalty) },
                    `Repeat penalty: ${repeat_penalty}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Repeat last N (0 = off)"
                value={rmb?.engine?.repeat_last_n ?? ''}
                min={0}
                step={1}
                disabled={rmbBusy}
                onApply={(repeat_last_n) =>
                  void patchKnob(
                    { repeat_last_n: Math.max(0, Math.round(repeat_last_n)) },
                    `Repeat last N: ${repeat_last_n}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Seed (−1 = random)"
                value={rmb?.engine?.seed ?? ''}
                step={1}
                disabled={rmbBusy}
                onApply={(seed) =>
                  void patchKnob({ seed: Math.round(seed) }, `Seed: ${seed}`)
                }
              />
              <RmbEngineNumber
                label="RoPE freq scale (0 = off)"
                value={rmb?.engine?.rope_freq_scale ?? ''}
                min={0}
                step={0.05}
                disabled={rmbBusy}
                onApply={(rope_freq_scale) =>
                  void patchKnob(
                    { rope_freq_scale: Math.max(0, rope_freq_scale) },
                    `RoPE scale: ${rope_freq_scale}`,
                  )
                }
              />
              <RmbEngineNumber
                label="RoPE freq base (0 = off)"
                value={rmb?.engine?.rope_freq_base ?? ''}
                min={0}
                step={100}
                disabled={rmbBusy}
                onApply={(rope_freq_base) =>
                  void patchKnob(
                    { rope_freq_base: Math.max(0, rope_freq_base) },
                    `RoPE base: ${rope_freq_base}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Typical-P (0 = off)"
                value={rmb?.engine?.typical_p ?? ''}
                min={0}
                step={0.05}
                disabled={rmbBusy}
                onApply={(typical_p) =>
                  void patchKnob(
                    { typical_p: Math.max(0, Math.min(1, typical_p)) },
                    `Typical-P: ${typical_p}`,
                  )
                }
              />
              <RmbEngineNumber
                label="TFS-Z (0 = off)"
                value={rmb?.engine?.tfs_z ?? ''}
                min={0}
                step={0.05}
                disabled={rmbBusy}
                onApply={(tfs_z) =>
                  void patchKnob(
                    { tfs_z: Math.max(0, tfs_z) },
                    `TFS-Z: ${tfs_z}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Mirostat (0/1/2)"
                value={rmb?.engine?.mirostat ?? ''}
                min={0}
                step={1}
                disabled={rmbBusy}
                onApply={(mirostat) =>
                  void patchKnob(
                    { mirostat: Math.max(0, Math.min(2, Math.round(mirostat))) },
                    `Mirostat: ${mirostat}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Mirostat tau"
                value={rmb?.engine?.mirostat_tau ?? ''}
                min={0}
                step={0.1}
                disabled={rmbBusy}
                onApply={(mirostat_tau) =>
                  void patchKnob(
                    { mirostat_tau: Math.max(0, mirostat_tau) },
                    `Mirostat tau: ${mirostat_tau}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Mirostat eta"
                value={rmb?.engine?.mirostat_eta ?? ''}
                min={0}
                step={0.01}
                disabled={rmbBusy}
                onApply={(mirostat_eta) =>
                  void patchKnob(
                    { mirostat_eta: Math.max(0, mirostat_eta) },
                    `Mirostat eta: ${mirostat_eta}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Presence penalty"
                value={rmb?.engine?.presence_penalty ?? ''}
                min={0}
                step={0.1}
                disabled={rmbBusy}
                onApply={(presence_penalty) =>
                  void patchKnob(
                    { presence_penalty: Math.max(0, presence_penalty) },
                    `Presence: ${presence_penalty}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Frequency penalty"
                value={rmb?.engine?.frequency_penalty ?? ''}
                min={0}
                step={0.1}
                disabled={rmbBusy}
                onApply={(frequency_penalty) =>
                  void patchKnob(
                    { frequency_penalty: Math.max(0, frequency_penalty) },
                    `Frequency: ${frequency_penalty}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Main GPU (0 = first)"
                value={rmb?.engine?.main_gpu ?? ''}
                min={0}
                step={1}
                disabled={rmbBusy}
                onApply={(main_gpu) =>
                  void patchKnob(
                    { main_gpu: Math.max(0, Math.round(main_gpu)) },
                    `Main GPU: ${main_gpu}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Threads batch (0 = auto)"
                value={rmb?.engine?.threads_batch ?? ''}
                min={0}
                step={1}
                disabled={rmbBusy}
                onApply={(threads_batch) =>
                  void patchKnob(
                    { threads_batch: Math.max(0, Math.round(threads_batch)) },
                    `Threads batch: ${threads_batch}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Dry multiplier (0 = off)"
                value={rmb?.engine?.dry_multiplier ?? ''}
                min={0}
                step={0.1}
                disabled={rmbBusy}
                onApply={(dry_multiplier) =>
                  void patchKnob(
                    { dry_multiplier: Math.max(0, dry_multiplier) },
                    `Dry: ${dry_multiplier}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Dry base"
                value={rmb?.engine?.dry_base ?? ''}
                min={0}
                step={0.05}
                disabled={rmbBusy}
                onApply={(dry_base) =>
                  void patchKnob(
                    { dry_base: Math.max(0, dry_base) },
                    `Dry base: ${dry_base}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Dry allowed length"
                value={rmb?.engine?.dry_allowed_length ?? ''}
                min={0}
                step={1}
                disabled={rmbBusy}
                onApply={(dry_allowed_length) =>
                  void patchKnob(
                    { dry_allowed_length: Math.max(0, Math.round(dry_allowed_length)) },
                    `Dry length: ${dry_allowed_length}`,
                  )
                }
              />
              <RmbEngineNumber
                label="Dry penalty last N (−1 = all)"
                value={rmb?.engine?.dry_penalty_last_n ?? ''}
                step={1}
                disabled={rmbBusy}
                onApply={(dry_penalty_last_n) =>
                  void patchKnob(
                    { dry_penalty_last_n: Math.round(dry_penalty_last_n) },
                    `Dry last N: ${dry_penalty_last_n}`,
                  )
                }
              />
              <RmbEngineNumber
                label="XTC probability (0 = off)"
                value={rmb?.engine?.xtc_probability ?? ''}
                min={0}
                max={1}
                step={0.05}
                disabled={rmbBusy}
                onApply={(xtc_probability) =>
                  void patchKnob(
                    { xtc_probability: Math.min(1, Math.max(0, xtc_probability)) },
                    `XTC: ${xtc_probability}`,
                  )
                }
              />
              <RmbEngineNumber
                label="XTC threshold"
                value={rmb?.engine?.xtc_threshold ?? ''}
                min={0}
                max={1}
                step={0.05}
                disabled={rmbBusy}
                onApply={(xtc_threshold) =>
                  void patchKnob(
                    { xtc_threshold: Math.min(1, Math.max(0, xtc_threshold)) },
                    `XTC threshold: ${xtc_threshold}`,
                  )
                }
              />
            </div>

            <div className="grid grid-cols-2 gap-x-2 gap-y-1">
              <FormToggle
                label="Flash attention"
                description="-fa on (CUDA/Metal)"
                checked={Boolean(rmb?.engine?.flash_attn ?? true)}
                disabled={rmbBusy}
                onChange={(flash_attn) =>
                  void patchKnob({ flash_attn }, `Flash attention: ${flash_attn ? 'on' : 'off'}`)
                }
              />
              <FormToggle
                label="Use Jinja"
                description="--jinja chat templates"
                checked={Boolean(rmb?.engine?.use_jinja ?? true)}
                disabled={rmbBusy}
                onChange={(use_jinja) =>
                  void patchKnob({ use_jinja }, `Jinja templates: ${use_jinja ? 'on' : 'off'}`)
                }
              />
              <FormToggle
                label="mlock"
                description="Lock model in RAM"
                checked={Boolean(rmb?.engine?.mlock)}
                disabled={rmbBusy}
                onChange={(mlock) => void patchKnob({ mlock }, `mlock: ${mlock ? 'on' : 'off'}`)}
              />
              <FormToggle
                label="no-mmap"
                description="Disable memory mapping"
                checked={Boolean(rmb?.engine?.no_mmap)}
                disabled={rmbBusy}
                onChange={(no_mmap) =>
                  void patchKnob({ no_mmap }, `no-mmap: ${no_mmap ? 'on' : 'off'}`)
                }
              />
              <FormToggle
                label="no-kv-offload"
                description="Keep KV cache on CPU"
                checked={Boolean(rmb?.engine?.no_kv_offload)}
                disabled={rmbBusy}
                onChange={(no_kv_offload) =>
                  void patchKnob({ no_kv_offload }, `no-kv-offload: ${no_kv_offload ? 'on' : 'off'}`)
                }
              />
            </div>

            <div className="space-y-1">
              <FormLabel>Sampler order (--samplers)</FormLabel>
              <FormSelect
                size="sm"
                disabled={rmbBusy}
                value={rmb?.engine?.samplers || ''}
                options={[
                  { value: '', label: 'llama.cpp default' },
                  { value: 'top_k;top_p;min_p;temp', label: 'top_k;top_p;min_p;temp' },
                  { value: 'top_k;top_p;min_p;typ;temp', label: 'top_k;top_p;min_p;typ;temp' },
                  { value: 'top_k;top_p;min_p;rep_pen;temp', label: 'top_k;top_p;min_p;rep_pen;temp' },
                  { value: 'mirostat_v2;top_k;top_p;min_p;temp', label: 'mirostat_v2;top_k;top_p;min_p;temp' },
                ]}
                onChange={(samplers) =>
                  void patchKnob(
                    { samplers },
                    samplers ? `Samplers: ${samplers}` : 'Samplers: default',
                  )
                }
              />
            </div>

            <div className="space-y-1">
              <FormLabel>RoPE scaling (--rope-scaling)</FormLabel>
              <FormSelect
                size="sm"
                disabled={rmbBusy}
                value={rmb?.engine?.rope_scaling || ''}
                options={[
                  { value: '', label: 'None (default)' },
                  { value: 'linear', label: 'Linear' },
                  { value: 'yarn', label: 'YaRN (long-context)' },
                ]}
                onChange={(rope_scaling) =>
                  void patchKnob(
                    { rope_scaling },
                    rope_scaling ? `RoPE scaling: ${rope_scaling}` : 'RoPE scaling: none',
                  )
                }
              />
              {rmb?.engine?.rope_scaling === 'yarn' ? (
                <div className="grid grid-cols-2 gap-x-2 gap-y-1 mt-1">
                  <RmbEngineNumber
                    label="YaRN orig ctx"
                    value={rmb?.engine?.yarn_orig_ctx ?? ''}
                    min={0}
                    step={1024}
                    disabled={rmbBusy}
                    onApply={(yarn_orig_ctx) =>
                      void patchKnob(
                        { yarn_orig_ctx: Math.max(0, Math.round(yarn_orig_ctx)) },
                        `YaRN orig ctx: ${yarn_orig_ctx}`,
                      )
                    }
                  />
                  <RmbEngineNumber
                    label="YaRN factor"
                    value={rmb?.engine?.yarn_factor ?? ''}
                    min={0}
                    step={0.5}
                    disabled={rmbBusy}
                    onApply={(yarn_factor) =>
                      void patchKnob(
                        { yarn_factor: Math.max(0, yarn_factor) },
                        `YaRN factor: ${yarn_factor}`,
                      )
                    }
                  />
                  <RmbEngineNumber
                    label="YaRN beta fast"
                    value={rmb?.engine?.yarn_beta_fast ?? ''}
                    min={0}
                    step={0.01}
                    disabled={rmbBusy}
                    onApply={(yarn_beta_fast) =>
                      void patchKnob(
                        { yarn_beta_fast: Math.max(0, yarn_beta_fast) },
                        `YaRN beta fast: ${yarn_beta_fast}`,
                      )
                    }
                  />
                  <RmbEngineNumber
                    label="YaRN beta slow"
                    value={rmb?.engine?.yarn_beta_slow ?? ''}
                    min={0}
                    step={0.01}
                    disabled={rmbBusy}
                    onApply={(yarn_beta_slow) =>
                      void patchKnob(
                        { yarn_beta_slow: Math.max(0, yarn_beta_slow) },
                        `YaRN beta slow: ${yarn_beta_slow}`,
                      )
                    }
                  />
                </div>
              ) : null}
            </div>

            <div className="space-y-1">
              <FormLabel>Tensor split (--tensor-split)</FormLabel>
              <input
                type="text"
                className="ui-input ui-input-sm mb-1 font-mono w-full"
                disabled={rmbBusy}
                defaultValue={rmb?.engine?.tensor_split || ''}
                key={`ts-${rmb?.engine?.tensor_split || ''}`}
                placeholder="e.g. 0,512 (multi-GPU VRAM split)"
                onBlur={(e) => {
                  const tensor_split = e.target.value.trim()
                  if (tensor_split === (rmb?.engine?.tensor_split || '')) return
                  void patchKnob(
                    { tensor_split },
                    tensor_split ? `Tensor split: ${tensor_split}` : 'Tensor split cleared',
                  )
                }}
              />
            </div>

            <div className="space-y-1">
              <FormLabel>KV cache type</FormLabel>
              <FormSelect
                size="sm"
                disabled={rmbBusy}
                value={rmb?.engine?.cache_type || ''}
                options={[
                  { value: '', label: 'Auto (default)' },
                  { value: 'f16', label: 'f16 (default)' },
                  { value: 'q8_0', label: 'q8_0 (less VRAM)' },
                  { value: 'q4_0', label: 'q4_0 (least VRAM)' },
                ]}
                onChange={(cache_type) =>
                  void patchKnob(
                    { cache_type },
                    cache_type ? `KV cache: ${cache_type}` : 'KV cache: auto',
                  )
                }
              />
            </div>

            <div className="space-y-1">
              <FormLabel>MMProj (vision) GGUF</FormLabel>
              <input
                type="text"
                className="ui-input ui-input-sm mb-1 font-mono w-full"
                disabled={rmbBusy}
                defaultValue={rmb?.engine?.mmproj || ''}
                key={`mmproj-${rmb?.engine?.mmproj || ''}`}
                placeholder="C:\…\mmproj-model-f16.gguf"
                onBlur={(e) => {
                  const mmproj = e.target.value.trim()
                  if (mmproj === (rmb?.engine?.mmproj || '')) return
                  void patchKnob(
                    { mmproj },
                    mmproj ? `MMProj: ${mmproj.replace(/^.*[\\/]/, '')}` : 'MMProj cleared',
                  )
                }}
              />
            </div>

            <div className="space-y-1">
              <FormLabel>Chat template (path)</FormLabel>
              <input
                type="text"
                className="ui-input ui-input-sm mb-1 font-mono w-full"
                disabled={rmbBusy}
                defaultValue={rmb?.engine?.chat_template || ''}
                key={`ct-${rmb?.engine?.chat_template || ''}`}
                placeholder="C:\…\chat-template.jinja"
                onBlur={(e) => {
                  const chat_template = e.target.value.trim()
                  if (chat_template === (rmb?.engine?.chat_template || '')) return
                  void patchKnob(
                    { chat_template },
                    chat_template
                      ? `Template: ${chat_template.replace(/^.*[\\/]/, '')}`
                      : 'Template cleared',
                  )
                }}
              />
            </div>

            <FormHint>
              Changing any knob restarts llama-server with the new flags. Empty
              numeric fields fall back to llama-server defaults.
            </FormHint>
          </div>
        </details>

        <div className="flex flex-wrap gap-1.5 mt-1 mb-1">
          <FormActionButton
            variant="primary"
            disabled={rmbBusy}
            onClick={async () => {
              setRmbBusy(true)
              setRmbMsg('Starting RMB…')
              try {
                const r = (await startRmb()) as {
                  ok?: boolean
                  error?: string
                  model_path?: string
                }
                setRmbMsg(r?.ok ? 'RMB running' : r?.error || 'Start failed')
                await refreshRmb()
                if (r?.ok) {
                  const st = await getRmbStatus().catch(() => null)
                  emitRmbChatModel(st, r?.model_path)
                }
                onSettingsSaved?.()
              } catch (e) {
                setRmbMsg(String(e))
              } finally {
                setRmbBusy(false)
              }
            }}
          >
            Start RMB
          </FormActionButton>
          <FormActionButton
            disabled={rmbBusy}
            onClick={async () => {
              setRmbBusy(true)
              try {
                await stopRmb()
                setRmbMsg('RMB stopped')
                await refreshRmb()
              } catch (e) {
                setRmbMsg(String(e))
              } finally {
                setRmbBusy(false)
              }
            }}
          >
            Stop
          </FormActionButton>
          <FormActionButton
            disabled={rmbBusy}
            onClick={async () => {
              setRmbBusy(true)
              setRmbMsg('Switching chat to RMB…')
              try {
                const r = (await applyRmbAsProvider()) as {
                  start?: { ok?: boolean; error?: string; model_path?: string }
                  status?: RmbStatus
                }
                setRmbMsg(
                  r?.start?.ok
                    ? 'Chat provider = RMB — ready for messages'
                    : r?.start?.error || 'Provider set; host may still be loading',
                )
                await refreshRmb()
                const st = await getRmbStatus().catch(() => null)
                emitRmbChatModel(st || r?.status, r?.start?.model_path)
                onSettingsSaved?.()
              } catch (e) {
                setRmbMsg(String(e))
              } finally {
                setRmbBusy(false)
              }
            }}
          >
            Use as chat provider
          </FormActionButton>
          <FormActionButton
            variant="ghost"
            disabled={rmbBusy}
            onClick={() => void refreshRmb()}
          >
            Refresh list
          </FormActionButton>
        </div>
        <FormHint>
          Selecting a GGUF restarts the host and switches chat to that model automatically.
          Stop only if you want the host off.
        </FormHint>
      </SettingsSection>

      {/* Local vision — SmolVLM2 image decode */}
      <SettingsSection
        {...sectionProps('vision')}
      >
        <FormHint>
          Local <strong style={{ color: 'var(--text-secondary)' }}>SmolVLM2 2.2B</strong>{' '}
          (Apache 2.0 · llama.cpp) — image understanding + local assist. Downloads with
          Remedy (~1.6 GB, one-time). Starts with Remedy. Not optional.
        </FormHint>
        {(vision?.warnings?.length || 0) > 0 && (
          <FormNotice tone="warn">
            {(vision?.warnings || []).map((w) => (
              <div key={w.slice(0, 48)}>{w}</div>
            ))}
          </FormNotice>
        )}
        <FormStatusCard>
          <FormStatusRow label="Model">
            {vision?.model?.name || 'SmolVLM2 2.2B'}
          </FormStatusRow>
          <FormStatusRow label="Status">
            {!vision
              ? '…'
              : vision.ready
                ? vision.running
                  ? 'Ready (running)'
                  : 'Ready (idle)'
                : vision.installed
                  ? vision.enabled
                    ? 'Installed'
                    : 'Installed · disabled'
                  : vision.progress?.phase === 'downloading' ||
                      vision.progress?.phase === 'extracting' ||
                      vision.progress?.phase === 'verifying'
                    ? `${vision.progress?.resumed ? 'Resuming…' : 'Installing…'} ${vision.progress?.message || ''}`
                    : vision.progress?.phase === 'cancelled'
                      ? 'Cancelled (resume available)'
                      : 'Not installed'}
          </FormStatusRow>
          {vision?.runtime_version ? (
            <FormStatusRow label="llama.cpp">{vision.runtime_version}</FormStatusRow>
          ) : null}
          {vision?.health?.cpu_runtime != null ? (
            <FormStatusRow label="Runtime">
              {vision.health.cpu_runtime ? 'CPU' : 'GPU/CUDA'}
              {vision.health.nvidia_detected ? ' · NVIDIA seen' : ''}
            </FormStatusRow>
          ) : null}
          {vision?.health?.ram_gb != null || vision?.health?.disk_free_gb != null ? (
            <FormStatusRow label="Resources">
              {vision.health?.ram_gb != null ? `RAM ~${vision.health.ram_gb} GB` : ''}
              {vision.health?.ram_gb != null && vision.health?.disk_free_gb != null
                ? ' · '
                : ''}
              {vision.health?.disk_free_gb != null
                ? `Disk free ~${vision.health.disk_free_gb} GB`
                : ''}
            </FormStatusRow>
          ) : null}
          {(vision?.progress?.phase === 'downloading'
            || vision?.progress?.phase === 'extracting'
            || vision?.progress?.phase === 'verifying') ? (
            <FormDownloadProgress
              label={
                vision.progress?.current_file
                  ? `${formatDownloadGb(vision.progress?.bytes_done)} / ${formatDownloadGb(vision.progress?.bytes_total)} · ${vision.progress.current_file}`
                  : vision.progress?.message
                    || `${formatDownloadGb(vision.progress?.bytes_done)} / ${formatDownloadGb(vision.progress?.bytes_total)}`
              }
              percent={
                (vision.progress?.bytes_total || 0) > 0
                  ? Math.round(
                      (100 * (vision.progress?.bytes_done || 0))
                        / (vision.progress?.bytes_total || 1),
                    )
                  : null
              }
            />
          ) : null}
        </FormStatusCard>
        {p.settingsMode === 'advanced' ? (
        <FormToggle
          checked={Boolean(vision?.enabled)}
          disabled={!vision?.installed || visionBusy}
          label="Enable for text-only chat models"
          description="When the provider cannot see images, decode locally into text."
          onChange={(on) => {
            void (async () => {
              setVisionBusy(true)
              setVisionMsg('')
              try {
                await updateSettings({ vision_enabled: on })
                await refreshVision()
                setVisionMsg(on ? 'Decoder enabled' : 'Decoder disabled')
              } catch (err) {
                setVisionMsg(err instanceof Error ? err.message : String(err))
              } finally {
                setVisionBusy(false)
              }
            })()
          }}
        />
        ) : null}
        {p.settingsMode === 'advanced' ? (
        <FormToggle
          checked={Boolean(vision?.force_decode)}
          disabled={!vision?.installed || !vision?.enabled || visionBusy}
          label="Prefer local decoder even if chat model has vision"
          description="Sends a short text brief to the provider instead of image pixels."
          onChange={(on) => {
            void (async () => {
              setVisionBusy(true)
              setVisionMsg('')
              try {
                await updateSettings({ vision_force_decode: on })
                await refreshVision()
                setVisionMsg(
                  on
                    ? 'Prefer local decoder even when the chat model has vision'
                    : 'Using provider vision when the model supports it',
                )
              } catch (err) {
                setVisionMsg(err instanceof Error ? err.message : String(err))
              } finally {
                setVisionBusy(false)
              }
            })()
          }}
        />
        ) : null}
        <div className="flex flex-wrap gap-1.5">
          {!vision?.installed ? (
            <>
              {(vision?.progress?.phase === 'downloading'
                || vision?.progress?.phase === 'extracting'
                || vision?.progress?.phase === 'verifying') ? (
                <FormHint>Downloading with Remedy…</FormHint>
              ) : (
              <FormActionButton
                variant="primary"
                disabled={visionBusy}
                onClick={() => {
                  void (async () => {
                    setVisionBusy(true)
                    setVisionMsg('Retrying local model download…')
                    try {
                      const preferCuda = Boolean(vision?.health?.nvidia_detected)
                      const r = await installVision({ prefer_cuda: preferCuda })
                      if (
                        r.mode === 'already_installed'
                        || r.mode === 'local_files'
                        || r.mode === 'bundled'
                      ) {
                        setVisionMsg(r.message || 'Local model ready — starts with Remedy')
                        setVisionBusy(false)
                      } else {
                        startVisionInstallPoll()
                        setVisionMsg(
                          r.message
                            || 'Downloading SmolVLM2 2.2B — server starts when finished.',
                        )
                      }
                      await refreshVision()
                    } catch (err) {
                      setVisionBusy(false)
                      setVisionMsg(err instanceof Error ? err.message : String(err))
                    }
                  })()
                }}
              >
                Retry download
              </FormActionButton>
              )}
              {p.settingsMode === 'advanced' ? (
              <FormActionButton
                disabled={visionBusy}
                onClick={() => {
                  void (async () => {
                    setVisionBusy(true)
                    setVisionMsg('Looking for existing files…')
                    try {
                      const r = await activateVisionBundle()
                      if (r.ok === false || r.error) {
                        setVisionMsg(
                          r.error || 'No local files found — retry the download.',
                        )
                      } else {
                        setVisionMsg(r.message || 'Activated — starts with Remedy')
                      }
                      await refreshVision()
                    } catch (err) {
                      setVisionMsg(err instanceof Error ? err.message : String(err))
                    } finally {
                      setVisionBusy(false)
                    }
                  })()
                }}
              >
                Use existing files
              </FormActionButton>
              ) : null}
              {(vision?.progress?.phase === 'downloading'
                || vision?.progress?.phase === 'extracting'
                || vision?.progress?.phase === 'verifying') && (
                <FormActionButton
                  onClick={() => {
                    void (async () => {
                      try {
                        await cancelVisionInstall()
                        setVisionMsg('Cancel requested…')
                        await refreshVision()
                      } catch (err) {
                        setVisionMsg(err instanceof Error ? err.message : String(err))
                      }
                    })()
                  }}
                >
                  Cancel
                </FormActionButton>
              )}
            </>
          ) : (
            <>
              {!vision.running ? (
                <FormActionButton
                  disabled={visionBusy}
                  onClick={() => {
                    void (async () => {
                      setVisionBusy(true)
                      try {
                        const r = await startVisionServer()
                        if (!r.ok) setVisionMsg(r.error || 'Start failed')
                        else setVisionMsg('Local server started')
                        await refreshVision()
                      } catch (err) {
                        setVisionMsg(err instanceof Error ? err.message : String(err))
                      } finally {
                        setVisionBusy(false)
                      }
                    })()
                  }}
                >
                  Start server
                </FormActionButton>
              ) : (
                <FormActionButton
                  disabled={visionBusy}
                  onClick={() => {
                    void (async () => {
                      setVisionBusy(true)
                      try {
                        await stopVisionServer()
                        setVisionMsg('Server stopped')
                        await refreshVision()
                      } catch (err) {
                        setVisionMsg(err instanceof Error ? err.message : String(err))
                      } finally {
                        setVisionBusy(false)
                      }
                    })()
                  }}
                >
                  Stop server
                </FormActionButton>
              )}
              {vision.health?.nvidia_detected && vision.health?.cpu_runtime ? (
                <FormActionButton
                  disabled={visionBusy}
                  onClick={() => {
                    void (async () => {
                      setVisionBusy(true)
                      setVisionMsg('Switching to CUDA runtime…')
                      try {
                        await reinstallVisionRuntime(true)
                        startVisionInstallPoll()
                      } catch (err) {
                        setVisionBusy(false)
                        setVisionMsg(err instanceof Error ? err.message : String(err))
                      }
                    })()
                  }}
                >
                  Use CUDA
                </FormActionButton>
              ) : null}
              {vision.health && !vision.health.cpu_runtime ? (
                <FormActionButton
                  disabled={visionBusy}
                  onClick={() => {
                    void (async () => {
                      setVisionBusy(true)
                      setVisionMsg('Switching to CPU runtime…')
                      try {
                        await reinstallVisionRuntime(false)
                        startVisionInstallPoll()
                      } catch (err) {
                        setVisionBusy(false)
                        setVisionMsg(err instanceof Error ? err.message : String(err))
                      }
                    })()
                  }}
                >
                  Use CPU
                </FormActionButton>
              ) : null}
            </>
          )}
          <FormActionButton variant="ghost" onClick={() => void refreshVision()}>
            Refresh
          </FormActionButton>
        </div>
        {visionMsg ? (
          <div className="text-[10px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
            {visionMsg}
          </div>
        ) : null}
        {onOpenHelp ? (
          <FormActionButton
            variant="ghost"
            className="mt-1"
            onClick={() => onOpenHelp('14-visual-decoder')}
          >
            Help: local vision
          </FormActionButton>
        ) : null}
      </SettingsSection>
    </>
  )
}
