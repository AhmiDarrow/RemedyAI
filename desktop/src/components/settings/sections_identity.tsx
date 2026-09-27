/** Settings form sections — identity. */
import { useState, type ReactNode } from 'react'
import { useI18n } from '../../i18n'
import type { SettingsFormProps } from './formTypes'
import { SettingsSection } from '../SettingsSection'
import { SettingsFlow } from './SettingsFlow'
import {
  FormActionButton,
  FormHint,
  FormLabel,
  FormNotice,
  FormSegmented,
  FormSelect,
  FormToggle,
} from './formUi'
import { Field, PERSONAS } from './shared'
import { VoiceSection } from './VoiceSection'
import { PhoneSection } from './PhoneSection'
import { ConnectSection } from './ConnectSection'
import { TOOL_PROCESS_MODES } from '../../utils/toolLabels'
import { isLinuxDesktop } from '../../utils/platform'

function PersonaWipeControl(): ReactNode {
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const ready = typed.trim().toUpperCase() === 'WIPE'

  const run = async () => {
    if (!ready || busy) return
    setBusy(true)
    setMsg('')
    try {
      const { apiFetch } = await import('../../api/client')
      await apiFetch('/memory/persona-wipe', {
        method: 'POST',
        body: JSON.stringify({ confirm: 'WIPE' }),
      })
      setMsg('Persona wiped. Remedy no longer remembers facts about you.')
      setTyped('')
      setOpen(false)
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : 'Wipe failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsFlow title="Forget personal memories" summary="Review and clear remembered facts and goals" busy={busy}>
      <FormLabel>Delete personal memories</FormLabel>
      <FormHint>
        Delete remembered facts about you, relationship context, and life goals.
        Chats, API keys, skills, and this app stay. Cannot be undone.
      </FormHint>
      {!open ? (
        <FormActionButton
          variant="danger"
          onClick={() => {
            setOpen(true)
            setMsg('')
          }}
        >
          Wipe persona…
        </FormActionButton>
      ) : (
        <FormNotice tone="error">
          <div className="space-y-2">
            <div>
              This deletes your saved profile, personal memories, relationship context, and life
              goals. Type <strong>WIPE</strong> to confirm.
            </div>
            <input
              type="text"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder="Type WIPE"
              autoComplete="off"
              spellCheck={false}
              className="ui-input w-full"
              aria-label="Type WIPE to confirm persona wipe"
            />
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={!ready || busy}
                onClick={() => void run()}
                className="px-3 py-1.5 rounded text-xs font-semibold disabled:opacity-40"
                style={{ background: 'var(--error)', color: 'var(--error-foreground)' }}
              >
                {busy ? 'Wiping…' : 'Wipe what Remedy knows about me'}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setOpen(false)
                  setTyped('')
                }}
                className="px-3 py-1.5 rounded text-xs"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </FormNotice>
      )}
      {msg ? (
        <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          {msg}
        </div>
      ) : null}
    </SettingsFlow>
  )
}

export function SettingsSections_identity(p: SettingsFormProps): ReactNode {
  const {
    sectionProps,
    projectPath,
    setProjectPath,
    browserHomeUrl,
    setBrowserHomeUrl,
    privacyShield = null,
    persona,
    setPersona,
    userName,
    setUserName,
    agentName,
    setAgentName,
    agentGender,
    setAgentGender,
    uiLanguage,
    setUiLanguage,
    uiLanguages,
    accessScope,
    setAccessScope,
    launchAtLogin,
    setLaunchAtLogin,
    startInTray,
    setStartInTray,
    skipQuitWarn,
    setSkipQuitWarn,
    webToolsEnabled,
    setWebToolsEnabled,
    httpBootstrap,
    setHttpBootstrap,
    privacyMode,
    soulFieldEnabled,
    setSoulFieldEnabled,
    setPrivacyMode,
    approvalMode,
    setApprovalMode,
    trustProfile,
    setTrustProfile,
    thinkingLevel,
    setThinkingLevel,
    toolProcess,
    setToolProcess,
    onToolProcessChange,
    handleBrowseProject,
    settingsMode = 'simple',
  } = p
  const { t } = useI18n()
  const langOptions = (uiLanguages.length ? uiLanguages : [{ id: 'auto', name_en: 'Auto', name_native: 'Auto', rtl: false, chrome: true }]).map(
    (row) => ({
      value: row.id,
      label:
        row.id === 'auto'
          ? t('settings.languageAuto')
          : row.name_native === row.name_en
            ? row.name_native
            : `${row.name_native} — ${row.name_en}`,
    }),
  )

  return (
    <>
      <SettingsSection
        {...sectionProps('you-agent')}
        title={t('sec.youAgent')}
        summary={t('sec.youAgentSummary')}
      >
        <FormLabel>{t('settings.language')}</FormLabel>
        <FormSelect value={uiLanguage} onChange={setUiLanguage} options={langOptions} />
        <FormHint>{t('settings.languageHintShort')}</FormHint>
        {settingsMode === 'advanced' && <FormHint>{t('settings.helpEnglish')}</FormHint>}
        <Field
          label={t('settings.yourName')}
          value={userName}
          onChange={setUserName}
          placeholder="e.g. Alex"
        />
        <Field
          label={t('settings.partnerName')}
          value={agentName}
          onChange={setAgentName}
          placeholder="Remedy"
        />
        <FormLabel>{t('settings.partnerGender')}</FormLabel>
        <FormSegmented
          value={agentGender}
          onChange={setAgentGender}
          options={[
            { id: 'female', label: t('settings.female') },
            { id: 'male', label: t('settings.male') },
            { id: 'neutral', label: t('settings.neutral') },
          ]}
        />
        <FormLabel>
          Communication style
        </FormLabel>
        <FormSelect
          value={persona}
          onChange={setPersona}
          options={PERSONAS.map(p => ({ value: p.id, label: p.name }))}
          title="Communication style"
        />
        <FormHint>{PERSONAS.find(p => p.id === persona)?.description}</FormHint>
      </SettingsSection>

      <VoiceSection
        sectionProps={sectionProps('voice')}
        settingsMode={settingsMode}
      />
      <PhoneSection sectionProps={sectionProps('phone')} />
      <ConnectSection sectionProps={sectionProps('connect')} />

      {/* Project */}
      <SettingsSection
        {...sectionProps('workspace')}
      >
        <FormLabel>
          Default project folder
        </FormLabel>
        <div className="flex gap-1 mb-1">
          <input
            type="text"
            value={projectPath}
            onChange={(e) => setProjectPath(e.target.value)}
            placeholder="e.g. C:\Users\You\Projects\MyApp"
            className="ui-input flex-1 min-w-0"
          />
          <FormActionButton
            onClick={() => void handleBrowseProject()}
            className="flex-shrink-0"
          >
            <span className="inline-flex items-center gap-1" title="Browse for folder">
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
                <path
                  d="M1.5 3.5h4l1.5 1.5H14.5v8a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-9.5z"
                  stroke="currentColor"
                  strokeWidth="1.2"
                  fill="none"
                />
              </svg>
              Browse
            </span>
          </FormActionButton>
        </div>
        <FormHint>
          Type a path or browse. Save reloads the workspace (file tools, shell cwd, @file search).
        </FormHint>
        {(!projectPath.trim() || projectPath.trim() === '.') && (
          <FormNotice tone="warn">
            <strong>No project folder</strong> — tools use <strong>full</strong> access on
            this PC (your Windows user). Fine for general help; for coding, pick a project
            folder so work stays focused and safer.
          </FormNotice>
        )}
        <FormLabel className="mt-3">
          Browser homepage
        </FormLabel>
        <input
          type="url"
          value={browserHomeUrl}
          onChange={(e) => setBrowserHomeUrl(e.target.value)}
          placeholder="https://github.com/AhmiDarrow/RemedyAI"
          className="ui-input mb-1"
          spellCheck={false}
        />
        <FormHint>
          In-app Browser (⌂ Home). Default is the Remedy GitHub repo. Use http(s) only.
        </FormHint>
        {privacyShield && (
          <div
            className="mt-3 rounded px-2 py-2"
            style={{
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--border)',
            }}
          >
            <div className="flex items-center justify-between gap-2 mb-1">
              <label
                className="text-xs font-medium flex items-center gap-2"
                style={{ color: 'var(--text-primary)' }}
              >
                <input
                  type="checkbox"
                  checked={privacyShield.enabled}
                  onChange={(e) => privacyShield.onToggle(e.target.checked)}
                  disabled={privacyShield.busy}
                />
                Browser Privacy Shield
              </label>
              <button
                type="button"
                className="text-[10px] px-1.5 py-0.5 rounded"
                style={{
                  border: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                  opacity: privacyShield.busy ? 0.5 : 1,
                }}
                disabled={privacyShield.busy}
                onClick={() => privacyShield.onRefresh()}
                title="Re-download EasyList / EasyPrivacy"
              >
                {privacyShield.busy ? 'Updating…' : 'Update lists'}
              </button>
            </div>
            <FormHint>
              {privacyShield.message}
              {!privacyShield.ready && privacyShield.enabled
                ? ' (first run downloads filter lists).'
                : ''}
            </FormHint>
            <div
              className="text-[9px] leading-snug mt-1"
              style={{ color: 'var(--text-muted)', opacity: 0.85 }}
              title={privacyShield.attribution}
            >
              Blocks ad/tracker navigations and hides many page ads (Brave engine + EasyList).
              Not a full browser extension — use ↗ system browser for full uBlock Origin.
            </div>
          </div>
        )}
      </SettingsSection>

      {/* Privacy — simple + advanced (what leaves this PC to the model) */}
      <SettingsSection {...sectionProps('privacy')}>
        <FormHint>
          Cloud models receive your chat and tool results. Choose how much personal
          information to include.
        </FormHint>
        <FormToggle
          checked={privacyMode} onChange={setPrivacyMode}
          label="Redact personal information"
          description="Remove email addresses, phone numbers, and similar identifiers from tool results before sending them to your model. Results may contain less detail."
        />
        <FormHint>Stored API keys are kept out of model input in both modes.</FormHint>
        <FormToggle
          checked={soulFieldEnabled} onChange={setSoulFieldEnabled}
          label="Remember our working relationship"
          description="Keep personal context and continuity when you switch models. Turning this off stops updates; it does not erase existing memories."
        />
        <PersonaWipeControl />
      </SettingsSection>

      {/* Access */}
      <SettingsSection
        {...sectionProps('access')}
      >
        <FormLabel>Filesystem scope</FormLabel>
        <FormSelect
          value={accessScope}
          onChange={setAccessScope}
          disabled={!projectPath.trim() || projectPath.trim() === '.'}
          className="mb-1"
        >
          <option value="untrusted">Untrusted project (strict)</option>
          <option value="project">Project + Desktop/Docs/Downloads</option>
          <option value="home">Project + full home folder</option>
          <option value="full">Full user machine (you grant)</option>
        </FormSelect>
        <FormHint>
          {!projectPath.trim() || projectPath.trim() === '.' ? (
            <>
              Scope is forced to <strong>full</strong> while no project folder is set.
              Choose a workspace in General to limit file access.
            </>
          ) : (
            <>
              <strong>Untrusted</strong> = project root only + always Ask for shell/write
              (use for downloaded folders). Full still runs with your user account permissions.
            </>
          )}
        </FormHint>
      </SettingsSection>

      {/* Security & power (owner keeps full capability; defaults stay safe) */}
      <SettingsSection {...sectionProps('security-power')} title="Permissions" summary="When Remedy asks before acting">
        <FormHint>
          Choose when Remedy asks before acting. Payment and message-sending checkpoints remain required.
        </FormHint>
              <div className="mb-2">
                <FormLabel>Approvals</FormLabel>
                <FormSegmented
                  value={approvalMode}
                  onChange={setApprovalMode}
                  options={[
                    { id: 'ask', label: 'Ask', title: 'Safe default — confirm shell/write/skills' },
                    {
                      id: 'auto',
                      label: 'Auto',
                      title: 'In-project — build and write without prompts; jail stays outside the folder',
                    },
                    {
                      id: 'full',
                      label: 'Full',
                      title: 'No write jail (auth still closed). Warns when leaving the project.',
                    },
                  ]}
                />
                <FormHint>
                  {approvalMode === 'full'
                    ? 'Full allows file and command actions beyond your project, with warnings. Protected credentials remain restricted.'
                    : approvalMode === 'auto'
                      ? 'Auto allows routine work within your project without repeated prompts. Access outside it stays restricted.'
                      : 'Ask lets you review higher-impact actions before they run.'}
                </FormHint>
              </div>
              <div className="mb-2">
                <FormLabel>Thinking level</FormLabel>
                <FormSegmented
                  value={thinkingLevel}
                  onChange={setThinkingLevel}
                  options={[
                    { id: 'off', label: 'Off' },
                    { id: 'low', label: 'Low' },
                    { id: 'medium', label: 'Medium' },
                    { id: 'high', label: 'High' },
                  ]}
                />
                <FormHint>
                  Also on the status bar. High = more deliberation when the model supports it.
                </FormHint>
              </div>
              <FormToggle
                checked={webToolsEnabled}
                onChange={setWebToolsEnabled}
                label="Search and read the web"
                description="Allow web search and public-page fetching. This does not control cloud models or connected accounts."
              />
              <SettingsFlow title="Advanced permissions" summary="Trust behavior and browser access" saveBehavior="draft">
              <div className="mb-2">
                <FormLabel>Trust</FormLabel>
                <FormSegmented
                  value={trustProfile}
                  onChange={setTrustProfile}
                  options={[
                    {
                      id: 'conservative',
                      label: 'Conservative',
                      title: 'Still ask before shell, files, and skills even in Auto',
                    },
                    {
                      id: 'balanced',
                      label: 'Balanced',
                      title: 'Follow Approvals. Mail and payment always stop.',
                    },
                    {
                      id: 'autonomous',
                      label: 'Autonomous',
                      title: 'Skip in-project high-impact asks like Auto. Mail and payment still stop.',
                    },
                  ]}
                />
                <FormHint>
                  {trustProfile === 'conservative'
                    ? 'Even Auto still asks before shell, files, and skills. Mail and payment always stop.'
                    : trustProfile === 'autonomous'
                      ? 'In-project high-impact work runs without extra prompts, like Auto. Mail and payment always stop.'
                      : 'Follows Approvals (Ask / Auto / Full). Mail and payment always stop.'}
                </FormHint>
              </div>
              <FormToggle
                checked={httpBootstrap}
                onChange={setHttpBootstrap}
                label="Allow browser sign-in on this computer"
                description="Lets the local WebUI obtain access to Remedy. The desktop works with this off. Only enable it if other users on this computer may access Remedy."
              />
              </SettingsFlow>
            </SettingsSection>

      {/* Always ready */}
      <SettingsSection {...sectionProps('always-ready')}>
              {!isLinuxDesktop() && (
              <FormToggle
                checked={launchAtLogin}
                onChange={setLaunchAtLogin}
                label="Start with Windows"
                description="Uses the Windows Startup folder (Settings → Apps → Startup) — not the registry Run key."
              />
              )}
              <FormToggle
                checked={startInTray}
                onChange={setStartInTray}
                label={isLinuxDesktop() ? "Start minimized" : "Start hidden in tray"}
                description={
                  isLinuxDesktop()
                    ? "Off = window opens normally (recommended). On = start minimized to the taskbar (WSLg has no tray)."
                    : 'Off = window opens normally (recommended). On = only a tray icon until you click it. Independent of "Start with Windows".'
                }
              />
              <FormToggle
                checked
                disabled
                onChange={() => {}}
                label={
                  isLinuxDesktop()
                    ? "Close window (✕) minimizes to the taskbar"
                    : "Close window (✕) always hides to tray"
                }
                description={
                  isLinuxDesktop()
                    ? "Always on: ✕ keeps Remedy running and leaves a Windows taskbar button (WSLg has no tray). Fully stop from the app menu → Quit."
                    : "Always on for the always-ready partner: the OS ✕ / Alt+F4 hides Remedy to the system tray and keeps the local API running. Fully stop only from tray Quit."
                }
              />
              <FormToggle
                checked={skipQuitWarn}
                onChange={setSkipQuitWarn}
                label="Don't warn when quitting (server stops)"
                description="Opt-in only. Quit fully stops the local API; use Switch to WebUI or hide-to-tray to keep the server running."
              />
      </SettingsSection>

      {/* Tool process visibility */}
      <SettingsSection {...sectionProps('tool-process')}>
        <FormHint>
          How much <em>Process</em> detail to show under replies — same list, more depth.
          The chat answer is always complete (never truncated by this setting).
        </FormHint>
        <FormSegmented
          value={toolProcess}
          onChange={(id) => {
            setToolProcess(id)
            onToolProcessChange?.(id)
          }}
          options={TOOL_PROCESS_MODES.map((m) => ({
            id: m.id,
            label: m.label,
            title: m.hint,
          }))}
        />
        <FormHint>
          {TOOL_PROCESS_MODES.find((m) => m.id === toolProcess)?.hint}
        </FormHint>
      </SettingsSection>
    </>
  )
}
