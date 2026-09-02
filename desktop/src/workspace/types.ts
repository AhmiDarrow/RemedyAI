export type SlideId =
  | 'sessions'
  | 'settings'
  | 'files'
  | 'terminal'
  | 'browser'
  | 'scratch'
  | 'automations'

export const SLIDE_META: Record<
  SlideId,
  { label: string; short: string; popout: boolean }
> = {
  sessions: { label: 'Sessions', short: '≡', popout: false },
  settings: { label: 'Settings', short: '⚙', popout: false },
  files: { label: 'Files', short: '📁', popout: false },
  terminal: { label: 'Terminal', short: '>_', popout: true },
  browser: { label: 'Browser', short: '🌐', popout: true },
  scratch: { label: 'Scratch', short: '✎', popout: true },
  automations: { label: 'Automations', short: '⚡', popout: false },
}

export const ALL_SLIDES: SlideId[] = [
  'sessions',
  'settings',
  'files',
  'terminal',
  'browser',
  'scratch',
  'automations',
]
