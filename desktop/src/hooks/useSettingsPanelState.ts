/**
 * Settings panel mode, search, and section navigation state.
 * Keeps SettingsPanel focused on data load/save.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  SETTINGS_SECTION_META,
  loadLastSettingsSection,
  saveLastSettingsSection,
  type SettingsSectionId,
} from '../utils/settingsSearch'
import {
  loadSettingsMode,
  saveSettingsMode,
  isSectionVisibleInMode,
  type SettingsMode,
} from '../utils/settingsMode'
import { sectionMatchesSearch } from '../components/SettingsSection'

export function useSettingsPanelNav() {
  const [mode, setMode] = useState<SettingsMode>(() => loadSettingsMode())
  const [search, setSearch] = useState('')
  const [section, setSection] = useState<SettingsSectionId>(
    () => loadLastSettingsSection() || 'provider',
  )

  useEffect(() => {
    saveSettingsMode(mode)
  }, [mode])

  useEffect(() => {
    saveLastSettingsSection(section)
  }, [section])

  const visibleSections = useMemo(() => {
    return SETTINGS_SECTION_META.filter((s) => {
      if (!isSectionVisibleInMode(s.id, mode)) return false
      if (!search.trim()) return true
      return sectionMatchesSearch(s, search)
    })
  }, [mode, search])

  const setModeAndPersist = useCallback((m: SettingsMode) => {
    setMode(m)
  }, [])

  return {
    mode,
    setMode: setModeAndPersist,
    search,
    setSearch,
    section,
    setSection,
    visibleSections,
  }
}
