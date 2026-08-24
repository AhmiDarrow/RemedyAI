/**
 * Settings panel mode, search, and section open state.
 * Extracted from SettingsPanel so load/save stays the main concern.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  SETTINGS_SECTION_META,
  loadLastSettingsSection,
  saveLastSettingsSection,
  type SettingsSectionId,
} from '../utils/settingsSearch'
import {
  ADVANCED_ONLY_SECTIONS,
  loadSettingsMode,
  saveSettingsMode,
  isSectionVisibleInMode,
  type SettingsMode,
} from '../utils/settingsMode'
import { sectionMatchesSearch } from '../components/SettingsSection'

export function useSettingsPanelState() {
  const [settingsSearch, setSettingsSearch] = useState('')
  const [forceSection, setForceSection] = useState<string | null>(null)
  const [visionSectionOpen, setVisionSectionOpen] = useState(false)
  const [rmbSectionOpen, setRmbSectionOpen] = useState(false)
  const [settingsMode, setSettingsModeRaw] = useState<SettingsMode>(
    () => loadSettingsMode(),
  )

  const setSettingsMode = useCallback((m: SettingsMode) => {
    setSettingsModeRaw(m)
    saveSettingsMode(m)
  }, [])

  const matchSec = useCallback(
    (id: SettingsSectionId) => {
      const meta = SETTINGS_SECTION_META[id]
      return sectionMatchesSearch(
        settingsSearch,
        meta.title,
        meta.summary,
        meta.keywords,
      )
    },
    [settingsSearch],
  )

  const sectionProps = useCallback(
    (id: SettingsSectionId) => {
      const modeHidden = !isSectionVisibleInMode(id, settingsMode)
      const searchHidden = settingsSearch.trim().length > 0 && !matchSec(id)
      return {
        id,
        title: SETTINGS_SECTION_META[id].title,
        summary: SETTINGS_SECTION_META[id].summary,
        keywords: SETTINGS_SECTION_META[id].keywords,
        forceOpen:
          forceSection === id
          || (settingsSearch.trim().length > 0 && matchSec(id) && !modeHidden),
        hidden: modeHidden || searchHidden,
        onOpenChange: (isOpen: boolean) => {
          if (isOpen) {
            setForceSection(id)
            saveLastSettingsSection(id)
            if (id === 'vision') setVisionSectionOpen(true)
            if (id === 'rmb') setRmbSectionOpen(true)
          }
        },
      }
    },
    [forceSection, matchSec, settingsSearch, settingsMode],
  )

  // Remedy asked to open a section (app_control / update_settings).
  useEffect(() => {
    const onSec = (ev: Event) => {
      const id = String(
        (ev as CustomEvent<{ section?: string }>).detail?.section || '',
      )
      if (!id) return
      if (ADVANCED_ONLY_SECTIONS.has(id)) setSettingsMode('advanced')
      setForceSection(id)
      saveLastSettingsSection(id)
      if (id === 'vision') setVisionSectionOpen(true)
      if (id === 'rmb') setRmbSectionOpen(true)
    }
    window.addEventListener('remedy:settings-section', onSec)
    return () => window.removeEventListener('remedy:settings-section', onSec)
  }, [setSettingsMode])

  /** Reset search / force when the panel closes; restore last section on open. */
  const onPanelOpenChange = useCallback((open: boolean) => {
    if (open) {
      const last = loadLastSettingsSection()
      if (last) setForceSection(last)
    } else {
      setVisionSectionOpen(false)
      setRmbSectionOpen(false)
      setSettingsSearch('')
    }
  }, [])

  return {
    settingsSearch,
    setSettingsSearch,
    forceSection,
    setForceSection,
    visionSectionOpen,
    setVisionSectionOpen,
    rmbSectionOpen,
    setRmbSectionOpen,
    settingsMode,
    setSettingsMode,
    matchSec,
    sectionProps,
    onPanelOpenChange,
  }
}
