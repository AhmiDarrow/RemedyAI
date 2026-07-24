# What’s new (recent)

High-level product notes for owners. Full detail lives in repo `CHANGELOG.md`.

## 0.10.36 — Help wiki foundation + setup reliability

- Config writer fixed (root keys before TOML tables) — stops corrupt `config.toml`.  
- First-run setup auto-open hardened; real errors on save / OAuth.  
- **In-app Help wiki** (this manual): search, TOC, offline chapters.  

## 0.10.35 — First-run after wipe

- Sidecar always `--skip-setup`; Setup wizard is the UI first-run path.  
- Auth + settings load before models.  
- Error screen **Open setup** + clearer install guidance.  
- Uninstall options dialog font / ASCII labels.  

## 0.10.33–0.10.34 — Security & CI

- Local API auth on by default; DPAPI secrets; zip quarantine.  
- Tool env scrub; approval defaults; tiered context caps.  
- Signed desktop release pipeline hygiene.  

## 0.10.30–0.10.32 — Skills + installer UX

- Skill lifecycle, progressive disclosure, learning loop.  
- Interactive installer no longer launches before finish page.  
- Uninstall soft-fail (no abort on dialog errors).  

## 0.10.18–0.10.25 — Partner desktop UX

- Chat bubbles, tool process modes, prompt history.  
- Approvals banner, tray Settings, xAI OAuth, themes.  
- Memory harness controls, sessions polish.  

## How to update

See [Updates & uninstall](08-updates-and-uninstall). Prefer GitHub Releases for this repository only.

## Related

- [Overview](00-overview) · [Troubleshooting](09-troubleshooting)
