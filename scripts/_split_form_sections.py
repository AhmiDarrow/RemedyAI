"""Split FormSections.tsx into domain modules with *only used* destructuring."""
from __future__ import annotations

import re
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "desktop" / "src" / "components" / "settings"
# Prefer original if we already overwrote — restore from git if needed
src_path = root / "FormSections.tsx"

# If orchestrator already, recover original from git
text = src_path.read_text(encoding="utf-8")
if "SettingsSections_provider" in text:
    import subprocess

    raw = subprocess.check_output(
        ["git", "show", "HEAD:desktop/src/components/settings/FormSections.tsx"],
        cwd=root.parents[3],
    )
    # parents: settings -> components -> src -> desktop -> Old-Remedy? 
    # root = .../desktop/src/components/settings
    # parents[0]=components [1]=src [2]=desktop [3]=Old-Remedy
    text = raw.decode("utf-8")
    print("restored FormSections from HEAD for split")

lines = text.splitlines(keepends=True)

iface_start = next(i for i, l in enumerate(lines) if l.startswith("export interface SettingsFormProps"))
fn_start = next(i for i, l in enumerate(lines) if l.startswith("export function SettingsFormSections"))
return_idx = next(i for i, l in enumerate(lines) if i > fn_start and l.strip() == "return (")
frag_start = next(i for i, l in enumerate(lines) if i > return_idx and l.strip() == "<>")
frag_end = next(i for i, l in reversed(list(enumerate(lines))) if l.strip() == "</>")

markers: list[tuple[int, str]] = []
for i, l in enumerate(lines):
    m = re.search(r"sectionProps\('([^']+)'\)", l)
    if m and i > frag_start:
        markers.append((i, m.group(1)))

order: list[str] = []
seen: set[str] = set()
for _, n in markers:
    if n not in seen:
        seen.add(n)
        order.append(n)


def section_start(name: str) -> int:
    start = next(i for i, n in markers if n == name)
    s = start
    while s > frag_start:
        t = lines[s].strip()
        if (
            t.startswith("{/*")
            or t.startswith("<SettingsSection")
            or t.startswith("{settingsMode")
            or t.startswith("<MessengersSection")
            or t.startswith("<AssistantSection")
        ):
            return s
        s -= 1
    return start


ranges: dict[str, tuple[int, int]] = {}
for idx, name in enumerate(order):
    s = section_start(name)
    e = section_start(order[idx + 1]) if idx + 1 < len(order) else frag_end
    ranges[name] = (s, e)

groups = {
    "provider": ["provider", "provider-catalog"],
    "identity": [
        "you-agent",
        "workspace",
        "privacy",
        "access",
        "security-power",
        "always-ready",
        "tool-process",
    ],
    "localModels": ["rmb", "vision"],
    "rest": [
        "memory-harness",
        "advanced",
        "channels",
        "assistant",
        "license",
        "theme",
        "help",
        "mcp",
        "about",
    ],
}

# Known prop keys from SettingsFormProps (manual list of destructure names)
PROP_KEYS = [
    "sectionProps",
    "provider",
    "model",
    "setModel",
    "baseUrl",
    "setBaseUrl",
    "apiKey",
    "setApiKey",
    "apiKeySet",
    "projectPath",
    "setProjectPath",
    "browserHomeUrl",
    "setBrowserHomeUrl",
    "privacyShield",
    "persona",
    "setPersona",
    "userName",
    "setUserName",
    "agentName",
    "setAgentName",
    "agentGender",
    "setAgentGender",
    "accessScope",
    "setAccessScope",
    "launchAtLogin",
    "setLaunchAtLogin",
    "startInTray",
    "setStartInTray",
    "closeToTray",
    "setCloseToTray",
    "skipQuitWarn",
    "setSkipQuitWarn",
    "webToolsEnabled",
    "setWebToolsEnabled",
    "httpBootstrap",
    "setHttpBootstrap",
    "privacyMode",
    "setPrivacyMode",
    "approvalMode",
    "setApprovalMode",
    "harnessMode",
    "setHarnessMode",
    "harnessMinPct",
    "setHarnessMinPct",
    "harnessMaxPct",
    "setHarnessMaxPct",
    "thinkingLevel",
    "setThinkingLevel",
    "allowSkillCreation",
    "setAllowSkillCreation",
    "autoApproveThreshold",
    "setAutoApproveThreshold",
    "logLevel",
    "setLogLevel",
    "sarcasmMode",
    "setSarcasmMode",
    "toolProcess",
    "setToolProcess",
    "onToolProcessChange",
    "catalog",
    "showAdvanced",
    "setShowAdvanced",
    "xaiAuth",
    "xaiLoginBusy",
    "xaiUserCode",
    "xaiVerifyUrl",
    "xaiLoginMsg",
    "handleXaiSignIn",
    "handleXaiLogout",
    "vision",
    "swarm",
    "visionBusy",
    "setVisionBusy",
    "visionMsg",
    "setVisionMsg",
    "refreshVision",
    "startVisionInstallPoll",
    "rmb",
    "rmbBusy",
    "setRmbBusy",
    "rmbMsg",
    "setRmbMsg",
    "refreshRmb",
    "onSettingsSaved",
    "connectedList",
    "providerSearch",
    "setProviderSearch",
    "enabledProviders",
    "setEnabledProviders",
    "enabledModels",
    "setEnabledModels",
    "catalogExpand",
    "setCatalogExpand",
    "skillsBudget",
    "setSkillsBudget",
    "primaryProviders",
    "advancedProviders",
    "activeMeta",
    "showBaseUrl",
    "providerModels",
    "customName",
    "setCustomName",
    "handleProviderChange",
    "handleBrowseProject",
    "themeId",
    "onThemeChange",
    "density",
    "onDensityChange",
    "customAccent",
    "onCustomAccentChange",
    "updateInfo",
    "checkingUpdates",
    "updateStatus",
    "onCheckUpdates",
    "onInstallUpdate",
    "onOpenHelp",
    "settings",
    "messengers",
    "messengerDrafts",
    "setMessengerDrafts",
    "assistant",
    "assistantDraft",
    "setAssistantDraft",
    "onAssistantAccountsChanged",
    "settingsMode",
    "models",
]


def used_props(body: str) -> list[str]:
    # Strip string/template/comment noise so word matches mean real identifiers.
    stripped = re.sub(r"/\*.*?\*/", " ", body, flags=re.S)
    stripped = re.sub(r"//.*?$", " ", stripped, flags=re.M)
    stripped = re.sub(r"`(?:\\.|[^`\\])*`", " ", stripped)
    stripped = re.sub(r"'(?:\\.|[^'\\])*'", " ", stripped)
    stripped = re.sub(r'"(?:\\.|[^"\\])*"', " ", stripped)
    used = []
    for k in PROP_KEYS:
        # Require identifier start (not property access like rmb.model / p.models).
        if re.search(rf"(?<![\w.]){k}\b", stripped):
            used.append(k)
    # always need sectionProps
    if "sectionProps" not in used:
        used.insert(0, "sectionProps")
    return used


def build_destructure(used: list[str]) -> str:
    parts = []
    aliases = {
        "closeToTray": "closeToTray: _closeToTray",
        "setCloseToTray": "setCloseToTray: _setCloseToTray",
        "swarm": "swarm: _swarm",
    }
    defaults = {
        "privacyShield": "privacyShield = null",
        "customName": "customName = ''",
        "messengers": "messengers = []",
        "messengerDrafts": "messengerDrafts = {}",
        "assistant": "assistant = null",
        "assistantDraft": "assistantDraft = {}",
        "settingsMode": "settingsMode = 'simple'",
    }
    for k in used:
        if k in defaults:
            parts.append(defaults[k])
        elif k in aliases:
            parts.append(aliases[k])
        else:
            parts.append(k)
    # group related for readability
    return "    " + ",\n    ".join(parts) + ",\n"


types_header = """/** Shared props for Settings form sections. */
import type { Dispatch, SetStateAction } from 'react'
import type { Settings, MessengerInfo } from '../../api/settings'
import type { VisionStatus, NanoSwarmStatus } from '../../api/vision'
import type { RmbStatus } from '../../api/rmb'
import type { XaiAuthStatus } from '../../api/auth'
import type { ProviderInfo, ConnectedProvider } from '../../api/providers'
import type { ThemeId } from '../../themes'
import type { UpdateInfo } from '../../api/updates'
import type { ModelInfo } from '../../App'
import type { Density } from '../../utils/chatPrefs'
import type { SettingsMode } from '../../utils/settingsMode'
import type { ToolProcessMode } from '../../utils/toolLabels'
import type { SettingsSectionId } from '../../utils/settingsSearch'
import type { MessengerDraftMap } from '../../utils/messengerDrafts'
import type { AssistantDraft, AssistantStatus } from './AssistantSection'

"""
types_body = "".join(lines[iface_start:fn_start]).lstrip("\n")
(root / "formTypes.ts").write_text(types_header + types_body, encoding="utf-8")
print("wrote formTypes.ts")


def imports_for(body: str, used: list[str]) -> str:
    imps = [
        "import type { ReactNode } from 'react'",
        "import type { SettingsFormProps } from './formTypes'",
        "import { SettingsSection } from '../SettingsSection'",
    ]
    if re.search(r"\bConnectedProvider\b", body):
        imps.insert(1, "import type { ConnectedProvider } from '../../api/providers'")
    if re.search(r"\bDensity\b", body):
        imps.insert(1, "import type { Density } from '../../utils/chatPrefs'")
    if re.search(r"\bField\b", body) or re.search(r"\bPERSONAS\b", body):
        if re.search(r"\bPERSONAS\b", body):
            imps.append("import { Field, PERSONAS } from './shared'")
        else:
            imps.append("import { Field } from './shared'")
    if re.search(r"\bopenExternalUrl\b", body):
        imps.append("import { openExternalUrl } from '../../api/auth'")
    if re.search(r"\bTHEME_LIST\b", body):
        imps.append("import { THEME_LIST } from '../../themes'")
    if re.search(r"\bThemeColorDot\b", body):
        imps.append("import { ThemeColorDot } from '../ThemeSwitcher'")
    if re.search(r"\bHOTKEYS\b", body):
        imps.append("import { HOTKEYS } from '../../hotkeys'")
    if re.search(r"\bTOOL_PROCESS_MODES\b", body):
        imps.append("import { TOOL_PROCESS_MODES } from '../../utils/toolLabels'")
    if re.search(
        r"\b(activateVisionBundle|installVision|cancelVisionInstall|reinstallVisionRuntime|startVisionServer|stopVisionServer|formatDownloadGb)\b",
        body,
    ):
        imps.append(
            """import {
  activateVisionBundle,
  installVision,
  cancelVisionInstall,
  reinstallVisionRuntime,
  startVisionServer,
  stopVisionServer,
  formatDownloadGb,
} from '../../api/vision'"""
        )
    if re.search(r"\b(startRmb|stopRmb|patchRmbSettings|applyRmbAsProvider)\b", body):
        imps.append(
            """import {
  startRmb,
  stopRmb,
  patchRmbSettings,
  applyRmbAsProvider,
} from '../../api/rmb'"""
        )
    if re.search(r"\bupdateSettings\b", body):
        imps.append("import { updateSettings } from '../../api/settings'")
    if re.search(r"\bMessengersSection\b", body):
        imps.append("import { MessengersSection } from './MessengersSection'")
    if re.search(r"\bAssistantSection\b", body):
        imps.append("import { AssistantSection } from './AssistantSection'")
    if re.search(r"\bgetServerUrl\b", body):
        imps.append("import { getServerUrl } from '../../api/client'")
    return "\n".join(imps) + "\n\n"


for g, ids in groups.items():
    chunks: list[str] = []
    for sid in ids:
        if sid not in ranges:
            continue
        s, e = ranges[sid]
        chunks.append("".join(lines[s:e]))
    body = "".join(chunks)
    used = used_props(body)
    # always include setCustomName if customName used
    if "customName" in used and "setCustomName" not in used:
        used.append("setCustomName")
    d = build_destructure(used)
    fn = (
        f"/** Settings form sections — {g}. */\n"
        + imports_for(body, used)
        + f"export function SettingsSections_{g}(p: SettingsFormProps): ReactNode {{\n"
        + f"  const {{\n{d}  }} = p\n\n"
        + "  return (\n    <>\n"
        + body
        + "    </>\n  )\n}\n"
    )
    # polish selects
    fn = re.sub(
        r'className="w-full rounded px-2 py-1 text-xs mb-2 outline-none"\s*\n\s*style=\{\{\s*\n\s*background: \'var\(--bg-tertiary\)\',\s*\n\s*color: \'var\(--text-primary\)\',\s*\n\s*border: \'1px solid var\(--border\)\',\s*\n\s*\}\}',
        'className="ui-select w-full mb-2"',
        fn,
    )
    fn = re.sub(
        r'className="w-full rounded px-2 py-1 text-xs outline-none"\s*\n\s*style=\{\{\s*\n\s*background: \'var\(--bg-tertiary\)\',\s*\n\s*color: \'var\(--text-primary\)\',\s*\n\s*border: \'1px solid var\(--border\)\',\s*\n\s*\}\}',
        'className="ui-select w-full"',
        fn,
    )
    fn = fn.replace(
        'className="block mb-1" style={{ color: \'var(--text-muted)\' }}',
        'className="block mb-1 text-[0.68rem] font-semibold uppercase tracking-wide" style={{ color: \'var(--text-muted)\' }}',
    )
    fn = fn.replace(
        'className="block mb-0.5" style={{ color: \'var(--text-muted)\' }}',
        'className="block mb-1 text-[0.68rem] font-semibold uppercase tracking-wide" style={{ color: \'var(--text-muted)\' }}',
    )
    out = root / f"sections_{g}.tsx"
    out.write_text(fn, encoding="utf-8")
    print(f"wrote {out.name} used={len(used)} body={len(body.splitlines())}")

main = """/** Settings form sections — domain orchestrator (FormSections attack). */
import type { ReactNode } from 'react'
import type { SettingsFormProps } from './formTypes'
export type { SettingsFormProps } from './formTypes'
import { SettingsSections_provider } from './sections_provider'
import { SettingsSections_identity } from './sections_identity'
import { SettingsSections_localModels } from './sections_localModels'
import { SettingsSections_rest } from './sections_rest'

/** Renders all settings sections, split by domain for maintainability. */
export function SettingsFormSections(p: SettingsFormProps): ReactNode {
  return (
    <div className="settings-form-sections space-y-3">
      <SettingsSections_provider {...p} />
      <SettingsSections_identity {...p} />
      <SettingsSections_localModels {...p} />
      <SettingsSections_rest {...p} />
    </div>
  )
}
"""
src_path.write_text(main, encoding="utf-8")
print("rewrote FormSections.tsx")
print("done")
