/** Settings form sections — localModels. */
import type { ReactNode } from 'react'
import type { SettingsFormProps } from './formTypes'
import { SettingsSection } from '../SettingsSection'
import { FormHint } from './formUi'
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
} from '../../api/rmb'
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
                <strong style={{ color: 'var(--text-secondary)' }}>Remedy Muscle Bridge</strong>
                {' '}— on-device agent host for long coding sessions (llama.cpp). Context is automatic —
                Session Brief + harness prune/offload; you never manage it. While RMB is running,
                SmolVLM is <strong>unloaded</strong> until you stop RMB. Drop any GGUF in{' '}
                <code className="text-[9px]">~/.remedy/rmb/models/</code>.
              </FormHint>
              <div
                className="rounded-md px-2 py-1.5 mb-2 text-[10px] space-y-0.5"
                style={{
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
              >
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>Engine</span>
                  <span>{rmb?.engine || 'llama.cpp'}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>Model</span>
                  <span className="text-right truncate max-w-[60%]">
                    {rmb?.model?.name || rmb?.model_id || '—'}
                  </span>
                </div>
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>Status</span>
                  <span>
                    {!rmb
                      ? '…'
                      : rmb.ready
                        ? 'Ready'
                        : rmb.running
                          ? 'Starting…'
                          : rmb.model_present && rmb.runtime_present
                            ? 'Stopped'
                            : rmb.not_ready_hint || 'Not ready'}
                  </span>
                </div>
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>SmolVLM</span>
                  <span>
                    {rmb?.vision_suspended || rmb?.running
                      ? 'Suspended (RMB exclusive)'
                      : 'Available when RMB stops'}
                  </span>
                </div>
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>Endpoint</span>
                  <span className="font-mono text-[9px] truncate max-w-[60%]">
                    {rmb?.base_url || 'http://127.0.0.1:8787/v1'}
                  </span>
                </div>
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>Context</span>
                  <span>
                    {rmb?.ctx_size ?? 8192} tok · {rmb?.profile || 'agent'}
                    {rmb?.endless_session?.silent_context ? ' · auto memory' : ''}
                  </span>
                </div>
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>GPU</span>
                  <span>{rmb?.nvidia ? 'NVIDIA detected' : 'CPU / no NVIDIA'}</span>
                </div>
                {rmb?.model_path ? (
                  <div className="flex justify-between gap-2">
                    <span style={{ color: 'var(--text-muted)' }}>GGUF</span>
                    <span className="truncate max-w-[65%] text-right font-mono text-[9px]" title={rmb.model_path}>
                      {rmb.model_path.replace(/^.*[\\/]/, '')}
                    </span>
                  </div>
                ) : null}
              </div>
              {rmbMsg ? (
                <div className="text-[10px] mb-2" style={{ color: 'var(--text-secondary)' }}>
                  {rmbMsg}
                </div>
              ) : null}
              <div className="flex flex-wrap gap-1.5 mb-2">
                <button
                  type="button"
                  className="px-2 py-1 rounded text-[10px] font-medium"
                  style={{
                    background: 'var(--accent)',
                    color: 'var(--accent-fg, #fff)',
                    opacity: rmbBusy ? 0.6 : 1,
                  }}
                  disabled={rmbBusy}
                  onClick={async () => {
                    setRmbBusy(true)
                    setRmbMsg('Starting RMB…')
                    try {
                      const r = (await startRmb()) as { ok?: boolean; error?: string }
                      setRmbMsg(r?.ok ? 'RMB running' : r?.error || 'Start failed')
                      await refreshRmb()
                    } catch (e) {
                      setRmbMsg(String(e))
                    } finally {
                      setRmbBusy(false)
                    }
                  }}
                >
                  Start RMB
                </button>
                <button
                  type="button"
                  className="px-2 py-1 rounded text-[10px]"
                  style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
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
                </button>
                <button
                  type="button"
                  className="px-2 py-1 rounded text-[10px] font-medium"
                  style={{
                    border: '1px solid var(--accent)',
                    color: 'var(--accent)',
                    opacity: rmbBusy ? 0.6 : 1,
                  }}
                  disabled={rmbBusy}
                  onClick={async () => {
                    setRmbBusy(true)
                    setRmbMsg('Switching chat to RMB…')
                    try {
                      const r = (await applyRmbAsProvider()) as {
                        start?: { ok?: boolean; error?: string }
                      }
                      setRmbMsg(
                        r?.start?.ok
                          ? 'Chat provider set to RMB — start a new message'
                          : r?.start?.error || 'Configured; start may still be loading',
                      )
                      await refreshRmb()
                      onSettingsSaved?.()
                    } catch (e) {
                      setRmbMsg(String(e))
                    } finally {
                      setRmbBusy(false)
                    }
                  }}
                >
                  Use as chat provider
                </button>
                <button
                  type="button"
                  className="px-2 py-1 rounded text-[10px]"
                  style={{ border: '1px solid var(--border)', color: 'var(--text-muted)' }}
                  disabled={rmbBusy}
                  onClick={() => void refreshRmb()}
                >
                  Refresh
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5 mb-1">
                {(['agent', 'turbo', 'quality'] as const).map((pid) => (
                  <button
                    key={pid}
                    type="button"
                    className="px-2 py-0.5 rounded text-[10px] capitalize"
                    style={{
                      border: '1px solid var(--border)',
                      background:
                        (rmb?.profile || 'agent') === pid
                          ? 'color-mix(in srgb, var(--accent) 18%, transparent)'
                          : 'transparent',
                      color: 'var(--text-secondary)',
                    }}
                    disabled={rmbBusy}
                    onClick={async () => {
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
                    }}
                  >
                    {pid}
                  </button>
                ))}
              </div>
              {/* Model / GGUF / context — any model path */}
              <div className="mt-2 mb-1 space-y-1.5">
                {(rmb?.catalog?.models?.length ?? 0) > 0 ? (
                  <div>
                    <label className="block mb-1 text-[0.68rem] font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                      Catalog model
                    </label>
                    <select
                      className="ui-select w-full mb-2 text-[10px]"
                      disabled={rmbBusy}
                      value={rmb?.model_id || rmb?.catalog?.default_model_id || ''}
                      onChange={async (e) => {
                        const model_id = e.target.value
                        setRmbBusy(true)
                        try {
                          await patchRmbSettings({ model_id, enabled: true })
                          setRmbMsg(`Model: ${model_id}`)
                          await refreshRmb()
                        } catch (err) {
                          setRmbMsg(String(err))
                        } finally {
                          setRmbBusy(false)
                        }
                      }}
                    >
                      {(rmb?.catalog?.models || []).map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.name}
                          {m.approx_gb != null ? ` (~${m.approx_gb} GB)` : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                ) : null}
                {(rmb?.discovered_ggufs?.length ?? 0) > 0 ? (
                  <div>
                    <label className="block mb-1 text-[0.68rem] font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                      Discovered GGUF
                    </label>
                    <select
                      className="ui-select w-full mb-2 text-[10px] font-mono"
                      disabled={rmbBusy}
                      value={rmb?.model_path || ''}
                      onChange={async (e) => {
                        const model_path = e.target.value
                        setRmbBusy(true)
                        try {
                          await patchRmbSettings({ model_path, enabled: true })
                          setRmbMsg(`GGUF: ${model_path.replace(/^.*[\\/]/, '')}`)
                          await refreshRmb()
                        } catch (err) {
                          setRmbMsg(String(err))
                        } finally {
                          setRmbBusy(false)
                        }
                      }}
                    >
                      <option value="">— pick file —</option>
                      {(rmb?.discovered_ggufs || []).map((g) => (
                        <option key={g.path || g.name} value={g.path || ''}>
                          {g.name}
                          {g.size_gb != null ? ` (${g.size_gb} GB)` : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                ) : null}
                <div>
                  <label className="block mb-1 text-[0.68rem] font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                    Model path (any .gguf)
                  </label>
                  <input
                    type="text"
                    className="ui-select w-full mb-2 text-[10px] font-mono"
                    disabled={rmbBusy}
                    defaultValue={rmb?.model_path || ''}
                    key={rmb?.model_path || 'rmb-path'}
                    placeholder="C:\\…\\model.gguf or leave blank to auto-discover"
                    onBlur={async (e) => {
                      const model_path = e.target.value.trim()
                      if (model_path === (rmb?.model_path || '')) return
                      setRmbBusy(true)
                      try {
                        await patchRmbSettings({
                          model_path: model_path || '',
                          enabled: true,
                        })
                        setRmbMsg(model_path ? 'Model path saved' : 'Path cleared')
                        await refreshRmb()
                      } catch (err) {
                        setRmbMsg(String(err))
                      } finally {
                        setRmbBusy(false)
                      }
                    }}
                  />
                </div>
                <div className="flex gap-2">
                  <div className="flex-1">
                    <label className="block mb-1 text-[0.68rem] font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                      Context size
                    </label>
                    <select
                      className="ui-select w-full mb-2 text-[10px]"
                      disabled={rmbBusy}
                      value={String(rmb?.ctx_size ?? 8192)}
                      onChange={async (e) => {
                        const ctx_size = parseInt(e.target.value, 10)
                        setRmbBusy(true)
                        try {
                          await patchRmbSettings({ ctx_size, enabled: true })
                          setRmbMsg(`Context: ${ctx_size}`)
                          await refreshRmb()
                        } catch (err) {
                          setRmbMsg(String(err))
                        } finally {
                          setRmbBusy(false)
                        }
                      }}
                    >
                      {[4096, 8192, 12288, 16384, 32768].map((n) => (
                        <option key={n} value={n}>
                          {n}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="flex-1">
                    <label className="block mb-1 text-[0.68rem] font-semibold uppercase tracking-wide" style={{ color: 'var(--text-muted)' }}>
                      GPU layers (−1 = all)
                    </label>
                    <input
                      type="number"
                      className="ui-select w-full mb-2 text-[10px]"
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
              </div>
              <FormHint>
                Put a coding GGUF (e.g. Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf) in{' '}
                <code className="text-[9px]">~/.remedy/rmb/models/</code> or paste a full path above.
                Restart RMB after changing model/ctx. Install Local vision once if llama-server is
                missing (shared runtime).
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
                <div
                  className="rounded-md px-2 py-1.5 mb-2 text-[10px] space-y-1"
                  style={{
                    background: 'color-mix(in srgb, #f59e0b 12%, var(--bg-primary))',
                    border: '1px solid var(--border)',
                    color: 'var(--text-secondary)',
                  }}
                >
                  {(vision?.warnings || []).map((w) => (
                    <div key={w.slice(0, 48)}>{w}</div>
                  ))}
                </div>
              )}
              <div
                className="rounded-md px-2 py-1.5 mb-2 text-[10px] space-y-0.5"
                style={{
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
              >
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>Model</span>
                  <span>{vision?.model?.name || 'SmolVLM2 2.2B'}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>Status</span>
                  <span>
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
                  </span>
                </div>
                {vision?.runtime_version ? (
                  <div className="flex justify-between gap-2">
                    <span style={{ color: 'var(--text-muted)' }}>llama.cpp</span>
                    <span>{vision.runtime_version}</span>
                  </div>
                ) : null}
                {vision?.health?.cpu_runtime != null ? (
                  <div className="flex justify-between gap-2">
                    <span style={{ color: 'var(--text-muted)' }}>Runtime</span>
                    <span>
                      {vision.health.cpu_runtime ? 'CPU' : 'GPU/CUDA'}
                      {vision.health.nvidia_detected ? ' · NVIDIA seen' : ''}
                    </span>
                  </div>
                ) : null}
                {vision?.health?.ram_gb != null || vision?.health?.disk_free_gb != null ? (
                  <div className="flex justify-between gap-2">
                    <span style={{ color: 'var(--text-muted)' }}>Resources</span>
                    <span>
                      {vision.health?.ram_gb != null ? `RAM ~${vision.health.ram_gb} GB` : ''}
                      {vision.health?.ram_gb != null && vision.health?.disk_free_gb != null
                        ? ' · '
                        : ''}
                      {vision.health?.disk_free_gb != null
                        ? `Disk free ~${vision.health.disk_free_gb} GB`
                        : ''}
                    </span>
                  </div>
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
              </div>
              <label className="flex items-start gap-2 mb-2 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={Boolean(vision?.enabled)}
                  disabled={!vision?.installed || visionBusy}
                  onChange={(e) => {
                    const on = e.target.checked
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
                <span>
                  <span className="block" style={{ color: 'var(--text-primary)' }}>
                    Enable for text-only chat models
                  </span>
                  <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                    When the provider cannot see images, decode locally into text.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2 mb-2 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={Boolean(vision?.force_decode)}
                  disabled={!vision?.installed || !vision?.enabled || visionBusy}
                  onChange={(e) => {
                    const on = e.target.checked
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
                <span>
                  <span className="block" style={{ color: 'var(--text-primary)' }}>
                    Prefer local decoder even if chat model has vision
                  </span>
                  <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                    Sends a short text brief to the provider instead of image pixels — usually
                    fewer tokens and lower cost. Falls back to provider vision if the decoder
                    is not ready.
                  </span>
                </span>
              </label>
              <div className="flex flex-wrap gap-1.5">
                {!vision?.installed ? (
                  <>
                    <button
                      type="button"
                      disabled={visionBusy}
                      className="px-2 py-1 rounded text-[10px] font-medium"
                      style={{ background: 'var(--accent)', color: '#fff' }}
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
                    </button>
                    <button
                      type="button"
                      disabled={visionBusy}
                      className="px-2 py-1 rounded text-[10px]"
                      style={{
                        background: 'var(--bg-tertiary)',
                        color: 'var(--text-muted)',
                        border: '1px solid var(--border)',
                      }}
                      title="If files already exist under ~/.remedy/vision"
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
                    </button>
                    {(vision?.progress?.phase === 'downloading'
                      || vision?.progress?.phase === 'extracting'
                      || vision?.progress?.phase === 'verifying') && (
                      <button
                        type="button"
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-primary)',
                          border: '1px solid var(--border)',
                        }}
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
                      </button>
                    )}
                  </>
                ) : (
                  <>
                    {!vision.running ? (
                      <button
                        type="button"
                        disabled={visionBusy}
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-primary)',
                          border: '1px solid var(--border)',
                        }}
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
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={visionBusy}
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-primary)',
                          border: '1px solid var(--border)',
                        }}
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
                      </button>
                    )}
                    {vision.health?.nvidia_detected && vision.health?.cpu_runtime ? (
                      <button
                        type="button"
                        disabled={visionBusy}
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-primary)',
                          border: '1px solid var(--border)',
                        }}
                        title="Use CUDA llama-server (same SmolVLM2 weights)"
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
                      </button>
                    ) : null}
                    {vision.health && !vision.health.cpu_runtime ? (
                      <button
                        type="button"
                        disabled={visionBusy}
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-muted)',
                          border: '1px solid var(--border)',
                        }}
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
                      </button>
                    ) : null}
                  </>
                )}
                <button
                  type="button"
                  className="px-2 py-1 rounded text-[10px]"
                  style={{ color: 'var(--text-muted)' }}
                  onClick={() => void refreshVision()}
                >
                  Refresh
                </button>
              </div>
              {visionMsg ? (
                <div className="text-[10px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
                  {visionMsg}
                </div>
              ) : null}
              {onOpenHelp ? (
                <button
                  type="button"
                  className="text-[10px] mt-1 underline"
                  style={{ color: 'var(--accent)' }}
                  onClick={() => onOpenHelp('14-visual-decoder')}
                >
                  Help: local vision
                </button>
              ) : null}
            </SettingsSection>

            {/* Memory Harness */}
    </>
  )
}
