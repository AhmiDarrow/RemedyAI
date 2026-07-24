# What’s new (recent)

High-level product notes for owners. Full detail lives in repo `CHANGELOG.md`.

Ship **one** installer/tag for the current series (**v0.10.42**).

## 0.10.42 — GitHub skill, MCP host, Plan mode, checkpoints

- Bundled **github** skill (`gh` PRs/issues/CI) ships with the package.
- **`remedy-mcp`** / `remedy mcp serve` — export skills & plans to Cursor / Claude Desktop.
- **Plan mode** actually blocks shell/file tools; structured plans + mid-task checkpoints in the Memory panel.
- Learning loop observability (What I learned, re-use metrics) and thinner agent core modules.

## 0.10.41 — Setup UX, free try, WebUI, status dock

- **Setup** decluttered (larger UI, free-provider chips).
- **Settings** sections expand/collapse.
- **WebUI** button (was “Web”) opens the browser chat; SPA packaging + bootstrap fixes for *Failed to fetch*.
- **Status dock** (bottom): server online + **visual model download progress** after you opt in.
- **Demo / free providers** — try Remedy with no API key (rate-limited gateway) or free Gemini/Groq/etc keys; see Free providers chapter.

## 0.10.40 — Visual decoder

- **Local visual decoder** (opt-in): llama.cpp + **Qwen2.5-VL 3B** turns screenshots/photos into structured text for **text-only** chat models.
- Settings: install, cancel/resume, enable, prefer-local (saves provider vision tokens), **Switch to CUDA** when NVIDIA is detected.
- Setup wizard optional Vision step; composer banner when images need decode.
- Data under `~/.remedy/vision/`; removed on config wipe / full uninstall.

## 0.10.39 — ComfyUI from scratch

- **ComfyUI skill** now includes full bootstrap: install portable ComfyUI, start it, get Flux.2 Klein models, workflows, then generate images into chat — even on a PC that had nothing installed (with your approval for downloads).
- Seeded skills auto-upgrade when the package ships a newer skill version.

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
