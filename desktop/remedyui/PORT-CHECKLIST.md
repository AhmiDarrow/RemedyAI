# Remedy Desktop → RemedyUI port checklist

Items stay red until proven (headless tests on the box, then a hidden Windows run on Joe's PC). First ship is 1.0 on Windows, Linux and macOS. Grove is in. Full browser parity is in. `com.remedy.desktop`, the installer asset name, the minisign key, NSIS `/S /UPDATE` and `~/.remedy` stay intact.

Identity and engine pins: RemedyUI `main` at `6f36405aa0840a2c332b80a1c17375d7ea4901c6` (`net`, `image`, `webview`, `playback`, `desktop`, `term`, and the preview features).

## Shell (this milestone)

- [x] `cargo remedy` app at `desktop/remedyui`, version 1.0.0, id `com.remedy.desktop`
- [x] Window, Dark Forest default, every shared palette in golden previews
- [x] Studio session list with always-visible New (+) and Close (×): 32 px hit target, 20 px glyph, high-contrast in all themes, hover and focus from the `icon_button` sheet
- [x] Close to tray, tray menu, single instance
- [x] Status bar (mind not connected yet)
- [ ] Spawn `remedy-runtime` on `:7400` and show live status
- [ ] Import the saved theme from the Tauri web view's localStorage
- [ ] Title bar extras (server menu, command palette, About, language picker)
- [ ] Autostart
- [ ] Hidden Windows install / tray / single-instance proof on Joe's PC

## Still red (later phases)

- [ ] Chat core (feed, composer, SSE, markdown, approvals, model badge)
- [ ] Settings (every category, providers, RMB/muscle, interop, fonts, notices)
- [ ] House-rules setup and the one-time update offer
- [ ] Workspace: Files, Scratch, Automations, skills, Terminal, Browser (full parity)
  - Browser rail and the Rust computer-use host are in `src/browser` and register as the rail body. The poller is the only `jobs/next` consumer (`take=1&driver=rust`). Files, Scratch, Automations, and Terminal are the rails port. The skills library is still open.
- [ ] Voice, messengers, Hive, diagnostics, usage, help, downloads, QR
- [ ] Grove (ported on `remedyui-grove`; Linux `cargo test` still has to prove it)
- [ ] Update jump from the last Tauri build through the same signed `latest.json`
- [ ] Joe reviews screenshots before ship; no tag or release without him
