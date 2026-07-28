# Computer use — local soak checklist

**Branch:** `feature/computer-use` only. Do **not** push or merge until this checklist is solid.

Run server + Desktop from this branch (not the installed stable release).

## Preconditions

- [ ] Checkout `feature/computer-use`
- [ ] Local server from this tree (`remedy serve` / desktop sidecar)
- [ ] Desktop app built/running with host poller (status chip **PC host** when live)
- [ ] Build mode (not Plan) for click/type tests

## Desktop path (no browser rail required)

- [ ] `computer_monitors` returns at least one display
- [ ] `computer_screenshot` saves a PNG under `~/.remedy/computer/shots/`
- [ ] `computer_screenshot monitor=0` captures primary
- [ ] `computer_snapshot` returns window refs `w1…` with titles
- [ ] `computer_snapshot mode=controls` returns UIA `c1…` when comtypes installed (optional)
- [ ] `computer_click ref=wN` focuses that window (visible raise)
- [ ] `computer_click ref=cN` hits a control (if UIA available)
- [ ] `computer_type` types into a focused notepad / editor
- [ ] **Stop** mid-type stops further input (no runaway keystrokes)

## Browser path (Desktop host live)

- [ ] **PC host** chip visible in status bar
- [ ] `computer_navigate` opens URL in **in-app** Browser rail (rail auto-opens)
- [ ] `computer_snapshot` returns `e1…` elements
- [ ] `computer_click ref=eN` activates that control
- [ ] `computer_screenshot` returns WebView PrintWindow or rail crop (path under `~/.remedy/computer/shots/`)
- [ ] Stop while a browser job is pending cancels it (no stuck host)

## Hybrid / routing

- [ ] URL-ish task prefers browser tools when host is up
- [ ] “Open Start menu / desktop installer” uses desktop tools
- [ ] Host offline: navigate falls back to system browser; snapshot falls back to windows

## Plan mode

- [ ] Snapshot / screenshot / navigate / monitors allowed
- [ ] Click / type blocked with Plan mode message

## Provider-agnostic

- [ ] Same tools work under at least two chat providers (e.g. xAI + DeepSeek)

## Regression (coding agency still works)

- [ ] Short file edit + `bash_exec` still works
- [ ] Concurrent turns / session switch still OK

## Sign-off

| Field | Value |
|-------|--------|
| Date | |
| Tester | |
| Branch SHA | |
| Ready to merge master? | no / yes |
| Notes | |

When solid: merge to master locally, then only push when you explicitly choose a release path.
