/**
 * Per-session LLM binds + model list refresh for the status bar / send path.
 * Extracted from App.tsx so tab switching and multi-provider state stay testable.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  fetchModels,
  pickDefaultModel,
  type DiscoveryStatus,
  type ModelSource,
} from '../api/modelDiscovery'
import {
  listConnectedProviders,
  pickerFromConnectedResponse,
  setSessionLlm as applySessionLlm,
  type ConnectedProvider,
} from '../api/providers'
import { updateSettings } from '../api/settings'
import type { ChatSession } from '../types'

export interface ModelInfo {
  id: string
  name: string
  provider: string
  default: boolean
  /** 'endpoint' = listed live by the provider; 'catalog' = backend default row. */
  source?: ModelSource
}

export interface SessionLlmBind {
  provider: string
  model: string
}

export function useSessionLlm(opts: {
  activeId: string | null
  sessions: ChatSession[]
  streaming: boolean
  /** Any tab streaming (not only the focused one). */
  runningCount?: number
  /** Local API is up — hydrate the status-bar picker (not only Settings save). */
  apiReady?: boolean
}) {
  const { activeId, sessions, streaming, runningCount = 0, apiReady = false } = opts

  // Unknown until settings / session binds load — never a seeded provider/model.
  const [model, setModel] = useState('')
  const [llmProvider, setLlmProvider] = useState('')
  const [models, setModels] = useState<ModelInfo[]>([])
  /** Last `/models` failure (network or discovery); the bar shows it as a hint. */
  const [modelsError, setModelsError] = useState<string | null>(null)
  const [modelsDiscovery, setModelsDiscovery] = useState<DiscoveryStatus | null>(null)
  const [connectedProviders, setConnectedProviders] = useState<ConnectedProvider[]>(
    [],
  )
  const [sessionLlmMap, setSessionLlmMap] = useState<
    Record<string, SessionLlmBind>
  >({})
  const [switchToast, setSwitchToast] = useState<string | null>(null)
  const activeIdRef = useRef(activeId)
  activeIdRef.current = activeId
  const pendingSwitches = useRef(new Set<string>())
  const [switchingKeys, setSwitchingKeys] = useState<ReadonlySet<string>>(new Set())
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current) }, [])

  /**
   * Authoritative per-session LLM binds. Status bar + send() read this only.
   * Never clobber an existing entry from session-list polls.
   */
  const setSessionBind = useCallback(
    (sessionId: string, provider: string, modelId: string) => {
      const p = (provider || '').trim()
      const m = (modelId || '').trim()
      if (!sessionId || !p || !m) return
      setSessionLlmMap((prev) => {
        const cur = prev[sessionId]
        if (cur?.provider === p && cur?.model === m) return prev
        return { ...prev, [sessionId]: { provider: p, model: m } }
      })
      if (sessionId === activeIdRef.current) {
        setLlmProvider(p)
        setModel(m)
      }
    },
    [],
  )

  // Seed map from server for tabs that have never been bound locally.
  useEffect(() => {
    setSessionLlmMap((prev) => {
      let changed = false
      const next = { ...prev }
      for (const s of sessions) {
        if (!s.llm_provider || !s.model) continue
        if (next[s.id]?.provider && next[s.id]?.model) continue
        next[s.id] = { provider: s.llm_provider, model: s.model }
        changed = true
      }
      return changed ? next : prev
    })
  }, [sessions])

  // On tab switch only: load that tab's bind into bar state (no poll thrash).
  useEffect(() => {
    if (!activeId) return
    const ov = sessionLlmMap[activeId]
    if (ov?.provider && ov?.model) {
      setLlmProvider(ov.provider)
      setModel(ov.model)
      return
    }
    const sess = sessions.find((s) => s.id === activeId)
    if (sess?.llm_provider && sess?.model) {
      setSessionBind(activeId, String(sess.llm_provider), String(sess.model))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tab change only
  }, [activeId])

  const barProvider = useMemo(() => {
    if (activeId && sessionLlmMap[activeId]?.provider) {
      return sessionLlmMap[activeId]!.provider
    }
    return llmProvider
  }, [activeId, sessionLlmMap, llmProvider])

  const barModel = useMemo(() => {
    if (activeId && sessionLlmMap[activeId]?.model) {
      return sessionLlmMap[activeId]!.model
    }
    return model
  }, [activeId, sessionLlmMap, model])

  const activeProviderRef = useRef(barProvider || llmProvider)
  activeProviderRef.current = barProvider || llmProvider
  const modelRequests = useRef(new Map<string, number>())

  /** Refresh model list via GET /models[?provider=…] (live endpoint discovery). */
  const refreshModels = useCallback(
    async (opts?: { selectDefault?: boolean; provider?: string }) => {
      const want = (opts?.provider || llmProvider || '').toLowerCase()
      const request = (modelRequests.current.get(want) ?? 0) + 1
      modelRequests.current.set(want, request)
      const sessionAtStart = activeIdRef.current
      try {
        const data = await fetchModels(want || undefined)
        if (modelRequests.current.get(want) !== request) return null
        const activeProv = data.provider || want
        const list: ModelInfo[] = data.models.map((m) => ({
          id: m.id,
          name: m.name,
          provider: m.provider || activeProv,
          default: Boolean(m.default),
          source: m.source,
        }))
        setModels((prev) => {
          const others = prev.filter((m) => m.provider && m.provider !== activeProv)
          return [...list, ...others]
        })
        const disc = data.discovery || null
        if (activeProviderRef.current.toLowerCase() === want) {
          setModelsDiscovery(disc)
          setModelsError(
            disc && disc.attempted && !disc.ok
              ? `Couldn't list models from ${disc.url || activeProv}: ${
                  disc.error || (disc.status != null ? `HTTP ${disc.status}` : 'no response')
                }`
              : null,
          )
        }
        if (opts?.selectDefault && activeIdRef.current === sessionAtStart && activeProviderRef.current.toLowerCase() === want) {
          const next = pickDefaultModel('', list, data.default)
          if (next) setModel(next)
        }
        return { ...data, models: list }
      } catch (e: unknown) {
        // Keep the prior list; surface the failure instead of only logging it.
        const msg = e instanceof Error ? e.message : String(e)
        if (modelRequests.current.get(want) === request && activeProviderRef.current.toLowerCase() === want) {
          setModelsError(`Model list unavailable${want ? ` for ${want}` : ''}: ${msg}`)
        }
        console.warn('Model refresh failed:', msg)
        return null
      }
    },
    [llmProvider],
  )

  const showSwitchToast = useCallback((toast: string | null | undefined) => {
    if (!toast) return
    setSwitchToast(toast)
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setSwitchToast(null), 4200)
  }, [])

  const refreshConnected = useCallback(async () => {
    try {
      const conn = await listConnectedProviders()
      setConnectedProviders(pickerFromConnectedResponse(conn))
      return conn
    } catch {
      return null
    }
  }, [])

  const onProviderModelChange = useCallback(
    (prov: string, mid: string) => {
      if (streaming || !prov) return
      const sessionId = activeId
      const key = sessionId ?? '__default__'
      if (pendingSwitches.current.has(key)) {
        showSwitchToast('Finishing the previous model change…')
        return
      }
      pendingSwitches.current.add(key)
      setSwitchingKeys(new Set(pendingSwitches.current))
      const modelId = (mid || '').trim()
      void (async () => {
        try {
          if (sessionId) {
            const result = await applySessionLlm(sessionId, prov, modelId || undefined, false)
            // Only confirmed binds enter the send path. A late result updates its
            // own session cache; setSessionBind never changes another tab's bar.
            setSessionBind(sessionId, result.provider || prov, result.model || modelId)
            if (activeIdRef.current === sessionId) showSwitchToast(result.toast || 'Model updated')
          } else {
            const result = await updateSettings({
              llm_provider: prov,
              ...(modelId ? { llm_model: modelId } : {}),
            })
            if (activeIdRef.current === null) {
              setLlmProvider(result.llm_provider || prov)
              setModel(result.llm_model || modelId)
              showSwitchToast('Default model updated')
            }
          }
          void refreshModels({ provider: prov })
          void refreshConnected()
        } catch (error: unknown) {
          if (activeIdRef.current === sessionId) {
            const message = error instanceof Error ? error.message : String(error)
            showSwitchToast(message || 'Could not confirm the model change. Check the connection and retry.')
          }
        } finally {
          pendingSwitches.current.delete(key)
          setSwitchingKeys(new Set(pendingSwitches.current))
        }
      })()
    },
    [streaming, activeId, setSessionBind, refreshModels, refreshConnected, showSwitchToast],
  )

  const onModelChange = useCallback(
    (id: string) => onProviderModelChange(barProvider || llmProvider, id),
    [barProvider, llmProvider, onProviderModelChange],
  )


  const refreshModelsRef = useRef(refreshModels)
  refreshModelsRef.current = refreshModels

  // Status-bar picker is hidden while connectedProviders is empty. Bootstrap
  // used to latch didBoot before this fetch, so StrictMode cancel / a late
  // token left the bar dead until Settings save called refreshConnected.
  useEffect(() => {
    if (!apiReady) return
    let cancelled = false
    void (async () => {
      for (let i = 0; i < 4; i++) {
        if (cancelled) return
        const conn = await refreshConnected()
        if (cancelled) return
        const rows = pickerFromConnectedResponse(conn)
        if (rows.length > 0) {
          const p = (conn?.active_provider || '').trim()
          if (p) void refreshModelsRef.current({ provider: p })
          return
        }
        await new Promise((r) => window.setTimeout(r, 300 * (i + 1)))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [apiReady, refreshConnected])

  // Session bind may differ from Settings default — list that provider's models
  // without waiting for a Settings reload.
  const fetchedModelsFor = useRef('')
  useEffect(() => {
    if (!apiReady) return
    const p = (barProvider || llmProvider || '').trim()
    if (!p || fetchedModelsFor.current === p) return
    fetchedModelsFor.current = p
    setModelsError(null)
    setModelsDiscovery(null)
    void refreshModels({ provider: p })
  }, [apiReady, barProvider, llmProvider, refreshModels])

  // RMB settings / GGUF load → keep status bar + session bind on the same stem
  useEffect(() => {
    const onRmb = (ev: Event) => {
      const d = (ev as CustomEvent<{ stem?: string; path?: string }>).detail
      const stem = (d?.stem || '').trim()
      if (!stem) return
      if (streaming || runningCount > 0) {
        showSwitchToast(`Loaded ${stem} — apply after this turn`)
        return
      }
      const onRmbAlready =
        (barProvider || llmProvider || '').toLowerCase() === 'rmb'
      if (activeId && !onRmbAlready) {
        // Do not PUT global llm_* — that steals new chats / Settings default.
        showSwitchToast(`RMB loaded ${stem} — switch provider to use it`)
        return
      }
      onProviderModelChange('rmb', stem)
    }
    window.addEventListener('remedy:rmb-model-changed', onRmb)
    return () => window.removeEventListener('remedy:rmb-model-changed', onRmb)
  }, [
    activeId,
    streaming,
    runningCount,
    barProvider,
    llmProvider,
    setSessionBind,
    refreshModels,
    refreshConnected,
    showSwitchToast,
    onProviderModelChange,
  ])

  // While on RMB, periodically adopt the host's Loaded GGUF (no user action)
  useEffect(() => {
    if ((barProvider || llmProvider) !== 'rmb') return
    let cancelled = false
    const tick = async () => {
      if (streaming) return
      try {
        const { getRmbStatus } = await import('../api/rmb')
        const st = await getRmbStatus()
        if (cancelled || !st) return
        const stem =
          (st.chat_model || st.llm_model || '').trim()
          || (st.model_path
            ? st.model_path.replace(/^.*[\\/]/, '').replace(/\.gguf$/i, '')
            : '')
        if (!stem) return
        // Status display only — never rewrite session bind without apply.
        if (stem !== (barModel || model)) {
          setLlmProvider('rmb')
          setModel(stem)
          void refreshConnected()
        }
      } catch {
        /* offline */
      }
    }
    void tick()
    const id = window.setInterval(() => void tick(), 8000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [
    barProvider,
    llmProvider,
    barModel,
    model,
    activeId,
    streaming,
    refreshConnected,
  ])


  return {
    modelSwitching: switchingKeys.has(activeId ?? '__default__'),
    model,
    setModel,
    llmProvider,
    setLlmProvider,
    models,
    setModels,
    modelsError,
    modelsDiscovery,
    connectedProviders,
    setConnectedProviders,
    sessionLlmMap,
    setSessionLlmMap,
    setSessionBind,
    barProvider,
    barModel,
    switchToast,
    setSwitchToast,
    refreshModels,
    refreshConnected,
    onProviderModelChange,
    onModelChange,
    showSwitchToast,
  }
}
