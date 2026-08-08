/**
 * Per-session LLM binds + model list refresh for the status bar / send path.
 * Extracted from App.tsx so tab switching and multi-provider state stay testable.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiFetch } from '../api/client'
import {
  listConnectedProviders,
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
}

export interface SessionLlmBind {
  provider: string
  model: string
}

export function useSessionLlm(opts: {
  activeId: string | null
  sessions: ChatSession[]
  streaming: boolean
}) {
  const { activeId, sessions, streaming } = opts

  const [model, setModel] = useState('gpt-4o-mini')
  const [llmProvider, setLlmProvider] = useState('openai')
  const [models, setModels] = useState<ModelInfo[]>([])
  const [connectedProviders, setConnectedProviders] = useState<ConnectedProvider[]>(
    [],
  )
  const [sessionLlmMap, setSessionLlmMap] = useState<
    Record<string, SessionLlmBind>
  >({})
  const [switchToast, setSwitchToast] = useState<string | null>(null)

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
      if (sessionId === activeId) {
        setLlmProvider(p)
        setModel(m)
      }
    },
    [activeId],
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

  /** Refresh model list via GET /models[?provider=…] (live endpoint discovery). */
  const refreshModels = useCallback(
    async (opts?: { selectDefault?: boolean; provider?: string }) => {
      try {
        const q = opts?.provider
          ? `?provider=${encodeURIComponent(opts.provider)}`
          : ''
        const data = await apiFetch<{
          models: ModelInfo[]
          default: string
          provider?: string
        }>(`/models${q}`)
        let list = data.models || []
        const activeProv = (
          data.provider ||
          opts?.provider ||
          llmProvider ||
          ''
        ).toLowerCase()
        if (activeProv === 'demo') {
          const { demoModelOptions, isDemoModelAllowed, DEMO_DEFAULT_MODEL } =
            await import('../utils/demoModels')
          list = demoModelOptions(list).map((m) => ({
            id: m.id,
            name: m.name,
            provider: 'demo',
            default: Boolean((m as ModelInfo).default),
          }))
          setModels(list)
          if (opts?.selectDefault) {
            const def =
              list.find((m) => m.id === data.default && isDemoModelAllowed(m.id)) ??
              list.find((m) => m.id === DEMO_DEFAULT_MODEL) ??
              list[0]
            if (def) setModel(def.id)
          }
          return { ...data, models: list, provider: 'demo' }
        }
        setModels((prev) => {
          const others = prev.filter((m) => m.provider && m.provider !== activeProv)
          const tagged = list.map((m) => ({
            ...m,
            provider: m.provider || activeProv,
          }))
          return [...tagged, ...others]
        })
        if (opts?.selectDefault) {
          const def = list.find((m) => m.id === data.default) ?? list[0]
          if (def) setModel(def.id)
        }
        return { ...data, models: list }
      } catch (e: unknown) {
        console.warn('Model refresh failed:', e instanceof Error ? e.message : e)
        return null
      }
    },
    [llmProvider],
  )

  const showSwitchToast = useCallback((toast: string | null | undefined) => {
    if (!toast) return
    setSwitchToast(toast)
    window.setTimeout(() => setSwitchToast(null), 4200)
  }, [])

  const onProviderModelChange = useCallback(
    (prov: string, mid: string) => {
      if (streaming) return
      if (!activeId) {
        setLlmProvider(prov)
        setModel(mid)
        void updateSettings({ llm_provider: prov, llm_model: mid }).catch(() => {})
        return
      }
      setSessionBind(activeId, prov, mid)
      void applySessionLlm(activeId, prov, mid, false)
        .then((r) => showSwitchToast(r.toast))
        .catch(() => {})
      void refreshModels({ provider: prov }).catch(() => {})
      void listConnectedProviders()
        .then((conn) => {
          setConnectedProviders(
            conn.picker?.length ? conn.picker : conn.connected || [],
          )
        })
        .catch(() => {})
    },
    [streaming, activeId, setSessionBind, refreshModels, showSwitchToast],
  )

  const onModelChange = useCallback(
    (id: string) => {
      if (!activeId) {
        setModel(id)
        void updateSettings({ llm_model: id }).catch(() => {})
        return
      }
      const prov = barProvider || llmProvider
      setSessionBind(activeId, prov, id)
      void applySessionLlm(activeId, prov, id, false)
        .then((r) => showSwitchToast(r.toast))
        .catch(() => {})
    },
    [activeId, barProvider, llmProvider, setSessionBind, showSwitchToast],
  )

  return {
    model,
    setModel,
    llmProvider,
    setLlmProvider,
    models,
    setModels,
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
    onProviderModelChange,
    onModelChange,
    showSwitchToast,
  }
}
