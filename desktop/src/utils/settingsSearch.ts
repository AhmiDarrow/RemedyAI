/** Section metadata for Settings search / deep-links. */
export type SettingsSectionId =
  | 'provider'
  | 'provider-catalog'
  | 'you-agent'
  | 'workspace'
  | 'access'
  | 'security-power'
  | 'always-ready'
  | 'tool-process'
  | 'vision'
  | 'memory-harness'
  | 'theme'
  | 'advanced'
  | 'help'
  | 'mcp'
  | 'channels'
  | 'assistant'
  | 'about'
  | 'license'

export const SETTINGS_SECTION_META: Record<
  SettingsSectionId,
  { title: string; summary: string; keywords: string }
> = {
  provider: {
    title: 'Provider',
    summary: 'Model & API key',
    keywords: 'llm openai anthropic xai ollama key model',
  },
  'provider-catalog': {
    title: 'Provider catalog',
    summary: 'Which providers appear',
    keywords: 'enable disable models budget skills catalog',
  },
  'you-agent': {
    title: 'You & Agent',
    summary: 'Names & persona',
    keywords: 'user name agent persona identity',
  },
  workspace: {
    title: 'Project workspace',
    summary: 'Folder for tools',
    keywords: 'project path folder directory cwd browser home homepage github url',
  },
  access: {
    title: 'Access & permissions',
    summary: 'Filesystem scope',
    keywords: 'scope untrusted home full project jail',
  },
  'security-power': {
    title: 'Security & power',
    summary: 'Approvals, web, bootstrap',
    keywords: 'approval auto ask shell web_fetch bootstrap token ssrf thinking',
  },
  'always-ready': {
    title: 'Always ready',
    summary: 'Startup & tray',
    keywords: 'login tray startup quit windows',
  },
  'tool-process': {
    title: 'Tool process',
    summary: 'Visibility of tool steps',
    keywords: 'process trail full medium off diagnostics',
  },
  vision: {
    title: 'Local vision',
    summary: 'Image decode',
    keywords: 'vision qwen llama screenshot ocr image',
  },
  'memory-harness': {
    title: 'Memory harness',
    summary: 'Chat compression',
    keywords: 'harness compact prune context percent budget',
  },
  theme: {
    title: 'Theme',
    summary: 'Appearance',
    keywords: 'theme density accent dark light color',
  },
  advanced: {
    title: 'Advanced',
    summary: 'Learning, logs, tone',
    keywords: 'skill creation log level sarcasm threshold learning',
  },
  help: {
    title: 'Help & shortcuts',
    summary: 'Manual & keys',
    keywords: 'f1 wiki hotkeys keyboard',
  },
  mcp: {
    title: 'MCP host',
    summary: 'Expose skills to other apps',
    keywords: 'mcp host server external client',
  },
  channels: {
    title: 'Messengers',
    summary: 'Telegram, Discord, WhatsApp…',
    keywords:
      'telegram discord slack mattermost whatsapp teams matrix signal messenger channel bot gateway continuity',
  },
  assistant: {
    title: 'Personal assistant',
    summary: 'Calendar, mail, budget (local)',
    keywords:
      'assistant personal calendar gmail mail outlook hotmail yahoo budget debt bills brief oauth accounts money disclaimer',
  },
  about: {
    title: 'About',
    summary: 'Version & WebUI',
    keywords: 'version update webui about',
  },
  license: {
    title: 'License',
    summary: 'Free tier & commercial',
    keywords: 'license commercial free indie enterprise copyright',
  },
}

export function loadLastSettingsSection(): string | null {
  try {
    return localStorage.getItem('remedy.settingsLastSection')
  } catch {
    return null
  }
}

export function saveLastSettingsSection(id: string): void {
  try {
    localStorage.setItem('remedy.settingsLastSection', id)
  } catch {
    /* ignore */
  }
}
