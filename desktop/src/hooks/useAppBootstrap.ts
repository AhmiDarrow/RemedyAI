/**
 * First-load bootstrap when the local API is ready:
 * settings hydrate, connected providers, models/agents, first-run wizard.
 */

import { useEffect, type Dispatch, type SetStateAction } from 'react'
import { listAgents, listCommands } from '../api/messages'
import { listConnectedProviders, type ConnectedProvider } from '../api/providers'
import { getSettings } from '../api/settings'
import type { ThinkingLevel, ApprovalMode } from '../components/StatusBar'
import { normalizeToolProcess, type ToolProcessMode } from '../utils/toolLabels'
import type { ModelInfo } from './useSessionLlm'

type ServerState = 'connecting' | 'ready' | 'error'

export function useAppBootstrap(opts: {
  serverState: ServerState
  refreshSessions: () => Promise<unknown>
  refreshModels: (opts?: {
    selectDefault?: boolean
    provider?: string
  }) => Promise<{ default?: string; models?: ModelInfo[] } | null>
  setModel: (m: string) => void
  setLlmProvider: (p: string) => void
  setConnectedProviders: Dispatch<SetStateAction<ConnectedProvider[]>>
  setThinkingLevel: Dispatch<SetStateAction<ThinkingLevel>>
  setApprovalMode: Dispatch<SetStateAction<ApprovalMode>>
  setPrivacyMode: Dispatch<SetStateAction<boolean>>
  setToolProcessMode: Dispatch<SetStateAction<ToolProcessMode>>
  setUserName: Dispatch<SetStateAction<string>>
  setPartnerName: Dispatch<SetStateAction<string>>
  setAppVersion: Dispatch<SetStateAction<string>>
  setAskUserName: Dispatch<SetStateAction<boolean>>
  setShowSetupWizard: Dispatch<SetStateAction<boolean>>
  setServerError: Dispatch<SetStateAction<string>>
  setAgentDefs: Dispatch<
    SetStateAction<{ name: string; description: string }[]>
  >
}) {
  const {
    serverState,
    refreshSessions,
    refreshModels,
    setModel,
    setLlmProvider,
    setConnectedProviders,
    setThinkingLevel,
    setApprovalMode,
    setPrivacyMode,
    setToolProcessMode,
    setUserName,
    setPartnerName,
    setAppVersion,
    setAskUserName,
    setShowSetupWizard,
    setServerError,
    setAgentDefs,
  } = opts

  useEffect(() => {
    if (serverState !== 'ready') return
    let cancelled = false
    ;(async () => {
      try {
        const { ensureApiToken } = await import('../api/client')
        await ensureApiToken()
      } catch {
        /* continue — settings may still work offline later */
      }
      if (cancelled) return

      let settings: Awaited<ReturnType<typeof getSettings>> | null = null
      const sessionsPromise = refreshSessions()
      try {
        settings = await getSettings()
      } catch (e: unknown) {
        console.warn('getSettings failed, retrying auth:', e)
        try {
          const { clearApiToken, ensureApiToken } = await import('../api/client')
          clearApiToken()
          await ensureApiToken()
          settings = await getSettings()
        } catch (e2: unknown) {
          console.warn('getSettings retry failed:', e2)
        }
      }
      if (cancelled) return
      try {
        await sessionsPromise
      } catch {
        /* refresh already swallows */
      }
      if (cancelled) return

      if (!settings) {
        setShowSetupWizard(true)
        setServerError(
          'Could not load settings yet — complete setup once the local server is ready. '
            + 'If save fails, use Retry then Open setup.',
        )
        return
      }

      const needsWizard =
        Boolean(settings.needs_setup)
        || settings.setup_completed === false
        || settings.config_exists === false

      if (needsWizard) {
        setShowSetupWizard(true)
      }

      if (settings.llm_model) setModel(settings.llm_model)
      if (settings.llm_provider) setLlmProvider(settings.llm_provider)
      try {
        const conn = await listConnectedProviders()
        setConnectedProviders(
          conn.picker?.length ? conn.picker : conn.connected || [],
        )
        if (conn.active_provider) setLlmProvider(conn.active_provider)
        if (conn.active_model) setModel(conn.active_model)
      } catch {
        /* picker falls back to models-only */
      }
      const tl = String(settings.thinking_level || 'high').toLowerCase()
      if (tl === 'off' || tl === 'low' || tl === 'medium' || tl === 'high') {
        setThinkingLevel(tl)
      }
      const am = String(settings.approval_mode || 'ask').toLowerCase()
      if (am === 'ask' || am === 'auto') setApprovalMode(am)
      setPrivacyMode(Boolean(settings.privacy_mode))
      setToolProcessMode(
        normalizeToolProcess(settings.tool_process ?? settings.show_tool_calls),
      )
      const un = (settings.user_name || '').trim()
      setUserName(un)
      const pn = (settings.name || '').trim()
      if (pn) setPartnerName(pn)
      if (settings.version) setAppVersion(String(settings.version))
      if (!needsWizard && !un) {
        try {
          const skipped = localStorage.getItem('remedy.userName.skipped')
          if (!skipped) setAskUserName(true)
        } catch {
          setAskUserName(true)
        }
      }

      try {
        const [modelsData, agents] = await Promise.all([
          refreshModels({ selectDefault: !settings?.llm_model }),
          listAgents().catch(() => null),
        ])
        if (cancelled) return
        if (agents) {
          setAgentDefs(
            Array.isArray(agents)
              ? agents
              : (agents as { agents?: { name: string; description: string }[] })
                  .agents || [],
          )
        }
        if (!settings?.llm_model && modelsData?.default) {
          setModel(modelsData.default)
        }
        if (settings?.llm_model) {
          setModel(settings.llm_model)
        }
        void listCommands().catch(() => null)
      } catch (e: unknown) {
        console.warn('Secondary startup load failed:', e)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [
    serverState,
    refreshModels,
    refreshSessions,
    setModel,
    setLlmProvider,
    setConnectedProviders,
    setThinkingLevel,
    setApprovalMode,
    setPrivacyMode,
    setToolProcessMode,
    setUserName,
    setPartnerName,
    setAppVersion,
    setAskUserName,
    setShowSetupWizard,
    setServerError,
    setAgentDefs,
  ])
}
