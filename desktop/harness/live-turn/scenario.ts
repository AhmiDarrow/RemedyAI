/**
 * A scripted long agent turn, modelled on a real portal-addon repair:
 * thinking, eight interim narration lines, ~40 tool calls (a few failing),
 * server progress, then the final answer. Times are ms from turn start.
 */
export type ScenarioEvent =
  | { at: number; type: 'thinking'; text: string }
  | { at: number; type: 'text'; text: string }
  | { at: number; type: 'tool_call'; id: string; name: string; args: Record<string, unknown> }
  | { at: number; type: 'tool_result'; id: string; name: string; ok: boolean; preview: string }
  | { at: number; type: 'progress'; label: string; step: number; total: number }
  | { at: number; type: 'status'; text: string }

const PORTAL = 'C:\\Games\\Portal\\addons'

const NARRATION = [
  "I'll reach the portal folder through the shell.",
  'Cmd is eating the search patterns; switching to PowerShell for addon-agnostic work.',
  'Found the addon manifest. Reading the loader and its config next.',
  'The loader points at a renamed entry file, so the addon never starts.',
  'Patching the entry path and the version guard in the manifest.',
  'The project tools are in place. Next I am wiring the loader.',
  'Running the addon self-check against the patched files.',
  'Self-check passes. Cleaning up the scratch copies I made.',
]

type Tool = [name: string, args: Record<string, unknown>, ok: boolean, preview: string]

const ROUNDS: Tool[][] = [
  [
    ['bash', { command: `cd ${PORTAL} && dir` }, true, '14 File(s)  3 Dir(s)'],
    ['bash', { command: 'findstr /s /i "loader" *.json' }, false, 'FINDSTR: Cannot open *.json'],
    ['bash', { command: 'findstr /s /i "entry" *.lua' }, false, 'FINDSTR: Bad command line'],
  ],
  [
    ['bash', { command: 'powershell -c "Get-ChildItem -Recurse -Filter *.json"' }, true, 'manifest.json, config.json, …'],
    ['grep', { pattern: 'entry', path: `${PORTAL}\\portal-hub` }, true, '3 matches'],
    ['grep', { pattern: 'loader', path: `${PORTAL}\\portal-hub` }, true, '5 matches'],
    ['file_read', { path: `${PORTAL}\\portal-hub\\manifest.json` }, true, '42 lines'],
    ['glob', { pattern: '**/*.lua' }, true, '18 files'],
  ],
  [
    ['file_read', { path: `${PORTAL}\\portal-hub\\loader.lua` }, true, '211 lines'],
    ['file_read', { path: `${PORTAL}\\portal-hub\\config.json` }, true, '17 lines'],
    ['grep', { pattern: 'require\\(', path: `${PORTAL}\\portal-hub` }, true, '9 matches'],
    ['file_read', { path: `${PORTAL}\\portal-hub\\init.lua` }, false, 'file not found'],
    ['glob', { pattern: '**/init*.lua' }, true, 'init_main.lua'],
  ],
  [
    ['file_read', { path: `${PORTAL}\\portal-hub\\init_main.lua` }, true, '96 lines'],
    ['bash', { command: 'git -C portal-hub log --oneline -5' }, true, '5 commits'],
    ['bash', { command: 'git -C portal-hub show --stat HEAD~1' }, true, 'renamed init.lua → init_main.lua'],
    ['grep', { pattern: 'init.lua', path: `${PORTAL}` }, true, '2 matches'],
  ],
  [
    ['file_write', { path: `${PORTAL}\\portal-hub\\manifest.json` }, true, 'wrote 42 lines'],
    ['file_write', { path: `${PORTAL}\\portal-hub\\loader.lua` }, true, 'wrote 211 lines'],
    ['file_read', { path: `${PORTAL}\\portal-hub\\manifest.json` }, true, '42 lines'],
    ['bash', { command: 'luac -p loader.lua' }, false, 'luac: not recognized'],
    ['bash', { command: 'powershell -c "Test-Json (gc manifest.json -Raw)"' }, true, 'True'],
    ['grep', { pattern: 'min_version', path: `${PORTAL}\\portal-hub` }, true, '1 match'],
  ],
  [
    ['file_write', { path: `${PORTAL}\\portal-hub\\config.json` }, true, 'wrote 17 lines'],
    ['file_read', { path: `${PORTAL}\\portal-hub\\loader.lua` }, true, '211 lines'],
    ['bash', { command: 'portal.exe --addon-check portal-hub' }, false, 'exit 3: entry not registered'],
    ['file_write', { path: `${PORTAL}\\portal-hub\\loader.lua` }, true, 'wrote 214 lines'],
    ['bash', { command: 'portal.exe --addon-check portal-hub' }, true, 'ok'],
  ],
  [
    ['bash', { command: 'portal.exe --addon-check --all' }, true, '6 addons ok'],
    ['file_read', { path: `${PORTAL}\\portal-hub\\addon.log` }, true, '12 lines'],
    ['grep', { pattern: 'ERROR', path: `${PORTAL}\\portal-hub\\addon.log` }, true, 'no matches'],
    ['bash', { command: 'git -C portal-hub diff --stat' }, true, '3 files changed'],
  ],
  [
    ['bash', { command: 'del /q portal-hub\\*.bak' }, true, 'removed 3 files'],
    ['glob', { pattern: '**/*.bak' }, true, 'no files'],
    ['bash', { command: 'git -C portal-hub status --short' }, true, ' M 3 files'],
    ['file_read', { path: `${PORTAL}\\portal-hub\\manifest.json` }, true, '42 lines'],
  ],
]

const THINKING =
  'The addon folder is under the portal install. The search pattern escaping in cmd is the problem, ' +
  'so use PowerShell for the recursive search. Then compare the manifest entry with what the loader requires. '

export const ANSWER =
  "The portal addon loads again.\n\n" +
  "**What was wrong:** the last update renamed `init.lua` to `init_main.lua`, but `manifest.json` and " +
  "`loader.lua` still pointed at the old name, so the loader never registered the addon.\n\n" +
  "**What I changed:**\n" +
  "- `manifest.json`: entry now `init_main.lua`; minimum version guard relaxed to 2.4.\n" +
  "- `loader.lua`: registers the entry before the version check.\n" +
  "- `config.json`: removed the stale `init.lua` override.\n\n" +
  "`portal.exe --addon-check --all` reports all 6 addons OK. Restart the portal to pick it up."

export function buildScenario(): { events: ScenarioEvent[]; end: number } {
  const ev: ScenarioEvent[] = []
  let t = 150
  for (const chunk of THINKING.match(/.{1,24}(\s|$)/g) || []) {
    ev.push({ at: t, type: 'thinking', text: chunk })
    t += 60
  }
  let n = 0
  const total = ROUNDS.reduce((a, r) => a + r.length, 0)
  ROUNDS.forEach((round, ri) => {
    const line = NARRATION[ri]!
    for (const w of line.match(/\S+\s*/g) || []) {
      ev.push({ at: t, type: 'text', text: w })
      t += 25
    }
    t += 120
    for (const [name, args, ok, preview] of round) {
      const id = `call_${++n}`
      ev.push({ at: t, type: 'tool_call', id, name, args })
      t += 90 + ((n * 37) % 110)
      ev.push({ at: t, type: 'tool_result', id, name, ok, preview })
      ev.push({ at: t, type: 'progress', label: 'Taking that in…', step: n, total })
      t += 30
    }
    if (ri === 3) {
      ev.push({ at: t, type: 'thinking', text: 'Rename confirmed in history. Patch manifest and loader together. ' })
      t += 80
    }
    if (ri === 5) {
      const later =
        'The first self-check failed because the entry still was not registered, so the second write to the loader has to happen before I trust the all-addon run. ' +
        'I should also read the addon log after the check passes, in case a warning slipped through. ' +
        'Only then is it safe to delete the scratch copies and leave a clean tree.'
      ev.push({ at: t, type: 'thinking', text: later })
      t += 120
    }
  })
  t += 200
  for (const w of ANSWER.match(/\S+\s*/g) || []) {
    ev.push({ at: t, type: 'text', text: w })
    t += 18
  }
  return { events: ev, end: t + 50 }
}
