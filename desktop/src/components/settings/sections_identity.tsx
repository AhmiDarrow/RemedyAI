/** Settings form sections — identity. */
import type { ReactNode } from 'react'
import type { SettingsFormProps } from './formTypes'
import { SettingsSection } from '../SettingsSection'
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
import { TOOL_PROCESS_MODES } from '../../utils/toolLabels'

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
    setPrivacyMode,
    approvalMode,
    setApprovalMode,
    thinkingLevel,
    setThinkingLevel,
    toolProcess,
    setToolProcess,
    onToolProcessChange,
    handleBrowseProject,
  } = p

  return (
    <>
      <SettingsSection
        {...sectionProps('you-agent')}
      >
        <Field
          label="Your name (what Remedy calls you)"
          value={userName}
          onChange={setUserName}
          placeholder="e.g. Alex"
        />
        <FormHint>
          Saved to your profile so Remedy can address you naturally.
        </FormHint>
        <Field
          label="Partner name"
          value={agentName}
          onChange={setAgentName}
          placeholder="Remedy"
        />
        <FormHint>
          Call your partner anything — default is Remedy.
        </FormHint>
        <FormLabel>Partner gender</FormLabel>
        <FormSegmented
          value={agentGender}
          onChange={setAgentGender}
          options={[
            { id: 'female', label: 'Female' },
            { id: 'male', label: 'Male' },
            { id: 'neutral', label: 'Neither / AI' },
          ]}
        />
        <FormHint>
          Presentation only — not medical. Default female; change anytime.
        </FormHint>
        <FormLabel>
          Persona
        </FormLabel>
        <div className="space-y-1.5 mb-1">
          {PERSONAS.map((p) => (
            <label
              key={p.id}
              className="flex items-start gap-2 px-2 py-1.5 rounded cursor-pointer"
              style={{
                background: persona === p.id ? 'var(--bg-tertiary)' : 'transparent',
                border: persona === p.id ? '1px solid var(--accent)' : '1px solid var(--border)',
              }}
            >
              <input
                type="radio"
                name="settings-persona"
                value={p.id}
                checked={persona === p.id}
                onChange={() => setPersona(p.id)}
                className="mt-0.5"
                style={{ accentColor: 'var(--accent)' }}
              />
              <span>
                <span className="block font-medium" style={{ color: 'var(--text-primary)' }}>
                  {p.name}
                </span>
                <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  {p.description}
                </span>
              </span>
            </label>
          ))}
        </div>
        <FormHint>
          Persona is a communication style. Identity stays your partner — change anytime.
        </FormHint>
      </SettingsSection>

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
          Remedy is local-first. Chat and tool results still go to{' '}
          <strong style={{ color: 'var(--text-secondary)' }}>your chosen LLM</strong> when
          you use a cloud model. Privacy mode tightens what we send — default stays off for
          maximum speed and capability.
        </FormHint>
        <button
          type="button"
          role="switch"
          aria-checked={privacyMode}
          onClick={() => setPrivacyMode(!privacyMode)}
          className="w-full flex items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left transition-colors"
          style={{
            background: privacyMode
              ? 'color-mix(in srgb, var(--accent) 14%, var(--bg-tertiary))'
              : 'var(--bg-tertiary)',
            border: `1px solid ${privacyMode ? 'var(--accent)' : 'var(--border)'}`,
          }}
          title={
            privacyMode
              ? 'Privacy mode on — click to return to full-speed secret scrub only'
              : 'Privacy mode off — click for tighter tool caps + email/phone scrub to the model'
          }
        >
          <div className="min-w-0">
            <div
              className="text-xs font-semibold"
              style={{ color: privacyMode ? 'var(--accent)' : 'var(--text-primary)' }}
            >
              {privacyMode ? 'Privacy mode on' : 'Privacy mode off'}
            </div>
            <div className="text-[10px] leading-snug mt-0.5" style={{ color: 'var(--text-muted)' }}>
              {privacyMode
                ? 'Email, phone, and SSN shapes redacted · shorter tool results · still secret-safe'
                : 'Lightning path — secrets redacted, full tool context for capable work'}
            </div>
          </div>
          <span
            className="flex-shrink-0 relative inline-flex h-6 w-11 rounded-full transition-colors"
            style={{
              background: privacyMode ? 'var(--accent)' : 'var(--border)',
            }}
            aria-hidden
          >
            <span
              className="absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform"
              style={{
                left: privacyMode ? 'calc(100% - 1.35rem)' : '0.125rem',
              }}
            />
          </span>
        </button>
        <div className="text-[10px] leading-snug mt-2" style={{ color: 'var(--text-muted)' }}>
          Also on the status bar. API keys never leave this PC as model input. Keys stay under{' '}
          <code className="text-[10px]">~/.remedy/auth/</code> (DPAPI on Windows).
        </div>
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
              Choose a folder above to use Project / Home / Untrusted jails.
            </>
          ) : (
            <>
              <strong>Untrusted</strong> = project root only + always Ask for shell/write
              (use for downloaded folders). Full still runs as your Windows user.
            </>
          )}
        </FormHint>
      </SettingsSection>

      {/* Security & power (owner keeps full capability; defaults stay safe) */}
      <SettingsSection {...sectionProps('security-power')}>
        <FormHint>
          Defaults are safe. <strong style={{ color: 'var(--text-secondary)' }}>Auto</strong>{' '}
          approvals and opt-in tools never remove your power — they let Remedy finish work
          when you say so. Hard wipe/privilege blocks stay on for everyone.
        </FormHint>
              <div className="mb-2">
                <div className="text-xs font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
                  Approvals
                </div>
                <div className="flex gap-1 mb-1">
                  {(
                    [
                      { id: 'ask' as const, label: 'Ask', hint: 'Safe default — confirm shell/write/skills' },
                      {
                        id: 'auto' as const,
                        label: 'Auto',
                        hint: 'Work until done — full owner power on trusted scope',
                      },
                    ] as const
                  ).map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => setApprovalMode(m.id)}
                      className="flex-1 py-1.5 rounded text-xs font-medium"
                      title={m.hint}
                      style={{
                        background: approvalMode === m.id ? 'var(--accent)' : 'var(--bg-tertiary)',
                        color: approvalMode === m.id ? '#fff' : 'var(--text-secondary)',
                        border: '1px solid var(--border)',
                      }}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
                <FormHint>
                  {approvalMode === 'auto'
                    ? 'Auto: shell, write, edit, and skills run without prompts (except Untrusted scope). Use when you want Remedy to finish the job.'
                    : 'Ask: high-impact tools show Approve/Deny. Soft-risk patterns are labeled on the banner.'}
                </FormHint>
              </div>
              <div className="mb-2">
                <div className="text-xs font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
                  Thinking level
                </div>
                <div className="flex gap-1 mb-1 flex-wrap">
                  {(['off', 'low', 'medium', 'high'] as const).map((lvl) => (
                    <button
                      key={lvl}
                      type="button"
                      onClick={() => setThinkingLevel(lvl)}
                      className="flex-1 min-w-[3rem] py-1.5 rounded text-xs font-medium capitalize"
                      style={{
                        background: thinkingLevel === lvl ? 'var(--accent)' : 'var(--bg-tertiary)',
                        color: thinkingLevel === lvl ? '#fff' : 'var(--text-secondary)',
                        border: '1px solid var(--border)',
                      }}
                    >
                      {lvl}
                    </button>
                  ))}
                </div>
                <FormHint>
                  Also on the status bar. High = more deliberation when the model supports it.
                </FormHint>
              </div>
              <FormToggle
          checked={privacyMode}
          onChange={setPrivacyMode}
          label="Privacy mode (tighter model egress)"
        />
              <div className="text-[10px] leading-snug mb-1.5 pl-6" style={{ color: 'var(--text-muted)' }}>
                Same control as the Privacy section / status bar. Off by default so Remedy stays fast.
              </div>
              <FormToggle
          checked={webToolsEnabled}
          onChange={setWebToolsEnabled}
          label="Enable web_fetch (public HTTP only)"
        />
              <div className="text-[10px] leading-snug mb-1.5 pl-6" style={{ color: 'var(--text-muted)' }}>
                Opt-in. Private/localhost/metadata hosts are blocked (SSRF + DNS pin). Public web stays available.
              </div>
              <FormToggle
          checked={httpBootstrap}
          onChange={setHttpBootstrap}
          label="Allow browser token bootstrap"
        />
              <div className="text-[10px] leading-snug pl-6" style={{ color: 'var(--text-muted)' }}>
                On (default): browser Web UI can get the local token on loopback. Off: desktop IPC only
                (still full power in the app). Override anytime with{' '}
                <code className="text-[10px]">REMEDY_HTTP_BOOTSTRAP</code>.
              </div>
            </SettingsSection>

            {/* Always ready */}
            <SettingsSection
              {...sectionProps('always-ready')}
            >
              <FormToggle
          checked={launchAtLogin}
          onChange={setLaunchAtLogin}
          label="Start with Windows"
        />
              <FormToggle
          checked={startInTray}
          onChange={setStartInTray}
          label="Start hidden in tray"
        />
              <div className="text-[10px] leading-snug mb-1.5 pl-6" style={{ color: 'var(--text-muted)' }}>
                Off = window opens normally (recommended). On = only a tray icon until you click it.
                Independent of “Start with Windows”.
              </div>
              <label className="flex items-center gap-2 mb-1" style={{ opacity: 0.95 }}>
                <input
                  type="checkbox"
                  checked
                  disabled
                  readOnly
                  style={{ accentColor: 'var(--accent)' }}
                  aria-label="Close window always hides to tray"
                />
                <span style={{ color: 'var(--text-primary)' }}>
                  Close window (✕) always hides to tray
                </span>
              </label>
              <div className="text-[10px] leading-snug mb-1.5 pl-6" style={{ color: 'var(--text-muted)' }}>
                Always on for the always-ready partner: the OS ✕ / Alt+F4 hides Remedy to the
                system tray and keeps the local API running (Web UI and chat stay warm).
                Fully stop Remedy only from the tray menu <strong>Quit</strong> (or app menu Quit) —
                that stops the local server.
              </div>
              <FormToggle
          checked={skipQuitWarn}
          onChange={setSkipQuitWarn}
          label="Don&apos;t warn when quitting (server stops)"
        />
              <FormHint>
                Opt-in only. Uses the Windows <strong>Startup folder</strong> (Settings → Apps → Startup) —
                not the registry Run key. Quit fully stops the local API (browser WebUI dies);
                use <strong>Switch to WebUI</strong> or hide-to-tray to keep the server running.
              </FormHint>
            </SettingsSection>

            {/* Tool process visibility */}
            <SettingsSection
              {...sectionProps('tool-process')}
            >
              <FormHint>
                How much <em>Process</em> detail to show under replies — same list, more depth.
                The chat answer is always complete (never truncated by this setting).
              </FormHint>
              <div className="flex gap-1 mb-1 flex-wrap">
                {TOOL_PROCESS_MODES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => {
                      setToolProcess(m.id)
                      onToolProcessChange?.(m.id)
                    }}
                    className="flex-1 min-w-[3.5rem] py-1.5 rounded text-xs font-medium"
                    title={m.hint}
                    style={{
                      background: toolProcess === m.id ? 'var(--accent)' : 'var(--bg-tertiary)',
                      color: toolProcess === m.id ? '#fff' : 'var(--text-secondary)',
                      border: '1px solid var(--border)',
                    }}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              <FormHint>
                {TOOL_PROCESS_MODES.find((m) => m.id === toolProcess)?.hint}
              </FormHint>
            </SettingsSection>

            {/* RMB — local agent host (llama.cpp, coding + tools) */}
    </>
  )
}
