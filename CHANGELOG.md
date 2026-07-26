# Changelog

All notable changes to Remedy (`remedy-ai`) are documented here.

## [0.14.5] — 2026-07-26

### UX: stream queue, live usage, sticky answer, export speed

- **Send while streaming:** Enter queues the next prompt; Ctrl+Enter (or
  right-click Send) **interrupts** and sends now. Queue bar supports After /
  Interrupt / Cancel / Clear.
- **Sticky live dock:** thinking + final answer stay pinned at the bottom of
  the chat while tool process dumps stay capped above.
- **Token ticker:** live estimate from partial tokens while streaming; correct
  **grok-4.5** pricing (was matched as grok-4); better session estimates.
- **Export/import:** yield UI before work; strip base64 images + cap message
  bodies; Save dialog picks path then Rust writes (no multi-MB PowerShell copy).
- **Chat monogram:** `public/icon.png` / favicon / logo regenerated from alpha
  `assets/remedy_icon.png` (true RGBA).

## [0.14.4] — 2026-07-25

### Fix: brand assets in UI + silent autoupdate host

- **Alpha brand kit wired end-to-end:** `assets/remedy_icon.png` /
  `remedy_logo.png` (true alpha + brightened masters, mono light/dark variants)
  regenerate into `desktop/public/{logo,icon,favicon}.*` and the full Tauri
  icon set via `scripts/setup_branding.py`.
- UI surfaces use the new art: boot splash, React splash, title-bar menu,
  Setup wizard, About, Update screen, chat empty/bubbles (`RemedyLogo`),
  favicon / tray / taskbar / installer icons.
- Splash rendering uses smooth scaling (`image-rendering: auto`) so the logo
  is not pixelated.
- **Autoupdate: no black CMD flashes.** Install-progress host and detached
  update script spawn `powershell.exe` directly with `CREATE_NO_WINDOW` +
  breakaway flags (no `cmd /c start`). Progress UI keeps the WinForms popup;
  console stays hidden.
- **Autoupdate unicode cleanup:** status strings + `remedy-update-ui.ps1` are
  ASCII-safe for Windows PowerShell 5.1 (no mojibake from ellipsis/arrows).
- NSIS silent relaunch uses `Exec` instead of `cmd /c start`.
- Docs gate: `check_docs.py` configures UTF-8 stdio so Windows cp1252 consoles
  do not fail the hotkeys surface.

## [0.14.3] — 2026-07-25

### Fix: chat images + session export + agency continuity

- **Chat images (provider-agnostic):** markdown `![alt](assets/…)` and absolute
  local paths now load via `GET /api/media` + desktop `ChatImage` (blob URL with
  auth). Works for any model that embeds local paths — not only data: URIs.
- **Session export:** Tauri native Save dialog (`save_text_file`) so export works
  in WebView (anchor download was a no-op).
- Brand assets kit under `assets/` (alpha masters + mono variants + previews).
- Agency: tool-gating history continuity, auto-approve config wiring (from 0.14.2).

## [0.14.2] — 2026-07-25

### Fix: auto-approve actually grants full power

Status-bar thumbs-up / Settings `approval_mode=auto` was **saved to config** and
shown in the UI, but the process still ran as **ask** because
`config_to_agent_config()` never copied `approval_mode` into `AgentConfig`.
Result: shell/file tools still returned `APPROVAL_REQUIRED` and the banner
still asked for permission.

- Load `approval_mode` (+ access_scope / harness / thinking) into AgentConfig
- Re-sync APPROVALS from config.toml on every tool ask + partner status poll
- Switching to **auto** auto-approves pending items (banner clears)
- Per-turn runtime sync applies approval_mode before chat streams

### Fix: stay on task (agency / tool gating)

Desktop session log (assets/logos work): after the first turn used tools, short
follow-ups like **"go with your suggestions"**, **"progress?"**, **"eta"**, and
**"troubleshoot"** set `tools=[]` + `force_answer` on step 0 — the model only
streamed "Processing…" and never called tools again.

- Expand **action kicks** and **asset/image tool hints** in `message_wants_tools`
- **History-aware tool enablement**: keep tools on when recent turns used tools
  or Session Brief has open tasks (pure chit-chat still stays tool-free)
- **False-progress nudge**: if the model claims to be working without native
  `tool_calls`, force a tool-using recovery step
- Auto-learned skill titles use **tool-name patterns** (not full path prompts)
- Desktop **session export** download: attach anchor to DOM (Tauri/WebView)

## [0.14.1] — 2026-07-25

### Fix: autoupdate UX + single relaunch (regression)

- **Two-stage progress** (as intended):
  1. In-app **download** screen  
  2. After Remedy closes → **new Install Progress** popup (STA WinForms) for
     silent install + relaunch  
- Install host: `-STA`, `cmd start` / independent process, status JSON after exit.  
- **Single relaunch**: TEMP `RemedyDesktop-UpdaterOwnsRelaunch.flag` + `/NOAUTOLAUNCH`
  so NSIS POSTINSTALL does not also start the app (fixes double window).  
- Update script kills only app shells (never the progress PowerShell host).  
- Docs/manual updated; pipeline + hooks contract tests.

## [0.14.0] — 2026-07-25

### Refactor: ReAct peel + Settings modularization

- Extract **`agent_tool_batch`** (`execute_tool_calls`, `progress_marker`) and
  **`agent_react_loop`** (`call_llm_stream`) from `agent.py` — orchestrator is
  ~700 lines; stream/batch are independently unit-tested.
- Split desktop Settings: **`SettingsPanel`** (state/save) +
  **`settings/FormSections`** + **`settings/shared`** (Field, personas).
- Agency battery tests (file_edit uniqueness, mission prefix ids, plan-mode
  tool filter, pairing) without live LLM.
- CI: Windows pytest subset for path/shell-sensitive modules; desktop
  `npm test` + production build on Ubuntu.

## [0.13.3] — 2026-07-25

### Polish: Settings completeness, search, docs sync

- Settings **search**, last-section remember, **lazy vision** status fetch.
- New / completed knobs: harness min/max %, thinking level, allow_skill_creation, auto_approve_threshold, log_level, sarcasm_mode; **License** + **Channels** honesty sections.
- Default config template lists modern keys; Help overview/security include license summary.
- Cargo.toml license → `LicenseRef-Proprietary`; desktop unit tests for settings search / build kick.
- LICENSE/COMMERCIAL source-available terms (from prior unreleased docs) included in this series.

## [0.13.2] — 2026-07-25

### Security & power (review fixes — owner power preserved)

- **`web_fetch` SSRF**: pin-on-resolve (connect to public IP with Host/SNI); redirect re-validation; fail closed on mixed private DNS. Public web fetch power unchanged when `web_tools_enabled` is on.
- **Skill ZIP import**: stream extract with per-member and total uncompressed caps (decompression bombs); Zip Slip unchanged.
- **Approvals**: Ask remains default; Auto = work-until-done (no prompts on trusted scopes). Soft-risk shell patterns labeled on Ask banners. `file_edit` in high-impact set for Ask only.
- **HTTP bootstrap**: Settings **Allow browser token bootstrap** + `http_bootstrap` config; env override; default on so Web UI keeps working; desktop still prefers IPC.
- **Desktop prefs**: `desktop.json` load/save via `serde_json` (no brittle string contains).
- **Docs**: security manual + minisign pubkey table in `WINDOWS_SIGNING.md` / README.
- Settings UI section **Security & power** (approvals, web_fetch, bootstrap).

## [0.13.1] — 2026-07-25

### Fix: agency tool robustness

- **`repo_search`**: parse Windows `C:\path:line:text` correctly; fall back to pure Python when ripgrep errors (not only when missing).
- **Missions**: `mission_status` / `mission_update` accept **short id prefixes** (first 8 chars of UUID).
- **`web_fetch`**: SSRF guard blocks localhost/private/metadata hosts (including redirects).
- **`job_run` explore**: handles file paths; clearer empty-query message.

## [0.13.0] — 2026-07-25

### Feature: coding agency (Build-class tool plane)

- **`file_edit`**: unique search/replace (or `replace_all`); time-travel undo compatible.
- **`repo_search`**: project text search via ripgrep when available, pure-Python fallback.
- **Missions**: `mission_start`, `mission_status`, `mission_update`, `mission_verify` —
  durable goal/checklist/verify for work-alone multi-step builds.
- **`job_run`**: silent explore/verify jobs (summary only; one partner voice).
- **`web_fetch`**: optional HTTP fetch when `web_tools_enabled` is true.
- Work-alone + tool intent packs steer models toward edit/search/mission loops.
- Agency battery fixtures under `scripts/agency_battery/`; manual chapter `18-agency`.

## [0.12.3] — 2026-07-25

### Refactor: agent.py peel (orchestrator thinner)

- Extract modules (mypy-covered where new): `agent_context`, `agent_post_turn`,
  `agent_local_tools`, `agent_llm`, `agent_history`, `agent_session`.
- `BasicRuntime` keeps ReAct stream/tool loop; registration and HTTP helpers
  are thin wrappers. ~2.4k → ~1.9k lines in `agent.py`.

## [0.12.2] — 2026-07-25

### Fix: autoupdate double relaunch

- In-app updater passes `/NOAUTOLAUNCH` so NSIS POSTINSTALL does not start the app;
  the update script performs the **single** relaunch after verify.
- Prevents two Remedy windows after a successful silent update.

### Refactor: agent context extract

- Move turn context assembly (`_build_context`) to `remedy.core.agent_context`
  (mypy-covered); `agent.py` remains the ReAct orchestrator.

## [0.12.1] — 2026-07-25

### Feature / UX: update & setup progress

- **In-app update:** out-of-process progress host (`remedy-update-ui`) stays visible through silent NSIS install and relaunch (no blank desktop gap).
- **First-run local model:** finish step shows live download %; **Use app while downloading** enters the app without waiting.

### Feature / UX: comfort & safety

- **Token ticker:** click the **$** amount to hide estimated cost (tokens remain); preference persists.
- **Full access** amber chip in the status bar when scope is full / untrusted.
- System note when tools run without a project jail.

### Hardening

- Partner Memory: stricter always/never gates; ignore one-off “always run the tests now” chatter.
- Shell hard-block list: allow common Windows/dev inspection (`Select-String`, `git`, `rg`); soft-risk helpers for Start-Process / `$()`.
- Docs/series strings **0.12.x**; update manual describes progress host.

## [0.12.0] — 2026-07-25

### Feature: Partner Memory — toward an AI that never forgets

- **Partner Memory** injects a budget-capped durable block every turn (identity, preferences, constraints).
- **Quiet distillation** learns safe high-confidence preferences from natural chat (heuristics first; no setup).
- **`/forget`**, **`/pin`**, improved **`/whoami`** for transparent fix/inspect.
- Project-scoped facts, pin, gentle decay, hybrid fact+FTS search with token re-rank.
- Secrets (API keys/passwords) refused for auto-store and `/remember`.
- Skills rank with cost (duration) signal; duration tracked on skill feedback.

### Feature: Work alone (autonomous continuity)

- When the user says they are stepping away or asks Remedy to handle work end-to-end, continuity injects an **autonomous** policy pack: high agency, finish tests/docs, only stop on hard blockers.

### Docs

- Memory manual: Partner Memory just-works section; commands `/forget` `/pin`.

## [0.11.7] — 2026-07-25

### Feature: session project = tool jail (turn binding)

- Each stream turn applies the **session** `project_path` to the agent (not leftover prior session).
- **No project** sessions → full access for that turn; project sessions jail to that folder.

### Feature: multi-select, drag-drop, pagination

- Sidebar checkboxes + Shift+click range; bulk move toolbar; drag sessions onto project folders.
- `POST /api/sessions/bulk-project`; list sessions paginated (`has_more`, limit up to 500).
- **Load more** in the sidebar; optional **New-in-project sets default** Settings path.

### Polish

- Dropped deprecated `License ::` classifier (PEP 639); tree structure unit snapshot test.

## [0.11.6] — 2026-07-25

### Feature: sessions nested under project folders

- Sidebar groups **No project** first, then each project path as a collapsible parent with session children.
- **+ Add project folder** (browse/type), **+** on a folder for new chat in that project, move session between projects.
- API: explicit empty `project_path` creates/clears no-project sessions (no silent inherit).

### Feature: empty project path = full access

- Unset / `.` project no longer jails tools to install/cwd; access scope becomes **full** (user home as default root).
- Settings warns that picking a folder is better for focused coding.

### Polish (0.11.5 follow-through)

- Session list limit raised (200) for larger trees.
- Creating/moving under a project registers it in the known-projects list.
- Tests: session project API, access-scope unset, frontend grouping helpers.

## [0.11.5] — 2026-07-25

### Fix: stuck agent on “proceed” / short kicks

- Short messages like **proceed**, **continue**, **go ahead**, **do it**, **proceed with all fixes** now enable tools (session log bug: tools=`[]` → force_answer → thinking-only stubs).
- Plan mode auto-exits on build/proceed language so file tools load without a manual toggle.
- Composer stays typable while streaming; **Shift+Tab** toggles Plan ↔ Build.

### Feature: tool process Min / Med / Full / Full+ contract

- **Answer text is never truncated** by process mode.
- **Full / Full+**: complete raw tool args and every result; process + thinking expanded by default; no silent preview cuts.
- Status bar cycles Min → Med → Full → Full+.
- Chat polish: wider assistant bubbles, flat thinking strip, better prose/code typography.

## [0.11.4] — 2026-07-25

### Feature: NanoToken BPE v2 (battery-trained default)

- Default pack **`remedy-bbpe-v2`** (4000 merges) trained on first-party repo source/tests/docs/skills **plus** live multi-provider agent battery transcripts (DeepSeek V4 Flash/Pro, Grok 4.5/4.3 tool+skill turns). Secrets scrubbed; no third-party tokenizer merges.
- **`remedy-bbpe-v1`** retained for fallback/comparison.
- Train/retrain: `scripts/nanotoken_battery_and_train.py` (`--from-corpus`, `--merges`, `--skip-live`); measure pack vs provider usage: `scripts/nanotoken_ratio_eval.py`.
- Progress logging in `train_bpe` for long runs.
- Docs: `docs/manual/17-nanoswarm.md` BPE section; What’s new; README bullet; Helper tip updated.

### Fix: file_read accepts offset/limit

- Models often pass `limit`/`offset` like `list_dir`; previously TypeError aborted the tool. Optional line windows + schema docs; unknown kwargs ignored.

## [0.11.3] — 2026-07-25

### Feature: Remedy-owned NanoToken BPE

- Clean-room **byte-level BPE** engine (`bpe_engine.py`) — no tiktoken/Gigatoken/HF deps.
- Shipped pack **`remedy-bbpe-v1`** trained on first-party synthetic corpus; retrain via `scripts/train_nanotoken_bpe.py`.
- Swarm **assignment** maps provider/model → Remedy pack; `provider_changed` remeasures with that pack.
- Heuristic weight packs remain fallback (`REMEDY_BPE=0` forces heuristic).
- Status/API: `/api/nanoswarm/token/assignment`, `/token/packs`; Helper tip for BPE.

### Feature: finish continuity expansion plan

- **Session LLM**: per-session `llm_provider` + model in SQLite; status-bar switch toast; models refresh after switch; tabs restore provider independently.
- **NanoToken**: family weight packs (`token_tables`), message cache, multiprovider usage ledger + CSV export.
- **Nanoswarm**: Guard, Helper, Pack, Goal, Scout (warm-up), Health (failover chip); shared SkillRegistry + single speculative worker.
- **Skills at scale**: active budget, archive/unarchive, archive unused 90d, packs API, budget banner, project-scoped ranking boost.
- **list_dir**: default page size 200 + offset pagination.
- **Security**: untrusted access scope (project-only + always-ask); webhook constant-time; vision Zip Slip; `REMEDY_HTTP_BOOTSTRAP` helper.
- **UI**: provider catalog enable/models, Usage & Continuity dashboard, stream RAF + plain-text streaming.

## [0.11.2] — 2026-07-25

### Fix: restore live provider model discovery (DeepSeek / xAI / cloud)

- **Root cause**: 0.10.44 perf path limited live `GET {base}/models` to ollama/openrouter/custom only — DeepSeek and xAI fell back to a **stale catalog** (`deepseek-chat`, old Grok ids) after API renames.
- **Restore**: OpenAI-compatible providers query the endpoint again by default (90s cache; opt out `REMEDY_LIVE_MODELS=0`).
- **Catalog fallbacks**: DeepSeek → `deepseek-v4-flash` / `deepseek-v4-pro`; xAI → `grok-4.5` / `grok-4.3` / `grok-4`.
- **Legacy id migration**: `deepseek-chat`/`reasoner` → V4 Flash; old `grok-3*` → current Grok family (normalize + runtime sync).

### Feature: smarter nano swarm utilization (still one Remedy voice)

- **ContextSnapshot** uses shared swarm bots (token/router/memory/pattern/skill) instead of disposable instances.
- **Pattern → remedies**: low tool success windows trigger stuck recovery guidance.
- **Skill ranks**: speculative prep warms catalog; skill intent reuses cache (no chat branding).
- **Learn pre-gate**: pattern nanobot can skip noisy auto-learn traces.
- **Provider change** events notify the swarm for token calibration buckets.
- **Router heuristics** expanded (implement/debug/PR/plan/memory phrasing).
- Docs: `docs/manual/17-nanoswarm.md` (operator guide; not session UI).

## [0.11.1] — 2026-07-25

### Fix: Windows Defender / SmartScreen posture (hardening)

- **Persistence.A!ml (legacy):** still never writes `HKCU\…\Run`; scrub uses Rust **`winreg`** and NSIS **`DeleteRegValue`** (no hidden PowerShell Bypass on every launch/Settings poll).
- **Wacatac / Bearfoos class:** PyInstaller sidecar gets a real **PE version resource** + **icon** (Company/Product/FileVersion); bundle **publisher/copyright/descriptions** set; Cargo package identity filled; UPX remains off.
- Docs: Defender threat inventory in `docs/DESKTOP.md`, install/troubleshooting, `WINDOWS_SIGNING.md`.
- Tests: `tests/test_build_desktop_version.py` for PE version resource content.

## [0.11.0] — 2026-07-24

### Feature: Continuity layer (ContextSnapshot + remedies + project learning)

- **ContextSnapshot**: single-pass tokens, fill, intent policy, brief touch, quality remedies.
- **Intent → policy packs**: silent system focus for memory / skill / plan / tool turns.
- **Quality remedies**: auto recovery guidance when re-explain or stuck rates rise.
- **Structural prune**: collapse old completed tool spans; keep recent pairs full.
- **Speculative prep**: background brief/memory warm between tools and after turns.
- **Project learning**: `~/.remedy/project_learning/` fingerprints (earlier compress, pinned notes).
- **Session quality**: tokens saved, stuck/re-explain rates; `/harness` + partner status.
- **Full+** tool process: only place for advanced continuity activity; UI is “Local vision”.
- **Docs**: continuity philosophy in README, F1 wiki (`16-continuity-philosophy`), manual.

### Feature: Session quality baselines + Full+ diagnostics (earlier in 0.11)

### Feature: Remedy Nano Swarm + local Qwen (first-run download, auto-start)

- **Nano swarm** (`remedy.nanoswarm`): Token, Pattern, Memory, Skill, Router, Helper (reserved) + coordinator.
- **In-house TokenNanobot**: class-weighted estimates + usage calibration (no third-party tokenizer).
- **Shared runtime catalog** (`remedy.runtime`): one pinned **Qwen2.5-VL 3B** for vision, nano, helper.
- **Delivery**: Qwen **not** in the installer (size). **First-run download** of pinned files (Setup Wizard / Settings); same SHA catalog on every PC.
- **Packaging policy**: `tauri.conf.json` does **not** embed `resources/local`; that folder is offline staging only (gitignored weights + README).
- **Starts with Remedy**: `auto_start` + API lifespan + post-install start; no manual Start for normal use.
- **Runtime**: CPU default; CUDA when NVIDIA detected (same Qwen weights). Optional `REMEDY_LOCAL_BUNDLE` for dev/airgap only.
- **APIs**: `GET /api/nanoswarm/status`, `POST /api/vision/activate`, install = download-or-activate; partner status includes swarm.
- **Desktop**: Setup Wizard download on finish; Settings install + swarm panel; Composer hints updated.
- **Agent**: tool steps → PatternNanobot; harness → TokenNanobot; `/harness` shows swarm.
- Manual: `docs/manual/14-visual-decoder.md`.

## [0.10.45] — 2026-07-25

### Fix: setup free UX, tray start, usage placement, vision uninstall

- **Setup free path**: Demo + Ollama cards and a free-key dropdown (no chip flea market).
- **Start hidden in tray** decoupled from “Start with Windows”; `desktop.json` is authoritative;
  window is shown/focused when tray-start is off (fixes always-minimized launches).
- **Usage & cost ticker** lives in the session sidebar footer (bottom-left).
- **Uninstall**: stops `llama-server` and removes `~/.remedy/vision` (llama.cpp + GGUF) on
  config wipe and full wipe (NSIS scripts + `remedy uninstall`).

## [0.10.44] — 2026-07-25

### Feature: Skills HITL overrides, pack export, Time Travel, token cost ticker

- **Skills panel**: force-promote / quarantine toggles; CodeMirror markdown editor for
  `SKILL.md`; multi-select **Export Pack** (ZIP) + import; APIs for quarantine + body PUT.
- **Time Travel**: timeline panel (status bar / command palette); click a step to soft-revert
  chat, restore best-effort `file_write` undo log, drop later checkpoints.
- **Token & cost ticker** (hideable): live run + session tokens/cost; prefers provider usage
  when present, else estimates; list-price breakdown in expand panel.

### Perf: end-to-end speed (Settings, startup, chat UI, secrets)

- **Secrets path**: Windows `icacls` harden no longer runs on every `auth_dir()` read (~90–100ms each);
  harden only on create/write. mtime-cache for `load_provider_keys`; warm secrets at serve start.
- **Config**: `load_config()` mtime-cached for all routes; invalidate/seed on write.
- **Models**: skip live remote `/models` for closed cloud catalogs (use curated list + configured model);
  live discovery kept for Ollama / OpenRouter / custom / local URLs; TTL 90s; shorter timeouts.
  Override with `REMEDY_LIVE_MODELS=1`.
- **Settings GET**: skip no-op migrate/write; no fingerprints unless requested; light vision only.
- **Desktop**: shorter splash; parallel sessions+settings; keep splash token; defer update check 25s;
  adaptive vision/checkpoint polling; project_path cache on new session; `memo` message bubbles.

### Fix: Settings / connection stability + durable debug logs

- **Root cause**: vision `is_running()` called `urlopen` with multi-second timeouts against a dead
  `llama-server` port from **async** handlers, freezing the whole uvicorn event loop. Desktop
  `/api/status` probes then timed out → status bar flipped Connected ↔ Disconnected; Settings
  waited on the same path (~4–9s measured).
- Vision probe: TCP port first, skip HTTP when closed, short timeouts, 2.5s cache; Settings uses
  `get_status(light=True)`; `/api/vision/status` runs in a worker thread.
- New public **`GET /api/ping`** for liveness; status bar prefers it + requires 2 failures before
  showing offline.
- Settings panel loads core config first, vision/desktop prefs in the background.
- **`setup_serve_logging`**: rotating files under `~/.remedy/logs/` (`remedy.log`, `errors.log`,
  always-on `debug.log`); request middleware logs `SLOW` for handlers ≥500ms.
- Docs: troubleshooting section for disconnect flaps + log locations.

## [0.10.43] — 2026-07-24

### Fix: CI mypy + full rebuild of 0.10.42 features

- Resolve `uv run mypy` failures in agent tool extracts, `/plan approve`, and MCP stdio server.
- Rebuild/publish package including vision-shutdown-on-exit and all 0.10.42 product work.

## [0.10.42] — 2026-07-24

### Feature: Bundled **github** skill + packaged MCP host

- New bundled skill **`github`**: PRs, issues, CI, releases via `gh` + git (safe defaults; no force-push unless asked). Seeded to `~/.remedy/skills/github` on discover.
- **MCP packaging**: console script **`remedy-mcp`** (`remedy.tools.mcp_server:main`) in addition to `remedy mcp serve`.
- Desktop Settings → MCP host copies config using `remedy-mcp`.

### Fix: stop vision decoder (llama-server) on Remedy shutdown

- API FastAPI **lifespan** + `atexit` call `stop_server` so the local VL process does not outlive the sidecar.
- `stop_server` kills the in-process handle **and** any PID in `vision.json` (Windows process tree via `taskkill /T`).
- Desktop full quit: POST `/api/vision/stop`, tree-kill sidecar, then best-effort `taskkill` of `llama-server.exe`.
- Hide-to-tray / WebUI still keeps the decoder if the server stays up.

### Plan mode, plans, checkpoints, learning (personal partner roadmap)

- **Plan mode is real**: desktop sends `plan_mode`; server allowlists plan/goal tools and blocks shell/file at `call_tool`.
- **Structured plans** under `~/.remedy/plans/`; API + `/plan` slash commands; Memory panel Plan tab.
- **Mid-task checkpoints** under `~/.remedy/checkpoints/`; auto-save on long Build; Memory · CP status chip.
- **Learning loop**: hard probation tests; atomic `skill_stats.json`; lifecycle owns status; Skills “What I learned” + re-use metrics API.
- **Agent decomposition**: `agent_learn`, `agent_goals`, `agent_workspace_tools`, `agent_skill_tools`, `agent_memory_tools`.
- Skills wipe removes `skill_stats.json` (CLI + NSIS).

## [0.10.41] — 2026-07-24

### UX: Setup declutter, collapsible Settings, status dock, WebUI

- **Setup wizard** simplified: larger type, shorter copy, free-provider **chips** (not long cards), cleaner vision opt-in.
- **Settings** categories expand/collapse via `SettingsSection` (Provider open by default).
- Status bar **Web → WebUI**; quit/settings/title menu wording aligned.
- **Bottom status dock**: server status + **visual decoder install progress** (bar + %) so opt-in downloads are visible without opening Settings.
- **WebUI reliability**: package SPA as Tauri `webui` resource; sidecar `REMEDY_WEBUI_DIR`; same-origin local-bootstrap; friendly page when SPA missing; wait for :7400 before opening browser.

### Feature: Free / zero-setup providers

- Curated free options list + **Demo (LLM7)** guest path so first chat needs no API key.
- `GET /api/providers/free`; Setup free chips; bootstrap can land on demo when nothing configured.
- Help/manual: `15-free-providers`; tests: `tests/test_free_providers.py`.

## [0.10.40] — 2026-07-24

### Feature: Local visual decoder (image → text for text-only models)

- New first-class package `remedy.vision`: opt-in **llama.cpp** `llama-server` + pinned **Qwen2.5-VL 3B** (GGUF + mmproj).
- When the chat model has no native vision, attachments are decoded into a structured brief (scene, OCR, UI, design) before the main LLM runs.
- **Prefer local decoder even if chat model has vision** (`vision.force_decode`) to save provider image tokens; falls back to native vision if decoder is not ready.
- REST: `/api/vision/status|catalog|install|install/cancel|reinstall-runtime|uninstall|start|stop|test`.
- Desktop: Settings Visual decoder (progress, cancel/resume, CUDA switch, warnings), Setup wizard Vision step, composer banner.
- Install: cancel + HTTP Range resume of `.partial` files; host health (RAM/disk/CPU/NVIDIA) warnings.
- Uninstall wipe: `~/.remedy/vision` removed on config wipe / full purge (CLI + desktop NSIS wipe script).
- Metrics: `remedy_vision_decode_total`, `remedy_vision_decode_seconds`.
- Agent tool `vision_decode` (status / install / decode).
- Anthropic adapter: OpenAI-style `image_url` parts → native image blocks.
- Help/manual: `14-visual-decoder`; tests: `tests/test_vision.py`.

## [0.10.39] — 2026-07-24

### Feature: ComfyUI skill — from-scratch bootstrap

- Bundled **comfyui** skill **v1.1.0**: end-to-end instructions for a blank machine —
  download official Windows portable (or git), start server, fetch Flux.2 Klein models,
  API workflows, then `comfyui` generate into chat (works with any chat provider).
- `status` / `locate` when nothing is installed point at the bootstrap path (not only “start”).
- Agent tool description + ReAct policy: bootstrap if empty, then generate; paste markdown images.
- Seeded skills refresh when bundled frontmatter `version` is newer (opt-out: `.user_locked`).
- Tests: version refresh seed; ComfyUI discovery still green.

## [0.10.38] — 2026-07-24

### Fix: xAI OAuth “Cannot reach local API” on fresh install (0.10.37)

- **Root cause:** with API auth on, the auth middleware returned **401** for browser **OPTIONS**
  CORS preflights (no `Authorization` header). Chromium/Tauri then surface
  `Failed to fetch` → *Cannot reach local API at http://127.0.0.1:7400 (/auth/xai/login)*.
  Splash `/api/status` still worked (simple GET, no preflight).
- **Fix:** allow `OPTIONS` through auth so CORS middleware can answer preflights; expand
  default CORS origins for Tauri 2 (`https://tauri.localhost`, asset/ipc hosts).
- Setup/Settings xAI sign-in waits for local API health before starting device login.
- Tests: OPTIONS preflight must not 401; `https://tauri.localhost` allowed.

## [0.10.37] — 2026-07-24

### Security (power preserved, outsiders hardened)

- CORS `*` refused while API auth is on (blocks browser token theft).
- Constant-time Bearer compare; optional `REMEDY_HTTP_BOOTSTRAP=0` for desktop-only tokens.
- Refuse **auth-off + non-loopback bind** unless `REMEDY_ALLOW_INSECURE_BIND=1` (owner escape hatch).
- Quarantined skills cannot `skill_activate` (prompt injection) until Trust; scripts already blocked.
- Skill script env scrubbed of provider keys (same as bash sandbox).
- Telegram ignores chats when allowlist empty unless `REMEDY_TELEGRAM_ALLOW_ALL=1`.
- Desktop prefers Tauri IPC token before HTTP bootstrap; updater requires signed `latest.json` URL match.
- **Auto-approve and full shell remain available** for the owner — no capability removed.

### Feature: Grok-style diff colors in chat

- Unified diffs in chat code fences (`diff`/`patch` or auto-detected) show **red removals** and **green additions**, with muted meta/hunk lines.
- Tool process (Proc) args/results use the same highlighting when content looks like a patch.

### Feature: offline Help wiki + technical owner's manual

- **`docs/manual/`** — full owner chapters (install → troubleshooting → CLI/API).
- **In-app Help wiki** (`F1` / `Ctrl+/` / status bar **Help** / logo menu): searchable TOC, markdown articles, in-wiki navigation, Esc to close.
- Product glue: Settings deep-links, Skills panel guide link, error-screen Troubleshooting, About → Help, command palette entries, `/help` points to the wiki.
- **Settings → About: Report an issue on GitHub** (pre-fills version in the issue template); Help footer link too.
- Vitest coverage for the help catalog.

### Pre-push polish (same version)

- Version surfaces aligned (`latest.json`, package-lock, Cargo.lock); installer URL uses `Remedy.Desktop_*`; stale minisign signatures cleared on version bump.
- `scripts/sync_help_manual.py` keeps docs/manual ↔ desktop help articles in sync.
- **Docs sync pipeline** (`scripts/check_docs.py` + CI step): gates help copies, version surfaces, catalog ids, slash commands vs `_BUILTIN_COMMANDS`, hotkeys vs `hotkeys.ts`, and README test-count claims — same “check / sync” model as version control.
- Setup finish copy: F1/Ctrl+/ open Help wiki; What’s new splits 0.10.36 vs 0.10.37 correctly.
- Settings xAI sign-in re-bootstraps local API token and **persists provider=xAI** on connect; Help report-issue prefills version.
- CLI wizard + `mark_setup_completed` use safe TOML write (scalars before tables; scrub secrets).
- `server-ready` / `server-error` / Retry / Open data folder use official Tauri bridge helpers.
- Hotkeys wired from `hotkeys.ts` SSOT; GET `/settings` no longer rewrites config mid first-run.
- OAuth poll status switches active provider to xAI so chat uses new credentials immediately.
- Tests: API `_write_config` order, xAI OAuth host lock, reportIssue + formatApiErrorBody.

## [0.10.36] — 2026-07-24

### Fix: first-run setup save + xAI OAuth + corrupt config.toml

- **Root cause**: settings writer put root keys *after* TOML `[table]` sections.
  Those keys became part of the last table and could duplicate (`Cannot overwrite a value`),
  so `load_config` returned `{}`, setup looked incomplete, and finish save failed.
- Config writer now always emits **all root scalars first, then tables** (API + `mark_setup_completed`).
- Corrupt / unreadable `config.toml` forces first-run wizard again.
- Setup finish and xAI sign-in re-bootstrap the local API token and surface the real API error
  (no more opaque “Failed to save settings. Is the server running?”).
- First-run: if settings cannot load yet, **Setup opens automatically**; **Open setup** warms auth first.
- `apiFetch`: clearer network/timeout/401 messages; token bootstrap retries.

## [0.10.35] — 2026-07-24

### Fix: first-run after full wipe + uninstall UI

- Desktop sidecar always passes **`--skip-setup`** so the CLI wizard cannot block the API.
- Fresh home gets a default `config.toml` with `setup_completed = false`; **Setup Wizard** runs once the server is up.
- Startup loads **auth + settings first**, then models; wizard no longer depends on models succeeding.
- Token bootstrap retries on 401; splash pre-warms token; longer health wait for skill seed.
- Error screen: **Open setup** + clearer first-install guidance.
- Uninstall options dialog: system font / visual styles / ASCII labels (no mojibake).

## [0.10.34] — 2026-07-24

### CI / release hygiene

- Ruff config tuned so CI lint is green (E501/noise ignores; real F/N issues fixed).
- Duplicate test renamed; signing secrets confirmed for signed Desktop Release.

## [0.10.33] — 2026-07-24

### Security, tests, and performance (Phases A–C)

**Phase A — Trust & safety**
- Local API auth **on by default** (`~/.remedy/auth/local_api_token`); desktop loads Bearer automatically; disable with `REMEDY_API_AUTH=0`.
- Zip Slip protection on skill pack import; **quarantine blocks `skill_run`**.
- Secret store: never grant Everyone ACL; xAI credentials DPAPI-encrypted on Windows.
- Updater: only `AhmiDarrow/RemedyAI` release URLs + known GitHub asset CDNs.
- Default approval (ask mode) for `bash_exec`, `file_write`, `skill_run`.
- Tool subprocess env scrubbed of secrets; webhooks require auth when API key set.

**Phase B — Tests**
- New: `test_api_auth`, `test_zip_import_security`, `test_skill_tools`, `test_skills_api`, `test_session_stream`, `test_updater_api`, `test_secret_acl_no_everyone`.
- Desktop: vitest + `sanitizeChat` unit test.

**Phase C — Scale & polish**
- Tiered context caps (tool 64k / file 128k / history 1.5M); `REMEDY_FULL_CONTEXT=1` for legacy unlimited.
- Strong auto-compress when harness fill is high; skill body inject cap 24k.
- Fixed skill catalog ranking (workspace-aware, no discard); context/skill metrics.
- MessageFeed windowing (last 80 messages + “show earlier”).

## [0.10.32] — 2026-07-24

### Fix: interactive installer no longer launches before finish page

- NSIS POSTINSTALL only auto-starts Remedy for **silent / passive / update** installs.
- Interactive installs wait for the finish page (“desktop shortcut” + “Run Remedy”).
- In-app auto-update passes `/UPDATE` so update-mode hooks stay correct.

## [0.10.31] — 2026-07-24

### Fix: uninstall no longer aborts when options dialog fails

- NSIS PREUNINSTALL only **Abort**s on intentional Cancel (exit code 1).
- PowerShell/WinForms errors (exit 2+) keep user data and **still remove the app**.
- Default choices file written before the dialog so wipe never hard-fails.
- Pure-ASCII options script; safer full-wipe (no live install-dir delete mid-run).
- Logs: `%TEMP%\RemedyDesktop-UninstallOptions.log`, `…UninstallWipe.log`.

## [0.10.30] — 2026-07-24

### Skills system (unique strength)

Progressive disclosure, closed-loop learning, ranking, and governance for the
skill library — see `docs/SKILL_LIFECYCLE.md`.

- **No force-ACTIVE on discover**: curated bundled skills stay ready; auto-generated
  and quarantined skills keep probation status from frontmatter.
- **Progressive disclosure**: ranked catalog in context; tools `skill_activate`,
  `skill_run`, `skill_search` load full bodies / scripts on demand.
- **Post-turn auto-learn**: multi-step successful tool runs distill into probation
  skills; activations and script runs feed `record_skill_feedback` + promote/demote.
- **Durable stats**: `~/.remedy/skill_stats.json` so lifecycle survives restarts.
- **Ranking**: `match_skills` by status, description, tags, effort, success rate,
  workspace hints.
- **Merge + lineage**: same-name traces merge recovery notes instead of duplicating;
  honest `lifecycle_confidence` in learning history.
- **Trigger-oriented descriptions** + failure protocol on generated skills.
- **Pack export/import**: ZIP packs; imports land in **quarantine** until trusted.
- **API**: richer `SkillInfo`, `GET/POST /api/skills…` status, feedback, export, import.
- **Desktop Skills panel v2**: status chips, hard-won badge, search, activate/disable/
  trust, success/fail feedback.
- **Effort-weighted lifecycle** (from 0.10.29 tree): hard-won skills resist demote/prune.

## [0.10.29] — 2026-07-24

### Fix: auto-update install + relaunch

- Detach update PowerShell with `cmd /c start` + `CREATE_BREAKAWAY_FROM_JOB` so `app.exit()` no longer kills the installer script mid-flight.
- Upgrade in place via NSIS `/D=<current install dir>`; discover binaries under both `%LOCALAPPDATA%\Programs\Remedy Desktop` and `%LOCALAPPDATA%\Remedy Desktop`.
- Prefer relaunching a binary whose mtime advanced (detect real replace); log to `%TEMP%\RemedyDesktop-Update.log`.
- POSTINSTALL relaunch via `cmd /c start` so the new app survives NSIS exit.
## [0.10.28] — 2026-07-24

### Fix: DeepSeek / long turns cut off mid-answer

- SSE idle timeout **120s → 900s** (DeepSeek thinking pauses no longer kill the stream).
- Never soft-empty after tools/thinking: promote `reasoning_content` to the answer; retry synthesis up to 8×.
- DeepSeek `max_tokens` uses API-legal caps (chat 8k / reasoner 64k) so oversized 128k requests stop 400ing the turn; auto-continue on `finish_reason=length`.
- Final-answer rounds stream live; ReAct budget **256** steps; length continuations effectively unlimited.
- Last synthesis asks for a **complete** answer (not a short stub).

## [0.10.27] — 2026-07-24

### Fix: desktop update check (tray + Settings)

- Tray **Check for updates** now opens Settings and runs a real check (no longer only focuses the chat composer).
- Compare against the **desktop shell** version, not only the Python sidecar (prevented 0.10.25 EXE from seeing 0.10.26 when the sidecar was already newer).
- Merge Tauri + API update sources; always show **This app** vs **Latest release**.
- Cache-bust GitHub `latest.json`; `/api/updates/check?current=` for shell version.

## [0.10.26] — 2026-07-24

### Agent headroom (no cut-off answers / thinking / tools)

- **Provider `max_tokens`**: always **128k** completion budget — never throttled by thinking level or tool vs answer.
- **No soft-trim** of history answers, tool results, file reads, bash stdout/stderr (OOM safety only at 50M chars).
- **Harness prune**: dedupe only by default — does **not** shorten tool/assistant bodies.
- **ReAct**: up to **128** steps; removed early force-answer at step 8; **64** length auto-continues.
- **History**: 2000 messages / large char budget; drop oldest turns instead of slicing mid-message.
- **Thinking default**: **high**; nudges say finish fully, never truncate.
- **UI**: full answers (no collapse); tall thinking panel with full text.
- **Sessions**: export/import as plain-text `.txt` (round-trip) via API + desktop.

### Session export / import

- `GET /api/sessions/{id}/export?format=txt|md` — default plain-text export.
- `POST /api/sessions/import` — create session from `.txt` / legacy `.md` / freeform.
- Desktop: Sidebar Import/Export, command palette, `/export`, `/import-session`.

## [0.10.25] — 2026-07-24

### Partner desktop UX polish

- **Tool process** modes: **Off** (minimal) · **Medium** (labels + short results) · **Full** (complete raw args/stdout). Settings + status-bar **Proc** cycle. Process log stays under the message, collapsed after the turn.
- **Stick-to-bottom** chat feed for tokens, thinking, tools, and full process dumps; detach when user scrolls up; **↓** resumes follow. Process panel has the same rule.
- **Chat**: sleek shrink-wrap bubbles; user initials/name; icon-only copy/edit; image lightbox; progress bar for tools/jobs.
- **Branding**: title-bar wordmark (logo menu: Settings, About, Updates); session avatars use circuit-R icon.
- **You & Agent**: `user_name` (what Remedy calls you) before agent name; first-run name prompt; profile sync.
- **Sessions**: auto-title from first prompt; double-click / ✎ rename; search, pin, tags.
- **Tray**: Show, Settings, Check for updates, About, Quit.
- **Themes**: Neutral Dark; density cozy/compact; custom accent; accurate theme swatches; smoother switches.
- **ComfyUI / local discover**: portable discovery, image embed path, tool progress SSE.
- **Auth / keys**: per-provider secret store; DSML strip + pseudo-tool recovery; thinking stream to UI.

## [0.10.24] — 2026-07-24

### xAI OAuth in frozen desktop (hard fix)

- Device OAuth **must** use `https://auth.x.ai` (never `accounts.x.ai` → 307 `/sign-in`).
- Refuse wrong host; no redirect following for device/token POSTs.
- PyInstaller builds force `--paths src` + PYTHONPATH so site-packages cannot pin old OAuth.
- Sidecar start kills anything on :7400 (prevents dual stale servers).
- Diagnostics: `GET /api/auth/xai/oauth-meta` shows `oauth_build` / device URL.

## [0.10.23] — 2026-07-24

### Desktop release rebuild

- Rebuild sidecar + installer so **xAI OAuth (`auth.x.ai`)** is in the frozen
  desktop package (0.10.22 source fix was easy to miss if an older sidecar stayed installed).
- Includes Defender Persistence.A!ml fix (Startup folder, no HKCU Run).

## [0.10.22] — 2026-07-24

### xAI OAuth 307 fix

- Device-code + token endpoints now use **`https://auth.x.ai`** (was
  `accounts.x.ai`, which returns **307** to `/sign-in?redirect=…` and broke
  “Sign in with xAI”).
- Verification URLs still open on `accounts.x.ai` (as returned by xAI).

### Windows Defender Persistence.A!ml (critical)

- **Stop writing HKCU Run** for “Start with Windows” (triggered `Behavior:Win32/Persistence.A!ml`).
- Autostart now uses a **Startup folder** `.lnk` only (Settings → Apps → Startup).
- On launch / toggle / uninstall: **scrub legacy Run keys** (`RemedyDesktop`, etc.).
- Installer PREUNINSTALL removes Startup shortcut + Run leftovers.

## [0.10.21] — 2026-07-23

### Final partner phase (goals · approve · knowledge)

- **Goals loop**: tools `goal_add` / `goal_list` / `goal_complete` / `goal_verify`; slash `/goal`, `/goals`.
- **Approvals**: high-impact bash patterns require explicit approve; API + `/approve` `/deny`; desktop **ApprovalBanner**.
- **Knowledge packs**: import `.md`/`.txt` folders via `POST /api/memory/import` and `/import <path>`.
- **Partner status**: `GET /api/partner/status` + status-bar chip (approvals, goals, harness, scope).

## [0.10.20] — 2026-07-23

### Remaining phases + prompt history

- **Composer ↑ / ↓**: shell-style previous/next prompt history (localStorage, up to 80 entries).
- **Always ready runtime**: close-to-tray (hide, keep sidecar), start-in-tray, tray menu Show/Quit, left-click tray to show.
- **Desktop prefs** file `~/.remedy/desktop.json` + Tauri commands.
- **Setup finish**: optional “Keep Remedy ready” + ↑ tip.
- **Handoff** includes Memory Harness Session Brief when present.

## [0.10.19] — 2026-07-23

### Partner plan (remaining phases)

- **Access scope**: `project` | `home` | `full` multi-root path resolution; Settings control; agent hot-reload.
- **Always ready**: Start with Windows (HKCU Run), start-in-tray / close-to-tray prefs in Settings + config.
- **Memory Harness**: auto compress nudges by context fill; artifact tracking on file tools; Settings mode.
- **Companion skills**: `remember-me`, `design-critique`, `personal-briefing`, `write-with-user`, `decision-journal`.
- Slash already: `/compact`, `/harness`, `/remember`, `/whoami`.

## [0.10.18] — 2026-07-23

### Partner vision (Phase A foundation)

- **System identity**: partner framing (knowledge, design, code, PC tasks when allowed); medical disclaimer retained.
- **Desktop chat**: user messages on the **right**, Remedy on the **left**, themed bubble tokens for all palettes.
- **Settings**: persona + agent name; project path **input + Browse**; save reports **Remedy reloaded** / project loaded.
- **Native folder picker** (`pick_folder` Tauri command) for project workspace.
- **Memory Harness (L0–L2)**: mechanical send-view prune; Session Brief; `compress_context` tool; real `/compact`, `/harness`, `/remember`, `/whoami`; profile injection.
- Empty chat copy: “Your partner is ready.”

### Branding / taskbar icon

- Multi-size `icon.ico` (16–256) from circuit-R monogram via `scripts/setup_branding.py`.
- Runtime `set_icon` on main window so taskbar matches tray (not stale medical PE cache).
- Docs: Windows icon-cache clear steps in `docs/DESKTOP.md`.

## [0.10.17] — 2026-07-23

### Branding (not medical)

- Clarify Remedy is a **software coding agent** for projects/code — not medical
  or clinical software (README, pyproject, system prompt, desktop setup copy).
- Replace caduceus / healing brand prompts and splash/logo assets with tech
  wordmark + circuit monogram (no medical symbols).

## [0.10.16] — 2026-07-23

### Fixed

- **Splash hang on "Ready"**: parent re-renders with inline `onReady` restarted the
  health-poll effect mid-handoff; handoff now uses stable callback refs and a
  single mount lifecycle.
- **White splash flash**: boot splash and React splash force a dark background
  (`#0a0a1a`) regardless of system light theme.
- **Auto-update reliability**: longer unlock delay, PowerShell-scheduled silent
  NSIS (`/S /NCRC`) with post-install relaunch fallback; clearer manual URL on
  failure. Release workflow renames installers to space-free asset names so
  `latest.json` URLs match GitHub assets.

## [0.10.15] — 2026-07-23

### xAI OAuth + API key (OpenCode-style dual auth)

- First-class **xAI (Grok)** provider with `https://api.x.ai/v1`
- **Sign in with xAI** device-code OAuth (desktop Settings + Setup wizard)
- Secondary **console API key** path (`xai-…` / `XAI_API_KEY`)
- Tokens stored in `~/.remedy/auth/xai.json`; refresh on expiry / HTTP 401
- CLI: `remedy auth login|logout|status|apikey xai`
- Env bootstrap: `XAI_API_KEY` preselects xAI on clean/default config

### Providers & self-setup

- Catalog: **Groq**, **Mistral**, plus OpenAI / Anthropic / Google / DeepSeek / OpenRouter / Ollama
- `GET /api/providers` is the desktop source of truth (auth modes, models, advanced flag)
- Known brands hide Base URL; **Custom** lives under Advanced
- Ollama auto-detect (`GET /api/providers/ollama/detect`) with setup-wizard hint
- Desktop opens OAuth verification via Tauri shell (fallback `window.open`)

### API

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/providers` | Provider catalog |
| `GET` | `/api/providers/ollama/detect` | Local Ollama probe |
| `GET` | `/api/auth/xai` | xAI auth status |
| `POST` | `/api/auth/xai/login` | Start device-code OAuth |
| `GET` | `/api/auth/xai/login/status` | Poll OAuth session |
| `POST` | `/api/auth/xai/apikey` | Save console API key |
| `DELETE` | `/api/auth/xai` | Sign out / clear tokens |

## [0.10.14] — 2026-07-23

### Desktop polish

- Splash holds **at least 3 seconds** (and longer if server still starting); fade-out handoff
- Kill white flash: themed HTML boot splash + early background
- Theme default **System** (follow OS light/dark); improved reading contrast on all themes
- Hotkey registry + **Settings → Help & shortcuts**; `/help` includes keyboard shortcuts
- Empty chat and setup finish tip Shift+Enter / Ctrl+/

## [0.10.13]
 — 2026-07-23

### Fixed (remaining review backlog)

- Metrics registry/counters/histograms are actually thread-safe (locks).
- FTS MATCH failures log at debug before LIKE fallback.
- TOML writer omits `None` keys instead of writing empty strings.
- SSE stream idle timeout (120s) ends stuck keep-alive rounds.
- Sandbox workdir/allowed_paths compare after consistent resolve.
- Learning trace dict builder validates/aliases tool keys more safely.

## [0.10.12]
 — 2026-07-23

### Fixed (review + stop-the-agent failures)

- **DeepSeek HTTP 400** `reasoning_content must be passed back`: assistant tool
  turns now include `reasoning_content` from the stream; repair+retry if missing.
- **API failures no longer abort the whole turn**: soft-recover up to 3 times,
  force a final answer from tool context instead of stopping cold.
- Stream exceptions end with a recoverable user message (session intact).
- **CLI `remedy tool run`**: uses BasicRuntime workspace-jailed tools (no bypass).
- **Security**: Windows dangerous commands (reg, takeown, icacls, …); Windows
  recursive del/rmdir patterns; stop flagging bare `2>/dev/null`.
- **SecurityError** tool results use SECURITY_BLOCKED (clearer than generic exception).
- Larger history/context (48k char budget, more steps/tokens) for long project reviews.
- Workspace jail unit tests + reasoning_content tests.

## [0.10.11]
 — 2026-07-23

### Fixed

- **remedy-desktop.exe stays in Task Manager after close**: Windows does not kill
  child processes when the UI exits, and cleanup only ran on window Destroyed.
  Now tree-kills the sidecar PID (`taskkill /T`), force-stops leftover
  remedy-desktop images / :7400 listeners, and runs shutdown on CloseRequested,
  Destroyed, ExitRequested, and Exit.

## [0.10.10] — 2026-07-23

### Fixed

- **DeepSeek (and other OpenAI-compatible providers) stream crash**: agent only
  treated `provider_name == openai` as SSE, so DeepSeek responses
  (`text/event-stream`) were read with `resp.json()` and failed with
  unexpected mimetype. Now all OpenAI-compatible adapters use SSE streaming.

## [0.10.9] — 2026-07-23

### Fixed

- Auto-update aborted with **Cant write remedy-desktop.exe**: installer ran while the
  sidecar/main process still held file locks. Now force-kills sidecar processes,
  schedules silent install (~2s) after app exit, and NSIS PREINSTALL retries kills
  + best-effort delete of locked binaries.

## [0.10.8] — 2026-07-23

### Fixed

- CI desktop build: TypeScript unused variable in useUpdateChecker failed tsc -b (blocked 0.10.5-0.10.7 installers).

## [0.10.7] — 2026-07-23

### Fixed (one-click update pipeline)

- **Silent install**: used MSI-style `/PASSIVE` which NSIS ignores → multi-step
  wizard. Now launches the installer with **`/S`** (true silent NSIS).
- **Relaunch**: NSIS hooks only killed processes; no POSTINSTALL launch. Added
  `NSIS_HOOK_POSTINSTALL` to `Exec` `Remedy Desktop.exe` after install.
- **One click**: Update screen required a second “Update & Relaunch” press. It
  now **auto-starts** download/install when opened.
- **Detached installer**: spawn with `DETACHED_PROCESS` so install survives app exit.
- **Download hardening**: 10-minute timeout, reject HTML content-types, validate
  PE `MZ` header + min size, refuse update-available without installer URL.
- **Concurrency**: block double-start of in-flight updates.

## [0.10.6] — 2026-07-23

### Fixed

- **About showed Version v0.9.0** while the updater reported 0.10.x — `GET /api/settings`
  crashed with `NameError: name 'version' is not defined` (should use
  `_remedy_version`). Settings never loaded, so the UI fell back to the hard-coded
  `0.9.0` placeholder.
- Same bug on `/api/updates/check` (`current = version`).
- urllib call used `_urllib.request.urlopen` after `import urllib.request as _urllib`
  (AttributeError); corrected to `_urllib.urlopen`.
- About panel prefers the desktop shell version from the update checker when present.

## [0.10.5] — 2026-07-23

### Fixed

- **Check for Updates no longer looks like a no-op** — errors were swallowed and
  the Settings panel only rendered status when `updateInfo` was set, so failed
  checks left a blank area after the button.
- Desktop update fetch tries **all** metadata sources (no longer aborts after the
  first URL error), uses a **15s timeout**, and runs off the UI thread.
- Frontend always surfaces current/latest/up-to-date/error after a check; falls
  back to `/api/updates/check` when the Tauri path reports an error.
- Python `/api/updates/check` also tries GitHub API when `latest.json` fails and
  returns combined error strings instead of silent desktop failures.

## [0.10.4] — 2026-07-23

### Fixed

- **ReAct tool-call pairing** — OpenAI-compatible APIs require every assistant
  `tool_calls[].id` to be followed by a matching `role=tool` message. Large
  multi-tool turns (e.g. “review project”) could previously emit fewer tool
  results than tool calls when:
  - parallel execution hit `MAX_PARALLEL_TOOLS` and dropped the remainder,
  - fingerprint dedupe collapsed identical calls to a single result,
  - a tool raised and the error path used a random `tool_call_id`.
- Missing or empty streaming tool-call `id`s are normalized before the next
  provider request.
- Defense-in-depth: `ensure_tool_call_pairings()` sanitizes the message list
  before every LLM request so incomplete pairings cannot ship.

### Tests

- Added `tests/test_tool_call_pairing.py` for normalize / sanitize / parallel
  cap / dedupe / exception id pairing.

## [0.10.3] — 2026-07-23

### Added

- Agent recovery contract with suggestive tool errors and one recovery nudge.
- Stream-path chat latency metrics; expanded mypy surface.
- Themed custom title bar matching app theme.

### Fixed

- Long LLM streams no longer cut off mid-answer (`finish_reason=length` auto-continue).
- Restore full original prompt in composer on Edit.
- Enable `createUpdaterArtifacts` for signed auto-updates.
