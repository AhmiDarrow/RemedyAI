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
serves only `prompt.turn_end`, `voice.*` and `vision.*` (Go serves the
rest, including `workspace.*` and `text.*` now).

- Start the packaged desktop app on Windows. Passing: Server info shows
  the Python worker attached, and a chat turn works.
- Ask Remedy to read your mail or calendar, and say "let's go over house
  rules again". Passing: both work (they're served in Go now).
- With the app running, run `remedy tool list` and then
  `remedy tool run workspace.list`. Passing: `workspace.*` and `text.*` show
  runtime `go`, and the run lists your project folder (never your whole home
  folder or the install folder). With no project set, the last-resort
  folder is ~/Remedy, the one the app already started the worker in; the
  old Python tool used Documents/Remedy only when started from a drive root.
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

## 8. Results of the PC run (2026-11-26, Windows 11)

Run on the owner's PC against this branch. Fixes for what it found are on the branch.

### Local CI

- `scripts/prepush.py` stops before any lane on the `[Unreleased]` notes, and at the first red lane after that, so
  the lanes were run one at a time through `verify_tree`.
- Green: ruff, mypy, mypy-exclude, docs sync, desktop (vitest + build), rust (Tauri shell and iroh sidecar),
  android (gradle: unit tests, lint, debug and release builds), native (go test, race, vet, boundaries, benchmarks,
  zig test debug and ReleaseSafe, ISA check).
- pytest: 8,189 passed on Windows and 8,051 under WSL. The same two fail on both, and neither is this branch's:
  the `[Unreleased]` notes check, and `test_sanitize_script_exits_clean_on_current_tree` (the notes again on
  Windows; under WSL `git ls-files` cannot read a Windows worktree's git folder).
- Not run: the WSL cargo step and the wheel build (`uv build`), because their lanes stop at pytest.
- The native lane runs `go test` before it builds the Zig core, so on a clean checkout the Go tests that spawn a
  process skip. Run again with the core built, they all pass, after one test fix (below).

### Found and fixed

- A directory junction to the auth folder was not refused on Windows: `filepath.EvalSymlinks` does not follow
  junctions. File tools and `/api/media` now ask the system where an open path really is (`sysres.RealPath`). The
  8.3 short-name case was already refused; both have Windows tests now.
- The local tool API (`/api/tools/invoke`) built its policy without the guard settings, so in Auto it ran every
  shell command and fetch without asking. Seen live: a PowerShell command wrote a file outside the project.
- The write jail read a nested `sh -c` but not a nested `powershell -Command "..."` or `cmd /c "..."`, so a Windows
  shell inside a script wrote anywhere. It now reads both, takes PowerShell's shortened switches (`-com`,
  `/Command`), and refuses `-EncodedCommand` inside a script.
- A shell command could read a secret through a junction (`findstr . link/local_api_token`): the core reads the
  spelling only. A spawn is now refused when a path it names leads into the auth folder.
- `TestBashPropagatesExitCode` used cmd syntax; the bash tool runs PowerShell where it is installed.
- The RemedyUI desktop (`desktop/remedyui`, the desktop that ships) was not part of the sweep and had what the
  Tauri shell was fixed for: opening a file from the Files rail went through `cmd /c start` (a file named
  `x&calc.txt` ran `calc`), Open terminal doubled only the straight quote, and the Web UI link was accepted on a
  prefix (`http://127.0.0.1.example.com/` passed). All three fixed, with tests.

### Checked and passing

- Batch files: `a&calc` and `x|y` reach the script as one argument each and Calculator does not open; `"`, `%`,
  `!` and a line break are refused.
- Auto mode, project scope: `cmd /c echo hi > C:/Windows/x.txt`, a PowerShell `-Command` writing outside, and
  `robocopy ... /LOG:C:/outside/log.txt` all ask. A path rooted with one backslash and no drive letter is not
  caught by the Auto text filter but is refused by the write jail.
- Secret folder: refused by path, by junction and under full access, for read, grep and glob.
- Tool worker root: `C:/`, `C:`, the `C$` share on localhost, the `?` and `.` device forms of `C:`, a share on a
  server that is not there, and a cwd of the drive root all fall back to Documents/Remedy.
- WSL bash under a bound project is refused at the cwd check, as section 2 expected. Whether that is wanted is
  still the owner's call.
- `withGlobalTauri` off: nothing in `desktop/src` or the shell needs `window.__TAURI__`; every use falls back to
  the official package or `__TAURI_INTERNALS__`.
- Server info and Open logs folder: `build.rs` declares no app command list, so the shell's own commands are
  allowed without an entry in the permission files.

### Not run

- The Tauri desktop in a real window (section 3's smoke pass, decision card, long-command card, rail typing). Its
  rules are covered by the unit tests in the rust and desktop lanes; nobody has clicked through it.
- Computer scroll and drag on the real screen; the caps are covered by the Zig tests, which ran on Windows.
- Phone checks (section 4): no phone was attached. The Android gradle lane passed.
- macOS and a Linux desktop.

### Noticed, not changed

- On a new home, Settings reports `approval_mode: auto` while the runtime acts as Ask until the mode is saved once.

## 9. Go CLI checks

The `remedy` command now runs in Go (`remedy-runtime`) for auth, config,
settings, user, memory, handoff, session, desktop, exec, open, serve, tool,
gateway, chat, connect-relay, setup, update, uninstall, mcp and computer. The rest still runs the Python CLI until it
moves over. None of these change the display resolution or scale.

- `remedy --version` and `remedy --help`. Passing: both answer from Go, and
  `remedy-runtime --probe` shows `"cli": 1`.
- `remedy desktop launch`, then close the terminal. Passing: the desktop app
  stays open (it is started through explorer.exe, not as a child).
- `remedy open` with the app running. Passing: the default browser opens the
  one-time WebUI link.
- `remedy exec -- format C:` and `remedy exec -- cmd /c takeown /f C:\Windows`.
  Passing: both print "Execution blocked by security policy" and exit 2, and
  nothing runs. `remedy exec -- cmd /c echo hi` prints hi and exit code 0.
- `remedy auth login` (or `remedy settings set` for a key): at the hidden
  prompt, type a few characters, then press Ctrl-C. Passing: nothing you typed
  showed, and the terminal echoes normally again afterwards.
- `remedy config init` with `USERPROFILE` set and with it unset. Passing: the
  config lands in the same home folder the app uses, and the Documents path it
  prints is your real Documents folder (including a OneDrive-redirected one).
- With the desktop app running, `remedy memory backup` and
  `remedy memory vacuum`. Passing: the backup file opens, vacuum either
  finishes or says the database is busy, and the app keeps working.
- `remedy memory` commands that write the repair marker and backup manifest.
  Passing: both files appear under the Remedy home with Windows paths that
  open.
- `remedy setup`: at the API key and bot token questions, type a key.
  Passing: nothing you type shows, and the key ends up in the secret store,
  not in config.toml.
- `remedy update --check` from a pip install. Passing: it names the installed
  and latest versions. From the desktop app's own copy it only reports.
- `remedy uninstall --dry-run`, then `remedy uninstall --purge` on a test
  account with vision installed and llama-server running. Passing: the dry
  run removes nothing; the real run stops llama-server, removes `vision/` and
  the Remedy home, leaves the desktop app to Windows Apps, and pip only ever
  touches `remedy-ai`.
- `remedy computer host start`, then queue a job from the app (open a URL,
  snapshot, click, type, a key combo). Passing: the URL opens in the browser,
  the click and typing land where the snapshot said, and every job reports
  done or an error. With the desktop app's own computer use running, the CLI
  host steps aside.
- `remedy mcp serve` from an MCP client, and `remedy mcp serve --http`.
  Passing: tools list and a call work; `--http --port 7400` is refused.
- Zig's spawn policy lets these through: `python -m pip`, `python -c`, `git
  fetch/pull/rev-list/status`, `powershell` (the llama-server lookup) and
  skill scripts run with `python`.

## 10. End-of-turn learning in Go

- Have a few turns with the desktop app, one ending "thanks, that works" and
  one ending "that's wrong". Passing: alive.json gets an "accepted" and an
  "unhappy" outcome, and nothing errors while the app has the file open.
- With the memory panel open, have a session with eight or more notes and
  finish a turn. Passing: a Consolidation entry appears, and the panel keeps
  working (no "database is locked").
- With a learned skill on probation that has passed in several sessions,
  finish a turn. Passing: its SKILL.md status moves on while the app is
  running, and created_at or the file time is read right in your time zone.
