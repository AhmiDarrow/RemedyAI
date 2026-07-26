export type SlideId =
  | 'sessions'
  | 'settings'
  | 'files'
  | 'terminal'
  | 'browser'
  | 'scratch'

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
}

export const ALL_SLIDES: SlideId[] = [
  'sessions',
  'settings',
  'files',
  'terminal',
  'browser',
  'scratch',
]
