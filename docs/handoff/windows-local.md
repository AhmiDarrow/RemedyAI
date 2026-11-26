# PC checks for the bug-sweep and red-team branch

Branch: `claude/project-thread-c6n2kw`. Everything here was fixed and tested
on a Linux cloud box. These are the checks that need your PC: Windows,
macOS, a real GUI, hardware or a phone. Each item says what to do and what
"passing" looks like. Nothing on this branch is merged to `master`.

## 0. The full local CI on Windows

1. `git fetch origin claude/project-thread-c6n2kw && git checkout claude/project-thread-c6n2kw`
2. `uv run python scripts/prepush.py`
3. Passing: every lane green except the CHANGELOG `[Unreleased]` check,
   which was already failing on `master` (your notes there are untouched;
   whether to move them to `docs/UNRELEASED.md` is your call).
4. The Android lane (gradle) and the WSL lane were not run in the cloud.
5. Everything else was run here and passes: go test, go test -race, go vet,
   zig build test (debug and ReleaseSafe), the ISA check, pytest (8,046),
   vitest (663), npm run build, and cargo test for the Tauri shell, phone
   core and iroh sidecar. ruff, mypy, the docs sync, the Go
   language-boundary check and the benchmark budgets pass too (the first
   three and the boundary check were failing on `master`).

## 1. Zig core on Windows and macOS

The Windows and macOS Zig builds only reached the link step here (no SDKs).

- `cd native/zig && zig build -Doptimize=ReleaseSafe && zig build test`
  on Windows, and on a Mac if you have one. Passing: builds and tests pass.
- Scroll and drag: in a chat, ask Remedy to scroll a long page 5 clicks
  down and to drag a window by its title bar. Passing: both work. A
  scroll count over 100 is now capped at 100, and coordinates over 65,536
  are refused.
- XTest typing (Linux desktop only, not fixed): typing capital letters or
  `!@#` through computer.type may come out lowercase or unshifted. Check
  on a Linux desktop if you use one.

## 2. Windows process and path handling

- `.bat`/`.cmd` spawn: have Remedy run a small batch file with the argument
  `a&calc`. Passing: Calculator does not open, and the batch file either
  sees `a&calc` as one argument or the run is refused. Arguments holding
  `"`, `%`, `!` or a line break are refused for `.bat`/`.cmd`.
- Shell guards: in Auto mode, ask for `cmd /c echo hi > C:\Windows\x.txt`
  and a PowerShell `-Command` that writes outside the project. Passing: both
  ask or are refused. Also check a `robocopy ... /LOG:C:\outside\log.txt`.
- Secret folder through 8.3 names: with a file tool, try reading
  `%USERPROFILE%\REMEDY~1\auth\local_api_token` (use `dir /x` to find the
  short name) and through a directory junction to `.remedy\auth`. Passing:
  refused. The 8.3 case is not fixed in code; if it's readable, tell me.
- Worker root: start the Python tool worker with its root set to `C:\` and to
  a `\\server\share` path. Passing: it falls back to Documents\Remedy.
- WSL bash under a bound project looks like it's refused at the cwd check
  (a `/mnt/c` path compared with `C:\` roots). Check whether that's what
  you want.

## 3. Desktop app (Tauri)

Run `npm run tauri:dev` (and a release build if you can).

- General smoke with `withGlobalTauri` now off: open a chat, send a message,
  stream a reply, approve a tool, open Settings, quit from the tray. Passing:
  nothing broken. If something is, the setting is in `tauri.conf.json`.
- tauri:dev still loads a freshly built `native/zig/zig-out` library ahead
  of an older staged one (the library search no longer walks up from the
  working folder). Passing: after `zig build`, `tauri:dev` uses the new one
  (Server menu info shows the ABI).
- Server menu: "Server info" and "Open logs folder". Passing: both work. The
  review thought they might be missing from the permission files.
- Switch to Web UI (Windows): it now opens through ShellExecute, not
  `cmd /C start`. Passing: the browser opens Remedy's web UI and you're
  signed in.
- Open terminal in a folder whose name has a curly quote, e.g.
  `C:\tmp\Ahmi’s test`. Passing: the terminal opens in that folder and runs
  nothing else.
- Browser rail: type `http://tauri.localhost/`, `http://127.1:7400/`,
  `http://0x7f.1:7400/` and your API port into the rail. Passing: all are
  refused. `http://localhost:3000` (a dev server) still opens.
- Decision card: with two approvals queued, press Yes. Passing: the one on
  screen is approved, not the next. A card that just came to the front
  ignores Yes/No for half a second, so a double click can't approve a card
  you haven't seen. Easy to drop if you don't want it.
- Approval card for a very long command now ends with
  "… [cut: N more bytes not shown]".
- Suggestion, not changed: `capabilities/default.json` grants `windows:
  ["main"]`, which may also cover the rail's child webview. Switching to
  `webviews: ["main"]` would be tighter, but it needs a real build to test.
- The Tauri desktop does not check that whatever answers on the API port is
  really Remedy before it sends the token (only RemedyUI does). SECURITY.md
  now says so. A real check belongs in the startup token path in
  `desktop/src/api/client.ts`.

## 4. Phone (Remedy Connect)

- Stream a long chat reply on the phone and approve a tool from the phone
  while it streams. Passing: the approval goes through at once (it used to
  wait for the stream).
- Pair, then change a setting on the PC while the phone is idle. Passing:
  the save is instant (it used to stall up to 3 minutes).
- From the phone, try to set a session's project folder to some other
  folder. Passing: refused unless it's a folder the PC already uses. Also
  try changing the default workspace and the Sleev gateway URL from the
  phone. Passing: refused.
- Android: `cd android && .\gradlew.bat --no-daemon testDebugUnitTest lintDebug assembleDebug assembleRelease`.

## 5. Behaviour changes to know about

- `/house-rules` now works in the app: the Go runtime answers it (before,
  only an unused Python handler knew it, so it went to the model as a
  message). `/house-rules revisit` reopens the questions on the PC; a paired
  phone can read the rules but not reopen them.
- Auto quant for the local mind now sees the GPU and RAM. On Windows and
  macOS it used to read every machine as 16 GB with no GPU, so a small card
  may now get a smaller quant than before. Worth a look on your PC.
- An API key you give during onboarding or a house-rules change is now
  actually stored (it used to be dropped while the audit said "stored").
  One the model proposes in chat still waits for your yes and then has to
  be entered in Settings (see below).

- An API key proposed in chat (house rules) now waits for your yes, and the
  pending store strips secrets, so you'll have to enter the key in Settings.
- Delegating a mission now asks in Auto (Full still runs it).
- A model's turn can only call tools it was shown; internal tools
  (shell.exec, workspace.*, setup.session, prompt.*) are still reachable from
  the local tool API and the CLI.

## 6. Python worker after the cleanup

About 21,000 lines of Python that nothing ran are gone, and the worker now
serves only `text.*`, `workspace.*`, `prompt.turn_end`, `voice.*` and
`vision.*` (Go serves the rest).

- Start the packaged desktop app on Windows. Passing: Server info shows
  the Python worker attached, and a chat turn works.
- Ask Remedy to read your mail or calendar, and say "let's go over house
  rules again". Passing: both work (they're served in Go now).
- Speak to Remedy and have her talk back, and run a vision install. Passing:
  both still work (still Python).

## 7. Design questions (not changed)

- Messenger senders join your focused desktop session (history and a
  permanent mirror). Tests assert this on purpose. Keep it?
- Interop uses one shared token and the caller picks its own peer id, so
  tasks can't be scoped per peer and any caller with the token can claim a
  stored peer's permissions. Fixing it needs per-peer credentials.
- Go house rules still count an empty origin as the owner in
  `canChange`/`originMayChangeHouseRules` (low risk today, because owner
  turns always send "desktop").
- A relative `auth/<file>` inside a shell command run from the Remedy home
  folder isn't caught by the Zig spawn jail.
