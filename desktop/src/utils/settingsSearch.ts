/** Section metadata for Settings search / deep-links. */
export type SettingsSectionId =
  | 'provider'
  | 'provider-catalog'
  | 'you-agent'
  | 'voice'
  | 'phone'
  | 'workspace'
  | 'access'
  | 'security-power'
  | 'privacy'
  | 'always-ready'
  | 'tool-process'
  | 'vision'
  | 'rmb'
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
    keywords: 'user name agent persona identity wipe memory forget whoami soul',
  },
  voice: {
    title: 'Voice',
    summary: 'Speak, hear, turn-taking',
    keywords:
      'voice speak hear mic kokoro whisper tts stt smart-turn turn-taking aloud quiet grove speech hq high-quality chatterbox human robot',
  },
  phone: {
    title: 'Phone',
    summary: 'A voice on the line',
    keywords: 'phone call sip telephony baresip line dial talk hq chatterbox',
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
    keywords: 'approval auto ask shell web_fetch bootstrap token ssrf thinking privacy',
  },
  privacy: {
    title: 'Privacy',
    summary: 'What leaves this PC to your model',
    keywords:
      'privacy mode pii email phone scrub redact tool results llm egress cloud mail calendar page',
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
    summary: 'SmolVLM2 · image decode',
    keywords: 'vision local model smolvlm llama screenshot ocr image dependency',
  },
  rmb: {
    title: 'RMB',
    summary: 'Local agent · coding + tools',
    keywords:
      'rmb remedy muscle bridge local agent llama coding tools gguf qwen coder offline private huggingface hugging face pull download',
  },
  'memory-harness': {
    title: 'Memory harness',
    summary: 'Chat compression',
    keywords: 'harness compact prune context percent budget',
  },
  theme: {
    title: 'Appearance',
    summary: 'Theme, text size, motion',
    keywords:
      'theme density accent dark light color appearance font size text large accessibility contrast motion reduce a11y',
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
