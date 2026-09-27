import { SETTINGS_SECTION_META, type SettingsSectionId } from './settingsSearch'
import { isSectionVisibleInMode, type SettingsMode } from './settingsMode'

export const SETTINGS_CATEGORIES = ['general', 'models', 'connections', 'privacy', 'system'] as const
export type SettingsCategory = typeof SETTINGS_CATEGORIES[number]

/** Every section has one home. Search deliberately crosses these boundaries. */
export const SECTION_CATEGORY: Record<SettingsSectionId, SettingsCategory> = {
  'you-agent': 'general', voice: 'general', workspace: 'general', theme: 'general',
  provider: 'models', 'provider-catalog': 'models', rmb: 'models', vision: 'models',
  'memory-harness': 'models',
  phone: 'connections', connect: 'connections', channels: 'connections',
  assistant: 'connections', mcp: 'connections',
  access: 'privacy', 'security-power': 'privacy', privacy: 'privacy',
  'always-ready': 'system', 'tool-process': 'system', advanced: 'system',
  help: 'system', about: 'system', license: 'system',
}

export function isSettingsSection(id: string | null): id is SettingsSectionId {
  return id !== null && Object.prototype.hasOwnProperty.call(SETTINGS_SECTION_META, id)
}

export function settingsSectionVisible(
  id: SettingsSectionId, category: SettingsCategory, mode: SettingsMode,
  searching: boolean, matches: boolean,
): boolean {
  return searching ? matches : SECTION_CATEGORY[id] === category && isSectionVisibleInMode(id, mode)
}
