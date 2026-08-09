/** Settings form sections — localModels. */
import type { ReactNode } from 'react'
import type { SettingsFormProps } from './formTypes'
import { SettingsSection } from '../SettingsSection'
import {
  FormActionButton,
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
            {rmb?.ctx_size ?? 8192} tok · {rmb?.profile || 'agent'}
            {rmb?.nvidia ? ' · NVIDIA' : ' · CPU'}
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

        <FormSegmented
          value={((rmb?.profile || 'agent') as 'agent' | 'turbo' | 'quality')}
          options={[
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
                setRmbMsg(`Profile: ${pid}`)
                await refreshRmb()
              } catch (e) {
                setRmbMsg(String(e))
              } finally {
                setRmbBusy(false)
              }
            })()
          }}
        />
        <div className="flex gap-2 mt-2">
          <div className="flex-1 min-w-0">
            <FormLabel>Context size</FormLabel>
            <FormSelect
              size="sm"
              disabled={rmbBusy}
              value={String(rmb?.ctx_size ?? 8192)}
              options={[4096, 8192, 12288, 16384, 32768].map((n) => ({
                value: String(n),
                label: String(n),
              }))}
              onChange={(v) => {
                const ctx_size = parseInt(v, 10)
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
          (Apache 2.0 · llama.cpp) — image understanding + local assist. Required dependency
          when your chat model is text-only. One-time download (~1.6 GB). Starts with Remedy.
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
          {(vision?.progress?.bytes_total || 0) > 0 &&
          (vision?.progress?.phase === 'downloading' ||
            vision?.progress?.phase === 'extracting') ? (
            <div className="mt-1">
              <div
                className="h-1 rounded overflow-hidden"
                style={{ background: 'var(--bg-primary)' }}
              >
                <div
                  className="h-full rounded"
                  style={{
                    width: `${Math.min(
                      100,
                      Math.round(
                        (100 * (vision.progress?.bytes_done || 0)) /
                          (vision.progress?.bytes_total || 1),
                      ),
                    )}%`,
                    background: 'var(--accent)',
                  }}
                />
              </div>
              <div className="mt-0.5" style={{ color: 'var(--text-muted)' }}>
                {formatDownloadGb(vision.progress?.bytes_done)} /{' '}
                {formatDownloadGb(vision.progress?.bytes_total)}
                {vision.progress?.current_file
                  ? ` · ${vision.progress.current_file}`
                  : ''}
              </div>
            </div>
          ) : null}
        </FormStatusCard>
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
        <div className="flex flex-wrap gap-1.5">
          {!vision?.installed ? (
            <>
              <FormActionButton
                variant="primary"
                disabled={visionBusy}
                onClick={() => {
                  void (async () => {
                    setVisionBusy(true)
                    setVisionMsg('Downloading pinned local model…')
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
                Download &amp; install local model
              </FormActionButton>
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
                          r.error || 'No local files found — use Download & install.',
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
