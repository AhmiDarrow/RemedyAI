/** Shared props for Settings form sections. */
import type { Dispatch, SetStateAction } from 'react'
import type { Settings, MessengerInfo } from '../../api/settings'
import type { VisionStatus, NanoSwarmStatus } from '../../api/vision'
import type { RmbStatus } from '../../api/rmb'
import type { XaiAuthStatus } from '../../api/auth'
import type { ProviderInfo, ConnectedProvider } from '../../api/providers'
import type { ThemeId } from '../../themes'
import type { UpdateInfo } from '../../api/updates'
import type { ModelInfo } from '../../App'
import type { Density } from '../../utils/chatPrefs'
import type { SettingsMode } from '../../utils/settingsMode'
import type { ToolProcessMode } from '../../utils/toolLabels'
import type { SettingsSectionId } from '../../utils/settingsSearch'
import type { MessengerDraftMap } from '../../utils/messengerDrafts'
import type { AssistantDraft, AssistantStatus } from './AssistantSection'

export interface SettingsFormProps {
  sectionProps: (id: SettingsSectionId) => {
    id: string
    title: string
    summary: string
    keywords: string
    forceOpen?: boolean
    hidden?: boolean
    onOpenChange?: (open: boolean) => void
  }
  provider: string
  setProvider: Dispatch<SetStateAction<string>>
  model: string
  setModel: Dispatch<SetStateAction<string>>
  baseUrl: string
  setBaseUrl: Dispatch<SetStateAction<string>>
  apiKey: string
  setApiKey: Dispatch<SetStateAction<string>>
  apiKeySet: boolean
  projectPath: string
  setProjectPath: Dispatch<SetStateAction<string>>
  browserHomeUrl: string
  setBrowserHomeUrl: Dispatch<SetStateAction<string>>
  /** Desktop Privacy Shield (Brave adblock) — optional; web UI omits. */
  privacyShield?: {
    enabled: boolean
    ready: boolean
    message: string
    attribution: string
    onToggle: (on: boolean) => void
    onRefresh: () => void
    busy?: boolean
  } | null
  persona: string
  setPersona: Dispatch<SetStateAction<string>>
  userName: string
  setUserName: Dispatch<SetStateAction<string>>
  agentName: string
  agentGender: string
  setAgentGender: (v: string) => void
  setAgentName: Dispatch<SetStateAction<string>>
  accessScope: string
  setAccessScope: Dispatch<SetStateAction<string>>
  launchAtLogin: boolean
  setLaunchAtLogin: Dispatch<SetStateAction<boolean>>
  startInTray: boolean
  setStartInTray: Dispatch<SetStateAction<boolean>>
  closeToTray: boolean
  setCloseToTray: Dispatch<SetStateAction<boolean>>
  skipQuitWarn: boolean
  setSkipQuitWarn: Dispatch<SetStateAction<boolean>>
  webToolsEnabled: boolean
  setWebToolsEnabled: Dispatch<SetStateAction<boolean>>
  httpBootstrap: boolean
  setHttpBootstrap: Dispatch<SetStateAction<boolean>>
  privacyMode: boolean
  setPrivacyMode: Dispatch<SetStateAction<boolean>>
  approvalMode: 'ask' | 'auto'
  setApprovalMode: Dispatch<SetStateAction<'ask' | 'auto'>>
  harnessMode: string
  setHarnessMode: Dispatch<SetStateAction<string>>
  harnessMinPct: number
  setHarnessMinPct: Dispatch<SetStateAction<number>>
  harnessMaxPct: number
  setHarnessMaxPct: Dispatch<SetStateAction<number>>
  thinkingLevel: 'off' | 'low' | 'medium' | 'high'
  setThinkingLevel: Dispatch<SetStateAction<'off' | 'low' | 'medium' | 'high'>>
  allowSkillCreation: boolean
  setAllowSkillCreation: Dispatch<SetStateAction<boolean>>
  autoApproveThreshold: number
  setAutoApproveThreshold: Dispatch<SetStateAction<number>>
  logLevel: string
  setLogLevel: Dispatch<SetStateAction<string>>
  sarcasmMode: boolean
  setSarcasmMode: Dispatch<SetStateAction<boolean>>
  toolProcess: ToolProcessMode
  setToolProcess: Dispatch<SetStateAction<ToolProcessMode>>
  onToolProcessChange?: (mode: ToolProcessMode) => void
  catalog: ProviderInfo[]
  showAdvanced: boolean
  setShowAdvanced: Dispatch<SetStateAction<boolean>>
  xaiAuth: XaiAuthStatus | null
  xaiLoginBusy: boolean
  xaiUserCode: string
  xaiVerifyUrl: string
  xaiLoginMsg: string
  handleXaiSignIn: () => void
  handleXaiLogout: () => void
  vision: VisionStatus | null
  swarm: NanoSwarmStatus | null
  visionBusy: boolean
  setVisionBusy: Dispatch<SetStateAction<boolean>>
  visionMsg: string
  setVisionMsg: Dispatch<SetStateAction<string>>
  refreshVision: () => Promise<VisionStatus | null>
  startVisionInstallPoll: () => void
  rmb: RmbStatus | null
  rmbBusy: boolean
  setRmbBusy: Dispatch<SetStateAction<boolean>>
  rmbMsg: string
  setRmbMsg: Dispatch<SetStateAction<string>>
  refreshRmb: () => Promise<RmbStatus | null>
  onSettingsSaved?: () => void
  connectedList: ConnectedProvider[]
  providerSearch: string
  setProviderSearch: Dispatch<SetStateAction<string>>
  enabledProviders: string[] | null
  setEnabledProviders: Dispatch<SetStateAction<string[] | null>>
  enabledModels: Record<string, string[]>
  setEnabledModels: Dispatch<SetStateAction<Record<string, string[]>>>
  catalogExpand: string | null
  setCatalogExpand: Dispatch<SetStateAction<string | null>>
  skillsBudget: number
  setSkillsBudget: Dispatch<SetStateAction<number>>
  primaryProviders: ProviderInfo[]
  advancedProviders: ProviderInfo[]
  activeMeta: ProviderInfo
  showBaseUrl: boolean
  providerModels: Array<{ id: string; name: string; provider?: string }>
  customName?: string
  setCustomName?: Dispatch<SetStateAction<string>>
  handleProviderChange: (p: string) => void
  handleBrowseProject: () => void
  themeId: ThemeId
  onThemeChange: (id: ThemeId) => void
  density: Density
  onDensityChange?: (d: Density) => void
  customAccent: string
  onCustomAccentChange?: (hex: string) => void
  updateInfo: UpdateInfo | null
  checkingUpdates: boolean
  updateStatus?: string | null
  onCheckUpdates: () => void
  onInstallUpdate?: () => void
  models: ModelInfo[]
  onOpenHelp?: (articleId?: string) => void
  settings: Settings | null
  messengers?: MessengerInfo[]
  messengerDrafts?: MessengerDraftMap
  setMessengerDrafts?: Dispatch<SetStateAction<MessengerDraftMap>>
  assistant?: AssistantStatus | null
  assistantDraft?: AssistantDraft
  setAssistantDraft?: Dispatch<SetStateAction<AssistantDraft>>
  onAssistantAccountsChanged?: () => void
  settingsMode?: SettingsMode
}

