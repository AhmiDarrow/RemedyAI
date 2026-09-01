/** Settings form sections — rest. */
import type { ReactNode } from 'react'
import type { SettingsFormProps } from './formTypes'
import { FONT_SCALE_OPTIONS, type Density } from '../../utils/chatPrefs'
import { SettingsSection } from '../SettingsSection'
import {
  FormActionButton,
  FormHint,
  FormLabel,
  FormRange,
  FormSegmented,
  FormSelect,
  FormToggle,
} from './formUi'
import { openExternalUrl } from '../../api/auth'
import { THEME_LIST } from '../../themes'
import { ThemeColorDot } from '../ThemeSwitcher'
import { HOTKEYS } from '../../hotkeys'
import { MessengersSection } from './MessengersSection'
import { AssistantSection } from './AssistantSection'
import { LicenseText, ThirdPartyNotices } from './ThirdPartyNotices'
import { getServerUrl } from '../../api/client'

export function SettingsSections_rest(p: SettingsFormProps): ReactNode {
  const {
    sectionProps,
    harnessMode,
    setHarnessMode,
    harnessMinPct,
    setHarnessMinPct,
    harnessMaxPct,
    setHarnessMaxPct,
    allowSkillCreation,
    setAllowSkillCreation,
    autoApproveThreshold,
    setAutoApproveThreshold,
    logLevel,
    setLogLevel,
    sarcasmMode,
    setSarcasmMode,
    claimidxPublicLedger,
    setClaimidxPublicLedger,
    themeId,
    onThemeChange,
    density,
    onDensityChange,
    customAccent,
    onCustomAccentChange,
    fontScale = 'md',
    onFontScaleChange,
    reduceMotion = false,
    onReduceMotionChange,
    highContrast = false,
    onHighContrastChange,
    updateInfo,
    checkingUpdates,
    updateStatus,
    onCheckUpdates,
    onInstallUpdate,
    onOpenHelp,
    settings,
    messengers = [],
    messengerDrafts = {},
    setMessengerDrafts,
    assistant = null,
    assistantDraft = {},
    setAssistantDraft,
    onAssistantAccountsChanged,
    settingsMode = 'simple',
  } = p

  return (
    <>
      <SettingsSection
        {...sectionProps('memory-harness')}
      >
        <FormLabel>Mode</FormLabel>
        <FormSelect value={harnessMode} onChange={setHarnessMode} className="mb-1">
          <option value="auto">Auto — prune + nudge compress</option>
          <option value="manual">Manual — /compact only</option>
          <option value="off">Off</option>
        </FormSelect>
        <FormHint>
          Keeps long chats lean without deleting your transcript. Use{' '}
          <code style={{ color: 'var(--accent)' }}>/compact</code> or{' '}
          <code style={{ color: 'var(--accent)' }}>/harness</code>.
        </FormHint>
        <FormLabel>
          Auto min context % ({Math.round(harnessMinPct * 100)}%)
        </FormLabel>
        <FormRange
          min={5}
          max={90}
          step={1}
          value={Math.round(harnessMinPct * 100)}
          onChange={(pct) => {
            const next = pct / 100
            setHarnessMinPct(next)
            if (next >= harnessMaxPct) {
              setHarnessMaxPct(Math.min(0.99, next + 0.02))
            }
          }}
        />
        <FormLabel>
          Auto max context % ({Math.round(harnessMaxPct * 100)}%)
        </FormLabel>
        <FormRange
          min={10}
          max={99}
          step={1}
          value={Math.round(harnessMaxPct * 100)}
          onChange={(pct) => {
            const next = pct / 100
            setHarnessMaxPct(next)
            if (next <= harnessMinPct) {
              setHarnessMinPct(Math.max(0.05, next - 0.02))
            }
          }}
        />
        <FormHint>
          In Auto mode, prune starts near min and compress is nudged by max. Defaults 75% / 92%.
        </FormHint>
      </SettingsSection>

      {/* Advanced */}
      <SettingsSection
        {...sectionProps('advanced')}
      >
        <FormToggle
          checked={allowSkillCreation}
          onChange={setAllowSkillCreation}
          label="Allow skill creation (learning loop)"
        />
        <FormLabel>
          Learning auto-approve threshold ({autoApproveThreshold.toFixed(2)})
        </FormLabel>
        <FormRange
          min={0}
          max={100}
          step={5}
          value={Math.round(autoApproveThreshold * 100)}
          onChange={(pct) => setAutoApproveThreshold(pct / 100)}
        />
        <FormLabel>Log level</FormLabel>
        <FormSelect value={logLevel} onChange={setLogLevel}>
          {['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((l) => (
            <option key={l} value={l}>{l}</option>
          ))}
        </FormSelect>
        <FormToggle
          checked={sarcasmMode}
          onChange={setSarcasmMode}
          label="Sarcasm mode (tone flag)"
        />
        <FormToggle
          checked={claimidxPublicLedger}
          onChange={setClaimidxPublicLedger}
          label="Claimidx public ledger (opt-in claim submission)"
        />
        <FormHint>
          When enabled, Remedy can submit sanitized claims to the public GitHub ledger at
          github.com/claimidx/claimidx. Disabled by default — no data leaves your machine without this on.
        </FormHint>
        <FormHint>
          Advanced knobs only — defaults keep full owner power. Skill creation stays on so Remedy can improve.
        </FormHint>
      </SettingsSection>

      {setMessengerDrafts ? (
        <MessengersSection
          sectionProps={sectionProps('channels')}
          messengers={messengers}
          messengerDrafts={messengerDrafts}
          setMessengerDrafts={setMessengerDrafts}
        />
      ) : (
        <SettingsSection {...sectionProps('channels')}>
          <FormHint>
            Messenger settings unavailable.
          </FormHint>
        </SettingsSection>
      )}

      {setAssistantDraft ? (
        <AssistantSection
          sectionProps={sectionProps('assistant')}
          assistant={assistant}
          draft={assistantDraft}
          setDraft={setAssistantDraft}
          onAccountsChanged={onAssistantAccountsChanged}
          settingsMode={settingsMode}
        />
      ) : (
        <SettingsSection {...sectionProps('assistant')}>
          <FormHint>
            Personal assistant settings unavailable.
          </FormHint>
        </SettingsSection>
      )}

      {/* License */}
      <SettingsSection
        {...sectionProps('license')}
      >
        <div className="text-[10px] leading-snug space-y-1.5" style={{ color: 'var(--text-secondary)' }}>
          <p style={{ margin: 0 }}>
            <strong style={{ color: 'var(--text-primary)' }}>Source-available</strong> —
            free for solo developers and small indies (&lt;$1M revenue and &lt;20 FTE).
            Personal / education / research free.
          </p>
          <p style={{ margin: 0 }}>
            Larger orgs, multi-tenant SaaS, or commercial resale: email{' '}
            <code style={{ color: 'var(--accent)' }}>ahmitdarrow@gmail.com</code>
            {' '}(subject: RemedyAI commercial license).
          </p>
          <p style={{ margin: 0, color: 'var(--text-muted)' }}>
            Copyright (c) 2025–2026 Ahmi Darrow. Binding terms below and in repo LICENSE;
            summary in COMMERCIAL.md. The Windows installer shows the same terms before
            install. This build has no license keys or phone-home. Paid commercial
            licenses are available; the free grant is for qualifying users of this copy.
            You are responsible for how you use Remedy, including sites and accounts
            you point it at.
          </p>
          <LicenseText />
          {onOpenHelp ? (
            <button
              type="button"
              className="text-xs underline"
              style={{ color: 'var(--accent)', background: 'none', border: 0, padding: 0 }}
              onClick={() => onOpenHelp('04-security-and-data')}
            >
              Open Security &amp; data (includes bootstrap / power notes) →
            </button>
          ) : null}
          <ThirdPartyNotices />
        </div>
      </SettingsSection>

      {/* Theme */}
      <SettingsSection
        {...sectionProps('theme')}
      >
        <div className="flex flex-col gap-1">
          {THEME_LIST.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => onThemeChange(t.id)}
              className={`settings-choice-row flex items-center gap-2 px-2 py-1.5 text-xs text-left w-full${
                t.id === themeId ? ' is-active' : ''
              }`}
              style={{ color: 'var(--text-primary)' }}
            >
              <ThemeColorDot themeId={t.id} />
              <span>
                {t.name}
                {t.id === 'system' ? (
                  <span style={{ color: 'var(--text-muted)' }}> · match OS</span>
                ) : null}
              </span>
              {t.id === themeId && (
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="ml-auto">
                  <path d="M2 6l3 3 5-5" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </button>
          ))}
        </div>

        <div className="mt-3 space-y-2">
          <FormLabel>Text size</FormLabel>
          <FormSegmented
            value={fontScale}
            onChange={(s) => onFontScaleChange?.(s)}
            options={FONT_SCALE_OPTIONS.map((o) => ({
              id: o.id,
              label: o.label,
              title:
                o.id === 'sm'
                  ? 'Smaller text'
                  : o.id === 'md'
                    ? 'Default text'
                    : o.id === 'lg'
                      ? 'Larger text'
                      : 'Largest text',
            }))}
          />
          <FormHint>
            Scales chat, Settings, and menus. Shortcuts: Ctrl + / Ctrl − / Ctrl 0.
          </FormHint>
          <FormLabel>Density</FormLabel>
          <FormSegmented
            value={(density === 'compact' ? 'compact' : 'cozy') as Density}
            onChange={(d) => onDensityChange?.(d)}
            options={[
              { id: 'cozy', label: 'Cozy' },
              { id: 'compact', label: 'Compact' },
            ]}
          />
          <FormToggle
            checked={highContrast}
            onChange={(on) => onHighContrastChange?.(on)}
            label="Higher contrast"
          />
          <FormToggle
            checked={reduceMotion}
            onChange={(on) => onReduceMotionChange?.(on)}
            label="Reduce motion"
          />
          <FormLabel className="mt-2">Custom accent (optional)</FormLabel>
          <div className="flex items-center gap-2 mb-2">
            <input
              type="color"
              value={customAccent && /^#/.test(customAccent) ? customAccent : '#8b5cf6'}
              onChange={(e) => onCustomAccentChange?.(e.target.value)}
              className="w-8 h-8 rounded cursor-pointer border-0 bg-transparent"
              title="Pick accent color"
            />
            <input
              type="text"
              value={customAccent}
              onChange={(e) => onCustomAccentChange?.(e.target.value)}
              placeholder="#hex or empty"
              className="ui-input flex-1 font-mono"
            />
            {customAccent && (
              <FormActionButton onClick={() => onCustomAccentChange?.('')}>
                Reset
              </FormActionButton>
            )}
          </div>
        </div>
      </SettingsSection>

      {/* Help / Keyboard */}
      <SettingsSection
        {...sectionProps('help')}
      >
        <div className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
          Enter sends · Shift+Enter new line · F1 opens Help
        </div>
        {onOpenHelp && (
          <div className="flex flex-col gap-1.5 mb-2">
            <FormActionButton
              variant="primary"
              onClick={() => onOpenHelp()}
              className="w-full"
            >
              Open Help
            </FormActionButton>
            <div className="flex flex-wrap gap-1">
              {(
                [
                  ['02-first-run', 'Setup'],
                  ['03-providers-and-auth', 'Providers'],
                  ['14-visual-decoder', 'Vision'],
                  ['09-troubleshooting', 'Troubleshoot'],
                  ['13-whats-new', "What's new"],
                ] as const
              ).map(([id, label]) => (
                <FormActionButton key={id} onClick={() => onOpenHelp(id)}>
                  {label}
                </FormActionButton>
              ))}
            </div>
          </div>
        )}
        <div
          className="rounded-md overflow-hidden text-xs"
          style={{ border: '1px solid var(--border)' }}
        >
          {HOTKEYS.map((h) => (
            <div
              key={`${h.keys}-${h.action}`}
              className="flex justify-between gap-2 px-2 py-1.5"
              style={{
                borderBottom: '1px solid var(--border)',
                color: 'var(--text-secondary)',
              }}
            >
              <code style={{ color: 'var(--accent)', whiteSpace: 'nowrap' }}>{h.keys}</code>
              <span className="text-right" style={{ color: 'var(--text-primary)' }}>
                {h.action}
              </span>
            </div>
          ))}
        </div>
        <div className="text-xs mt-2 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          Connect a provider above to chat. Plan mode explores without editing files;
          Build mode can change your project. Data stays in ~/.remedy on this machine.
        </div>
      </SettingsSection>

      {/* MCP host — export skills to external MCP clients */}
      <SettingsSection
        {...sectionProps('mcp')}
      >
        <div className="text-xs space-y-2" style={{ color: 'var(--text-secondary)' }}>
          <p style={{ margin: 0, fontSize: '0.75rem' }}>
            Run Remedy as an MCP <strong>server</strong> so other MCP-compatible
            tools can use skills and plans on <em>this machine</em> (same-owner, local only).
          </p>
          <div
            className="p-2 rounded font-mono text-[11px] break-all"
            style={{
              background: 'var(--bg-primary)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
          >
            remedy mcp serve
            <br />
            # or: remedy-mcp
          </div>
          <button
            type="button"
            className="w-full py-1.5 rounded text-xs font-medium"
            style={{
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
            onClick={() => {
              const snippet = JSON.stringify(
                {
                  mcpServers: {
                    remedy: {
                      command: 'remedy-mcp',
                      args: [],
                    },
                  },
                },
                null,
                2,
              )
              void navigator.clipboard.writeText(snippet).catch(() => {
                /* ignore */
              })
            }}
          >
            Copy MCP client JSON
          </button>
          <ul
            className="m-0 pl-4 space-y-1"
            style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}
          >
            <li>
              Tools: skill list/search/get, plan list/show; script run opt-in only.
            </li>
            <li>Quarantined skills stay blocked until you Trust them in Skills.</li>
            <li>
              Script execution: set env <code>REMEDY_MCP_ALLOW_RUN=1</code> (owner choice).
            </li>
            <li>Logs go to stderr so the MCP JSON stream stays clean.</li>
          </ul>
          {onOpenHelp && (
            <button
              type="button"
              className="text-xs underline"
              style={{ color: 'var(--accent)', background: 'none', border: 0, padding: 0 }}
              onClick={() => onOpenHelp('10-cli-and-api')}
            >
              Open CLI &amp; API help →
            </button>
          )}
        </div>
      </SettingsSection>

      {/* About */}
      <SettingsSection
        {...sectionProps('about')}
      >
        <div className="flex items-center gap-3 mb-3">
          <img
            src="/icon.png"
            alt=""
            draggable={false}
            style={{ height: 36, width: 36, objectFit: 'contain', borderRadius: 8 }}
          />
          <img
            src="/logo.png"
            alt="Remedy"
            draggable={false}
            style={{ height: 28, width: 'auto', maxWidth: 180, objectFit: 'contain' }}
          />
        </div>
        <div className="text-xs mb-3 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          Personal AI partner on this machine — chat, files, terminal, browser rail,
          computer use. Continuity under{' '}
          <code style={{ fontSize: '0.65rem' }}>~/.remedy</code>; your model keys stay yours.
          Not a medical product.
        </div>
        <div
          className="rounded-lg px-3 py-2 mb-3 text-xs leading-relaxed"
          style={{
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
        >
          <div className="font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
            From the creator
          </div>
          My name is Ahmi, I hope you enjoy my Remedy.
        </div>
        <div className="space-y-1" style={{ color: 'var(--text-secondary)' }}>
          <div className="flex justify-between">
            <span style={{ color: 'var(--text-muted)' }}>Version</span>
            <span>
              v
              {updateInfo?.current_version
                || settings?.version
                || '—'}
            </span>
          </div>
          <div className="flex justify-between">
            <span style={{ color: 'var(--text-muted)' }}>Config</span>
            <span style={{ fontFamily: 'monospace', fontSize: '0.65rem' }}>~/.remedy/config.toml</span>
          </div>
        </div>
        {onOpenHelp && (
          <button
            type="button"
            className="mt-2 text-xs underline"
            style={{ color: 'var(--accent)', background: 'none', border: 0, padding: 0 }}
            onClick={() => onOpenHelp('13-whats-new')}
          >
            What&apos;s new in this release →
          </button>
        )}

        <div className="mt-3 pt-3 border-t space-y-1.5" style={{ borderColor: 'var(--border)' }}>
          <button
            type="button"
            onClick={() => {
              void (async () => {
                try {
                  const { isTauri, tauriInvoke } = await import('../../api/tauri')
                  if (isTauri()) {
                    await tauriInvoke('switch_to_web_ui')
                    return
                  }
                } catch (e) {
                  console.warn('switch_to_web_ui:', e)
                }
                await openExternalUrl(getServerUrl() + '/')
              })()
            }}
            className="w-full py-1.5 rounded text-xs font-medium transition-colors"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
            title="Minimize desktop to tray and open the WebUI chat in your default browser"
          >
            Switch to WebUI…
          </button>
          <div className="text-[10px] px-0.5" style={{ color: 'var(--text-muted)' }}>
            Hides Remedy to the tray and opens the local WebUI at{' '}
            <code style={{ color: 'var(--accent)' }}>{getServerUrl()}/</code>
            . Tray → Show Remedy returns to desktop.
          </div>
          <button
            type="button"
            onClick={() => {
              void onCheckUpdates()
            }}
            disabled={checkingUpdates}
            className="w-full py-1.5 rounded text-xs font-medium transition-colors"
            style={{
              background: checkingUpdates ? 'var(--bg-secondary)' : 'var(--bg-tertiary)',
              color: checkingUpdates ? 'var(--text-muted)' : 'var(--text-primary)',
              border: '1px solid var(--border)',
              cursor: checkingUpdates ? 'wait' : 'pointer',
            }}
          >
            {checkingUpdates ? 'Checking for updates…' : 'Check for Updates'}
          </button>
          <button
            type="button"
            onClick={() => {
              const ver =
                updateInfo?.current_version
                || settings?.version
                || 'unknown'
              void import('../../utils/reportIssue').then(({ openReportIssue }) =>
                openReportIssue(ver),
              )
            }}
            className="w-full py-1.5 rounded text-xs font-medium transition-colors"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
            title="Open GitHub Issues in your browser"
          >
            Report an issue on GitHub…
          </button>
          <div className="text-[10px] px-0.5" style={{ color: 'var(--text-muted)' }}>
            Opens{' '}
            <button
              type="button"
              className="underline"
              style={{ color: 'var(--accent)' }}
              onClick={() =>
                void import('../../utils/reportIssue').then(({ githubIssuesUrl }) =>
                  openExternalUrl(githubIssuesUrl()),
                )
              }
            >
              github.com/AhmiDarrow/RemedyAI/issues
            </button>
            {' '}— include version and steps when you can.
          </div>
          {/* Always reserve status area so a check never looks like a no-op */}
          <div className="mt-2 space-y-1 min-h-[2.5rem]">
            {checkingUpdates && (
              <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                Contacting GitHub releases…
              </div>
            )}
            {!checkingUpdates && updateStatus && (
              <div
                className="text-xs break-words"
                style={{
                  color: updateInfo?.update_available
                    ? 'var(--accent)'
                    : updateInfo?.error
                      ? 'var(--error, #ef4444)'
                      : 'var(--text-secondary)',
                }}
              >
                {updateStatus}
              </div>
            )}
            {updateInfo && (
                    <>
                <div className="flex justify-between text-xs">
                  <span style={{ color: 'var(--text-muted)' }}>This app</span>
                  <span style={{ color: 'var(--text-primary)' }}>
                    v{updateInfo.current_version}
                  </span>
                </div>
                {(updateInfo.latest_desktop || updateInfo.latest_python) && (
                  <div className="flex justify-between text-xs">
                    <span style={{ color: 'var(--text-muted)' }}>Latest release</span>
                    <span
                      style={{
                        color: updateInfo.update_available
                          ? 'var(--accent)'
                          : 'var(--text-primary)',
                      }}
                    >
                      v{updateInfo.latest_desktop || updateInfo.latest_python}
                    </span>
                  </div>
                )}
                {updateInfo.python_version &&
                  updateInfo.python_version !== updateInfo.current_version && (
                    <div className="flex justify-between text-xs">
                      <span style={{ color: 'var(--text-muted)' }}>Sidecar</span>
                      <span style={{ color: 'var(--text-muted)' }}>
                        v{updateInfo.python_version}
                      </span>
                    </div>
                  )}
                {updateInfo.update_available && (
                  <button
                    type="button"
                    onClick={() => onInstallUpdate?.()}
                    className="w-full mt-2 py-2 rounded text-xs font-semibold"
                    style={{ background: 'var(--accent)', color: '#fff' }}
                  >
                    Update & Relaunch
                  </button>
                )}
                {updateInfo.update_available && (
                  <div className="mt-1 text-[0.65rem]" style={{ color: 'var(--text-muted)' }}>
                    Downloads the installer, updates Remedy, and restarts — like Ollama.
                  </div>
                )}
                {!updateInfo.update_available &&
                  !updateInfo.error &&
                  !checkingUpdates && (
                    <div className="mt-1 text-xs" style={{ color: 'var(--success)' }}>
                      You&apos;re up to date
                    </div>
                  )}
                {updateInfo.error && (
                  <div
                    className="mt-1 text-xs break-words"
                    style={{ color: 'var(--error, #ef4444)' }}
                    title={updateInfo.error}
                  >
                    {updateInfo.error}
                  </div>
                )}
                    </>
                  )}
                </div>
              </div>
            </SettingsSection>
    </>
  )
}
