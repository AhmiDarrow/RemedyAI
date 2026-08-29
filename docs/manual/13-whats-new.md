# What's new (recent)

High-level product notes for owners. Full detail: repo `CHANGELOG.md`.

Current series: **v0.41.7** — life-task owner card, recipes, verify after writes.
**Public** release is still **[v0.41.5](https://github.com/AhmiDarrow/RemedyAI/releases/tag/v0.41.5)** · PyPI **`remedy-ai==0.41.5`**. Partner line still starts at 0.31.0.

## Contents

- [Unreleased](#unreleased---grove-connect-phone-remote) · [0.41.7](#0417---life-task-owner-card) · [0.41.6](#0416---hands-stay-on-first-run-talks) · [0.41.5](#0415---rmb-thinking-is-an-option) · [0.41.4](#0414---local-muscle-without-leftover-jobs) · [0.41.3](#0413---she-knows-the-house) · [0.41.2](#0412---closed-loop) · [0.41.1](#0411---checkpoints-on-the-wired-path) · [0.41.0](#0410---multilingual-plan-and-memory-authority) · [0.38.1](#0381---trust-gates) · [0.38.0](#0380---capability-architecture) · [0.31.2](#0312---this-round-this-folder) · [0.31.1](#0311---she-drives-her-own-ui) · [0.31.0](#0310---a-partner-for-any-ability-level) · [Hive](#hive) · [Research](#research) · [Game studio](#game-dev-studio) · [0.30.0](#0300---grove-voice-life-tasks-and-the-vault) · [0.26.2](#0262---host-in-remedys-hands) · [0.26.1](#0261---build-finishes) · [0.26.0](#0260---windows--linux-desktop) · [0.25.1](#0251---jail-stop-and-tab-isolation) · [0.25.0](#0250---stable-tabs--settings-chrome) · [0.24.0](#0240---host-bridge--first-home) · [0.23.2](#0232---defender-execution-false-positive) · [0.23.1](#0231---first-turn-agency) · [0.23.0](#0230---build-ability--companion) · [0.22.3](#0223---clear-mid-turn-stops) · [0.22.2](#0222---sleev-fail-open) · [0.22.1](#0221---sleev--gateway-lock) · [0.22.0](#0220---living-organism--builder-continuity) · [0.21.1](#0211---continuity--self-inject-safety) · [0.20.0](#0200---partner-metabolism--always-ready) · [0.19.0](#0190---parallel-multi-provider--background-turns) · older below

## Unreleased - Grove Connect (phone remote)

**Settings → Connect** stays **off** until you turn it on. Pair a phone with a
60-second QR on this PC. The phone drives **this computer**; it does not run
Remedy. Chosen IPv4 only; the local API on `:7400` stays loopback. Computer-use
poller and local-bootstrap are refused on Connect. Optional owner-run relay:
`remedy connect-relay`.

## 0.41.7 - Life-task owner card

A life task shows as one spoken sentence, **Yes / No / Explain**, and
live **Step N of M**. Recipes (`open` / `search` / `shop` / `fill` /
`sign_in`) plus a URL and vault handles expand on this PC — the model
does not invent the JSON. After a password, 2FA, or CAPTCHA wall the
Browser rail stays interactive and remaining steps continue. Pay / send
/ delete never auto-resume. **What Remedy did** shows intended vs
observed. Casual writes actually run the project's tests (Ask still
gates). Desktop clicks try UIA Invoke/Toggle before click-at-center.
Saying Yes, No, or Explain acts on the card.

This tree **v0.41.7**. Public is still **v0.41.5**.

## 0.41.6 - Hands stay on, first run talks

Enter while she is working steers the live turn (Ctrl+Enter interrupts, Alt+Enter queues).
If steer cannot land, the words wait in line — they do not stop her hands.
A new home actually talks on guest llm7 instead of dummy OpenAI. Leftover
"Stay with: Continue…" residue no longer arms as a job. Linux vision extract
keeps its library links. The Grok work pack keeps computer hands. RMB stays
off until you start it. Desktop OCR can click on-screen words when DOM/UIA
is empty. Native type finds a field by its visible label, same as click.

## 0.41.5 - RMB thinking is an option

Local models keep their thinking channel **on** unless you turn it off in
Settings → RMB (or `rmb action=settings thinking=off`). Jinja, MTP, mmap,
MoE CPU experts, draft layers, and reasoning budget are the same options
list — auto-load no longer hides them.

**Public:** GitHub **[v0.41.5](https://github.com/AhmiDarrow/RemedyAI/releases/tag/v0.41.5)** · PyPI **`remedy-ai==0.41.5`**.

## 0.41.4 - Local muscle without leftover jobs

A new chat does not inherit the last tab's "keep going" job. Asking how a
local model feels is an answer, not a tool storm. RMB lists GGUFs already
in her house and keeps a sibling MTP draft across Settings restarts.

## 0.41.3 - She knows the house

RMB is her muscle, not a Settings-only add-on. She can start, stop, and
switch chat onto it. She can list this PC's apps, walk the doors, and
say which tool drives each organ. A build without tests is still not
your goal done.

## 0.41.2 - Closed loop

She does not call a page loaded until she has seen it. A build without tests
is not your goal done. The phone keeps listening while a long turn runs.
Memory inject stays small and labeled. Inbox drops wait until you ask.

## 0.41.1 - Checkpoints on the wired path

Vault fill and payment-shaped clicks stop for you even in Auto (ordinary
typing still does not). Browser vault types only into a named snapshot
field. Stored provider keys stay on that provider's own host. Setup saves
keys into the secret store. High-quality voice stays opt-in. Self-inject
apply asks you first. Telegram does not rewrite the chat's model. Linux
can capture and click. Grove remembers your Studio chat.

## 0.41.0 - Multilingual, Plan, and memory authority

**Remedy is multilingual.** **Settings → You & Agent → Language** (default
**Auto**) pins chrome *and* replies to this PC and the language you type —
Spanish, Portuguese, French, German, Arabic, Hindi, Chinese, Japanese, Korean,
Swahili, and more; RTL flips the layout. Tools, code, and paths stay as
written. Help wiki stays English for now. Plan steps can record what she meant
to do, what she saw, and why a step stopped — a tool-ok is not your goal done.
Partner Memory is context, not a grant; Hive cannot write parent facts.
Payment and credential checkpoints still cannot be recovered around. Stale
screen snapshots are re-taken before a `ref=` click. The status-bar provider
list hydrates at launch.

Shipped in the **0.41** public line (current tag **v0.41.5**).

## 0.38.1 - Trust gates

A helper she hires cannot read your mail or calendar. Saying yes to one
skill run does not approve a different one. **Autonomous** still asks in an
untrusted folder. **Trust** in Settings no longer snaps back to Balanced
when you save something else. Voice status no longer stalls the window
while engines load.

Was public as PyPI **`remedy-ai==0.38.1`** · tag **v0.38.1** (superseded by **v0.41.5**).

## 0.38.0 - Capability architecture

She still does the work. Authority is now explicit: tools declare what they
may do, policy decides, generic shell does not inherit your GitHub or SSH
tokens, and a command that exits 0 is not treated as “the goal is done”
until verification says so. Turns have ids. Hive daughters cannot use
credentials the parent did not have. Pages she reads are observed facts,
not things you declared. Grove shows a single quiet line while a turn is
running (**Working…**, **Waiting for you…** when she needs a yes, **Checking…**
after tools until the next reply).

**Trust** lives in Settings → Security & power (Conservative / Balanced /
Autonomous). Conservative still asks before shell, files, and skills even
in Auto. Autonomous skips in-project high-impact asks the same way Auto
does. Mail and payment still always stop. README is a short pointer into
this manual, not a second wiki. Grove shows the live checklist while she
works. **Stop** on one chat does not resume that work from another tab.

Tagged as **v0.38.0**. **v0.38.1** is the follow-up that closes the
trust gates still open in that cut.

## 0.31.2 - This round, this folder

Thinking is **this model round’s scratchpad**, not a recap stack. Ask her to
open a directory in the Files rail, a URL in Browser, or a chat in Sessions —
she does it in the app. Scratch notes live on disk so she can read what you
typed. Source reads keep `bot_token: str`; long tool results say
`…[truncated]` on a line break instead of looking like a cut file.

PyPI **`remedy-ai==0.31.2`** · tag **v0.31.2**.

## 0.31.1 - She drives her own UI

Ask her to open Alongside, Voice settings, Help, or the Terminal rail —
she does it in the app. She does not click her own chrome. Changing a
setting opens that Settings section so you can see it. Grove / Studio
screenshots capture **her window**, not a random monitor. Linux `.deb`
and AppImage now build (the 0.31.0 tag never got a GitHub Release).

PyPI **`remedy-ai==0.31.1`** · tag **v0.31.1**.

## 0.31.0 - A partner for any ability level

This is a new Remedy. She is no longer “a coding agent that also has a
browser.” She is a partner who lives on this computer and finishes the
goal you set — errands, research, games, and code — for owners at every
ability level.

**Grove** is home. She **speaks and hears** locally (Chatterbox Nano on
bundled public-domain references, Kokoro until that lands, whisper for
hearing). One speak-aloud control on the status bar, which now sits on
Grove and Studio. You can talk while she works (mid-turn steer). Her
voice identity actually reaches the speakers, drifts with the
relationship, and stays put when you ask (`voice_hold`). The installed
app fetches a pinned Python into `~/.remedy/voice/` so Desktop can
speak — no `pip install` for the owner.

**Life tasks** — order, book, fill, pay — are whole jobs: one plan-level
approval, act → verify → retry, non-waivable checkpoints for money /
credentials / send / delete. Cards live in the **Vault** and type only
into verified fields. 2FA, CAPTCHA, and the last payment click are
designed owner moments. Reminders, mail, calendar, documents stay.

**Hive.** Silent foragers (one bounded job) and standing posts (pulse on
an interval). They never appear as extra chats. They report a capped
packet; she still talks to you. Diagnostics shows the roster.

**Research.** Notebooks, R, Julia, manuscripts: literature, a citation
library that has to resolve, analysis in *your* environment with a run
ledger, a priori power, reporting checklists. Fourteen field packs route
themselves; an ordinary coding turn does not list them.

**Game studio.** Godot 4, Phaser/Pixi, Bevy, Pygame, Love2D — engine
detection, headless verification, playtest, export. Unity/Unreal
knowledge. Optional MCP editor bridge.

**Web, licence, colour.** Fetch and search are on after install
(OpenSERP on loopback; DuckDuckGo HTML while it downloads). `web_fetch`
reads robots.txt. The installer shows the product terms and carries
third-party notices. Three colorblind-safe themes.

**Local models.** RMB defaults to **Qwen3.5-9B (Q6_K)** on measurements
— a 9B at 6-bit holds tool-call structure that larger 4-bit models drop.
The 35B-A3B stays in the catalog for VRAM-scarce setups. A local-model
harness fixed the agent loop so tool calls are not stripped, green
verifies no longer end the turn before the run, and markup is not shown
as the reply.

**Build stays the job.** Frontier models are not taught a syllabus every
turn. Long jobs keep going when the SSE pane blinks. Enter jumps to the
latest message. Money / send / close cannot be waived by auto/full.

**Browser sign-in.** “Sign in with Google” in the rail opens a real account
window instead of parking on Google’s transformer page.

PyPI **`remedy-ai==0.31.0`** · tag **v0.31.0**.

## Hive

Remedy can hire silent daughters for a slice of work — a forager for one
bounded job, a standing post that pulses on an interval. They never appear
as extra chats. They report a compact packet to her; she still talks to you.
Stop cancels foragers hired by that turn; posts keep going until she retires
them. Diagnostics shows the roster. See [Hive](28-hive.md).

## Research

Point Remedy at a notebook, an R or Julia analysis, a Snakefile or a
manuscript and she works the research loop: literature search, a citation
library that has to resolve, analysis in *your* environment with a run
ledger (so a figure can be checked against the data that produced it),
a priori power and effect sizes, and reporting checklists (CONSORT,
PRISMA, STROBE, ARRIVE, MDAR). Fourteen field packs
(`research-method`, `statistics`, `ml-research`, `clinical-research`,
…) route themselves from what you say and from the project files; an
ordinary coding turn does not list them. See [Research](27-research.md).

## Game dev studio

Remedy works game projects like a small studio. She recognises **Godot 4**,
Phaser/PixiJS, Bevy, Pygame/Arcade and Love2D projects (Unity and Unreal
are known, not driven), finds the engine from your environment or the
project folder, verifies every change with the engine itself — headless
runs, `--check-only` on scripts, scene references checked offline — plays
the build with screenshots and key presses, lists and runs export presets,
and keeps a design document and a cut list. Headless engine runs used to
be mistaken for GUI launches and returned nothing; that is fixed. A new
set of packs (`game-dev-studio`, `godot-4`, `game-assets`, engine packs,
`engine-mcp-bridge`) route themselves from what you say. Optional: list a
Godot MCP server in `config.toml` for live editor control.

Skills she learns are now actually graded by how the turn went; unused
ones retire after three weeks, and *Allow skill creation* in Settings is
a real switch. Providers discover models from their endpoints and a saved
custom endpoint becomes a provider of its own. See [Game dev](26-game-dev.md).

## 0.30.0 - Grove, voice, life tasks and the Vault

Remedy has a new home: **Grove**, the partner surface, with Studio one tap
away. She speaks and hears locally (Kokoro / faster-whisper, optional
Chatterbox for a human-bar voice), downloads show in the title bar, and
Settings stay on Grove. Life tasks — ordering, booking, paying — are whole
jobs with observed success and non-waivable owner checkpoints; payment
details live in the **Remedy Vault** and are never typed by guesswork.
Reminders fire, mail and calendar work on an app password, documents are
read. Telephony is bench-only (Phase 0): policy, transcripts and hard
checkpoints are in code; nothing is called. A review pass fixed form
filling, the phone checkpoints, and voice installs that could stall the
server. mypy now covers the whole tree.

Local line (not a public tag). The next public package is **0.31.0**.

## 0.26.2 - Host in Remedy's hands

Work turns drive this PC without an Ask pause. Build no longer sticks on a
hung `pytest --lf` ledger or a stale checklist in the user profile.
Frustrated follow-ups keep tools. Jail, Plan, and auth stay closed.

PyPI **`remedy-ai==0.26.2`** · tag **v0.26.2**.

## 0.26.1 - Build finishes

Build no longer stops to ask permission, and it cannot mark a page done
when the file is empty or missing. Frustrated “why is this failing?”
follow-ups keep tools on. The sidecar path with a space in
`Remedy Desktop` is not a write dest; overwriting `cmd.exe` still is.

PyPI **`remedy-ai==0.26.1`** · tag **v0.26.1**.

## 0.26.0 - Windows + Linux desktop

Remedy is one partner on **Windows and Linux** (including WSLg). Maximize
uses the work area of the monitor the window is on. Close on Linux
minimizes to the taskbar. Plan mode cannot write. `/reset` and Stop leave
a clean session. Settings no longer stall the Windows API. Write jail and
host rewrite cover Combine / `which` metacharacters. Linux first-run
downloads the same pinned llama.cpp runtime as Windows (Ubuntu CPU /
Vulkan). The `.deb` pulls WebKitGTK / GTK; the AppImage is more
self-contained.

PyPI **`remedy-ai==0.26.0`** · tag **v0.26.0**.

## 0.25.1 - Jail, Stop, and tab isolation

Project-bound shell dests now catch Windows root-relative paths and launched
script bodies. Local/RMB turns no longer flip Ask to Auto on disk. Start RMB
does not steal the chat provider; Stop stays stopped across API recycle.
Desktop Stop posts abort before killing the stream. Tabs no longer paint the
wrong transcript or double-send a promoted queue item.

PyPI **`remedy-ai==0.25.1`** · tag **v0.25.1**.

## 0.25.0 - Stable tabs + Settings chrome

Tabs stay isolated: drafts, attachments, RMB reloads, and computer-use jobs
no longer leak across chats. Settings Simple / Advanced is a single tab
track with quieter search and switch-style toggles. Stop aborts web fetch
between hops and ConPTY no longer leaks ReadFile workers.

PyPI **`remedy-ai==0.25.0`** · tag **v0.25.0**.

## 0.24.0 - Host Bridge + first home

Remedy learns **this PC** instead of one vendor or one shell. Host Bridge
rewrites POSIX to cmd, runs PowerShell via `-File`, and exposes
`host_run` / `host_mkdir` / `host_which` / `host_script`. **`/stretch`**
(alias `/home`) maps hardware, PATH tools, rooms, and local ports;
**`/whoami`** includes that census. GPU probe is vendor-neutral (NVIDIA /
AMD / Intel); RMB autofit uses VRAM.

Work turns stay armed until tools actually run. Settings no longer steal
the active chat’s model. Shell jail closes `C:/` dests and `$HOME`
redirects; `/api/files` no longer lists `SAM` / `win.ini` / `hosts` as
empty success. Packaged self-inject defaults **off**.

PyPI **`remedy-ai==0.24.0`** · tag **v0.24.0**.

## 0.23.2 - Defender Execution false positive

The desktop UI is **`Remedy Desktop.exe`**, not generic `app.exe`. Stops
Windows Defender ML (`Behavior:Win32/Execution.A!ml`) from treating first
launch as an attacker payload. PyPI **`remedy-ai==0.23.2`** · tag **v0.23.2**.

## 0.23.1 - First-turn agency

Short task kicks like **full bugsweep** now keep tools on and run recovered
DeepSeek XML dumps instead of leaving `tool_c` in the chat. PyPI
**`remedy-ai==0.23.1`** · tag **v0.23.1**.

## 0.23.0 - Build ability + companion

A Claude-class **machine build loop** plus a PC companion so Remedy can
finish software and design work from *this* machine — not just chat about
it. PyPI **`remedy-ai==0.23.0`** · tag **v0.23.0**.

- **`build_drive` / `build_parallel` / `apply_patch`** — spec → TDD → isolated
  hops → gate tower → review-fix. Overlays merge only when the oracle is
  green. `file_glob` and `todo_write`/`todo_read` cut list-dir thrash.
- **Companion** — clipboard, focused window, drop-a-file inbox, taste
  memory, and visual observe after UI writes. “Look at this / I copied”
  starts from the actual PC.
- **Play-to-iterate** — compile/run stay on the desktop after `computer_app`;
  write jail no longer mistakes `python.exe game.py` for a write.
- **Ship harden** — patch/hop jail fail-closed; uninstall targets only
  `remedy-ai`; messenger shares the desktop stream claim; Stop drains the
  next queued send; Browser rail blocks IMDS / public IPs.

## 0.22.3 - Clear mid-turn stops

Disconnects, Stop, and stream errors leave a **real chat message** (not a
vanishing status banner). Recovery after a dead Sleev proxy stays on the real
provider. PyPI **`remedy-ai==0.22.3`** · tag **v0.22.3**.

## 0.22.2 - Sleev fail-open

If the Sleev proxy is down (or a remote gateway is unreachable), Remedy **falls
open** to your normal provider instead of looping on “waiting for local model.”
PyPI **`remedy-ai==0.22.2`** · tag **v0.22.2**.

## 0.22.1 - Sleev + gateway lock

Optional **Sleev** token-compression gateway for long cloud sessions, with a
hard **loopback lock** so provider keys cannot be redirected off-machine without
an explicit owner opt-in. Full detail: `CHANGELOG.md` · PyPI **`remedy-ai==0.22.1`**
· GitHub tag **v0.22.1**.

- **Sleev routing** — Settings → Provider (or *“configure Sleev”* in chat); Ollama /
  RMB / Demo stay direct.
- **Loopback by default** — non-local gateway URLs need **Allow non-loopback Sleev
  gateway** (Advanced).
- **Theme menu stacking** — theme picker no longer paints under Streaming/Stop.
- **Soak scripts** — local API token loads through DPAPI decode (no sealed JSON
  in `Authorization` headers).

## 0.22.0 - Living organism + builder continuity

One continuous partner that *stays* mid-work — Soul Field on by default, organism
pulse, continuity steering, and stricter done/verify. Full detail: `CHANGELOG.md` ·
PyPI **`remedy-ai==0.22.0`** · GitHub tag **v0.22.0**.

- **Soul Field default on** — bond, open threads, organism mood on the status bar;
  Settings → Identity to opt out.
- **Organism pulse** — mood + forge (builder) + immune (false-done) + EU/DU on each
  real work turn.
- **Continuity steering** — open tasks and mid-ship resume inject so models do not
  monologue past unfinished work.
- **Multi-tab + messengers** — safer concurrent streams; remote chats get the same
  post-turn continuity as desktop.
- **Retention defaults** — sessions 180 days, attachments 90 days (set 0 to keep forever).
- **Ship path** — after green verify: `git_push` / `gh_release` tools; no re-pytest
  thrash on continue; Stop unblocks stuck 409 streams.
- **RMB** — does **not** auto-start with `remedy serve` (start from Settings when
  you want local muscle).

## 0.21.1 - Continuity + self-inject safety

One continuous partner across tabs and providers — stronger isolation and safer
self-improvement. Full detail: `CHANGELOG.md` · PyPI **`remedy-ai==0.21.1`** ·
GitHub tag **v0.21.1**.

- **Soul Field (experimental)** — providers are muscle; local soul carries
  identity, relationship residue, and open episodes so Remedy feels continuous
  across models (`docs/SOUL_FIELD.md`).
- **Turn-local continuity** — Session Brief, Partner State, and work roots freeze
  per stream so multi-tab work cannot cross-wire mid-turn.
- **Self-inject rollback** — restores the pre-round git snapshot (keeps unrelated
  dirty tracked files); only round-created untracked debris is removed.
- **Shell privilege nest** — `bash -c` / `pwsh -Command` payloads hard-block
  `reg` / `net user` / `schtasks /create` and kin.
- **Webhooks** — generic `X-Remedy-Webhook-Secret` works with Bearer middleware;
  Google Chat challenge skip is verification-shaped only.

## 0.20.0 - Partner metabolism + always-ready

The “so” leap: a **Partner Metabolism OS** under one voice, plus desktop chrome that
stays ready on this PC. Full detail: [19-metabolism](19-metabolism) · `CHANGELOG.md` ·
PyPI **`remedy-ai==0.20.0`** · GitHub tag **v0.20.0**.

- **Turn tiers L0–L3** — L0 instant local answers (model / skills / version / whoami);
  L1 lean chat; L2 full tools (review, implement, files, shell, computer); L3 deep /
  work-alone + force-spread.
- **Evidence + decisions** — tool facts get IDs; waste scoring; mid-turn delta inject.
- **Shadow + write jail** — high-blast dry-run on top of project write roots; shell
  cannot mutate `~/.remedy/auth` even under home scope; global package installs outside
  write roots are blocked.
- **Agency that actually runs tools** — “review project” keeps tools on; if the model
  only *says* “activating skill”, Remedy re-arms tools and demands real function calls.
- **Skills** — progressive disclosure; review injects change-safety procedure when useful;
  CLI `skill list` hides noisy auto-learned probation packs unless `--all`.
- **Desktop always-ready** — **✕ / Alt+F4 always hides to the system tray** (local API
  stays up). Full stop only from tray **Quit**. Multi-tab stream paint, abort UX, and
  session-scoped partner status.
- **Privacy mode (opt-in)** — status bar + Simple settings; redacts secret-shaped content
  on the provider path when on; **zero cost when off**.
- **Browser rail** — video **fullscreen stays inside the rail** (not full-app); Mobile /
  Desktop site toggle; same-window OAuth for major IdPs; chat attachment / Comfy images
  load with Bearer media auth; double-click chat links open in-rail.
- **Computer use** — host jobs require Bearer; shot TTL; multi-tab cancel; open_app
  protocol/UNC harden; host reliability (snapshot / page_text / click).
- **Security** — web_fetch SSRF (userinfo, CGNAT, redirects), Teams JWKS RS256, DPAPI
  local API token, identity export HMAC + rate limits, MCP residual purge, plugin
  path-required load.
- **Docs** — owner’s manual + README: About + What’s new near the top; architecture
  diagram in F1 overview.

## 0.19.0 - Parallel multi-provider + background turns

- **True parallel multi-provider:** Grok and DeepSeek (or any pair) can stream at the
  same time on one runtime — each turn freezes provider/model/key in a per-turn binding.
- **Background turns:** switch sessions without aborting live work; sidebar busy pulse;
  confirm before a 3rd concurrent turn.
- **Session sticky bind:** each chat keeps its provider+model pair (no more cross-tab
  404s sending Grok’s model to DeepSeek’s host).
- **Sidebar:** ↑↓ reorder for projects and sessions; clearer **Archive**; no false
  “drag onto folders” claims (Tauri drag is unreliable).
- **Agency:** mid-task `ok` / status-only lines keep tools on and re-nudge instead of
  stopping after a short snippet; DSML idle/stall recovery improved.
- **Attach:** image/file attach works again (WebView drops + paperclip picker).
- **Themes:** **Dark Forest** (muted moss on dark); calmer text on colored themes.
- **Memory panel** shows recent notes without a search query; control tokens no longer
  leak into chat (`@@status:…`).

## 0.18.6 - OS chrome + browser embed

- **Window min / max / close** use the real Windows title bar (no more dead WebView buttons after move).
- **Built-in Browser** auto-loads the homepage and recovers from blank embeds more reliably.
- Note: **✕ always → tray** is a **0.20.0** product rule (earlier builds could still full-quit on close depending on prefs).

## 0.18.5 - Telegram poll lock recovery

- **Messenger stays live after restarts:** a dead process can no longer “own” the Telegram poll forever on Windows.
- **Auto-recover** if a second instance or crash left the bot lock behind (heartbeat + retry).

## 0.18.4 - Messenger realtime + sync

- **Telegram realtime:** only one Remedy process may long-poll the bot (stops HTTP 409 “another poller” thrash).
- **No catch-up flood** on restart — update offset is saved; first run drains backlog without replaying into chat.
- **Desktop replies reach Telegram** when you chat in a messenger session (was inbound-only).
- **Smoother live sync** while a reply is streaming (no force full-thread reload mid-turn).
- **Concurrent sessions:** safer provider/model bind across tabs and messengers.

## 0.18.3 - Provider switch + stability

- **Status bar provider/model switch** sticks for the session (no more DeepSeek API + Grok model name mismatch).
- **Missing model / HTTP 404** stops cleanly with “switch model” (no soft-retry spam).
- **Quit warning “Don’t show again”** is saved before exit.
- **Update check on launch** after the local server is ready.
- **Fewer Windows cmd flashes** during search/spread/git tool work.

## 0.18.2 - Spread run fix

- **`spread_run` no longer fails** when the model passes `tasks` as a native list (common with tool calling). Process trail showed a red **Spread Run** error; that path is fixed.
- `tasks` accepts a JSON array, a single task object, or a JSON string; `goal=` still auto-plans workers.

## 0.18.1 - Run until finished + title bar

- **Long coding / missions keep going** until the work is done — soft “epochs” only compact context and checkpoint; they do **not** stop tools with a fake tool-limit answer (Build-class agency).
- Pathological loops still have a high safety ceiling; idle pauses only after long stretches with **no** tool activity.
- **Title bar:** min / max / close stay clickable after you move, minimize, or maximize the window (explicit drag on the middle strip; controls never steal-hit as drag). Always-hide-to-tray on ✕ is **0.20.0**.

## 0.18.0 - Spread + Library suggest

- **`spread_run`:** silent parallel explore/search/verify workers so multi-area tasks cover ground faster — still one Remedy voice.
- **Library skill check:** on real work, a soft tip when a signed Library pack would help; **Install** from the chip (or open Skills); never auto-installs without a click.
- **Hardening:** tighter path jail and shell approvals on jobs; Stop kills in-flight shell trees; chat hot path stays free of blocking local-model waits.

## 0.17.0 - Coding agency + Process trail

- **Code search** works for any text language (not just Python/JS) — optional bundled ripgrep; no need to install tools for basic discovery.
- Work on **any folder path** without forcing a project jail; focus folder is optional convenience.
- Stronger **Build** tools: longer shell timeouts, multi-file edits, explore/verify jobs, mission verify before “done”.
- **Process** Min / Med / Full is readable on long tool runs (no double chip clouds; grouped steps on Min/Med; full dumps on Full).

## 0.16.0 - Messengers + polish

- **Settings → Messengers:** connect Telegram (live) and modular Discord / Slack / Mattermost / Matrix / WhatsApp / Teams / Google Chat / Signal adapters — tokens stay in the secret store.
- Messenger threads show up as normal sessions in the desktop; history and live updates stay in sync.
- Skills Library refresh is smoother; empty chat shows a clean monogram; Memory Progress is calmer.
- Owner docs showcase workspace tools, local SmolVLM2, and messengers; download link always means **latest**.
- WebUI and desktop share one SPA; rebuild + restart picks up UI changes correctly.

## 0.15.9 - Skills Library visibility + first-session fix

- **Installed | Library** tabs fixed under the Skills title (not clipped by chrome).
- **Memory → Progress**: calmer checkpoint wording (not raw scare-logs).
- Empty chat monogram (no plate bubble); WebUI uses same SPA as desktop.
- First message after boot waits for sessions/model; re-bootstrap token on 401 after update.
- Messengers + session history SQL fix for long / messenger chats.

## 0.15.8 - Skills Library

- **Skills → Library:** browse the signed community catalog, install into quarantine, then **Trust**.
- **Installed** panel cleaned up: Trust / Promote / Quarantine / Archive / Edit / Delete without the old control clutter.
- Library installs are checksummed and path-safe; delete removes user packs under `~/.remedy/skills/`.

## 0.15.7 - Memory Harness v2

- Long chats stay sharp: Auto harness **enforces** a lean model send-view (your full transcript is still saved).
- **Session Brief** keeps intent, decisions *why*, files, and a history thread; local model can refresh the brief in the background.
- Process trail is **Min / Med / Full** (Full+ removed). Plan mode is per chat session.
- Browser only on one rail at a time (stable embed).

## 0.15.6 - Images in chat for every model

- Drag/drop or paste an image → it **shows in the chat bubble** (markdown preview), not only a file path.
- Works with any chat model; vision understanding still uses the provider or local visual decoder when available.
- Stream finish is smoother (no empty flash before the reply lands).

## 0.15.5 - Popout exit, embed browser, homepage

- **Fullscreen** (Terminal / Browser / Scratch): top bar **Exit fullscreen** + **Close**, or **Esc**.
- **Browser:** stays embedded in the panel; default homepage is the **Remedy GitHub** repo (change under **Settings → Project workspace → Browser homepage**).
- Quit and window chrome reliability improvements from the 0.15.x shell work.

## 0.15.4 - Chrome, chat, rails, browser

- **Title bar:** minimize / maximize / close work again (drag strip no longer steals clicks; close hides to tray).
- **Chat:** prompt stays at the **bottom**; empty-session landing page restored; session list clicks always load history.
- **Sessions:** open-tab chip strip removed; **Browse…** for project folders uses the native picker on the UI thread.
- **Browser:** embedded panel (iframe), not a blank popup window.
- **Terminal:** bright blinking block cursor; click to focus.
- **Rails:** thin strip → icons → open panel (both sides).
- **Usage ticker** above the composer; **About** includes Ahmi’s note.

## 0.15.3 - Shell + in-app tools

- True three-column workspace; in-app PowerShell and browser; original shell icons; path images.
- **New Session = root** (no project). New Project folder is first-run only, not every session.

## 0.15.2 - Workspace harden

- Safer workspace prefs (bad slide ids no longer crash).
- Plan stays visible after Approve -> Build until you Hide it.
- Browser URL hardening; scratch pad writes debounced.

## 0.15.1 - Workspace polish

- PowerShell terminal, Firefox browser open, archive fix, quieter plan banner.

## 0.15.0 - Workspace / plan mode / images

- Three-frame slides, image markup attach, session archive, Plan approve banner.

## 0.14.10 - Image viewer + markup

- Full-screen viewer for any chat image.
- Snipping-Tool-style markup; attach annotated PNG to your next prompt.

## 0.14.9 - Icons + faster export/import

- Theme-aware alpha chat monogram; bold tray plate; clearer taskbar icon.
- Native save/open dialogs; smaller/faster session export (tool dumps capped).

## 0.14.8 - Project etiquette (ship skill)

- Bundled **`project-etiquette`** skill: test -> docs -> build -> commit -> CI -> publish only if green.
- Same gate chain is default ship protocol in `AGENTS.md` (works for any serious project).

## 0.14.7 - Calmer update + install always starts

- One clear update message (download, then restart to finish).
- App waits until the install script is alive before closing.
- Multi-path install schedule from 0.14.6 kept (PowerShell + WScript + schtasks).

## 0.14.6 - Autoupdate install reliability + alpha logos

- Multi-path install schedule so install runs after close.
- Full alpha brand kit regenerated for public/ + Tauri icons.
