# Remedy Desktop → RemedyUI port checklist

Items stay red until proven (headless tests on the box, then a hidden Windows run on the owner's PC). First ship is 1.0 on Windows, Linux and macOS. Grove is in. Full browser parity is in. `com.remedy.desktop`, the installer asset name, the minisign key, NSIS `/S /NCRC /UPDATE /NOAUTOLAUNCH` and `~/.remedy` stay intact.

Identity and engine pins: RemedyUI `main` at `3142c66cde5e20b06028f955087326dab9a04b51` (`net`, `image`, `webview`, `playback`, `desktop`, `term`, `update`, `extra`, `webstorage`, `customize`, and the preview features `headless` / `devtools`). Packaging scripts live in `desktop/remedyui/packaging/`. They do not tag or publish. The shell lives on `origin/master` (Grove, settings, rails, usage, diagnostics, help, time travel, setup, computer-use, the 1.0 updater, and the audit fixes below).

## Shell (this milestone)

- [x] `cargo remedy` app at `desktop/remedyui`, version 2.1.2, id `com.remedy.desktop`
- [x] Window, Dark Forest default, every shared palette in golden previews
- [x] Studio session list with always-visible New (+) and Close (×): 32 px hit target, 20 px glyph, high-contrast in all themes, hover and focus from the `icon_button` sheet
- [x] Close to tray, tray menu, single instance
- [x] Status bar (mind not connected yet)
- [x] Import the saved theme from the Tauri web view's localStorage (`webstorage`, key `remedy-theme`, bundle `com.remedy.desktop`; only when `config.toml` has no `appearance.theme` yet)
- [x] Connect pairing QR (`extra` / `qr_code`)
- [x] Theme packs (`customize`: live windows attach `Customizations`, Appearance shows the editor)
- [ ] Spawn `remedy-runtime` on `:7400` and show live status (wired; last hidden Windows verify on `fa0a9900` saw the backend up)
- [ ] Title bar extras (server menu, command palette, About, language picker)
- [ ] Autostart
- [x] Hidden Windows install / tray / single-instance proof on the owner's PC (silent `/S /NCRC /UPDATE /NOAUTOLAUNCH` of `fa0a9900`, 23 Nov 2026)

## Still red (later phases)

- [ ] Chat core (feed, composer, SSE, markdown, approvals, model badge)
- [ ] Settings (every category, providers, RMB/muscle, interop, fonts, notices)
- [ ] House-rules setup and the one-time update offer (wired: splash, wizard, mind, conversation, offer pop-up, name prompt, update screen, download strip; first-run and auto-update design is still a local mockup on `design/setup-update`, not merged)
- [ ] Workspace: Files, Scratch, Automations, Terminal, Browser (full parity)
  - Browser rail and the Rust computer-use host are in `src/browser` and register as the rail body. The poller is the only `jobs/next` consumer (`take=1&driver=rust`). Files, Scratch, Automations, and Terminal are the rails port.
- [x] Skills sheet from the title bar, status dock, and command palette (reuses Studio's installed / library panel)
- [x] Grove Speak button uses `Microphone::open_platform` and `/api/voice/transcribe`, same path as Studio
- [ ] Voice (spoken replies, turn-taking), messengers, Hive, downloads
- [ ] Privacy Shield engine (waits on a RemedyUI webview request filter)
- [ ] Diagnostics, usage, help (wired in the shell; still red until a later proof pass)
- [ ] Grove (on `master`; Linux `cargo test --lib` / `--test app` are green; the owner's PC has seen default Grove on a hidden install of `fa0a9900`. A later pass still has to prove the rest.)
- [ ] Update jump from the last Tauri build through the same signed `latest.json` (feed, NSIS `/S /NCRC /UPDATE /NOAUTOLAUNCH`, tray and status offer, and download percent are wired; the install itself is the owner's PC)
- [ ] The owner reviews screenshots before ship; no tag or release without the owner's yes
