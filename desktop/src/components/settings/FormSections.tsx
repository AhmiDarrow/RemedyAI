/** Settings form sections — domain orchestrator. */
import type { ReactNode } from 'react'
import type { SettingsFormProps } from './formTypes'
export type { SettingsFormProps } from './formTypes'
import { SettingsSections_provider } from './sections_provider'
import { SettingsSections_identity } from './sections_identity'
import { SettingsSections_localModels } from './sections_localModels'
import { SettingsSections_rest } from './sections_rest'

/** Renders all settings sections, split by domain for maintainability. */
export function SettingsFormSections(p: SettingsFormProps): ReactNode {
  return (
    <div className="settings-form-sections">
      <SettingsSections_provider {...p} />
      <SettingsSections_identity {...p} />
      <SettingsSections_localModels {...p} />
      <SettingsSections_rest {...p} />
    </div>
  )
}
