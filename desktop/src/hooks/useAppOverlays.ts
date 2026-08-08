/**
 * Full-window overlay open flags (help, about, diagnostics, usage, palette…).
 * Extracted from App.tsx so panel state is one place.
 */

import { useCallback, useEffect, useState } from 'react'

export function useAppOverlays() {
  const [aboutOpen, setAboutOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [helpArticleId, setHelpArticleId] = useState<string | null>(null)
  const [usageOpen, setUsageOpen] = useState(false)
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [timeTravelOpen, setTimeTravelOpen] = useState(false)
  const [quitWarnOpen, setQuitWarnOpen] = useState(false)

  const openHelp = useCallback((articleId?: string) => {
    setHelpArticleId(articleId || null)
    setHelpOpen(true)
  }, [])

  // Dev / browser review: open wiki with ?help=1 or ?help=09-troubleshooting
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location.search)
      const h = params.get('help')
      if (h === null) return
      openHelp(h === '1' || h === '' || h === 'true' ? undefined : h)
    } catch {
      /* ignore */
    }
  }, [openHelp])

  return {
    aboutOpen,
    setAboutOpen,
    helpOpen,
    setHelpOpen,
    helpArticleId,
    setHelpArticleId,
    usageOpen,
    setUsageOpen,
    diagnosticsOpen,
    setDiagnosticsOpen,
    paletteOpen,
    setPaletteOpen,
    timeTravelOpen,
    setTimeTravelOpen,
    quitWarnOpen,
    setQuitWarnOpen,
    openHelp,
  }
}
