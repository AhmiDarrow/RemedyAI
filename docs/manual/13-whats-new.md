# What’s new (recent)

High-level product notes for owners. Full detail lives in repo `CHANGELOG.md`.

Ship **one** installer/tag for the current series (**v0.13.0**).

## 0.13.0 — Coding agency (Build-class tools)

- **`file_edit`** — precise search/replace (prefer over full-file rewrite).
- **`repo_search`** — project text search (ripgrep or built-in).
- **Missions** — `mission_start` / `update` / `verify` for work-alone checklists + tests.
- **`job_run`** — silent explore/verify jobs (no multi-agent chat).
- Optional **`web_fetch`** when `web_tools_enabled` is true.
- Work-alone policy steers toward missions + file_edit + verify loops.
- Manual: F1 → *Coding agency*.

## 0.12.3 — Agent runtime peel

- Agent core split further: post-turn, local tools, LLM HTTP, history, session binding modules.
- Same product behavior; thinner `agent.py` for safer maintenance.

## 0.12.2 — Single relaunch · thinner agent context

- In-app update: **one** relaunch after install (no double window from NSIS + updater).
- Agent: turn context assembly extracted to `agent_context` (typed under mypy).

## 0.12.1 — Progress that doesn’t disappear

- Update install keeps a **Remedy Update** progress window through silent install + relaunch.
- First-run vision download shows **live %** on finish; use the app while it downloads.
- Click ticker **$** to hide cost; **Full access** chip when no project jail.
- Safer shell hard-blocks for normal Windows greps; tighter Partner Memory gates.

## 0.12.0 — Partner Memory · work alone

- **Partner Memory**: quietly remembers preferences/identity across sessions (inject + distill); `/whoami`, `/forget`, `/pin`.
- Hybrid memory search (facts + notes); project-scoped facts; skill rank cost signal.
- **Work alone**: say “handle this on your own” / step away — Remedy keeps going until done or a hard blocker.

## 0.11.7 — Session tree power tools

- Session project binds tools each turn; multi-select + drag onto folders; load more; bulk API.
- Optional “New-in-project sets default” for Settings project path.

## 0.11.6 — Project tree sessions · empty project = full access

- Sidebar: **No project** + project folders with nested chats; add project; new-in-project; move session.
- Empty project path → full machine access (with Settings warning); pick a folder for focused work.
- Builds on 0.11.5 proceed-kick / Full process visibility.

## 0.11.5 — Never miss model output · stuck-kick fix

- **Tool process**: Min / Med / Full / Full+ — chat answer always complete; Full shows all process output expanded.
- **Stuck “proceed”**: short action kicks enable tools again; Plan auto-exits on proceed/build language.
- Composer typable while streaming; Shift+Tab Plan/Build; polished assistant chat look.

## 0.11.4 — NanoToken BPE v2 + tool polish

- **Default BPE pack `remedy-bbpe-v2`**: clean-room byte BPE trained on first-party repo + live DeepSeek/xAI tool/skill battery (v1 kept).
- **Retrain path**: `scripts/nanotoken_battery_and_train.py` (`--from-corpus` reuses a prior dump); ratio check `scripts/nanotoken_ratio_eval.py`.
- **file_read**: optional `offset`/`limit` line windows (models no longer crash the tool with extra args).
- F1 → *Continuity workers* documents packs, calibration band, and knobs (`REMEDY_BPE=0`).

## 0.11.3 — Owned NanoToken BPE + plan finish

- Clean-room BPE engine + `remedy-bbpe-v1`; swarm pack assignment; session LLM; skills scale; untrusted scope.

## 0.11.2 — Live models + smarter continuity workers

- **Live provider models**: `GET {base}/models` restored for DeepSeek, xAI, and other OpenAI-compatible clouds (was limited in 0.10.44 perf). Catalog is fallback only.
- **DeepSeek / xAI ids**: V4 Flash/Pro and current Grok family; legacy `deepseek-chat` and old `grok-3*` migrate automatically.
- **Nano swarm utilization**: shared bots in ContextSnapshot; pattern → stuck remedies; skill rank cache warmed off hot path; learn pre-gate; provider-change events.
- Operator doc: F1 → *Continuity workers* (`17-nanoswarm`).

## 0.11.1 — Windows Defender posture

- **No registry Run** for Start with Windows (Startup folder only); legacy Run scrub via Rust `winreg` + NSIS `DeleteRegValue` (no hidden PowerShell on launch).
- Sidecar PE **product identity** (Company/Product/FileVersion + icon) to reduce Wacatac/Bearfoos-class ML false positives.
- Installer/bundle publisher metadata filled. See F1 → Troubleshooting if Windows Security still mislabels a new unsigned build.

## 0.11.0 — Continuity layer, local vision download, session quality

- **Continuity**: silent context budget, Session Brief, intent policy packs, quality remedies, project learning — feels like one partner on any model (not a bot farm). See F1 → *How Remedy works (continuity)*.
- **Session quality**: tokens saved by compress, stuck/re-explain rates; `/harness` snapshot.
- **Local vision**: first-run download of pinned Qwen2.5-VL 3B (not in installer); starts with Remedy; idle stop.
- **Tool process Full+**: only advanced view for continuity internals; normal UI is “Local vision”.
- Installer stays small; optional offline stage via `scripts/stage_local_bundle.py`.

## 0.10.45 — Setup free UX, tray start, usage sidebar, vision wipe

- Setup free providers simplified (Demo / Ollama / free-key dropdown).
- “Start with Windows” no longer forces tray-only startup; window opens normally unless “Start hidden in tray” is on.
- Usage/cost stats sit in the session sidebar (bottom-left).
- Uninstall config/full wipe removes visual decoder (llama.cpp + models).

## 0.10.44 — Skills HITL, Time Travel, token cost ticker, perf

- **Skills**: force-promote / quarantine overrides; CodeMirror `SKILL.md` editor; **Export/Import Pack** ZIP.
- **Time Travel**: timeline UI to restore chat + best-effort workspace files to an earlier step.
- **Token & cost ticker**: hideable live run/session usage and estimated API cost.
- **Perf**: vision/status freezes fixed; secrets/config caching; faster Settings and splash; durable logs under `~/.remedy/logs/`.

## 0.10.43 — Rebuild (CI mypy + package)

- CI type-check fix and clean rebuild of the 0.10.42 feature set (github skill, MCP host, plan/checkpoints, vision shutdown).

## 0.10.42 — GitHub skill, MCP host, Plan mode, checkpoints, vision shutdown

- Bundled **github** skill (`gh` PRs/issues/CI) ships with the package.
- **`remedy-mcp`** / `remedy mcp serve` — export skills & plans to Cursor / Claude Desktop.
- **Plan mode** actually blocks shell/file tools; structured plans + mid-task checkpoints in the Memory panel.
- Learning loop observability (What I learned, re-use metrics) and thinner agent core modules.
- **Vision decoder** (local llama-server) stops on full Quit / API exit so it does not keep using RAM/GPU; hide-to-tray does not stop it.

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
