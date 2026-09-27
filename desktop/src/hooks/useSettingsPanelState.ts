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
import { useI18n } from '../i18n'
import {
  SECTION_CATEGORY, isSettingsSection, settingsSectionVisible, type SettingsCategory,
} from '../utils/settingsNavigation'

export function useSettingsPanelState() {
  const { t } = useI18n()
  const [settingsSearch, setSettingsSearch] = useState('')
  const [category, setCategoryRaw] = useState<SettingsCategory>('general')
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

  const setCategory = useCallback((next: SettingsCategory) => {
    setCategoryRaw(next)
    setSettingsSearch('')
    setForceSection(null)
  }, [])

  const matchSec = useCallback(
    (id: SettingsSectionId) => {
      const meta = SETTINGS_SECTION_META[id]
      return sectionMatchesSearch(
        settingsSearch,
        `${t(`sec.${id}`)} ${meta.title}`,
        `${t(`sec.${id}Sum`)} ${meta.summary}`,
        meta.keywords,
      )
    },
    [settingsSearch, t],
  )

  const sectionProps = useCallback(
    (id: SettingsSectionId) => {
      const hidden = !settingsSectionVisible(id, category, settingsMode, !!settingsSearch.trim(), matchSec(id))
      return {
        id,
        title: t(`sec.${id}`),
        summary: t(`sec.${id}Sum`),
        keywords: SETTINGS_SECTION_META[id].keywords,
        forceOpen: forceSection === id && !hidden,
        hidden,
        onOpenChange: (isOpen: boolean) => {
          if (isOpen) {
            setForceSection(id)
            saveLastSettingsSection(id)
            if (id === 'vision') setVisionSectionOpen(true)
            if (id === 'rmb') setRmbSectionOpen(true)
          } else {
            // User explicitly collapsed — drop the force pin so a later
            // parent re-render cannot reopen it.
            setForceSection((cur) => (cur === id ? null : cur))
          }
        },
      }
    },
    [category, forceSection, matchSec, settingsSearch, settingsMode, t],
  )

  // Remedy asked to open a section (app_control / update_settings).
  useEffect(() => {
    const onSec = (ev: Event) => {
      const id = String(
        (ev as CustomEvent<{ section?: string }>).detail?.section || '',
      )
      if (!isSettingsSection(id)) return
      setSettingsSearch('')
      setCategoryRaw(SECTION_CATEGORY[id])
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
      if (isSettingsSection(last) && isSectionVisibleInMode(last, loadSettingsMode())) {
        setCategoryRaw(SECTION_CATEGORY[last])
        setForceSection(last)
      }
    } else {
      setVisionSectionOpen(false)
      setRmbSectionOpen(false)
      setSettingsSearch('')
    }
  }, [])

  return {
    category,
    setCategory,
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
    visibleSectionCount: (Object.keys(SETTINGS_SECTION_META) as SettingsSectionId[])
      .filter((id) => !sectionProps(id).hidden).length,
    onPanelOpenChange,
  }
}
