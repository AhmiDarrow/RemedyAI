import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { fetchI18n, type UiLanguageOption } from '../api/i18n'
import { EN, formatMsg } from './en'
import { gapFor, gapLocaleId } from './gap'
import { intlLocale } from './locale'
import { getAgentIdentity, pronounVars, useAgentIdentity } from './pronouns'

type Catalog = Record<string, string>

interface I18nState {
  stored: string
  resolved: string
  dir: 'ltr' | 'rtl'
  catalog: Catalog
  languages: UiLanguageOption[]
  t: (key: string, vars?: Record<string, string>) => string
  setStored: (code: string) => void
  reload: (code?: string) => Promise<void>
}

const I18nContext = createContext<I18nState | null>(null)

function applyDocument(lang: string, dir: 'ltr' | 'rtl') {
  try {
    document.documentElement.lang = lang || 'en'
    document.documentElement.dir = dir
  } catch {
    /* */
  }
}

function hintFromNavigator(): string {
  try {
    return (navigator.language || '').trim()
  } catch {
    return ''
  }
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [stored, setStoredState] = useState('auto')
  const [resolved, setResolved] = useState('en')
  const [dir, setDir] = useState<'ltr' | 'rtl'>('ltr')
  const [catalog, setCatalog] = useState<Catalog>(EN)
  const [languages, setLanguages] = useState<UiLanguageOption[]>([])

  const reload = useCallback(async (code?: string) => {
    try {
      const data = await fetchI18n(code, hintFromNavigator() || undefined)
      setStoredState(data.ui_language || 'auto')
      setResolved(data.resolved || 'en')
      setDir(data.dir === 'rtl' ? 'rtl' : 'ltr')
      const resolvedNow = data.resolved || 'en'
      setCatalog({ ...EN, ...(data.catalog || {}), ...gapFor(resolvedNow) })
      if (data.languages?.length) setLanguages(data.languages)
      applyDocument(resolvedNow, data.dir === 'rtl' ? 'rtl' : 'ltr')
    } catch {
      const hint = hintFromNavigator()
      const resolvedNow = gapLocaleId(hint) || 'en'
      setResolved(resolvedNow)
      setCatalog({ ...EN, ...gapFor(hint) })
      applyDocument(intlLocale(hint) || 'en', 'ltr')
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const setStored = useCallback(
    (code: string) => {
      setStoredState(code)
      void reload(code)
    },
    [reload],
  )

  // Pronoun tokens ({they}, {their}, work{s}, {agent}, …) follow the configured
  // agent gender and name, and t() changes identity when the setting does.
  const identity = useAgentIdentity()
  const t = useCallback(
    (key: string, vars?: Record<string, string>) => {
      const raw = catalog[key] || EN[key] || key
      return formatMsg(raw, {
        ...(pronounVars(identity.gender, identity.name) as unknown as Record<string, string>),
        ...vars,
      })
    },
    [catalog, identity],
  )

  const value = useMemo<I18nState>(
    () => ({ stored, resolved, dir, catalog, languages, t, setStored, reload }),
    [stored, resolved, dir, catalog, languages, t, setStored, reload],
  )

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nState {
  const ctx = useContext(I18nContext)
  if (!ctx) {
    let docLang = 'en'
    try {
      docLang = document.documentElement.lang || 'en'
    } catch {
      /* */
    }
    const offline = { ...EN, ...gapFor(docLang) }
    return {
      stored: 'auto',
      resolved: gapLocaleId(docLang) || 'en',
      dir: 'ltr',
      catalog: offline,
      languages: [],
      t: (key, vars) => {
        const id = getAgentIdentity()
        return formatMsg(offline[key] || EN[key] || key, {
          ...(pronounVars(id.gender, id.name) as unknown as Record<string, string>),
          ...vars,
        })
      },
      setStored: () => {},
      reload: async () => {},
    }
  }
  return ctx
}
