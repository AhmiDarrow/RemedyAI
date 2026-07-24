# What’s new (recent)

High-level product notes for owners. Full detail lives in repo `CHANGELOG.md`.

Ship **one** installer/tag for the current series (**v0.10.38**).

## 0.10.38 — xAI OAuth on fresh install

- Fix: **Sign in with xAI** no longer fails with *Cannot reach local API … (/auth/xai/login)* when the server is actually up.
- Cause was CORS **OPTIONS** preflight blocked by API auth (looked like a dead server in the desktop UI).
- Also waits for the local API before starting device login.

## 0.10.37 — Help wiki, Web UI, tools, security

### Owner experience
- **In-app Help wiki** (this manual): **F1** / **Ctrl+/**, searchable TOC, offline chapters.
- **Switch to Web UI** — hide desktop to tray, open `http://127.0.0.1:7400/` (server stays up).
- **Quit warning** — full quit stops the local API; option not to warn again.
- **Report an issue** on GitHub (Settings / Help) with version prefilled.
- Composer auto-grows with word-wrap (then scrolls).
- **Diff colors** — red removals / green additions in chat and tool process (`file_write` edits).

### Tools (masterclass fixes)
- `file_write` preferred over PowerShell for text files; Desktop/Documents/Downloads allowed.
- Fixed `skill_activate` crash (`multiple values for argument 'name'`).
- Reliable tool process formatting for create vs edit.

### Security (power kept)
- CORS `*` blocked while auth is on; loopback bind defaults; auth-off on open bind needs explicit flag.
- Quarantined skills cannot load instructions until **Trust**.
- Skill scripts scrub secrets from env; Telegram needs allowlist (or `REMEDY_TELEGRAM_ALLOW_ALL=1`).
- Updater requires signed `latest.json` URL match.
- **Auto-approve and full shell remain available** when you choose them.

## 0.10.36 — First-run setup reliability

- Config writer fixed (root keys **before** TOML tables).
- First-run setup auto-open hardened; real errors on save / OAuth.

## 0.10.35 — First-run after wipe

- Sidecar `--skip-setup`; Setup wizard is the UI first-run path.
- Auth + settings load before models; **Open setup** on errors.

## 0.10.33–0.10.34 — Security & CI

- Local API auth on by default; DPAPI secrets; zip quarantine.
- Signed desktop release pipeline hygiene.

## 0.10.30–0.10.32 — Skills + installer UX

- Skill lifecycle, progressive disclosure, learning loop.
- Interactive installer finish-page launch; uninstall soft-fail.

## How to update

See [Updates & uninstall](08-updates-and-uninstall). Prefer GitHub Releases for this repository only.

## Related

- [Overview](00-overview) · [Security](04-security-and-data) · [Troubleshooting](09-troubleshooting)
