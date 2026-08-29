# Changelog

All notable changes to Remedy (`remedy-ai`) are documented here.

## [Unreleased]

### Fixed — peek is not the desktop poller; fail closed on secret/approval gaps

- SPA ``ui/command`` peek no longer marks the computer host connected. Only
  ``jobs/next`` and ``take=1`` do. Auto-learn omits text if the secret
  detector throws. Analysis tools refuse when the approval gate cannot
  load. Docker image inspect/pull is bounded. Host sessions no longer
  inherit ``GIT_ASKPASS``. Click success matches ``ok`` / ``ok:`` / ``ok-``.

### Fixed — one hidden git helper; soul uses the durable secret guard

- Draft/guard/PR git calls share ``run_unattended_git``. Ruff drafts use
  ``python -m ruff``. Soul secrets use ``looks_like_secret``. Dead SPA
  job-claim helpers are gone.

### Fixed — ``cd`` / ``mkdir -p`` / ``git add &&`` no longer flash a CMD

- ``CREATE_NO_WINDOW`` hid ``cmd.exe`` but not its children. Plain ``A && B``,
  ``cd src && pytest``, and ``mkdir -p out && gcc`` now run as hidden hops
  (cd/mkdir in-process). Git/gh children get a no-prompt env and closed
  stdin so a credential GUI cannot hang the turn. ``cmd /c start`` for
  open-app hides the wrapper console.

### Fixed — one computer-host poller, not two

- The desktop SPA no longer claims ``/api/computer/jobs/next``. Rust drives
  every rail job (click, type, snapshot, screenshot). Hello + rail-open
  peek stay in the SPA. Dual claim was racing snapshots and double-hitting
  the API on every coding turn.

### Fixed — auto-learn skills no longer mint tool-name titles or store secrets

- Learned skill titles prefer the owner's sentence (paths stripped). Tool
  names are never concatenated into the id. Vault tokens, ``api_key=`` /
  Bearer blobs, and provider key shapes are redacted or omitted so they
  cannot land in the skill catalog.

### Fixed — unattended git/gh never prompts and never inherits LLM keys

- Self-inject, ship, and issue-submit spawn git/gh with a scrubbed env:
  no ``GIT_ASKPASS`` GUI, ``GIT_TERMINAL_PROMPT=0``, and no XAI/OpenAI
  keys in the child. Hidden CREATE_NO_WINDOW on the remaining pytest /
  tesseract / Signal / gate-tower sites so those children do not flash
  a CMD either.

### Fixed — life-task and approval polls are not SLOW-spam

- ``GET /api/life-tasks/current`` and ``GET /api/approvals`` skip the
  500ms SLOW warning. A fat coding turn used to fill debug.log with
  those two hot polls.

### Fixed — no CMD flash for ``uv run pytest`` on Windows desktop

- The packaged app has no console. ``uv run`` hid itself then spawned
  python without the hide flag, so a CMD blinked on every test. Remedy
  now runs the project interpreter as ``python -m pytest`` (and the same
  for ruff/mypy). Background servers stay hidden; games still get a
  window. The life-task card polls slowly when idle so it does not fight
  a coding turn.

## [0.41.7] - 2026-08-29

Life-task owner card (Yes / No / Explain, Step N of M), recipes without
model JSON, resume after password/CAPTCHA, What Remedy did, verify after
casual writes, UIA Invoke/Toggle before click-at-center, and voice on
the same card. Pay / send / delete still cannot be waived.

### Added — voice Yes / No / Explain on the life-task card

- The card's spoken sentence is read aloud when Speak replies is on.
  Saying or typing Yes, No, or Explain acts on the card without a model
  round.

### Added — UIA Invoke/Toggle before click-at-center

- Desktop clicks try the control's Invoke or Toggle pattern first. Pixel
  click-at-center is the fallback when the pattern is missing.

### Added — verify actually runs after casual writes

- After ``file_write`` / ``file_edit`` with no build engine, Remedy runs
  ``job_run kind=verify`` itself. Ask still gates the shell; a missing
  suite is not treated as green.

### Added — What Remedy did (life-task review)

- Each drive step stores intended vs observed plus a short hash, and the
  last screenshot path when the rail saved one. The owner card has
  **What Remedy did**.

### Added — resume after password / 2FA / CAPTCHA

- When a life-task stops for sign-in or a human-check wall, the Browser
  rail stays interactive. After the wall URL/text is gone, remaining
  steps continue. Pay / send / delete never auto-resume.

### Added — life-task recipes (no model JSON)

- ``life_drive`` accepts ``recipe=open|search|shop|fill|sign_in`` plus
  ``url`` / ``query`` / ``vault`` handles. A goal like "buy milk on
  instacart" expands on this PC. Place order / sign-in / submit stay
  checkpoints.

### Added — owner card for a life-task drive

- Desktop renders the plan as one spoken sentence, Yes / No / Explain, and
  live ``Step N of M``. Pay / password / CAPTCHA still stop; Yes on that
  stop means you handled it — Remedy does not press it.
- ``GET /api/life-tasks/current`` + ``POST /api/life-tasks/act`` drive the
  card. Stream ``@@life_task`` updates the same surface.

### Added — native snapshot steers UIA first, screenshot last

- Desktop ``computer_snapshot`` reports how many window vs control refs it
  found and tells the model to click ``cN`` / names first. When UI Automation
  is empty it names screenshot/OCR as the fallback and forbids guessed x/y.

### Added — local / RMB operate pack can verify and recall

- Local (RMB / llama.cpp / Ollama) live rounds keep 16 schemas, including
  ``job_run``, ``bash_exec``, and ``memory_search``, instead of 8 that could
  only edit. Still far below the 194-schema dump.

### Added — on-PC code map and verify-after-writes

- ``code_map`` lists class/def/fn symbols under a tree (time-budgeted, same
  skip-dirs as file_glob). Tight operate pack is 40.
- After a write batch with no active build engine, Remedy injects the
  project's verify command so the model runs it instead of claiming green.

### Added — speakable Yes-No-Explain for life tasks

- Plan prompts, checkpoints, and blocked steps are one spoken sentence plus
  Yes / No / Explain. Tool names never appear in that sentence.

### Added — one yes for a life-task plan

- ``life_drive`` asks once for the whole plan in Ask mode ("Remedy will: … then
  stop for you at Place order"). Auto/Full skip that prompt. Pay / send /
  password / CAPTCHA still stop after the yes — no mode can waive them.

### Added — life-task evidence and resume

- Each ``life_drive`` writes a durable trail under ``~/.remedy/life_tasks/``
  (intended vs observed). ``life_drive(task_id=…)`` continues from the first
  unfinished step; pay/password checkpoints still stop. ``life_task_status``
  lists the evidence in plain language.

### Added — life-task drive: act, verify, retry, escalate

- ``life_drive`` runs a computer-use plan on this PC the way ``build_drive``
  runs code: each step is observed, one retry with a fresh snapshot, then a
  plain-language stop. Pay / send / password / CAPTCHA steps never auto-run.
  A tool returning ok is not a finished goal.

### Changed — git and explore jobs keep chrome live

- ``git_status`` and silent ``job_run kind=diff`` run their git probes in
  parallel instead of one after another.
- Explore listing + stack fingerprint, and verify's fingerprint lookup, run
  in a worker so a fat folder cannot stall SSE.

### Added — in-app git / reminder / clock answers skip the provider

- High-confidence ``git status`` / ``git diff`` / ``git log``, reminder list,
  and ``remind me in 30m to …`` run the local tool and reply without a
  Claude / GPT / DeepSeek / Gemini / OpenRouter / xAI round. Mutating git
  (commit, push, …) and multi-step asks still go to the model.
- ``what time is it`` / ``what's the date`` is an L0 clock on this PC.
- Same in-app skip for ``show todos``, ``list files`` (cwd only),
  ``run the tests``, clipboard, ``which python`` / ``where is git``, and
  ``list my goals``. ``pwd`` / project path is L0 on this PC. A path like
  ``list files in src`` still goes to the model.
- ``ship status``, ``mission status``, and ``what's on my screen`` run the
  local tools (no provider). Bare ``look at this`` / ``what's the mission``
  still go to the model.
- ``rmb status`` / ``list local ggufs`` and ``what windows are open`` run
  local status/list only (``rmb action=status|models``,
  ``computer_windows mode=list``). Start/stop RMB and close/focus windows
  still go to the model.
- Vault handles, F1 help list/read, hive/soul status, reload/search skills,
  ``remember that …`` / ``what do you remember about …``, mail connected,
  screenshot, and list monitors also skip the provider. ``remember to …``
  (a task) still goes to the model.

### Added — git_log on every live round; file I/O and usage SQLite off the loop

- ``git_log`` (read-only, no approval) stays in the operate core so Claude /
  GPT / DeepSeek / Gemini / OpenRouter / xAI can read history the way they
  already ``git_diff``. Tight pack is 39; other clouds keep 64.
- ``file_read`` / ``file_write`` / ``file_edit`` / ``file_edit_batch`` do
  disk work in a worker. A fat source file no longer freezes chrome or SSE.
- Usage summary / series / session routes and mid-turn ledger writes run
  off the event loop. Analysis artifact scans stop after 8s.

### Added — git_diff on every live round; glob walks stop; remaining SQLite off the loop

- ``git_diff`` (read-only, no approval) stays in the operate core so Claude /
  GPT / DeepSeek / Gemini / OpenRouter / xAI can review the patch before
  ``git_push``. Tight pack is 38; other clouds keep 64.
- ``file_glob`` walks stop after 8s and skip huge-root OS trees (AppData,
  Windows, …). A home-sized glob cannot occupy a worker until ``os.walk``
  finishes.
- User-profile save/load/facts, handoffs, and session summaries run off the
  event loop. Distill during a turn no longer freezes chrome.
- ``list_dir`` iterates the filesystem in a worker so a fat folder cannot
  stall SSE.

### Added — verify jobs on every live round; remaining chrome SQLite off the loop

- ``job_run`` stays in the operate core so Claude / GPT / DeepSeek / Gemini /
  OpenRouter / xAI can verify in the background the way they edit and click.
  Tight pack is 36; other clouds keep 64.
- Session delete, clear, revert, and bulk memory writes run off the event
  loop. Usage ledger sets ``busy_timeout`` like the other SQLite homes.

### Added — recall on every provider's live round; FTS off the loop

- ``memory_search`` and ``soul_recall`` stay in the operate core so Claude,
  GPT, DeepSeek, Gemini, OpenRouter, and xAI can look up partner memory
  while they code or click. Tight pack (xAI window) plus ``job_run``;
  other clouds keep 64. Catalog is still uncapped.
- Memory FTS / LIKE / upsert run in a worker thread. Speculative prep
  calls ``search_sync`` instead of ``asyncio.run`` on a background thread.
- CAS objects.db sets ``busy_timeout`` like memory.db.

### Changed — drop tests that skip on public CI or patch APIs that do not exist

- Update-check no longer has a skip-if-404 probe that patched a missing
  ``check_for_updates``. Poll-cache and the trusted GitHub URL family remain.
- Duplicate ``hive_caps`` "if importable" case removed (covered in
  ``test_hive_caps``). Gitignored prompt/pipeline "file exists" checks dropped.

### Fixed — tests match partner-system steering; list tool ids pair; versions align

- Partner injects are ``role=system`` so the model does not treat them as the
  owner talking. Loop tests now look at those injects, not only ``role=user``.
- ``ensure_tool_call_pairings`` coerces a list-shaped ``tool_call_id`` the same
  way ``normalize_tool_calls`` coerces the assistant id — a ``["call_a"]``
  tool result pairs instead of 400ing.
- First-run demo slot tests ignore live ``POE_API_KEY`` / sibling env keys.
- ``scripts/latest.json`` matches tree version 0.41.6. Voice status is a
  poller (no SLOW warn on 200). Stop mid-stream is abort, not a disconnect
  retry, when the abort flag is set.

### Fixed — vault fill is one owner yes; hive facts stay in the hive

- Approving a vault type / Place-order click once is enough. Authorize and
  the computer handler used different fingerprints, so Stop-and-retry asked
  twice. The inner gate now treats the authorize-side owner moment as spent;
  live-page payment / raw-PAN / CAPTCHA checkpoints still ask on their own.
- Vault tokens stay in the policy command as handles (`vault=card-visa`);
  typed passwords are a character count, not the value.
- `cas.fetch_hot` no longer hydrates `hive_*` fact/life objects into every
  session. A daughter's own slice still loads when her session is open.
- Authed `GET /api/status` COUNTs (and memory list/get) run off the event
  loop so Settings polls do not stall a turn.
- `computer_hover` (and key / scroll / drag / press-hold) open the browser
  rail the way click/type already did.

### Fixed — chrome stays live during chat I/O; tool rounds show work

- Session list / get / add-message SQLite runs in a worker thread so a fat
  turn does not freeze Settings, jobs/next, or SSE on any provider.
- JSON tool rounds (Grok, local RMB) emit ``Working…`` immediately, then
  thinking — the bar is not blank until the whole body arrives. SSE clouds
  already stream reasoning while buffering tool JSON.
- ``events.db`` uses WAL + busy_timeout like memory.db.

### Added — richer operate pack for non-Grok clouds; jobs wake on enqueue

- Tool-schema cap is per provider: local 8, Grok/xAI 32, Claude / GPT /
  DeepSeek / Gemini / OpenRouter-non-Grok 64. Press-hold, drag, screenshot,
  page_text, app, find stay on those live rounds. Grok still fits the window.
- `GET /computer/jobs/next?wait_ms=` blocks until a job is enqueued (capped
  5s), off the event loop. SPA and Rust idle long-poll instead of sleeping
  2s, so the first click after quiet is immediate on every provider.
- Hive `goal_add` still makes a daughter task; it no longer writes the
  owner's life board or soul dreams.

### Added — hover, and the Grok operate pack keeps full hands

- `computer_hover` moves the pointer onto a control (`text=` / `ref=`)
  without clicking — menus, tooltips, CSS `:hover` — same locator family as
  click. Trusted CDP/GDK move on the rail; native `move_mouse` on desktop
  (Windows + Linux).
- Cloud work-pack cap (32) now keeps `apply_patch`, `computer_scroll`,
  `computer_wait`, `computer_select`, and `computer_hover` alongside click /
  type / fill. A long Grok turn can still code *and* drive the PC the way
  Operator / Claude CUA do, without 194-schema prompt bloat.
- Writes invalidate `file_glob` cache the same way they invalidate
  `repo_search` / `file_read`.

### Fixed — stale Stop cannot kill a newer turn

- `POST /abort` accepts `epoch=` from stream `event: start`. A late Stop
  with an old generation is ignored. Desktop Stop always sends the live
  job's epoch. 409 recovery no longer abort-supersedes (join or retry
  POST). CLI / delete still omit epoch and abort whatever is current.

### Fixed — 409 recovery no longer kills a live turn

- When chrome thought the session was idle (dropped SSE) and the server
  still held the claim, a failed `/steer` used to abort with supersede.
  `nudge_full` and a steer blip now retry join and keep her hands on the
  page. Abort-and-resend only when `/steer` says `no_turn`.

### Fixed — vault type cannot use a routing token as a field

- `query=browser` / `label=auto` are drive targets, not field names. They
  no longer unlock vault type into whatever is focused. `computer_act` vault
  type also needs a named click/label. Ordinary type-into-focus is unchanged.

### Fixed — failed steer waits in line

- Enter while she works still steers the live turn. If `/steer` cannot land
  (nudge cap, a 4xx/5xx, a dropped request), the words **queue after** —
  they no longer interrupt. Ctrl+Enter still stops and sends now. Mid-turn
  queue holds 24 remarks (was 8); a full queue names `nudge_full` so chrome
  can wait instead of taking her hands off a click or payment.

### Fixed — desktop type locates the field by visible label

- `computer_type(query="What's happening?")` already relocated in the
  in-app rail. On native desktop it ignored the label and typed into
  whatever had focus. Desktop now resolves `query=` / `label=` (and a
  stale `ref=` plus query) via last snapshot / UIA then OCR, focuses that
  field, then types. Bare type without a locator still uses the focused
  control. Vault secrets may use a resolved label, not only `ref=`.

### Fixed — goal evidence joins list args

- `goal_complete` / `goal_verify` still did `(evidence or "").strip()` so
  `evidence=["tests green"]` died. Wrapper now joins via `coerce_text_arg`;
  empty after coerce still refuses on verify (no invented evidence).

### Fixed — companion_taste joins list fact

- `companion_taste` still did `(fact or "").strip()` / `fact.strip()` so a
  model sending `fact=["soft spacing"]` died before `remember_taste` could
  coerce. Wrapper now joins via `coerce_text_arg`; empty after coerce still
  shows existing taste (no invented fact). `clipboard_write` text and
  `companion_design` goal join the same way.

### Fixed — goal_add and memory tools join list args

- `goal_add(title=["Ship it"])` died with `'list' object has no attribute
  'strip'` before `LifeGoalStore` could coerce. Same family in
  `goal_set_next` title/action, `subgoal_open` title, `memory_search`
  query, and `memory_save` content/title. Wrappers now join via
  `coerce_text_arg`; empty after coerce still refuses like an empty
  string (no invented titles).

### Fixed — plan create joins list title/goal

- `PlanStore.create` still did `(title or "Untitled plan").strip()` so a model
  sending `title=["Ship it"]` died with `'list' object has no attribute
  'strip'` (same class as `todo_write` / `mission_start`). Title and goal now
  join via `coerce_text_arg`; empty after coerce still falls back to
  "Untitled plan". `drive_build` / the `build_drive` tool do the same for
  `goal=`. `plan_save` stores real prose, not a Python list repr.

### Fixed — computer_scroll locates the pane by visible text

- Guidance already said `computer_scroll` is addressed by **label** (text/ref)
  like click / press_hold / drag — but the tool only accepted x/y/dy, so a
  named pane or list always fell through to foreground-window center (often
  the wrong surface). Desktop now resolves `text=` / `ref=` via last snapshot
  / UIA then OCR (offscreen controls scroll-into-view first), then wheels at
  that point. Bare coords and dy still work. Browser rail resolves labels to
  coords before enqueue (no desktop rebuild). `approach_of` records a real
  text/ref/coords approach for scroll.

### Fixed - computer_drag locates endpoints by visible text

- Guidance already said `computer_drag` is addressed by **label** (text/ref)
  like click / press_hold, and `approach_of` learned a text approach — but the
  tool only accepted x/y/x2/y2, so a slider or kanban move by visible names
  always forced a screenshot. Desktop now resolves `from_text=` / `to_text=`
  (and `from_ref=` / `to_ref=`, plus `text=` / `ref=` aliases for the start)
  via last snapshot / UIA then OCR, same family as `computer_click`. Bare
  coords still work for canvas / pixel targets. Browser rail resolves labels
  to coords before enqueue (no desktop rebuild).

### Fixed — native press-hold locates the control by visible text

- `computer_press_hold(text="Hold to confirm")` already worked in the in-app
  rail (host JS locates by label). On native desktop it ignored the label
  and failed with "needs x/y or a ref", even though the tool schema
  advertises `text=` as the locator. Desktop now resolves the control the
  same way `computer_click` does: last snapshot / UIA tree, then OCR word
  boxes, then press-and-hold at that point. Offscreen controls scroll into
  view first. Bare x/y and snapshot refs still work.

### Fixed — remaining owner/model list.strip crashes

- Same-family `(message or "").strip()` still died when a model or API sent
  a JSON array (`["keep going"]`). Distill, ledger continue, taste, intent
  learn, local harness, open-work, ReAct stream pairing (`tool_call_id`),
  attachments, life goals/drive, L0, and session `req.message` now join via
  `coerce_text_arg`.

### Fixed — ReAct policy list messages no longer crash

- `looks_like_away_request` / `looks_like_companion_request` in the
  preamble still did `(message or "").strip()`; `suppress` hid the
  `'list' object has no attribute 'strip'` crash so the away/companion
  block never injected. Same-family detectors in `react_policy`,
  `build_engine`, `intent_policy`, and `fast_path` now join via
  `coerce_text_arg`.

### Fixed — type/type_text relocates by visible field text

- Host `type` / `type_text` used to write only the snapshot ref or
  `activeElement`. `query=` / `label=` / `hint=` now runs the same
  `__rmdyPick` family (exact-token `__rmdyScore` — `add` still must not
  match `address`) over editable fields and associated `<label>`s, then
  `__rmdyFieldOf` focuses the input. Stale ref + query relocates in one
  pass. Empty ref+query still uses the focused node.
- Desktop rebuild needed before the host JS is live.

### Fixed — browse intent list messages no longer crash

- `parse_browse_navigate_url(["https://mail.google.com"])` died with
  `'list' object has no attribute 'strip'` after `looks_like_url` started
  accepting arrays. Message/url/alias entry points now join via
  `coerce_text_arg` so a list kick still opens the rail.

### Fixed — first live GET /api/models no longer stacks TCP + a second RTT

- After the 5:52pm CT serve restart a Settings tick logged **SLOW GET
  /api/models 780ms** next to the RMB `not listening` probe (170ms). Two
  chrome callers (session + Settings) each ran `asyncio.open_connection`
  against a firewalled 8787; Windows Proactor + dropped SYN froze the loop
  so the second wait landed ~780ms. Local listen is now the same 150ms
  socket precheck as Ollama, single-flight, cached 3s, and runs in a
  worker so the loop stays free. xAI `/models` and `/language-models` start
  together (one RTT, same ids + aliases). Listing is not filtered.

### Fixed — computer click/url list args no longer crash

- Models send JSON arrays for `text`/`url` (`["Sign in"]`). Registry coerce
  is skipped on direct handler/router calls, so `(text or "").strip()` died
  with `'list' object has no attribute 'strip'`. `computer_click`,
  `computer_press_hold`, sibling locators, and `looks_like_url` now join
  arrays via `coerce_text_arg`.

### Fixed — hive pulse no longer steals the owner tab

- A daughter ReAct (`ensure_partner_state`, `compress_context`, session
  brief / work roots) wrote `BasicRuntime`'s process-live continuity even
  inside `begin_turn`. After the mother turn ended, live PartnerState /
  brief / roots stayed `hive_*`, so the next owner action mixed tabs.
  Setters now update only the turn ContextVar while a turn is active.

### Fixed — Matrix and Mattermost dual-poll after serve restart

- Telegram/Discord/Slack held `MessengerPollLock`; Matrix `/sync` and
  Mattermost WebSocket did not. Two serves raced the since cursor (dropped
  or duplicated room events). They now take the same exclusive lock, retry
  every 20s if locked out, and heartbeat while connected.


### Added — Linux computer-use clickable candidates

- `detect_ui_candidates` on Linux no longer returns an empty list. It walks
  AT-SPI for named buttons/fields, then OCR word boxes (tesseract), then the
  same pixel-edge Set-of-Mark fallback Windows already uses, so a marked
  screenshot can reach GTK/Qt apps and games. Tests fake AT-SPI/OCR so no
  live Linux session is required.

### Fixed — chrome connected poll no longer walks Downloads

- `GET /api/providers/connected` was 1473ms after the 5:52pm CT serve restart
  (presence 1232ms in the same tick). RMB GGUF discovery globbed every file
  in the user's Downloads folder (5s cache, pathlib listdir). Dump dirs now
  abort after 50ms and the list is cached 60s, so Settings stays snappy.
  House model dirs still scan fully.

### Fixed — OCR click-text hits the word, not the whole bar

- A merged OCR line (`Reply Retweet Share`) is also scored as each word, so
  click-by-text **Reply** lands on Reply (and **What's happening?** still
  lands on the phrase). Floor 40 matches the Rust host `click_text` scorer.
- Host `__rmdyScore` tokens are exact (`add` no longer hits `address` via
  `includes`). Query substring boosts need 3+ characters.


### Fixed — list/tuple goals and steps are real text

- `mission_start(goal=["Ship it"], steps=["a", "b"])` used to store
  `"['Ship it']"` or die with `'list' object has no attribute 'strip'`
  inside `create_mission` (same Aug 21 errors.log family as todo_write).
  Arrays and tuples now join into prose; nested step titles flatten.
- `todo_write` accepts a tuple of items the same way it accepts a list.
  `apply_patch` / `build_parallel` / `build_tdd` join list args instead
  of calling `.strip()` on a list.

### Fixed — list verify_command actually runs the tests

- `mission_start(verify_command=["pytest -q"])` crashed with `'list' object
  has no attribute 'strip'` (live errors.log Aug 21) or, after registry
  coercion, stored `["pytest -q"]` as the shell command so verify never ran.
  Arrays, argv tokens, and JSON-array strings now become a real command.
  Whitespace `run_auto_verify(command="   ")` falls back to the stored oracle
  instead of reporting oracle_missing.

### Fixed — OCR click-by-text reads on-screen phrases

- Word boxes from Windows.Media.Ocr / tesseract are grouped into labels
  (`What's happening?`, `Add a GIF`, `Sign in`) so a DOM-miss click lands
  on the composer, not a 1-word fragment. Action verbs score as buttons;
  composer placeholders score as textboxes.
- HiDPI rail captures divide by devicePixelRatio when clicking those boxes
  (was hardcoded `scale=1`). A missing last screenshot recaptures the rail
  instead of giving up.
- Press-and-hold-by-text uses the same token scorer as click-text (floor 40,
  stopwords, Post-is-a-button, app-banner downrank) instead of a substring
  match that could not see a textarea.

### Fixed — sandbox Stop no longer leaks a pending abort waiter

- Shell timeout / owner Stop cancelled `_wait_abort` without awaiting it.
  Windows then logged `Task was destroyed but it is pending` (errors.log
  2026-08-27 17:04). Every waiter is now cancelled *and* awaited.

### Fixed — chrome poll DEBUG also covers app/voice/rmb

- `/api/app/command`, `/api/voice/status`, and `/api/rmb/hf/progress` were
  still writing a DEBUG line every few hundred ms next to jobs/next.

### Fixed — voice pack install bootstraps pip

- First-run speaking died with `No module named pip` on uv venvs (no
  `--seed`) and stripped runtimes that ship `ensurepip` but not pip.
  Pack install now bootstraps pip (ensurepip, then uv, then get-pip.py)
  and still finds uv off PATH next to Python, so she gets a real local
  voice instead of falling through to OS speech forever.

### Fixed — Telegram poller comes back after serve restart

- A leftover `telegram_getupdates.lock` whose PID still looked live (Windows
  STILL_ACTIVE / PID reuse) used to refuse the new serve without trying the
  OS exclusive lock, so Telegram stayed silent. Flock/msvcrt is now the source
  of truth: free lock → reclaim; busy lock → stay out (no dual pollers).
- The persisted getUpdates offset is left alone on reclaim, so unread updates
  are not dropped.
- After we own the lock, HTTP 409 retries in ~2s for 90s (takeover of a dying
  previous poller) instead of lock-stepping 25s; lock retry starts at 2s
  instead of waiting 20s.

### Fixed — a dropped Grok stream keeps the turn

- A provider wait that dies as `CancelledError` (WinError 64 / mid-JSON
  RST on xAI) no longer paints **Generation stopped** or dumps **continue**
  into a new job. The same turn retries on a JSON POST, like a
  `ClientPayloadError`. Owner Stop still stops.

### Fixed — chrome polls no longer wait 1.5s

- `GET /api/providers/connected` was probing Ollama with a 1.5s urllib
  timeout on the asyncio loop. Windows often drops the SYN when nothing
  is listening, so Settings, presence, and even CORS OPTIONS sat behind
  that wait. Detect now fail-fast TCP-checks local hosts, caches for 3s,
  and the connected/presence handlers run in a worker thread. Presence
  GET reads the registry without rewriting it.

### Fixed — dual computer-host poll spam

- SPA no longer POSTs `/api/computer/host/hello` on every 120ms `jobs/next`
  tick. Hello is bounds/session only (~4s). `jobs/next` already marks the
  poller (`host_connected`). Idle claim escalates 800ms → 2s after a quiet streak; a real job stays
  at 120ms.
- Rust poller drops its redundant hello (no bounds; `jobs/next` is the
  heartbeat) and sleeps 50ms after work, then 150ms -> 800ms -> 2s when
  idle (matches SPA). Rail stays driveable; capability is unchanged.
- Empty claim_next scans set an idle-empty flag so later host polls skip
  glob+JSON reads until the next enqueue (filtered pollers cannot poison it
  by skipping sibling actions).
- Fast quiet host polls (<100ms, 2xx) no longer write DEBUG access lines into
  debug.log every tick — long Grok turns were filling the 8MB ring.

### Fixed — Linux vision install keeps its .so links

- Official llama.cpp Ubuntu tarballs ship relative versioned library
  symlinks (`libllama.so.0 -> libllama.so.0.0.N`). Extracting them no
  longer fails with "Tar symlink blocked", so local eyes can install
  on Linux first run. Absolute / escaping links stay refused.

### Fixed — first-run actually talks

- A new home no longer looks like Demo in Settings while the turn hits
  OpenAI with dummy key `unused` (401, then quarantine). Guest llm7 is
  used so she can speak before any paid key is set.

### Fixed — leftover jobs don't steal her hands

- Dream no longer turns "Stay with: Continue…" residue into life pledges,
  and soul missions no longer arm those as real jobs. Time Crystal already
  refused them; the muscle that starts work now does too.

### Changed — mid-turn talk steers

- Enter (and Send) while she's working folds into the live turn. No stop,
  no restart, no waiting in line. Ctrl+Enter still interrupts and sends
  now. Alt+Enter still queues after. Attachments still start a new turn
  (they queue after unless you interrupt).

### Fixed — RMB stays off until you start it

- A leftover Settings default of `llm_provider=rmb` no longer starts a
  watchdog, skips SmolVLM, or logs "Local model auto-started" while the
  host on :8787 is closed. RMB runs when you Start it, turn on auto-start,
  or send a turn that actually uses it.

### Fixed — Grok work pack still has hands

- The live operate cap (194 schemas → 32) keeps `computer_snapshot` /
  `click` / `type` / `act` and `web_fetch`, not only `computer_navigate`.
  A proceed-until-finished socials turn can still see the rail.

### Fixed — long desktop sessions keep building

- A follow-up like "ok on to assets" or "needs to work for Firefox and Chrome"
  no longer asks "are we just talking?" and drops the job. Short acks
  (`Good deal`, `sounds good`) still ask.
- Saying **yes** after Remedy's own "want me to add X?" continues the work.
- `ask_first` may peek once, then it must ask or stop — no 18-step
  `file_read` loop.
- xAI/Grok **tool** rounds no longer stream (same as local RMB). Mid-chunk
  TCP resets were killing long grok-4.6 builds; a drop now keeps the rest
  of the turn on a single JSON POST.
- RMB port refused is "local model is off", not eight disconnect waits.
- Partner nudges are system notes, not fake owner messages.
- `web_fetch` ignores in-app browser text unless that tab's URL matches.
- Checkpoint leftover "continue the last tool" is not a durable job.

### Added — on-screen OCR when DOM/UIA is empty

- Computer-use can read word boxes from the screenshot (Windows.Media.Ocr or
  tesseract) and click them by text/ref when the page has no DOM/UIA tree.

## [0.41.5] - 2026-08-26

### Changed — RMB thinking is an option, default on

- RMB no longer turns off Qwen/R1 thinking by default. Thinking is **on**
  unless you set it **Off** in Settings → RMB (or
  `rmb action=settings thinking=off`).
- Host auto-load still detects Jinja, MTP, mmap, and MoE from the GGUF.
  Those knobs are owner settings now — auto-load does not clobber them.

## [0.41.4] - 2026-08-26

### Fixed — local muscle without leftover jobs or a 256-token muzzle

- A new chat no longer inherits the previous tab's "Stay with: Continue…"
  job as a life pledge. Identity pledges still inject.
- "How does a local model feel?" answers in chat. Starting/stopping RMB
  and "how do I fix the about window" still get tools. Debug follow-ups
  on an open build stay armed.
- Local replies with tools stripped use a real answer budget (≥768 on a
  4k window), not the 256 trivia cap that cut the last RMB turn.
- `rmb status` lists house GGUFs, ngl, and MTP. `action=models` (and
  `files` with no repo) is local inventory. Empty `settings` dumps live
  config. Sibling `mtp-<stem>.gguf` auto-arms `--model-draft` so a
  Settings restart keeps MTP. The old `Remedy Muscle Bridge` folder is
  last in the search list.

## [0.41.3] - 2026-08-26

### Added — she knows the house

- The world map (Machine Map / first-home stretch) tracks her organs and
  this PC: `[House] <os> · RAM · GPU · RMB=… · vision=… · vault=n`.
- **`rmb`** drives the local llama.cpp muscle (status / start / stop / use /
  catalog / Hugging Face search+pull / settings). Autofit stays the default.
  Ask mode still checkpoints start and downloads. Skill **rmb-muscle**.
- **`house_status`**, **`computer_apps`**, **`house_walkthrough`**,
  **`house_addition`** are now chat tools (they already existed as code).
  `computer_app` launches from the Start Menu inventory, not a five-name
  alias list. Skill **her-house**.
- `local_discover` probes bundled RMB (`/health` :8787) and SmolVLM
  (`/health` :8740), not an external `run_host.py` product.

## [0.41.2] - 2026-08-26

### Fixed — closed loop: see, remember as one, don't faint

- Navigate no longer says `SUCCESS` before the page is seen (`pending_load`,
  `observed: false`). App/folder launch reports the foreground title or
  honestly "I don't see a window yet." The rail still opens instantly.
- Gate-tower skip-pass (no tests / no interpreter) stays `ok` so hops
  continue, but `verified` is false — Plan verify is not marked done.
- Messenger poll/WS enqueue inbound instead of waiting out a full ReAct
  turn. Companion inbox/drops inject only when the turn looks like a
  companion ask (tools still gather on demand).
- Retrieval drops inferred hits first under a char budget; working-memory
  inject is labeled not-a-grant. Long sessions consolidate notes after
  eight entries. `events.db` is pruned with retention (14 days).

## [0.41.1] - 2026-08-26

### Fixed — owner checkpoints and keys actually fire on the wired path

- Vault / click text now reach PolicyEngine; inner `needs_ask` still stops
  `{{vault:…}}` after a generic allow. Browser type requires snapshot `ref=`
  the same way desktop already did. Ordinary typing in Auto is unchanged.
- `GET /api/models` will not send a stored key to a caller-supplied host
  (same family as the provider probe). `remedy setup` writes keys into the
  secret store instead of scrubbing them into nowhere.
- Chatterbox HQ speak and first-run pack install honor `tts_quality`; Kokoro
  stays the default voice. HQ is still there when you turn it on.
- `self_inject_round` is owner-locked, runs the two-pass guard, and rolls
  back only the round's write set. The tool still applies on green after you
  approve.
- Messenger inbound no longer overwrites the session's provider from
  Settings' previous provider. Work-signal arming no longer ORs the loose
  "make me" / long-paste detector (that detector still packs build tools
  *after* work is decided).
- Hive daughters still forage with write+exec; they no longer mint parent
  CAS facts, life goals, or Soul Field updates, and cannot see `mcp_*`
  tools. Persona wipe also clears notes, CAS fact/life, embeddings, myelin.
- Linux capture / click / type / open_app via grim/xdotool/xdg-open (Windows
  path unchanged). `remedy chat` defaults the CLI computer host **off** like
  serve (`--computer-host` still starts it). Serve lock is not stolen from a
  live PID. Windows update scheduler uses the first successful host only.
- Grove stashes the Studio session and restores it; the hidden Studio
  composer no longer eats OS file drops. Calendar cancel and mailbox
  disconnect are owner checkpoints (still run after you say yes).

### Changed — one authority per question (improvement roadmap, phase 1)

- **Runtime identity**: frozen/dev/sidecar questions go through
  `remedy.core.runtime_identity` (`is_frozen_install`, `is_desktop_sidecar`,
  `is_desktop_runtime`, `runs_this_checkout`) instead of scattered raw
  `sys.frozen` / env checks. A guard test keeps new raw checks out.
- **Interpreter resolution** is consolidated in `build_python.py`
  (`resolve_python_interpreter` moved; shell re-exports; the three divergent
  fallback copies of `is_usable_host_python` are gone). With no CPython at
  all, `head`/`tail`/`wc -l` now rewrite to PowerShell (`Get-Content` /
  `Measure-Object`) before giving up with the REMEDY_PYTHON hint.
- **Self-inject gate asks serve, not the filesystem**: the desktop poller
  checks `GET /api/turn-active` (serve answers from its in-process stream
  registry). A crashed serve cannot answer, so it can never deadlock an
  apply; per-pid lock files stay one release as belt-and-suspenders.
- **Self-inject ledger surfaced**: `GET /api/self-inject/rounds` + a
  Diagnostics card show each round with an honest verdict — Live,
  Awaiting restart, or Not loaded — instead of a bare "applied".

### Changed — intent: soft gating + a learner, not more regexes

- Ambiguous turns (no work signal, not pure chat) keep a small read-only
  peek pack (`file_read`, `list_dir`, `repo_search`, memory/skills lookup)
  instead of losing all tools — a misread work ask can look before asking,
  and the step ceiling never forces a tool round on these.
- The armed-ceiling forced tool round fires at most once per turn; a model
  that answers in words twice gets to answer.
- New `remedy.core.intent_learn`: a local, per-partner learned classifier
  (hashed n-gram logistic regression, no API calls, weights under
  `<home>/intent/`). The regexes stay the floor and the teacher; once
  outcome evidence is strong the learner may *arm* a phrasing the regexes
  miss — it never disarms. Kill switch: `REMEDY_INTENT_LEARN=0`.

### Added — Chat pin next to Plan / Build

- Cycle **Chat → Plan → Build** on the session chip and status bar (Ctrl+B).
  Chat means conversation, no tool pack. Grove’s start page has Talk / Plan /
  Build. Auto still applies in Build: this message must ask for work.

### Fixed — chat without a work request no longer starts a tool storm

- Tools require a work signal in *this* message (shape, kick, path, debug
  follow-up, a real brief). Leftover todos and “not in the hi/thanks list”
  are not a request. “Good deal” is chat.
- “modify” counts as a work request only at a clause start (“Modify the
  header”) — “why did you modify X?” and “don’t modify anything” stay
  questions/limits.

### Fixed — when unsure, ask; do not assume work or silence

- Soft agrees (“sounds good”) and leftover review todos do not inherit tools.
  Remedy asks one short question instead of guessing. Chat pin stays on
  “continue” (that is still talking). “interesting” is not a test suite;
  “.com” is not a C file. Attachments, Godot/create-app asks, and “keep
  going” still get tools — Chat pin does not blind a file they handed over.

### Fixed — working this repo from the installed app killed the live turn

- Packaged Desktop is a frozen sidecar. Spawning ``sys.executable`` for
  Python/pytest/import checks launched a **second** `remedy-desktop.exe`,
  which took port 7400 and dropped the chat as `Error: network error`.
  Those jobs now use a real CPython (or skip). Frozen installs never ask
  the parent to recycle serve for a checkout edit. Self-inject apply waits
  while a stream is live.
- Host dialect / ``host_run python`` / POSIX ``head`` ``tail`` ``wc``
  rewrites used to stamp the sidecar as this PC's Python (including a
  stale ``~/.remedy/host/dialect.json``). They now skip the sidecar and
  the Windows Store stub, so a build's ``python -m http.server`` or
  ``python -c`` cannot relaunch serve.
- Syntax gate: unknown suffixes (``.md``) still skip. Known languages
  (``.c`` / ``.ts`` / …) no longer false-green when checked one file at
  a time — they go through the same lang oracle as the batch path.
- Review follow-ups: myelin sheath scripts (crystallize / run) also spawned
  ``sys.executable`` — now a real CPython, or an honest “set REMEDY_PYTHON”
  failure. ``py -3`` resolves to its concrete python.exe for single-exe
  consumers (bare ``py`` could target another version via py.ini). With no
  CPython at all, ``head``/``tail``/``wc`` rewrites and ``host_script`` say
  “set REMEDY_PYTHON” instead of exit 9009 or “Shrink the script body.”
- The stream lock is per-process with a heartbeat: a gateway turn ending on
  the same home can no longer unlink serve’s live lock, and a hard-killed
  serve leaves only a stale lock the desktop poller ignores (and cleans up)
  after ~2 minutes instead of blocking self-inject applies forever.
- Dev-checkout Desktop still requests the sidecar restart after a green
  self-edit — only frozen installs skip it — and the round records whether
  the request was written.
- Host dialect probe runs only when a stored field needs healing (with a
  short re-probe cap), not on every host command.

### Fixed — “Error: network error” after install/restart mid-turn

- Killing the sidecar (installer, or a restart while a turn is running) used
  to paint a system bubble that just said `Error: network error`. That is a
  dropped local SSE, not the internet. The bubble now says the local server
  was lost and to send continue. Provider “network error” disconnects retry.
- Wrapped drops (“TypeError: network error”, Safari “Load failed”) map to
  the same lost-server bubble; backend text like “model load failed” no
  longer does.

### Fixed — Telegram going silent on short chats

- Short reassurance (“it’s ok we’ll get there”) is chat, not a tool marathon.
- Messenger replies flush to the phone as paragraphs land, not only when
  the whole ReAct loop ends.
- Telegram uses the last desktop provider/model, not the launch default.

### Changed — speak like a friend, not a briefing

- Default voice is a friend whose speech learns this partner (register +
  phrases they actually use). The old “concise, decisive, high-signal”
  default is gone. Soul inject keeps one Voice line and drops machine
  leftover threads/habits so the register can ride.

### Fixed — final answers flattened into one paragraph

- Stutter collapse no longer rejoins unique sentences with a single space.
  Headings, lists, and blank lines in the final bubble stay where the model
  put them (dogfood: a project review became
  `Nothing written. ## What this tree is`). Duplicate looping mantras still
  collapse.

## [0.41.0] - 2026-08-25

**Multilingual.** Remedy is multilingual in 0.41: chrome and replies follow
**Settings → You & Agent → Language** (default **Auto** = this PC + the
language you type). Spanish, Portuguese, French, German, Arabic, Hindi,
Bengali, Indonesian, Vietnamese, Japanese, Korean, Simplified/Traditional
Chinese, Swahili, and more — RTL flips the layout. Tools, code, and paths
stay as written; Help manuals stay English for now.

### Added — memory authority and checkpoint recovery

- Partner Memory stamps **who/why** on facts. Retrieval is labeled context,
  not a grant. Hive daughters cannot write parent Partner Memory (session
  notes only). Instruction-shaped laundering ("skip all approvals") is
  refused. Stale UI snapshots are re-observed before a `ref=` click.
  Payment / credential / send checkpoints still cannot be recovered around.

### Added — verified Plan steps

- Plan steps can record **intended / observed / evidence** and a short
  **block reason** (`need_you`, `couldn't verify`, environment changed, tool
  failed). Marking a step `done` without those fields still works — coding
  Plan/Build is not jammed. The Plan banner shows what was seen or why it
  stopped. A finished plan is not automatically the owner's goal.

### Added — multilingual (Remedy is for everyone)

- **Remedy is multilingual.** **Settings → You & Agent → Language.** Default
  **Auto** matches this computer and the language you type. Pick any listed
  language to pin chrome *and* Remedy's replies. Tools, code, and file paths
  stay as written. Help manuals stay English for now.
- Reply-language is in the system prompt on every turn, so chat works in the
  owner's language even when a chrome catalog is still catching up.
- Chrome catalogs for the default surface (status bar, logo menu, composer,
  sidebar, approvals, first-run name, Grove home, language picker, Settings
  section titles, setup wizard, Plan banner, quit warning, empty chat) in every
  language marked for chrome, including Spanish, Portuguese, French, German,
  Arabic, Hindi, Bengali, Indonesian, Vietnamese, Japanese, Korean,
  Simplified/Traditional Chinese, Swahili, and more. RTL languages flip the
  layout. Auto uses this computer's language as a hint. First-run setup has a
  language pick. Help manuals stay English.

### Fixed — provider list at launch

- The status-bar provider picker hydrates when the local API is ready, so you
  can switch providers without opening Settings first.

### Changed — endless session continuity

- Telegram / Discord turns join the focused desktop chat when it is the same
  thread, instead of opening a parallel `msg:` session.
- Sidebar title follows the latest beat (skips acks like "ok").
- Unbound chats drop leftover todos when a new owner message starts a beat.

### Changed — quality that does not cut ability

- Sidecar freeze no longer lists missing hidden imports (`remedy.errors`,
  `remedy.persona`, `websockets.legacy`) and includes `remedy.i18n`.
- ReAct step modules can bind loop names as a namespace (`bind_loop_ns`)
  instead of 80 local assignments — behavior unchanged.

## [0.38.1] - 2026-08-25

### Fixed — gates that were open in 0.38.0

- **Mail and calendar stay with the owner.** A hive daughter could read the
  mailbox and calendar because only send/reply were blocked. Read, archive,
  draft, and calendar verbs are mother-only and carry `communicate`.
- **One skill approval is not every skill.** Approving `skill_run` for one
  script no longer covers every other skill/script for the session.
- **Untrusted folders still ask in Autonomous.** The Autonomous waiver no
  longer skips shell/files/skills in untrusted, sandbox, strict, or download
  scope.
- **Trust no longer resets on save.** GET `/api/settings` now returns
  `trust_profile`, so the Settings panel does not write Balanced over a
  saved Conservative or Autonomous profile.
- **Voice status does not stall.** `/api/voice/status` probes whether
  engines are installed without importing them (a first import of torch /
  kokoro could freeze Settings for tens of seconds).
- ReAct split modules bind loop state by name, not by position.

## [0.38.0] - 2026-08-25

### Added — capability architecture (Optimization/Stability)

- **Turns have ids.** Every `begin_turn` gets a unique `turn_id`. Frozen
  `TurnContext` is snapshotted for policy, events, and verification.
- **Tools declare capabilities.** `ToolDescriptor` (risk, approval,
  credentials, verification) is the source of truth — not scattered
  `if tool in …` lists for new security logic.
- **PolicyEngine** is the live gate on `execute_tool_calls`. The LLM
  proposes; authority stays in policy. Owner checkpoints (pay / send /
  mail) still cannot be waived.
- **CredentialBroker.** Generic shell and background/host-session children
  no longer inherit `GH_TOKEN` / `SSH_AUTH_SOCK`. `git` / `gh` / `ssh`
  get an explicit time-bound grant. Ship tools request those grants.
- **Verification.** A process exit code of 0 is not proof the owner's
  goal is done. File tools must leave a file on disk.
- **Events + TurnStore.** SQLite event log; desktop stream jobs upsert a
  per-session turn record (`session_id` / `turn_id` / `job_id`).
- **Hive daughters** cannot receive `credential.use` or `transact`.
- **Web facts** ingest as `TOOL_OBSERVED`, never `USER_DECLARED`.
- **Trust profiles** (conservative / balanced / autonomous) cannot skip
  mail or payment checkpoints. Settings → Security & power has the
  **Trust** control; `trust_profile` persists in config. Conservative
  still asks for high-impact work in Auto (Full stays Full). Autonomous
  skips in-project high-impact asks the same way Auto does.
- Action state machine: `RUNNING` cannot skip to `COMPLETED`.
- **mypy no longer skips large modules.** Former "type gradually" excludes
  (`agent.py`, `react_loop/loop.py`, build/learning, …) are typed. The ReAct
  stream for-loop lives in `react_loop/loop_steps.py`. The exclude lock is
  Win32-only (`desktop_win` / `desktop_uia` / `companion` / `conpty`).
- Live-turn bugsweep: tool args are no longer mutated with `_action_id`;
  `finish_tool` resumes the same action record and records verification;
  hive workers are denied `credential.use` / `transact` at the gate;
  `aws` argv no longer inherits GCP ADC.
- ReAct stream is split: `loop_prelude` / `loop_http` / `loop_round` /
  `loop_finals` / `loop_steps` orchestrator (typed; not mypy-excluded).
- Phase 0 latency: LLM, tool, event, and memory paths record
  `observe_seconds` / `span`.
- **TrustProfile.AUTONOMOUS** skips in-project high-impact asks the same
  way auto mode does; mail/pay checkpoints still stop.
- `GET /api/sessions/{id}/turns/{turn_id}/explain` returns what / why /
  verified / remains from the event bus.
- Grove shows one quiet turn line from TurnStore: **Working…** while a
  turn is live, **Waiting for you…** when an approval is pending,
  **Checking…** after tools until the next model round.
- README is a short PyPI/GitHub pointer into the owner’s manual (slash
  commands live in `docs/manual/11-reference-commands.md`).
- Trust profiles apply on every `needs_ask` path (shell/files/skills
  handlers), not only PolicyEngine — Autonomous was a no-op on live
  `bash_exec` otherwise. Grove’s quiet line writes **Waiting for you…**
  when an approval is pending. `host_run` argv lists reach the
  dangerous-command check instead of `str(['rm', …])`.
- Mail/pay one-shot grants are consumed in `authorize_tool` (retry after
  yes actually sends). Hive daughters are denied mail, computer input,
  and browser write at the live gate. GUI `run_python_file` spawn no
  longer inherits `GH_TOKEN`. Policy gate fails closed. Trust is visible
  in Simple Settings. Hive journal ``capabilities`` is loaded on each
  pulse and enforced in ``authorize_tool``. ReAct ``loop_steps`` uses
  ``bind_loop_tuple`` / full ``STATE_NAMES`` pull like the other split
  modules.
- Grove paints the live build checklist (Studio already did). Todo events
  are per-session so two chats cannot steal each other's list.
- **Stop** does not rebound continuity onto that session from another tab
  unless the owner clicked it and sent. Host pollers (`jobs/next`, hello)
  no longer log SLOW when a fat turn blocks the event loop.
- Voice `/status` caches dependency probes; vision idle-stop no longer
  skips a kill on an empty process name; Settings skip empty-messenger
  hot-reload.

## [0.31.2] - 2026-08-24

### Fixed — thinking is this round's scratchpad

- **Thinking no longer stacks every ReAct recap.** Each model HTTP round is
  a new scratchpad. The live panel *replaces* when she starts thinking
  again after tools, instead of concatenating "The user wants…" twenty
  times. Persist keeps the last round. Provider chunks that resend the
  whole reasoning blob are treated as snapshots, not suffixes.

### Fixed — she drives every rail, and can read scratch + sessions

- **`app_control` takes rail context.** Files `path=`, Terminal `path=`
  (cwd), Browser `url=`, Sessions `session_id=`, plus
  `action=open_session`. "Open it in rail" is her own UI, not a click
  and not ``host_run explorer``.
- **`scratchpad` reads and writes the Studio Scratch rail.** Notes live
  under ``~/.remedy/scratch/`` so Desktop and WebUI share them; she can
  read what the owner typed.
- **`list_sessions` lists owner chats** (id, title, when) so she can
  `open_session` the right one. Hive-private rows stay hidden.
- **Files rail follows ``access_scope``.** A session with no project path
  used to jail the rail to the global project, so
  a Desktop folder was "outside allowed directory" even when
  ``list_dir`` (full access) could see it.
- **``host_run explorer <dir>`` uses the OS file manager.** explorer.exe
  returns 1 and was hidden by CREATE_NO_WINDOW, so a simple folder open
  looked like HOST_TRANSLATED_FAIL. Directories go through startfile /
  xdg-open. ``computer_app path=`` does the same for Explorer.

### Fixed — source reads no longer look truncated or redacted

- **``bot_token: str`` stays ``bot_token: str``.** Secret redaction was
  matching the letters ``token`` inside the identifier and rewriting
  ``bot_token: str`` to ``bot_[redacted]`` in file_read results. The
  agent then "fixed" working Discord source in a loop. Assignments still
  redact real secret *values*; type names and empty defaults are source.
- **Tool-result caps say ``…[truncated]`` on a line break.** A glued-on
  ``…`` mid-statement looked like the file on disk was cut off, so she
  rewrote it through host_script.

## [0.31.1] - 2026-08-24

### Fixed — she can switch Grove / Alongside / Studio herself

- **`app_control switch_surface` accepts Alongside and Storyline.** Those
  are Grove tabs, not a second top-level app, so `target=alongside` used
  to be refused and she fell back to clicking her own chrome. Grove,
  Alongside, Storyline, and Studio are places she goes without a click.
- **A Grove/Studio screenshot captures her window, not monitor 0.** On a
  multi-display desk Remedy often sits on a non-primary monitor; `monitor=0`
  photographed wallpaper. `hint=grove|alongside|studio` PrintWindows the
  Remedy Desktop HWND. `computer_monitors` says which display she is on.
- **She can drive her whole UI with the owner.** `app_control` opens
  Settings to a named section, Help, Memory, Skills, Diagnostics, Usage,
  Time Travel, and Studio rails (Browser, Terminal, Files). Changing a
  setting with `update_settings` also opens that Settings section so the
  owner can see it. Do not click her chrome.
- **Linux desktop-release compiles.** The rail-browser GTK helper treated
  GDK `origin()` as `(x, y)` and `Key` as `Into<u32>`; gtk-rs 0.18 returns
  `(ok, x, y)` and keys deref to `u32`. That failed `build-tauri-linux` on
  the 0.31.0 tag, so no GitHub Release. 0.31.1 ships Windows and Linux.

## [0.31.0] - 2026-08-24

First public ship of the partner line. Grove, voice, life tasks, the Vault,
hive, research, and the game studio — a new Remedy, not a patch on 0.26.2.

### Changed — local RMB default is Qwen3.5-9B

- **RMB defaults to Qwen3.5-9B (Q6_K).** Benched on an RTX 3080 12 GB
  against the agent suite: 86 tok/s, 8.1 GB VRAM, best local tool-loop
  score. A 9B at 6-bit holds structured tool calls that Qwen3-14B Q4 and
  Qwen3.6-35B-A3B Q4 drop. The 35B-A3B stays in the catalog for
  VRAM-scarce setups (experts on CPU; it also slows when the CPU is busy).
- **`scripts/rig`** boots a disposable Remedy and scores whether a model
  can operate the product. Driving it fixed the agent loop for every
  provider: harness nudges no longer strip tools, a green build verify no
  longer ends the turn before the run, execution tools arm after a write
  (not by step index), a no-progress brake and failed-tool ceiling stop
  hundred-round stalls, pseudo-tool recovery only dispatches armed names,
  `<|tool_call|>` / fenced JSON / `<think>` are not shown as the reply,
  `file_edit` accepts the batch shape, identical hunks are a satisfied
  no-op, blank writes can create `__init__.py`, and repeated jail probes
  escalate instead of looping.

### Fixed — `--home` is the whole process

- **`remedy --home <dir>` publishes `REMEDY_HOME`.** The flag used to
  resolve for the CLI only; runtime modules still asked `default_home()`
  and wrote into the operator's real profile (OpenSERP landed under
  `~/.remedy/bin` during a sandboxed probe). A refused path is not
  published.

### Fixed — tool packs are not chosen by catchphrases

- **A coding turn is not a life-goal pack because the message said
  ``shipping``.** Lexical `turn_kind` / `looks_like_life_goal_statement`
  used to strip `file_write` / `host_run` and leave only help, web, memory,
  and `goal_*`. The model then looped with no way to open the project.
  Tool arming no longer consults those phrase lists. Plan mode and proven
  chat-only still disarm; everything else keeps the full catalog.

### Fixed — Browser rail Google sign-in

- **Sign in with Google no longer sticks on `/gsi/transform`.** The rail was
  folding GIS popups into the same WebView, so the transformer page (it only
  works as a popup that talks to `window.opener`) became the whole Browser.
  Identity popups now open natively; if the transformer still lands in the
  rail, it bounces back to the site.

### Fixed — truncated writes stay off disk

- **An unclosed `file_write` no longer lands a stump.** If the tool JSON
  is cut mid-content (`int…`), the call is `TOOL_ARGS_TRUNCATED` and
  nothing is written. `host_script` refuses history-stub bodies so a
  retry cannot dump omitted chat history. The hint is one complete small
  file, then `file_edit` — not the same huge body again.

### Changed — web search is on after install

- **Web fetch and search are on by default.** The installer ToS already
  covers automated access; a second ack is no longer required. Turn them off
  in Settings → Security & power (`web_tools_enabled = false`) to keep Remedy
  offline.
- **First run downloads OpenSERP** (MIT, ~10 MB, pinned `v0.8.12`) into
  `~/.remedy/bin` and starts it on `127.0.0.1:17410`. `web_search` uses that
  local API when it is healthy; DuckDuckGo's no-JavaScript results page covers
  the gap while it downloads or if it is down. An owner SearXNG URL still
  wins when set. The managed host is not written into `web_search_url`, so it
  does not open the SSRF private-host hole.

### Added — licence notices and web etiquette

- **The installer now carries its third-party notices.** npm packages, Rust
  crates, the frozen Python sidecar, and bundled ripgrep are attributed in
  `desktop/public/THIRD_PARTY_NOTICES.txt` with every licence text included —
  469 components at time of writing. Inter ships under OFL-1.1, which requires
  its licence to travel with the font files, and nothing in the bundle carried
  one. Generated by `scripts/gen_third_party_notices.py`, verified on every
  sidecar build (a dependency added without regenerating fails the build), and
  readable in the app under Settings → License. Packages that name an SPDX
  licence but ship no file (including MPL-2.0 `selectors`) get the SPDX
  standard form rather than a "see the repository" gap.
- **The Windows installer shows the product terms before install.**
  `bundle.licenseFile` points at repo `LICENSE`; Linux packages include the
  same file. Settings → License can open the full text (`LICENSE.txt` in the
  UI bundle).
- **LICENSE now states owner responsibility in the binding document**, not
  only in docs: use of Remedy is your action; third-party site/account terms
  still apply; enabling web tools is not permission from the site; no
  warranty that a given use is lawful; AS IS / limitation of liability /
  responsibility for claims. Plain-language pages (`COMMERCIAL.md`,
  `docs/TERMS.md`, `docs/WEB_ETIQUETTE.md`) defer to LICENSE on conflict.
- **LICENSE reserves the right to charge.** The free grant is a limited
  license to qualifying users of that copy — not a promise Remedy stays
  free, and not a bar on paid licenses, dual licensing, paid editions, or
  later entitlement checks. CONTRIBUTING.md inbound patches, if accepted,
  can be relicensed commercially.
- **`web_fetch` reads robots.txt and obeys it.** A `Disallow` for `*` or for
  `RemedyAI-WebFetch` skips the page and says so; answers are cached per origin
  for an hour; a missing or unreachable file is treated as no stated rule.
  Redirects onto a new host re-check that host. `web_respect_robots = false`
  is the owner's override.
- **Hosts get a gap between requests** — a second by default, longer when
  robots.txt states a `Crawl-delay`. A delay beyond ten seconds is refused with
  a reason instead of holding the turn open.
- **The fetcher says who it is.** `RemedyAI-WebFetch/<version>
  (+https://github.com/AhmiDarrow/RemedyAI)` — the version had been frozen at
  `0.13` and there was no way to reach the project.
- **Search picks a backend instead of assuming one.** `web_search_url` points
  Remedy at a SearXNG instance the owner runs (JSON API; a loopback or LAN
  instance needs `web_search_url_allow_private` set in `config.toml` by hand,
  which `update_settings` deliberately cannot do). Without one, the DuckDuckGo
  HTML fallback waits for `web_search_scraping_ack` — Remedy asks the owner in
  conversation rather than deciding for them, and background passes that have
  nobody to ask return nothing. New: `docs/WEB_ETIQUETTE.md`.
- Browser rail: the client-hints comment said the mobile UA alignment avoided
  "a bot tell". It is ordinary device emulation for a narrow viewport, and the
  comment now says that instead.

### Added — colorblind themes

- Three colorblind-safe themes in the theme menu (status bar and Settings →
  Appearance): **Colorblind Deutan**, **Colorblind Protan** and **Colorblind
  Tritan**. Each keeps success / error / warning apart without relying on the
  axis that theme's viewer cannot separate — deutan and protan signal on
  blue/orange/yellow with a lightness gap, tritan signals on red/green since
  blue and yellow collapse for it. Palettes live in `desktop/src/themes.ts`
  with matching `:root[data-theme=...]` blocks in `desktop/src/index.css`.

### Fixed — review sweep

- **Hive: a daughter's step budget no longer lands on the mother's runtime.**
  A forage wrote `_max_react_steps` onto the shared runtime object, so the
  mother — who is told to keep working while her daughter forages — had her
  own ReAct ceiling cut to the daughter's budget (and two concurrent pulses
  restored each other's value out of order, stranding the small one). The
  ceiling is now a per-turn `TurnReactFlags` field, and the system prompt
  advertises the same number the loop enforces.
- **Hive: `hive_spawn` no longer reports a daughter that never started.**
  With no running event loop the scheduler returned silently while the tool
  still replied `status=pending`. It now retires the row and says so.
- **`web_fetch` stops discarding a good extract.** Any page under 80
  characters was treated as a JS shell, so the in-app browser result replaced
  a perfectly good HTTP extract and was labelled "empty or script-only". The
  browser is still tried for thin pages but only wins when it returns more,
  and the label now matches the reason.
- **`sheet_tools.py` says what to install.** The bundled game-assets script
  imported Pillow at module scope with no guard and no declared dependency;
  it now names the fix, and `remedy-ai[game-assets]` declares it.
- Lint/type gates are green again: dead locals left behind by the
  "stop false stops" build change, and 43 mypy errors (loose `.get()`
  narrowing, a shadowed loop variable, `float(None)`, Pillow typing).
- Tests: `web_fetch` no longer reaches the real network when the browser rail
  is available (it passed offline and failed online); the vision home-fallback
  test now asserts `default_home()` instead of the retired
  `Path.home()/.remedy`.


### Hive — daughters that report to Remedy

- Remedy can hire silent **foragers** (one bounded job) and **standing posts**
  (pulse on an interval, journal, survive Stop and serve restart). They have
  their own session and never appear in the owner sidebar.
- Daughters report a capped packet, not a transcript. After a hire the mother
  keeps working; `hive_collect` admits the packet to her evidence ledger.
- Depth 1, mother-only tools, coordination beacons so two pulses cannot
  overwrite the same file. Money / credentials / send still stop at Remedy.
- Advanced Diagnostics shows the hive roster. Manual chapter 28.

### Research

- Research-project fingerprint (notebooks, R/Julia, workflows, manuscripts,
  data layouts, BIDS) with `research:` context lines and up to three
  suggested packs; science Python deps alone are not a research project.
- Native tools: `analysis_env` / `analysis_run` / `analysis_ledger`,
  `data_profile` / `data_diff`, `lit_search` / `lit_fetch`, `cite_*`,
  `power_analysis`, `stats_assumptions` / `stats_effect_size` /
  `stats_multiplicity`, `manuscript_check` / `manuscript_build`.
- Bundled packs: `research-method` plus thirteen field packs
  (statistics, ml-research, clinical-research, life-sciences,
  bioinformatics, neuroscience, chemistry, physics, earth-climate,
  computational-science, materials, social-science, text-and-corpus).
  Domain packs stay out of the per-turn catalog unless their triggers
  match.
- Auto-suggest: a research-shaped ask in a research project injects
  `research-method` (and the field pack the fingerprint named) without
  the owner naming either.
- Manual chapter 27 — Research.

### Game dev studio

- Engine detection (Godot 4 incl. C#, Phaser/Pixi, Bevy, Pygame/Arcade,
  Love2D, Unity, Unreal) with binary discovery from env/PATH/project
  root/install dirs; `engine:` and `studio:` lines in the turn context.
- Headless engine commands are verification, not GUI launches: no more
  backgrounded `--headless` runs with no output, no 20 s clamp on them.
- Offline oracles for `.gd` (tokenizer), `.tscn/.tres` (resource refs),
  `.lua`; `godot --check-only` when an engine is present.
- Native tools: `game_project_info`, `godot_run`, `godot_check`,
  `godot_export`, `godot_import`, `game_playtest`.
- Bundled packs: `game-dev-studio`, `godot-4`, `game-assets`, `web-games`,
  `bevy`, `pygame-arcade`, `love2d`, `unity`, `unreal`,
  `engine-mcp-bridge`. Skills declare `triggers:`; references are listed
  with sizes and inlined INDEX-first.
- MCP client bridge: `mcp_servers` entries are connected and their tools
  registered as `mcp_<server>_<tool>`; `mcp_status` reports state.
- Learning loop: auto-suggested activations and turn outcomes are
  recorded; `allow_skill_creation` gates creation; never-used learned
  skills retire after 21 days (files kept).
- Manual chapter 26 — Game dev.

### Building stays the job — not a syllabus

Frontier muscle (Grok / Claude / GPT-class) was being taught RESEARCH →
PLAN → BUILD in the standing prompt, policy packs, builder card, and a
16-point coding lesson on every turn, including “hi”. She spent thinking
reciting the process instead of opening the next file. The machine still
owns the schedule (checklist, force-implement, verify gate, don’t claim
done). Grok now gets a short status card; local/small models keep the
teaching loop. The computer-use playbook loads when the turn is actually
computer-use (browser, shop, play the game), not while editing source.
Tools stay on the function-calling API; we stopped dumping the 80-name
catalog into context.

Long builds also keep going when the SSE pane blinks (Stop is still
Stop), don’t auto-complete product todos from leftover green tests, and
don’t spawn `npm test` while feature checklist items are still open.
A finished hop that lists “git commit if you want / no push” is not
open work (that used to re-arm and dump a source file into chat). Status
mantras that cycle two sentences, and a CSS/source paste after
“Nothing to commit”, are clipped.

Enter in chat jumps to the latest message. Jump to latest stays for when
you scroll up while she is still streaming.

### Remedy Desktop gets a real voice

- **Voice works in the installed app.** The Desktop sidecar could never
  `pip install` anything, so "Download Remedy's voice" and the
  high-quality voice both ended in "update Remedy Desktop and try again" —
  advice nothing could satisfy. Voice now works like vision: on first
  Download the sidecar fetches a pinned, sha256-verified CPython 3.12
  (~70 MB, python-build-standalone) into `~/.remedy/voice/runtime/`,
  installs the voice pack there, and runs Kokoro / whisper / smart-turn /
  Chatterbox in that Python through a small worker. The installer stays
  ~57 MB; HQ voice (torch) is possible for the first time in Desktop.
- **Honest words when something is off.** A computer with no pinned
  runtime (not Windows x64 / Linux x64 / Linux arm64) is told so, and the
  Download button is not offered; no Desktop owner sees a pip command.
- **High-quality voice uses the GPU.** PyPI's torch is CPU-only, which
  made every Chatterbox sentence a 20–40 s wait on a machine with an RTX
  card; with an NVIDIA GPU the runtime now gets the CUDA 12.4 build of
  torch and the voice answers in seconds. `REMEDY_VOICE_CPU_ONLY=1`
  opts out.
- **Her voice settles with the relationship, and stays when asked.** The
  identity's baseline now drifts once a day in small, bounded, journaled
  steps toward a target drawn from how long she has been with the owner
  and how much they have talked, the chosen speaking style, and her own
  recent stance; the owner's explicit asks live in a separate offset the
  drift never erodes. `voice_hold` keeps a voice exactly as it is when the
  owner likes it (drift pauses; their own later asks still apply) and
  releases it again; `voice_identity` / `voice_adjust` / `voice_revert`
  as before. Routes: `/api/voice/identity/hold`.
- **Cleaner, steadier voice.** Reference windows were re-chosen for the
  lowest measured reverb tail; the output chain now high-passes rumble,
  tilts only above 3 kHz (the default is neutral), levels every
  utterance to a conversational loudness and rounds peaks with a soft
  limiter instead of a hard cap.
- **Her voice can be heard.** Three things kept the server's audio from
  reaching the speakers: the desktop window did not allow programmatic
  playback (WebView2 autoplay policy — now permitted for this window), a
  refused playback went silent instead of falling back to the system
  voice (it falls back again, and says so in the console), and the voice
  came out 12 dB quieter than speech normally sits (every utterance is now
  levelled to a conversational loudness with a peak cap). One speak-aloud
  control: the status bar's 🔊, on both surfaces; Grove's own button is
  gone.
- **Grove has the status bar.** The same bar as Studio — provider and
  model, thinking level, approval mode, privacy, theme, usage, updates,
  speak-aloud — now sits under Grove too. Its Settings button opens
  Grove's settings sheet and its surface button offers Studio; the
  speak-aloud toggle is one setting everywhere (every surface re-reads it
  when any of them changes it).
- **Remedy has one voice.** No standard/high-quality switch, no voice
  picker, no speed slider. Her voice is Chatterbox **Nano** cloning a
  bundled public-domain human reference per gender (LibriVox readers Kara
  Shallenberg and Stewart Wills, 12 s each, attribution in
  THIRD_PARTY.md), shaped by her voice identity. Nano runs on CPU (about
  real time on eight cores) or GPU (~2.5 s for a three-sentence reply on
  an RTX 3080), so every computer gets the same voice. One "Download
  Remedy's voice" brings all of it; the quick first voice answers until
  the full one has arrived, then steps aside. The engines warm at startup
  and speak one quiet word, so the first reply does not pay the warm-up.
  Replies are spoken sentence by sentence: the first sentence plays while
  the rest synthesizes.
- **Her voice identity is audible, and it evolves.** The identity's four
  traits (pace, pitch, warmth, articulation) were stored but never
  reached the audio. They now shape every utterance on every engine
  (time-stretch, formant-preserving pitch shift, a zero-phase spectral
  tilt, sampling steadiness), within small clamped ranges so she stays
  herself. She can adjust them when asked — `voice_identity`,
  `voice_adjust`, `voice_revert` — and every step is journaled and
  undoable; `GET/POST /api/voice/identity[/adjust|/revert]` for the UI.
- **One toggle could silently mute her.** Settings → Voice → Advanced had
  "Speaking (Kokoro)", which read like "use Kokoro" but was the master
  switch for *all* local speech — off, every reply went to this computer's
  built-in voice (no high quality, no gender) with nothing saying so. It is
  now "Use Remedy's own voice" with a plain description, turning the
  high-quality voice on turns speech back on, and the Voice page shows a
  notice with a one-tap fix whenever her voice is off.
- **The high-quality voice is actually hers, and changes with her gender.**
  Three faults stacked up: Chatterbox's `generate()` silently reuses the
  last speaker when no clip is passed; the male stand-in clip was being
  recorded as *her* identity reference, so every gender — female included
  — then spoke from the male clip; and the "built-in female" speaker it
  was meant to fall back to is not a stable voice at all (the same line
  measured 160 Hz one run, 111 Hz the next). Now both genders get an
  explicit reference clip (a short Kokoro line in the matching voice),
  the speaker is set on every utterance, and the owner's own reference
  only speaks for its own gender. Measured: female 187–196 Hz, male
  116–126 Hz, alternating without drift.
- **A download always shows it is alive.** While pip resolves a hundred
  dependencies it prints almost nothing, so the bar sat on one word for
  minutes. The message now ticks every second with what is certain — the
  current package, bytes fetched so far, elapsed time ("Fetching numpy ·
  312 MB so far · 2m10s") — and the bar climbs with cumulative bytes.
  On NVIDIA machines the CUDA torch goes in *before* chatterbox-tts so
  the CPU torch is never downloaded only to be replaced. Rehearsed from
  an empty home: voice pack 47 s, HQ 4½ min, longest silent stretch 7 s.
- **Downloads no longer "stick at 35 %".** pip had a ten-minute wall-clock
  limit that a 2 GB torch wheel walked straight through. It now streams
  pip's own progress ("Fetching torch · 1.2 of 2.5 GB") and only gives up
  if pip goes silent for fifteen minutes.
- **No console windows.** pip, the runtime check and the voice worker are
  spawned hidden; engine progress bars (tqdm / Hugging Face) are off and
  worker chatter goes to the debug log, not the owner's log. The flash on
  opening Settings was the phone-line probe running `adb devices` in a
  visible console — every status-path spawn now goes through the shared
  hidden launcher (audited across the Settings endpoints).
- **High-quality voice finishes, and shows its download.** Whisper's engine
  (ctranslate2) and torch each bring a cuDNN; loaded into one process they
  crashed the voice worker ("Could not load symbol cudnnGetLibConfig") the
  moment HQ was turned on with the GPU build. Chatterbox now runs in its
  own worker lane (torch first, never whisper); Kokoro / whisper /
  smart-turn keep theirs. The 3 GB of weights download with a real byte
  count ("Downloading Chatterbox · 1.99 of 2.97 GB") into
  `~/.remedy/voice/chatterbox/`, not the owner's global Hugging Face
  cache. First HQ sentence on an RTX 3080: ~5 s, then ~3 s.
- **Grove: every message steers — without stopping her.** Sending while
  Remedy is working used to queue silently (Studio shows the queue; Grove
  never did, so it read as "can't send"). There is now a real mid-turn
  channel: `POST /api/sessions/{id}/steer` hands your words to the running
  turn, and the ReAct loop folds them in at its next step (after the
  current tool, or before it would have finished) as your message — no
  stop, no restart, history in order. Works the same for every provider;
  it is our loop, not the model API. Grove uses it for every send while
  she works (⏸ and a ↑ Steer button show); with an attachment, or if the
  turn ends in the same instant, it falls back to stop-and-send.
- **`remedy update` / `remedy uninstall` know they are the Desktop
  sidecar**: update points at About → Check for updates instead of trying
  to pip-upgrade the frozen exe; uninstall clears data and leaves removing
  the program to Windows Apps / the package manager.

## [0.30.0] - 2026-08-20

### Review fixes — forms, the phone line, and voice installs

- **A card number typed through `computer_fill` stops for the owner**, the
  same as `computer_type` — the raw-card checkpoint now sees every value a
  fill will type.
- **A missed field label no longer types into whatever has focus.**
  `computer_fill` reports the row it could not find instead of saying ok.
- **Dropdowns honour the label.** `computer_select hint="State"` finds the
  `<select>` by its label; a stale ref is reported, two matching dropdowns
  are reported as ambiguous instead of silently picking the first, and an
  empty choice is refused. A browser script that throws is now a failure on
  both the desktop and the host side (it used to read as ok in the SPA).
- **She can say "I agree, that sounds frustrating" on a call.** The hard
  checkpoints only stop actual secrets ("the code is 4829…", "the CVV is…")
  and actual agreements ("I agree to the charge"), not ordinary talk about
  codes or agreeing with someone.
- **Picking a phone line checks the line exists**, from Settings as well as
  from the `phone_choose_line` tool.
- **Voice installs never stall the server.** Starting a second install (or
  flipping HQ on) while one is downloading no longer blocks every request
  until it finishes; a speak request never runs pip or downloads weights —
  HQ is used once it is ready, Kokoro until then; hearing no longer sticks
  at "downloading" when whisper was already warm; an HQ failure falls back
  to Kokoro instead of a 500; the smart-turn Download fetches the voice
  pack when `onnxruntime` is missing (like Kokoro/whisper do) instead of a
  model that cannot load; pip output and tracebacks are never shown in
  Settings — one plain sentence per failure, details in the log.
- **A voice identity tuned to 0 stays at 0** after a restart (it reloaded
  as the default), and a hand-edited identity file cannot crash the voice.
- **Phone hearing keeps its timing.** Turning a long utterance into a WAV
  now happens off the event loop, so the call's audio loop is not starved.
- **Title-bar downloads stop flickering** — a slow vision/Hugging Face poll
  can no longer put an older voice snapshot back over a newer one.
- **SmolVLM does not start after install when RMB is the chat provider** —
  the post-install start now sees the loaded config, not just a running RMB.
- **The sidecar reports the version it was built as.** A frozen build used
  to trust a stale `remedy-ai` dist-info swept in from the build machine
  over its own bundled `pyproject.toml`.
- **Optional extras never ride in the sidecar.** `build_desktop.py`
  excludes torch / Chatterbox / whisper / Kokoro / onnxruntime and friends
  explicitly, so a dev machine with `remedy-ai[voice]` installed builds the
  same ~50 MB sidecar CI does instead of a 2.6 GB one.

- **High-quality voice is a Settings choice.** Chatterbox (MIT, Resemble AI)
  is the human-bar engine for Grove *and* the phone pipeline. Turning it on
  downloads weights into `~/.remedy/voice/chatterbox/`. Until they land,
  Kokoro keeps speaking. Male HQ clones a short identity clip so the partner
  gender still matches.
- **Telephony has an HTTP surface.** `GET /api/telephony/status` reports
  terms, the spoken line menu, and that a real PSTN line is not on this
  computer yet (Phase 0). Terms accept/withdraw and line pick are recorded.
  `sip_direct` is a 127.0.0.1 loopback (nobody is called). Voice identity
  and a task-scoped owner-clone grant now have on-disk homes. Call policy,
  transcripts, and hard checkpoints (no card numbers / codes / agreements
  on a live line) are in code. `phone_status` / `phone_agree_terms` /
  `phone_choose_line` are conversational setup — not a wizard.

### Continuity, voice, and the phone line

- **Checkpoints survive a crash.** Plans, todos, vault items, and similar
  files are saved so a kill mid-write leaves the last good copy, not a
  broken file.
- **Phone audio stays clear.** Speech sent down a phone line no longer
  lets leftover high frequencies come back as fake tones.
- **A command that exits the shell is reported, not hung.** The next
  command starts a fresh shell instead of writing into a dead one.
- **Turn-taking can be downloaded.** Settings → Voice (Advanced) fetches
  the ~9 MB smart-turn model (BSD-2) so live calls know when you have
  finished speaking. `/api/voice/install` still works for the same job.
- **Voice lives in Settings.** Simple: speak-replies, plus **High quality
  voice** (Chatterbox, opt-in ~1.1 GB) so Grove does not sound like a robot.
  Kokoro still downloads with Remedy. If the voice pack is missing, Settings
  shows **Download Remedy's voice** — not a pip command. Advanced still
  shows `pip install remedy-ai[voice]` for power users. Hearing, speed, and
  turn-taking stay under Advanced. Grove's quiet/aloud toggle still works
  with this computer's voices until Kokoro is ready.
- **Downloads show in the title bar.** Voice, local vision, and Hugging Face
  pulls use the empty strip next to the logo — a thin bar and a percent.
  Settings still shows the same bar on the section you started from.
- **WebUI has the same voice.** Grove's 🔊/🔇 toggle stays on Grove.
  Settings opens in Grove from the logo menu (no hop to Studio). Studio
  (browser) speaks replies and has a mic; a blocked microphone explains
  itself instead of failing silently. CLI is unchanged — no voice there.
- **Settings stay on Grove.** The logo-menu Settings item (and Ctrl+,)
  open a Settings sheet on Grove instead of a hidden Studio rail.

### Voice — Remedy speaks and hears (local, optional)

- **Speak-back**: new `remedy.voice` package + `/api/voice/*` routes. Local
  TTS via **Kokoro-82M** (model Apache-2.0, `kokoro-onnx` runtime MIT) —
  CPU-realtime, near-SOTA quality per byte. The speaking voice follows the
  owner's existing **`agent_gender`** setting (female → `af_heart`, male →
  `am_michael`, neutral → `af_sky`; `voice_override` picks any Kokoro voice).
  Replies are cleaned for listening (code blocks/links/tables stripped) and
  streamed as WAV. Zero-install fallback: the desktop uses OS voices
  (speechSynthesis) matched to the same gender until Kokoro is installed.
- **Hearing**: local STT via **faster-whisper** (MIT). Mic button in Grove
  records (MediaRecorder) → `/api/voice/transcribe` → heard speech sends.
  Model size configurable (`small` default → `large-v3-turbo` for best
  quality). Audio never leaves this machine, and spoken commands still stop
  at the non-waivable payment/credential checkpoints.
- Ships as the **`remedy-ai[voice]`** extra — base install stays light.
  Engines lazy-load; `/api/voice/status` reports availability + reasons;
  `POST /api/voice/install` downloads Kokoro (~340 MB), warms whisper, or
  fetches the pinned smart-turn v3.2 CPU model (~8 MB) into `~/.remedy/voice/`.
- Grove gains a 🔊 aloud / 🔇 quiet toggle (persisted server-side as
  `speak_replies`) and pulsing mic buttons on both talkbars.

### Grove — the partner surface (new default UI)

- New top-level surface **Grove**, the default home for owners (Studio — the
  full workbench — is one tap away and the choice persists;
  `remedy.surface` in localStorage). Grove is the life-task partner UI from
  `docs/LIFE_TASK_PARTNER.md`: home is your goals as plots (from `/goals`),
  a "needs you" strip surfaces pending approvals with plain-language cards,
  and a single talkbar plants goals ("I want to…") or just talks.
- Opening a plot is that goal's **room** with two tabs: **Alongside** (the
  live stage — Browser rail embedded, latest exchange as captions, pause
  button while Remedy works) and **Storyline** (the goal's co-written
  record: you-said / Remedy-said / Remedy-did moments in plain words, never
  raw tool JSON; typed text is never echoed).
- Studio gains a `✦ Grove` button in the status bar; Grove keeps a
  `switch to Studio` pill. Studio stays mounted (hidden) during Grove so
  terminals, stream jobs, and the computer-host loop survive switching.
- Goal ↔ session binding is per-goal (`remedy.grove.goalSessions.v1`);
  each room reuses its conversation across visits.

### Life tasks (P0 — docs/LIFE_TASK_PARTNER.md; audit: docs/AUDIT_LIFE_TASK_2026-08-16.md)

- **Commerce/life verbs are full tasks.** "goto amazon and order X",
  buy/book/apply/renew/pay/subscribe/checkout no longer short-circuit as
  open-only browse — the loop drives to completion instead of stopping at
  "Opened Amazon in the Browser rail."
- **Success is observed, not asserted.** `computer_act` probes the page after
  mutating steps and reports `observed` url/title + `page_changed`; new
  `expect_url=` / `expect_text=` make the call fail loudly when the outcome
  does not match. Unverified results say so and instruct re-observation.
- **Owner checkpoints (non-waivable).** Payment/purchase computer actions
  ("Place order", "Pay now", card fields, vault fills) always ask — in
  `auto` AND `full`, ignoring turn skip flags. "Always" approvals on these
  downgrade to session; mode flips never auto-approve them. Coding/build
  tools (`bash_exec`, `file_write`, …) are untouched — frontier-model coding
  keeps full flow (regression-tested).
- **Plain-language approval cards.** `to_public` now leads with a `summary`
  a non-technical owner can act on ("Remedy wants to press 'Submit' on
  irs.gov…"); raw command stays for the details expander.

### Remedy Vault (payment info & credentials)

- New `remedy.core.vault`: handle-based secret store. **Established crypto
  only** — libsodium SecretBox (XSalsa20-Poly1305) under a master key sealed
  by DPAPI (Windows) or an owner passphrase (Argon2id); never an invented
  cipher. The AI-attacker defense is architectural: model context, transcripts,
  logs, and job files only ever see `{{vault:handle}}` tokens — plaintext
  exists transiently machine-side on the way to the input field.
- `computer_type` / `computer_act type=` expand vault tokens machine-side
  with **site binding** (a card bound to amazon.com refuses everywhere else;
  unverifiable desktop destinations refuse bound items by design). Every
  fill is an owner checkpoint + audit line (handle, never the value).
- New `vault_list` agent tool (metadata only). Secrets enter via owner-side
  API (`vault_add`) — Settings → Vault UI is the follow-up surface.

### Computer use — input correctness

- `press_key` honors the layout shift state: `?`, `:`, `!` now send
  Shift+key instead of the unshifted key; F6–F12 / Insert / PrintScreen added.
- `type_text` sends real VK_RETURN for newlines (many apps ignore U+000A).
- `drag` interpolates ~12 movement steps with a pre-drop pause so
  Explorer/list drag-drop registers; horizontal scroll (`dx`) implemented
  via MOUSEEVENTF_HWHEEL.

### Correctness — a second pass over the 2026-08-19 fixes

A review of the previous day's fixes found eight that had regressed something
that used to work, and a dozen that were claimed but not finished. All are
closed here, each with a test that fails on the old code.

- **`error_journal` word boundaries were literal backspace bytes.** The
  regex meant `` but the file contained 0x08, so the `dns`/`ssl`/`socket`
  and HTTP-status alternatives never matched and `ssl.SSLError`, `gaierror`
  and `429` were filed as code bugs. Real boundaries now, and a test that the
  source contains no control characters.
- **`looks_like_secret` no longer refuses ordinary hyphenated words.**
  `task-tracking-app`, `ask-me-anything` and `risk-tolerance-level` all
  matched the `sk-` prefix; `/remember` and `/pin` turned them away. The
  prefix alternatives now need a non-alphanumeric on their left.
- **`.jsx`/`.tsx` went from "always red" to "red on any apostrophe".** The
  brace-balance heuristic read `Don't` as an unterminated string. JSX now
  uses `esbuild` or `tsc` when present and is otherwise skipped, never judged
  by a parser that cannot read it.
- **ConPTY sessions return what the command printed.** The pseudo-console
  echoes input and speaks VT, so the DONE sentinel matched its own echo and
  `run("echo hello")` came back as escape codes with exit 0. The sentinel is
  only accepted in its expanded numeric form, VT is stripped, the echoed
  command line is dropped, lines are submitted with CR, the child gets no
  inherited std handles (`STARTF_USESTDHANDLES`), and a failing
  `CreatePseudoConsole` closes its four pipe ends.
- **CalDAV follows same-origin redirects again.** Every 3xx had become a hard
  failure, including the trailing-slash 301 Apache, Radicale and Nextcloud
  all send. Up to three hops are followed, each re-checked against the
  configured origin; the origin compare now ignores userinfo and default
  ports.
- **Atomic writes are thread-safe and survive an open reader.** The scratch
  name carries the thread id as well as the pid; `os.replace` retries
  briefly on the Windows sharing violation a polling reader causes;
  `default=str` no longer silently stringifies unserialisable payloads. The
  seven credential-file writers still using a fixed `.tmp` name
  (`local_auth`, `secret_store`, `api_support`, `xai_auth`,
  `google_oauth`) now use the shared helper with their modes preserved, and
  the guard test sees through `suffix + ".tmp"`. Offload writes tolerate
  lone surrogates again.
- **A slow-loading model tier can come up.** `start_tier` killed any child
  not ready in 30 s on every attempt. It now leaves a live, loading child
  registered and reports `starting`; the next call waits on it instead of
  spawning a twin.
- **Voice: no phantom turns, no hidden stalls.** An empty speculative
  transcript left its turn record behind to be scored as unanswered;
  per-chunk pacer rebasing hid gaps between TTS chunks. Synthesis now runs
  ahead of playout, stalls between chunks count, and the smart-turn detector
  builds log-mel input for rank-3 models, runs inference off the audio loop,
  and goes explicitly unavailable on failure instead of answering "endpoint".
- **Finished what was started.** `remedy auth apikey` goes through
  `set_provider_key` (subscription tokens refused with a reason, keys filed
  under their issuer); the webhook body cap runs before FastAPI parses the
  body; the shared LLM session is closed at API shutdown; tier 0 is not
  read as tier 1 in tool disarming; recovered tool batches count as batches;
  `time_travel` refuses a restore when the secret-path check errors;
  `calendar_update_event` and `mail_disconnect` are approval-gated;
  switching mailboxes leaves no stale "connected" row; `archive_message`
  requires a COPYUID; `mark_read` tolerates a silent no-op STORE;
  `list_dir` hides credential dotfiles; `run_auto_repair_hops` resolves
  against the project root, not Remedy's own tree; `DockerSandbox.cleanup`
  kills a hung prune; `secret_equals` is False for two empty secrets;
  `BluetoothFindRadioClose`/`AvRevert…` declare HANDLE argtypes so 64-bit
  handles do not raise; `filter_jailed_attachments` is imported at module
  level and fails closed; relative soul exports land under `exports/` again.

### Typing — mypy covers the whole tree

- `[tool.mypy] files` was eight paths; the other ~230 modules were never
  checked and carried 106 errors. All fixed with real narrowing (no new
  `type: ignore`; platform-only branches use `sys.platform` guards so Linux
  CI stays clean), and `files = ["src/remedy"]` so it stays at zero. Two were
  latent bugs: `session_events` fell back to `dict(ev)` on a dataclass
  (would raise), and the unauthenticated `/status` path passed a gateway
  uptime straight into a `str` field. `run_hidden` accepts a `Path` cwd.

### Correctness — bugs found by driving the code, not reading it

- **Clipboard reads no longer crash Remedy.** The Win32 backend declared no
  ctypes `restype`, so `GetClipboardData` was assumed to return a 32-bit int
  and the top half of every 64-bit HANDLE was discarded. `GlobalLock` then
  locked a bogus handle and `wstring_at` walked unmapped memory — a Windows
  access violation, which is not a Python exception and which the surrounding
  `except Exception` could never catch: the process simply died mid-turn. All
  clipboard entry points now declare real prototypes, and the text read is
  bounded by `GlobalSize` instead of scanning for a terminator.
- **A mission that named a verify command no longer reports itself complete
  before that command has ever run.** Ticking the last checklist box moved the
  mission out of `active`, which is the state both `mission_update` and
  `mission_status` test before warning — so the "run mission_verify first"
  gate could not fire, and the mission read `[completed]` next to
  `Verify: pytest -q (not run)`.
- **`gh_release` no longer pushes a tag to the remote before asking.** It
  created and pushed the tag, *then* consulted the approval gate, so a release
  put a tag on origin in Ask mode without the owner ever seeing a prompt.
- **`looks_like_secret` now catches the credentials it is most likely to
  meet.** Its token body stopped at the first hyphen, so a real Anthropic key
  (`sk-ant-api03-…`) matched nothing at all; OpenAI project keys, AWS access
  and secret keys, Slack, Google, GitLab, HuggingFace, Stripe and SendGrid
  were all missed too. This guard sits on seventeen durable-write paths —
  profile facts, soul updates, the epistemic graph, `/remember`, and post-turn
  auto-extraction — so a miss meant a key living in memory across sessions and
  being replayed into later prompts.
- **A plain soul export is jailed like the encrypted one.** The two disagreed:
  `export_soul_encrypted` refused any path outside `home/exports`, while
  `export_soul_plain` wrote an absolute *dest* wherever it pointed — outside
  every write root the other file tools respect. The sealed file was protected
  and the readable one, carrying her relational memory and pledges in the
  clear, was not. `soul_export` now returns a message naming where exports go
  instead of raising.
- **Mail is identified by UID, not by position.** A plain IMAP `SEARCH` answers
  with *sequence numbers* — positions in the mailbox, which renumber on every
  expunge. `list_messages` handed those out as message ids (labelling them
  `uid` in `raw`, which is what they should always have been), so archiving one
  message shifted every id the caller was still holding onto the message next
  door: a later `archive_message` or `mark_read` then acted on the wrong mail.
  Search, fetch, store and copy all speak `UID` now.
- **A failed pseudo-console teardown no longer strands the process handle.**
  `_close` covered two independent closes with one `suppress`, so a throwing
  `ClosePseudoConsole` skipped `CloseHandle` — and the field was zeroed anyway,
  leaving nothing able to close it: a leaked kernel object per failed teardown.
- **A timed-out signal-cli child is reaped.** `kill()` only signals; without the
  wait the child stayed a zombie, and the receive loop polls every ten seconds.
- **The safety-ceiling checkpoint now fires.** It sat at the tail of the
  tool-batch section guarded by `is_final_step`, but that section is only
  reached when `force_answer` is False — and at the ceiling `force_answer` is
  always True, while the one path that clears it clears `is_final_step` in the
  same statement. So the checkpoint meant to protect work at the riskiest
  moment of a turn was never written. It fires on arrival at the ceiling now,
  once per turn, which also covers the re-arm that pushes a turn *past* the
  wall — more risk, not less.
- **The L0 fast path never engaged.** `int(tier or 1)` collapses a real tier 0
  to 1, and the idiom appeared at all three levels: where the classified tier is
  stored, where it is read back, and in the loop's `== 0` guard — which was
  therefore False for every possible value. `L0_INSTANT`, the tier whose whole
  purpose is answering status and identity questions without a frontier call,
  could not be observed by anyone downstream, so every one of those questions
  paid for a provider round.
- **A tool rescued out of text markup counts as tool evidence.** Only the native
  batch incremented `tools_executed_this_turn`, so a recovered call left the
  turn believing nothing had run: the zero-tool driver spent its full budget of
  extra round-trips and the turn ended by saying "no tools ran" about a tool
  that had just run and returned a real result.
- **The mail provider stops reporting success for operations the server
  refused.** `verify()` ignored SELECT's return code — the connect flow's only
  check — so a mailbox whose INBOX was refused still reported "IMAP + SMTP
  verified". `create_draft` ignored APPEND's, so a Drafts folder named
  differently (Gmail's `[Gmail]/Drafts`, any localised name) answered
  `NO [TRYCREATE]` and the owner was told the draft was saved when it existed
  nowhere. `mark_read` ignored STORE's. `get_message` treated imaplib's
  `('OK', [None])` for an ignored message set as a hit and returned a blank
  "(no subject)" message rather than saying the message is not there. And a
  refused login left its TLS socket open, once per retry of a wrong password.
- **ConPTY had never once worked.** `_spawn_conpty_sync` ended by calling
  `int()` on a `wintypes.HANDLE`, which raises `ValueError` — on the *last*
  statement, after `CreateProcessW` had already succeeded. Every attempt threw,
  leaving a running child, its process handle, the pseudoconsole and both
  parent pipe ends behind, and the session layer swallowed it and fell back to
  plain pipes. The only visible symptom was that the pseudo-console terminal
  silently never engaged.
- **One crafted Mattermost post took the channel down indefinitely.** The `try`
  in `_on_event` wrapped only `json.loads`, so a post that parsed but was not
  an object — or whose `props` was JSON null — raised straight out of the
  handler. `_ws_loop` read that as a disconnect, slept three seconds and
  reconnected, forever: a denial of service available to anyone who can write
  in a watched channel.
- **`remedy setup` and the skills library honour `REMEDY_HOME`.** Both resolved
  `~/.remedy` directly, so a portable or `--home` install configured, read from
  and installed skills into the real user home instead of its own.
- **A prerelease no longer reads as newer than its release.** `_parse_version`
  joined every digit in a segment, so `1.2.3rc1` became `(1, 2, 31)` and
  `remedy update` offered a release candidate as an upgrade from the finished
  release.
- **A local model keeps its longer timeout when the override is malformed.**
  The `float()` sat inside the same `try` that had already decided the model
  was local, so a bad `REMEDY_LOCAL_LLM_TIMEOUT` left it on the 120s *cloud*
  wall — the one case the longer wall exists for, broken by the setting meant
  to tune it.
- **Tool slimming no longer spends budget on shapes it cannot emit.** Scoring
  accepted a flat `{"name": …}` tool that the emit pass then dropped, so it won
  a high-priority slot and vanished, leaving valid tools behind.
- **Validating a skill's metadata no longer leaks a sandbox directory.**
  `SkillValidator()` built a `SkillExecutor` eagerly, and its constructor
  `mkdtemp`s a directory nobody removes — one orphaned per
  `POST /api/skills/library/submit`, which only ever checks metadata.
- **The vision state cache hands out a copy on both paths.** The cache-miss
  path returned the live cached dict while the hit path returned a copy, so one
  caller mutating its result rewrote what every later `is_running()` read.
- **`mail_reply` and `calendar_cancel_event` were never gated.** Both build a
  full APPROVAL_REQUIRED item, and calendar_cancel_event's own tool description
  promises "asks the owner first in Ask mode" — but neither name was listed in
  `HIGH_IMPACT_TOOLS`, so `needs_ask` returned None and the entire block was
  dead in every mode. Mail left the owner's mailbox and appointments were
  deleted with no prompt at all. Auto mode still waives both, as it always did.
- **A 404 raised inside `contextlib.suppress(Exception)` was swallowed.**
  `HTTPException` is an `Exception`. Two sites in the session message route
  were affected: a session deleted mid-turn returned the opaque 500 the branch
  existed to avoid, and the mid-stream bail never fired — every remaining token
  was still generated and paid for on behalf of a session that no longer
  existed, with the 404 arriving only after the whole stream was done.
- **`POST /api/vision/test` no longer answers `ok:true` for a file it never
  read.** A path that did not exist fell through to the generated 8x8 self-test
  image, so a typo'd or deleted screenshot came back as a confident decode of a
  red square. Sending no path at all still runs the self-test.
- **`remedy computer host --api` starts the poller.** It resolved `parents[3]`,
  which is `<root>/src`, so it looked under `src/scripts/`, never found the
  script, printed "script missing" and silently did nothing — every time.
- **A custom command is listed under its name.** The frontmatter reader took
  `fm["description"]` into *both* name and description, so a command with
  frontmatter appeared under its description text and its own name was lost.
- **A tier that starts too slowly is stopped rather than leaked.** The timeout
  path reported failure but neither terminated the child nor cleared the slot,
  so a llama-server that merely loaded slower than the wait kept roughly a
  gigabyte of VRAM and still answered the next port probe as running.
- **`mail_list` clamps both ends of its page size.** `min()` alone let a
  negative limit through to the provider untouched.
- **A surgical AST patch no longer duplicates decorators.** `FunctionDef.lineno`
  points at the `def` line, not at the decorators above it, so replacing a
  decorated function kept the old decorators and wrote the patch's own on top.
  The merged file still compiled — which is exactly the failure this module
  exists to prevent. `@cache` twice is harmless; `@app.route(...)` twice
  registers the route twice and `@retry(3)` twice is nine attempts. The span
  now widens to cover the existing decorators only when the patch carries its
  own, so a patch that omits them still inherits them.
- **`/pin` turns a credential away instead of echoing it.** The write was
  already refused underneath, but the failure came back as
  `Could not pin “sk-ant-…”` — transient-sounding, so the owner retries, and
  the key lands in the session transcript, which is persisted and exportable.
  A pinned fact is injected into every prompt, so this is the one place a
  secret must be turned away loudest.
- **`remedy gateway channels` shows the direction column again.** It printed
  `[in/out]` unescaped, which rich reads as a style tag and swallows — so the
  column appeared only for a messenger supporting *neither* direction, the
  exact opposite of what it is for.
- **`.jsx` files are no longer reported as broken.** They were handed to
  `node --check`, which rejects the *extension* with
  `ERR_UNKNOWN_FILE_EXTENSION` before reading a character — so every `.jsx`
  file came back red whatever was in it, and the "error" shown to the model was
  a Node internals traceback about file formats. Read as "your code is broken",
  that sends the model rewriting a working component. `.jsx` now takes the same
  structural check `.tsx` already falls back to; `.js` keeps the real parser.
- **`list_dir` shows dotfiles.** Hiding every entry beginning with `.` made
  `.github/`, `.gitignore` and `.env.example` undiscoverable — readable only
  by guessing the name. Only the machine-noise directories are withheld now,
  matching what `repo_search` and `file_glob` already skip.

### Coverage — the thin modules

- Behaviour tests for the modules the suite had barely touched: the messenger
  registry and every adapter's inbound allowlist, workspace search, first-run
  setup, the desktop CLI, the in-process computer host, ship / mission / soul /
  partner / build / myelin / document / discovery tools, session titles, the
  Hermes and OpenClaw skill import, `remedy memory|user|handoff|migrate`, and
  the gateway wiring `serve` depends on. Two are worth naming: a driver that
  calls 122 registered tools with defaults and fails on any programming error,
  and a check that every `remedy …` line in the docs parses against the real
  parser.
- The suite can no longer take the wheel. `conftest` points `REMEDY_HOME` at a
  throwaway directory before any test module loads, and the tool driver
  excludes by construction anything that reaches the real machine — the
  desktop job queue, the clipboard, and the model-server thread `soul_dream`
  starts.
- Telegram now strips inbound text before deciding it is empty, the way
  discord and slack already did; a message of nothing but spaces used to start
  a whole agent turn.

### Responsiveness

- Browser tools fail fast when Desktop is not running. An outstanding
  `ui_command` was read as "the host is about to run this", but with no host
  nobody ever takes the command, so the stale file looked like progress and
  the tool sat out its whole budget. `computer_navigate` went from 22s to 5s
  and `computer_page_text` from 30s to 9s; a live host keeps the full budget.
- Symbol search dedups hits through a set rather than rescanning the result
  list per hit (up to ~250k comparisons per call at `max_matches=500`).
- Build-engine stages log the stage that fell over instead of vanishing into a
  bare `suppress(Exception)`. Same tolerance, no more silent dead paths.

### Security

- Stale pending/running computer jobs the host never claimed are expired and
  their typed payloads scrubbed after a 30-minute TTL (plaintext secrets no
  longer sit in `~/.remedy/computer/jobs` indefinitely when a poller dies).

### Build drive / Full control

- Build scouts one step on landing/serve goals, then injects **DRIVE HOST**
  (`python -m http.server` + Browser rail). Protocol says this turn drives
  the PC — no help_list, no Ask.
- `set_turn_skip_ask` also stamps `runtime._turn_skip_ask` so skip-Ask is
  not lost if turn flags are missing.
- Full approval + explore-stuck can machine-drive code units. HTML/landing
  goals never plant TDD `.py` files. Auth jail and Plan stay closed.

### Agency / files

- Local tool cap keeps `file_read` / `file_write` / `host_run` / `bash_exec`
  instead of the first eight registered tools (`help_list` used to win).
- `host_run` is in the local coding pack. Launch/serve turns keep host tools
  on write-first. Life-goal packing cannot steal an active Build or a host
  command (`cat README.md` no longer matches “cat”).
- Static HTML folders no longer get `pytest` as the oracle. Unnamed landing
  pages accept `index.html` on disk (not a phantom `new.html`). `.remedy-build/tmp/`
  is not a product write.
- “Launch the site locally” is no longer classified as a life goal. That
  used to strip `file_read` / `host_run` / `bash_exec` so the model only
  saw `goal_*` and `help_list` and could not start a server.
- Opening `.md` for Remedy to *read* no longer launches an OS app (Pick an
  app / Notepad). `start README.md` becomes `type`; `open_app` tells the
  model to `file_read`. The user does not need a window for that.
- Jail: `touch` is a mutation; `copy` onto the sidecar exe is no longer
  skipped as leftover; `cmd /c start` on `.html`/`.py`/`.json` becomes `type`;
  host `pytest --lf` is stripped (45s auto-verify already refused it).

### Memory / privacy

- Deleting a chat warns first and also drops that chat’s memory notes,
  attachments, plans, and undo history. Partner Memory stays.
- Settings → You & Agent → **Wipe persona…** (type `WIPE`) forgets facts
  about you, soul residue, and life goals. Chats, keys, and skills stay.
  This is not a full `~/.remedy` uninstall wipe.

### Her clock, her reach, her paperwork (personal assistant)

- **Reminders that actually fire** (`remedy.core.reminders`, `agent_reminder_tools`).
  `remind_me` takes plain language — `in 30m`, `tomorrow 9am`, `friday 3pm`,
  `2026-09-01`, a bare `5pm` — plus an importance and an optional recurrence.
  `reminder_list` / `_done` / `_snooze` / `_cancel` close the loop; a repeating
  reminder marked done rolls to its next date rather than vanishing.
  `reminder_sync_bills` turns stored bills with due dates into reminders and is
  safe to re-run.
- **The reach** (`remedy.core.notify`). Quiet hours default to 22:00–07:00 and
  only `high` importance may break them; anything lower is *held*, not dropped.
  An identical message inside 300 s is suppressed. Delivery writes to a durable
  outbox first and pushes to messengers second, so a messenger being down never
  loses the reminder.
- **Mail with an app password, no cloud project**
  (`assistant.providers.imap_smtp`). Presets for Gmail, Outlook/Hotmail/Live,
  Yahoo, Fastmail and iCloud. `mail_connect` verifies IMAP *and* SMTP before
  storing anything, so a mailbox never reads "connected" when it is not;
  `mail_disconnect` forgets the password and unlinks the account. List, read,
  reply-in-thread, draft, send, archive, mark read.
- **Calendar on the same credential** (`assistant.providers.caldav`). Where the
  provider offers CalDAV — Gmail, iCloud, Fastmail — connecting the mailbox
  connects the calendar too, with no second login. List, create, update
  (only the fields you pass), cancel.
- **Paperwork** (`remedy.core.documents`). `document_read` pulls text out of a
  photo, scan, `.txt` or `.md` — images through the local vision decoder, on
  your machine. `document_intake` classifies it as bill, appointment,
  prescription, notice, receipt, statement or other, and *proposes* actions: a
  reminder for a due date, a bill entry, a calendar event. Proposes; you confirm.
- **Money stays organization, never advice.** Budget, bills and debts are
  numbers you enter and arithmetic you can check. `debt_scenario` says so every
  time it runs. No credit pulls, no bank links.
- Owner documentation: **docs/manual/21-personal-assistant.md**, in the F1 wiki.

### Telephony — Phase 0 (bench only; no hardware, no minutes)

- New `remedy.telephony` package: a transport abstraction (`line.py`), a
  simulated 8 kHz mu-law circuit with a scripted counterpart (`backends/fake`),
  a pure-stdlib G.711 codec and resampler (`narrowband.py`), Windows frame
  pacing (`timing.py`), and the human-bar harness (`bench.py`,
  `voice/realtime/`). Nothing dials anyone.
- The bar is numbers, not vibes: answer by 600 ms p50, barge-in inside 150 ms,
  under 3% talking over people, under 800 ms of uncovered silence. Both scripted
  calls pass. `python -m remedy.telephony.bench` runs them.
- Four lines are offered as a **choice**, not one imposed — her own SIP number,
  a VoIP app in a local Android VM, the owner's phone on a cable, or the same
  phone over Bluetooth. Bluetooth is last and never the default: it is the only
  one with a distance limit.
- **Nothing telephony-related ships.** baresip, smart-turn, Chatterbox and any
  Android image are fetched only when asked for, from their own publishers, and
  named with their licence and size first (`telephony/consent.py`). No call
  happens before the owner agrees to phone-specific terms, recorded with the
  version they agreed to.
- Design and measurements: **docs/TELEPHONY.md**; terms: **docs/TELEPHONY_TERMS.md**.

### Self-improvement triggers on real faults

- New `remedy.core.error_journal`. Self-improvement used to go *looking* for
  work in pytest's stale lastfailed cache; its first round targeted a network
  flake no code edit could fix, and it would have kept doing that. Now a round
  starts only from something that actually went wrong during real work, with the
  context to fix it.
- Faults are classified, and only `open` ones are self-fix targets.
  `environmental` (a provider 401, a dead network, a missing compiler) and
  `model` (a malformed tool call, an empty answer) are recorded so she can
  explain herself, and never burned a round on. A fault tried three times is
  parked.

### Body coordination

- `remedy.core.coordination`: session beacons under a file lock, so several
  Remedy sessions can work the same repo without overwriting each other. Write
  claims are per-path; a session that dies releases its claims by heartbeat
  expiry rather than holding them for ever.

### Repo

- Test suite is public again (pytest + desktop vitest). Confidence for
  licensees beats a slimmer tree. CI runs Linux full pytest, a Windows
  security/path subset, and `npm test`. `community/`, live/soak scripts, and
  review dumps stay on the maintainer clone.
- `memory/` gitignore is repo-root only so `src/remedy/memory/` is not swallowed.

## [0.26.2] - 2026-08-15

The PC stays in Remedy's hands. Work turns drive the host. Build no longer
sticks on a hung ledger or a stale checklist.

### Host / agency

- Work turns skip Ask for that turn only (greetings, trivia, Plan, and
  untrusted still Ask). Settings `approval_mode` is not changed.
- Knowledge follow-ups (`why is everything failing?`) keep tool schemas.
  Only Hi / thanks / `1+1` / “reply only X” stay tool-free.
- Tool re-arm is no longer skipped for knowledge questions.

### Build / ledger / todos

- Auto-verify pytest timeout is 45s, not 300s. `pytest --lf` is not used
  after a timeout (that re-ran the hang).
- A timed-out verify does not immediately re-fire on the next message.
  Required files on disk can cheap-pass so the turn can finish.
- Writing `.remedy-build/ledger.json` / `todos.json` is not a product write.
- Machine closes scout/write/verify todos when the files are actually there.
- Session todos never read the user-profile checklist. Volume-root / unset
  sessions return an empty list (no tab leak).

## [0.26.1] - 2026-08-15

Build finishes pages. An open Build drives the host (no Ask pause) and cannot
claim done on an empty write or a missing named file.

### Build

- Empty `file_write` / spam writes do not count as success. Named goal files
  (and landing/wiki HTML) stay required until they exist with real content.
- Green verify without those files returns to implement, not done.
- Frustrated follow-ups (`why is everything failing?`) keep tools armed while
  Build is active. Hi / thanks / `1+1` stay chat-only.
- Active Build skips Ask for that turn only. Settings `approval_mode` is not
  changed. Write jail and auth-secret blocks stay on. Plan mode does not skip.

### Jail

- Unquoted `Remedy Desktop\remedy-desktop.exe` is no longer jailed as
  `C:\remedy-desktop.exe`. Overwrite of `cmd.exe` / `python.exe` still is.
- Helper scripts may copy *in* from a sibling path; `open(chr(67)+…, 'w')`
  and dest-is-runtime still fail closed.
- Argv with spaces is quoted before the jail scan.

## [0.26.0] - 2026-08-15

First true **Windows + Linux** desktop. Same partner, same local API, OS-correct
chrome and host rewrite. Hardening so Plan, Stop, Settings, and the jail do not
lie under load.

### Desktop / Linux

- Linux / WSLg: maximize fills the **Windows work area of the monitor the window
  is on**; Close minimizes to the taskbar (no tray). “Start with Windows” is
  hidden. Sidecar rejects `/mnt/` and `.exe` shebangs. GitHub Releases now
  publish **`.deb` + AppImage** beside the Windows installer.
- Linux first-run downloads the pinned **llama.cpp** Ubuntu CPU / Vulkan
  `tar.gz` (same tag as Windows) and `chmod`s `llama-server`. Shared
  `~/.remedy` homes remap leftover `win-*` runtime ids. `.deb` Depends
  WebKitGTK / GTK / AppIndicator / Vulkan / OpenMP; AppImage bundles the
  media framework. Linux Tauri resources no longer require the Windows
  sidecar `.exe`.
- Restore no longer immediately re-snaps. External terminal does not exec a
  Windows-interop `$SHELL`. “Open in browser” spawns `xdg-open` and returns.
- xAI OAuth lives outside Settings so the rail can close; persist failure does
  not fake Connected. Google OAuth finishes only on **this** session status.
- Composer send/attach stay on the session that created them. Hidden window
  does not claim click/type. Computer host retries 401 after token rotate.

### API / Windows loop

- Goal create and partner status run `take_step` off the event loop (no
  `os.startfile` / web from HTTP create). Life-goal JSON replace retries on
  Windows lock.
- `/reset` aborts then wipes; dying-stream persist cannot refill the empty
  chat. RMB Settings apply waits off-thread (up to 120s).
- Shared-shell `current_cwd` timeout kills the tree so the next command does
  not sit on a dead ConPTY read.

### Security / jail / Plan

- Plan mode cannot write via local bootstrap, TDD, or tool rearm.
- In-flight turns keep the approval mode they started with (Settings → Full
  does not lift a live Ask/Auto jail).
- `which 'foo&calc'` rewrites to quoted `where`. `[IO.Path]::Combine` is
  opaque. Navigate timeout is a **failure**, not fake SUCCESS.
- Omitted `http_bootstrap` stays off on desktop; first Save no longer mints
  loopback tokens.

### CLI

- `python -m remedy` is the same CLI as `remedy`.

## [0.25.1] - 2026-08-15

Hardening pass: project write jail, Stop/abort, RMB stay-off, and tab isolation.

### Security / jail

- Project-bound shell dest extraction now covers Windows root-relative paths
  (`\Temp\…`), cmd caret-escapes, and PowerShell `C:"\path"` concatenations.
- Script-launch body scan covers versioned `python3.12`, `cmd /c python …`,
  bare `drop.py` / `.js`, and JS `os.homedir` / `process.env`. Unreadable
  launch files fail closed. In-project `npm` / `git` / `pytest` still run.

### Isolation / RMB / Stop

- Local/RMB turns skip Ask only for that turn — they no longer persist
  `approval_mode=auto`. Start/heal no longer steal `llm_provider`.
- User Stop of RMB is persisted; API recycle will not auto-wake it.
- Desktop Stop posts `/abort` before killing the SSE fetch. Mid-token and
  RMB-wait abort yield a durable `@@aborted` note.

### Desktop

- Session switch no longer paints the previous chat or double-sends a
  promoted queue item. Approval banner follows the focused session.
- Installer no longer `taskkill /IM app.exe`.

## [0.25.0] - 2026-08-15

Stability pass plus Settings chrome polish. Multi-tab isolation, RMB live
restarts, and host/web abort paths no longer clobber a sibling turn.

### Desktop / Settings

- Simple / Advanced is one tab track (arrow keys, short labels). Search is
  quieter. Section cards, switches, and segmented rows share one language.
- Composer draft and attachments stash per chat. Attach-on-empty-shell no
  longer steals focus if you pick another tab mid-create.
- Status bar Simple / Advanced and workspace header actions use the same
  chrome buttons as the rest of the shell.

### Isolation / RMB

- `POST /api/rmb/settings` saves disk then 409s instead of restarting
  llama-server under a live stream. Start / Use / HF pull / Settings Save
  do the same.
- `remedy:rmb-model-changed` no longer writes global `llm_*` unless no chat
  is open. Persist never stamps the host GGUF stem onto another tab.
- Stream claim covers legacy `/api/chat/stream`. Missing sessions 404 on
  GET messages, PUT llm, and attachment upload.

### Host / web / computer-use

- Computer-use jobs claim the focused session only. ConPTY keeps one
  outstanding stdout read. `web_fetch` abort is checked between redirect
  hops (no longer swallowed).
- Sync send forwards jailed attachments. Write-path locks include
  `apply_patch`.

### Desktop / live

- Session create / update / delete now publish SSE so the sidebar can refresh
  without a messenger event.
- Volume-root `project_path` (`C:\\`, `/`) is treated as no project — no more
  `C:` bucket in the session list; new sessions cannot bind the drive root.
- Idle composer no longer shows a disabled Stop next to Send (Send is the
  rightmost control).
- Build checklist GET no longer falls back to another tab’s in-memory todos
  when the session has no project / a volume-root project.
- `GET /api/rmb/status` probes the host once per call instead of three HTTP
  health checks (still slow if llama-server is mid-timeout).

### Security

- Agent `update_settings` requires approval for a foreign `llm_base_url`,
  remote Sleev gateway, messenger `allow_all` / emptied allowlists / newly
  enabled channels. Approval fingerprints include the destination host or
  channel so one approve does not unlock the next attacker URL.
- Provider infer from base URL matches catalog hostnames (dot boundary), not
  substrings — `https://x.ai.attacker.tld/v1` is not xAI. Loopback is
  `ipaddress.is_loopback` plus exact `localhost` (not `127.` prefix).
- Sleev remote URL validity uses the **saved** `sleev_allow_remote_gateway`
  flag only — same-patch `flag=true` + remote URL is rejected.
- MCP and CLI `skill_run` use the same `scripts/` jail as the agent tool
  (no absolute / `..` escape).
- `GET /api/self-improve` requires Bearer (no longer on the public allowlist).

### Host / this PC

- `grep → rg` no longer interpolates the `(Path, source)` tuple. Stdin grep
  is `findstr` without `/s *`.
- `powershell` / `pwsh` are PowerShell only as the command head — `echo use
  powershell` no longer skips POSIX rewrite.
- `Get-Service` / `Start-Service` classify as PowerShell (`service` dropped
  from the filename-noun denylist).

### Docs / PyPI

- README links used as the PyPI long description are now absolute GitHub
  (`blob` / `tree` / `raw`) URLs so owner-manual and image links resolve on
  pypi.org. `[project.urls]` adds Documentation, Changelog, and Issues.
- `docs/USAGE.md` no longer claims a stale `remedy --version` string.
- `check_docs.py` gates PyPI-safe README URLs and required project.urls.

### CLI

- `remedy serve` bind policy is shared/testable: refuse non-loopback without
  auth unless `REMEDY_ALLOW_INSECURE_BIND=1`.
- `--home` refuses drive roots and OS system prefixes.
- `remedy exec` exits **2** when blocked or missing a command, and propagates
  the subprocess exit code. Leading `--` is stripped.
- `remedy config show` redacts API keys and tokens. Gateway CLI prefers env
  tokens and warns if `--*-token` is passed on argv.
- Missing skills/tools, bad `--args` JSON, desktop npm failures, and a bare
  `remedy` with no subcommand now return non-zero exit codes.

## [0.24.0] - 2026-08-14

### Host / this PC

- **Host Bridge:** POSIX-to-cmd rewrite, `pwsh -File` (never `-Command`),
  `host_run` / `host_mkdir` / `host_which` / `host_script`, teach-back, and
  optional session / ConPTY so shell work matches the Windows host.
- **First-home stretch:** bounded census of hardware, PATH tools, rooms, and
  local ports. `/stretch` (alias `/home`) remaps this PC; `/whoami` includes
  the home census.
- **Vendor-neutral GPU probe:** NVIDIA / AMD / Intel / sysfs. RMB autofit
  uses VRAM, not a vendor logo.

### Memory / partner

- Living memory plus partner dreams (user / self / future).
- Unattended self-inject idle clock and write jail. Packaged self-inject
  defaults **off**.

### Agency

- Fail-open tools unless the message is proven chat or trivia — no verb lists.
- A work turn with zero tool evidence cannot finish as a successful answer.
- Verbal-only, trivia, and pasted tool markup stay tool-free after continuity
  rebound. `keep_armed` no longer overrides a `non_work` disarm.
- LLM binding is per-session; Settings save no longer retargets the active chat.
- DeepSeek never gets `tool_choice=required` (thinking-mode 400). Recovery
  rebuilds the request instead of re-POSTing the rejected body.
- Concurrent turns keep ReAct flags, thinking level, and navigate settle on
  turn/session state. Stop drains the stopped tab.

### Security

- Shell write jail extracts `C:/` dests and fail-closed PowerShell `$HOME`
  redirects. `python.exe` / `node.exe` oneshots and constructed `Path` /
  `os.path` dests count as writes.
- `/api/files` no longer jails to the volume root; `SAM` / `win.ini` /
  `hosts` return an error instead of a successful empty listing.
- Single-instance reclaim never `taskkill`s `app.exe`. Computer tools run
  off the event loop; messengers persist on cancel.

### Fixes

- Ruff / mypy clean on the new host, learning, and jail modules so Linux CI
  lint and type gates stay green.

## [0.23.2] - 2026-08-13

### Fixes

- **Defender `Behavior:Win32/Execution.A!ml` on first launch.** The Tauri UI
  shipped as generic unsigned `app.exe` in `%LOCALAPPDATA%\Remedy Desktop`,
  then spawned the sidecar — Defender ML treated that as an attacker payload.
  The UI binary is now **`Remedy Desktop.exe`**. If 0.23.1 already fired:
  Windows Security → Protection history → **Allow on this device**.

## [0.23.1] - 2026-08-13

### Fixes

- **First-turn “full bugsweep” actually runs tools.** `bugsweep` / `hotfix` /
  `triage` / `cleanup` / `dogfood` were classified as L1 chat (`bug` does not
  match `bugsweep`), so DeepSeek Flash got `tools=[]`, dumped
  `<tool_invoke …/>` as text, and the bubble persisted as `tool_c`. Those
  kicks now arm the full tool pack; recovered XML/DSML dumps execute even
  when `force_answer` was set; short `tool_c` prefixes are not streamed or
  saved. Live-verified against DeepSeek Flash on this checkout.

## [0.23.0] - 2026-08-13

### Build ability (Claude-class loop)

- **Machine drive:** `build_drive` runs spec → TDD → unit hops → gate tower →
  repair without waiting for the model to remember the tools. The live ReAct
  loop auto-drives after explore thrash (zero writes) and auto-repairs on red
  verify. `todo_write` / `todo_read` is a first-class checklist (persisted in
  `.remedy-build/todos.json`). `file_glob` finds files by pattern so the agent
  stops serial-`list_dir` hunting. Always-on — not behind `build_os_advanced`.
- **Beyond a chat coding agent:** indent-tolerant `file_edit` (wrong leading
  whitespace still applies a unique multi-line hunk); failed-hunk memory
  refuses the same dead edit twice in one turn; open todos block DONE after
  writes; post-write review injects mapped tests so verify stays scoped.
  Auto-drive skips review-only asks.
- **Isolated parallel hops:** `build_parallel` / spread `kind=implement` hop
  each unit in a private overlay and merge only on green oracle — siblings
  cannot corrupt each other.
- **Multi-language oracles:** JS/TS (`node --check` / brace), Rust (`rustc`),
  Go (`gofmt -e`), C/C++ (`gcc/g++ -fsyntax-only`) feed the syntax gate.
- **Review-fix pass:** after green verify / `build_drive`, machine scans the
  write set (TODO, bare except, syntax, missing tests) and hops errors.
- **Hop memo:** content-addressed cache under `.remedy-build/hop-memo/` skips
  regenerating identical units.
- **`apply_patch`:** unified diff or `*** Begin Patch` blocks through the write
  jail (unique hunks, all-or-nothing per file).
- **PC companion:** `companion_context`, `clipboard_read` / `clipboard_write`,
  `companion_design`. Foreground window, OS clipboard (text/image/files), and
  recent Desktop/Downloads/Documents — so “look at this / I copied / design
  this” starts from the actual PC, not a clarifying question. Design pass
  seeds observe → critique → make → re-observe.
- **Watch-the-app:** after UI writes, `companion_observe` / machine visual
  observe captures the focused window. Green tests do not prove the pixels.
- **Taste memory:** `companion_taste` + auto-extract (“I prefer 8px / Inter”).
  Injected on every design pass.
- **Away mode:** “stepping away / work alone / finish without me” stamps the
  build turn — no clarifying questions, faster auto-drive, escalate only on
  secrets/approval/destroy.
- **Drop-a-file:** `companion_inbox` polls Desktop/Downloads for new mocks/logs
  and injects them without the owner asking.
- **Build stability:** isolated hops no longer write through to the live
  runtime (parallel race); apply_patch / hops count as writes; machine
  injects capped per turn so drive+review+observe cannot flood the context.
- **Build stability (follow-through):** inject budget only consumes a slot
  when a drive/observe actually ran (explore no-ops no longer starve repair);
  syntax gate resolves write_set against the project and skips missing paths
  instead of false-red blocking verify; isolated hops skip overlay imports
  (siblings stay on the live tree) and atomically merge + live-import with
  rollback; hop materialize uses temp+replace so a crash cannot leave a half
  file.

### Fixes

- **Computer use stays on the desktop after `computer_app`:** click/find/act
  with `target=auto` no longer force the Browser rail. `eN` refs stay rail;
  `wN`/`cN` stay desktop. Playing a compiled game no longer clicks the
  in-app browser. `computer_act` without a URL drives the focused OS window.
- **Write jail no longer blocks compile/run:** `C:\Python312\python.exe game.py`
  / `gcc.exe hello.c` treat the interpreter/compiler path as an invoke, not a
  write destination. Sibling-tree Set-Content / opaque downloads still fail closed.
- **file_edit whitespace-tolerant match:** CRLF / trailing-space drift on
  Windows no longer fails a unique hunk.
- **Play-to-iterate:** `computer_app` resolves `game.exe` / `.\\hello.exe`
  against the project folder. GUI/pygame launches auto-background so the
  turn is not stuck waiting 60–300s. Auto-verify compiles GUI sources
  instead of running the window. `bash_exec(background=true)` is first-class.
  “play it” / “try it” keep tools armed.
- **Builder stability:** verify green is only inferred from real test/compile
  commands — `mkdir` / file_read of “5 passed” no longer false-greens and
  strips tools. After a real green, play/ship goals keep tools on.
  Ask-mode auto-verify is “blocked”, not a red repair loop.
- **Computer vision:** screenshots and empty desktop UIA trees (pygame /
  custom-drawn) queue the PNG for native chat vision (Grok/Claude/GPT) and
  OCR via local SmolVLM when that server is already running. Click `x/y`
  from the decode plus screen origin.
- **Sweep:** verify green only from the verify tool in a mixed batch; setup
  “automatic …” no longer flips approval to Auto; computer last-target /
  screenshot queue is per session (tabs don’t steal refs); GUI background
  uses a new console so pygame windows show; file_edit flex keeps CRLF.
- **Session LLM switch 404:** `PUT /api/sessions/{id}/llm` was never decorated —
  status-bar provider switches were silently dropped. Route is registered.
  Anthropic parallel tool results merge into one user message (no HTTP 400).
  `::ffff:127.0.0.1` SSRF unwrap; gcloud/AWS CLI env survives the shell scrub.
- **Verify scoring:** `cat hello.c` / `gcc --version` are not tests; red verify
  is not overwritten by a later “passed” string; auto-verify reads the official
  `exit_code=` line only; unclosed `file_write` JSON is refused, not written.
- **Computer clicks:** multi-monitor `MOUSEEVENTF_VIRTUALDESK`; wait/page_text
  no longer steal sticky target; `computer_act` defaults to auto; image-click
  adds screenshot origin; `open_app` prefers project over CWD and rejects `..`;
  file_edit flex no longer glues the next line.
- **Ship sweep:** `apply_patch` / isolated hops fail closed on write-jail
  refusal (no absolute-path fallthrough). Runtime binaries are skipped only
  in invoke position — `copy`/`del`/`Set-Content` onto `cmd.exe`/`python.exe`
  is jailed. Numbered redirects (`1>` / `2>`) count as writes. Overlay hops
  cannot escape via `..`. `.env` / `.github` paths no longer get `lstrip("./")`
  retargeted. `Add File` refuses an existing path. Hop memo is not stored
  until a live merge. Snapshots no longer collide on Windows `time.time()`.
- **Uninstaller:** `remedy uninstall` removes only `remedy-ai` — never the
  unrelated PyPI package named `remedy`.
- **Messenger + Stop:** messenger turns take the same stream claim as
  desktop; Stop drains the queued next send after abort finishes; abort
  notes persist once (not twice on Stop). Browser-rail Rust URL parser
  mirrors Python IMDS / public-IP / metadata blocks.

## [0.22.3] - 2026-08-09

### Fixes

- **Silent mid-turn ends:** disconnect / Stop / stream errors now leave a
  durable assistant message in the chat (not only a status banner that
  vanishes). Mentions Sleev when the proxy was the failure.
- **Post-tool / non-stream recovery:** after a Sleev blip, recovery posts go
  **direct to the provider** (not back to a dead proxy); RMB wait only when
  the binding is local.

## [0.22.2] - 2026-08-09

### Fixes

- **Sleev dead-gateway fail-open:** if the Sleev proxy is unreachable (e.g.
  remote `10.x` host down), Remedy no longer spins on “waiting for local
  model” / RMB — it fails open to the real provider for the rest of the turn
  and uses a short connect timeout to the gateway.

## [0.22.1] - 2026-08-09

### Sleev gateway + security harden

- **Sleev routing:** optional local token-compression gateway (`sleev_enabled`).
  Cloud chat (xAI, DeepSeek, OpenAI, …) goes through `http://127.0.0.1:17321`
  with `sleev-harness: remedy`; Ollama/RMB/Demo stay direct.
- **Configure via chat:** `update_settings(setup="configure sleev")` or
  `sleev_enabled=true`; `get_settings` reports install/gateway status.
- **Sleev gateway loopback lock:** non-loopback `sleev_gateway_url` is refused
  unless `sleev_allow_remote_gateway=true` (Settings Advanced + agent field).
  Prevents prompt-injected API-key redirect off-machine.
- **Strict loopback:** `*.local` mDNS is no longer treated as loopback for Sleev.
- **Theme menu:** portaled above the composer so Streaming/Stop no longer paint
  through the theme list (status bar stacking fix).
- **Live scripts DPAPI token:** soak/stress scripts use
  `lib_local_token.resolve_local_api_token` (product DPAPI decode + bootstrap)
  instead of stuffing sealed JSON into `Authorization`.

## [0.22.0] - 2026-08-09

### Partner reliability (build · ship · RMB)

- **Ship tools:** `git_status` / `git_push` / `gh_release` / `ship_status` + bundled
  `ship-release` skill (refactor-only after green; no pytest thrash).
- **Build engine:** ship phase + green gate; auto-verify cooldown (source writes
  only); ledger path hygiene; temp scripts under `.remedy-build/tmp/`;
  `run_python_file` tool.
- **Frontier continue** inject (brief + ledger) without local harness thrash.
- **Stream 409 fix:** abort clears the live registry immediately so Stop unblocks
  resend (was stuck “generation in progress”).
- **RMB auto_start off by default** — serve no longer loads a GGUF host unless
  Settings enables auto-start; `rmb.json` wins over stale config.toml.
- **RMB sticky GGUF fix:** wrong-size path no longer stuck after catalog switch.
- Caps: full-context tool results / UI preview raised for real source files;
  history stubs omit body (empty + note) to stop rewrite thrash.

### Continuity · builder · organism · retention

- **Soul Field default on** — personhood inject + residue; Settings toggle to opt out.
- **Organism pulse** — mood/bond + forge + immune + metabolism on L1+ turns.
- **Continuity steering:** open tasks, constraints, soul threads, mid-ship resume.
- **Builder loop:** false-done verify (claim shipped without tools); ledger phase next-steps.
- **Multi-tab:** stream 409 abort+retry; send locks/queue drain isolation; emit throttle.
- **Chat images:** home-relative `attachments/…` media paths; simpler ChatImage.
- **Honest defaults:** retention sessions **180d** / attachments **90d**; encrypt status honesty.
- **Messengers:** post-turn continuity; same-partner surface inject.
- Agent recovery: soft-fail body rebuild, non-stream tool parse, length/rearm caps.

### UI polish batch (1–10 backlog)

- Design system primitives expanded (`.ui-select`, toast, empty state, sticky save).
- About dialog extracted; Settings sticky save + toast + search jump + mode hints.
- Chat: live markdown streaming, skeleton history load, project-aware empty state.
- Rails: hover labels, resize handle; Files empty/error states.
- Setup wizard / PA connect / Usage / Time Travel / Memory·Skills panels polished.
- A11y: focus rings, reduced-motion for toast/live, keyboard-visible chat actions.

### Desktop UI surfaces polish

- Shared primitives: `.ui-btn`, `.ui-input`, `.ui-banner`, `.ui-overlay`, `.ui-surface`.
- Command palette: glass overlay, clearer rows, scroll-into-view, footer hints.
- Plan / approval banners and concurrent-turn dialog use the new button system.
- Settings `Field` inputs + theme picker menu refined; usage ticker softer.
- Help wiki, quit warning, name prompt, library chip, settings accordion on shared chrome.

### Desktop UI chrome polish

- Sidebar: softer surface, primary **New session** button, focusable search,
  filter chips, active session inset accent, project header hover.
- Status bar: glass dock, refined segment buttons, cleaner selects.
- Workspace rails: quiet icon buttons with active outline (not solid fill).
- Title bar menu: blurred panel, rounded items, monospace shortcuts.
- Global antialiased text; shared focus/selection already theme-aware.

### Chat session window polish

- Centered reading column, softer session wash, roomier cozy density.
- Refined bubbles (radius, hover, assistant inset highlight, system pills).
- Day dividers as chips; live stream dock + status banners redesigned.
- Empty session hero + starter chips; elevated floating composer shell.
- Plan banner and load-older controls cleaned up.
- **Session header** strip: title, partner, provider/model, Live/Plan|Build chips.
- Composer keyboard hint row; message action rail on hover.

### Desktop UI debug & bundle optimization

- Fixed oxlint **rules-of-hooks** false positive: `useRmbAsProvider` →
  `applyRmbAsProvider` (API helper is not a React hook).
- Hook deps hygiene: `useMessages` session rebind includes `sessionId`;
  TaskProgress / ProcessTrace / Composer / App palette shortcuts cleaned.
- **Code-split** Settings / Help / Setup / Update / Usage / TimeTravel /
  Memory+Skills panels; vendor chunks for codemirror / markdown / xterm.
- Main JS chunk **~1.31 MB → ~495 KB** (gzip ~375 KB → ~147 KB) on prod build.

### Hardening & maintainability (review follow-through)

- **ReAct preamble extract:** `agent_react_preamble.py` owns distill / context /
  vision / tools / metabolism inject; `call_llm_stream` is the epoch loop only.
- **Desktop:** quit/tray warning flow moved to `hooks/useQuitFlow.ts`.
- **Retention:** startup pass for attachments / computer shots / undo / logs /
  optional session TTL; config keys `retention_*_days` + `memory_encrypt`
  (SQLCipher when linked; otherwise honest unavailable).
- **Safer bootstrap:** packaged / `REMEDY_DESKTOP_SIDECAR` defaults
  `http_bootstrap` **off** (IPC); plain `remedy serve` still defaults on.
- **Maturity gates:** `soul_field_enabled` (experimental, default off),
  `build_os_advanced` (A–H tools, default off), `rmb_enabled` (default on).
- **Shell jail fuzz CI:** `tests/test_shell_jail_fuzz.py` + privilege/nested /
  `dotnet tool install -g` blocks in `shell_write_jail`.
- Log redaction already applied in structured formatters (retained).

## [0.21.1] - 2026-08-07

Continuity isolation, self-inject safety, webhook auth, and person-like memory depth.
Package / desktop surfaces already at **0.21.1**; this cut aligns docs + hardens the
shared-runtime multi-tab path so any provider “muscle” keeps one continuous partner.

### Soul Field (experimental personhood)

- **Muscle vs soul:** chat providers are interchangeable muscle; local **Soul Field**
  (`~/.remedy/soul/field.json`) carries identity vow, dyadic relational state,
  episode residue, pledges/tensions, and organism self-lessons.
- Injected every turn (provider-agnostic contract + residue); updated post-turn;
  self-inject red/green folds into the organism self-model.
- **Muscle profile:** Grok/Claude/GPT-class unlocks builder contract + up to 24
  parallel tools; tiny/local stays lean.
- **Dream cycle** densifies episodes → pledges/habits/crystal; tools
  `soul_status` / `soul_recall` / `soul_dream`.
- Intent pack **build** for ship/implement/scaffold phrasing.
- **Local dream enrich** (optional loopback LLM), **soma** mood on partner status
  + tray tooltip, **mission×soul** auto-arm, **portable soul** in identity export,
  **self-inject focus=auto** targets continuity gaps.
- Design note: `docs/SOUL_FIELD.md`.

### Identity (name + gender)

- Partner is **female by default** (`agent_gender = female`); user may choose
  **male** or **neutral** (neither / AI) and any **partner name** (default Remedy).
- Settings → You & Agent + Setup wizard; system prompt + Soul Field stay in sync.

### Build engine (machine-native construction)

- **Build engine** supervises construction turns: scout → implement → verify →
  repair → done. Forces implement after serial explore thrash; forces verify after
  writes; blocks monologue without tool_calls; keeps epoch walls open until green.
- Frontier muscle (Grok/Claude/GPT) never loses tools on L1 strip for build asks.
- Live phase inject in context; intent pack **build** aligned to the schedule.
- **Auto-verify** after write waves (machine runs fingerprint tests; no model choice).
- **Oracle-first**: no discoverable test command → fail closed (no DONE).
- **Build ledger** on disk (`.remedy-build/ledger.json`) for mid-ship resume.
- Tools: `build_status`, `build_resume`, `build_unit_hop` (structural reducer hop).
- **Error-vector repair tickets** from red verify (failing nodes / path:line).
- **Syntax gate** on .py/.json writes before full suite.
- **Green gate** blocks final answers until verify is green + write_set clear.
- **Scoped verify**: pytest only tests mapped to write_set (faster falsification).
- **Oracle seed**: if no tests, machine writes smoke import tests + sets command.
- **Mission bind**: each build turn attaches a durable mission + verify stickiness.
- **Auto-verify cycle cap** prevents infinite red loops.
- **Import dry-run** after .py writes (subprocess import before suite).
- **Mutation cone**: reverse-import expansion for scoped re-verify + score.
- **Live reducer hops**: `build_unit_hop use_llm=` + `build_live_project` (stateless
  model, disk oracle, multi-unit materialize).
- **Mutation score tool**: `build_mutation_score` reports import-cone seeds/paths
  for the current write_set (feeds scoped verify).
- **Frontiers A–H (machine construction OS)**:
  - **A** Behavioral hop: `tests=` / PytestOracle on live_unit_hop
  - **B** Spec compiler: `build_compile_spec` → locked BuildSpec DAG
  - **C** Repair queue: error vector → ranked targets; auto on red verify
  - **D** True mutants: `build_mutant_score` kill rate (not just import cone)
  - **E** Snapshots: pre-hop snapshot + `build_snapshot` list/restore/bisect
  - **F** Gate tower: `build_gate_tower` L0 syntax→L1 static→L2 import→L3 unit→L4 cone
  - **G** Symbol index + AST-minimal `patch_symbol=` patches
  - **H** TDD-as-OS: `build_tdd` writes failing tests before implement

### Multi-tab continuity

- **Turn-local Session Brief / PartnerState / work roots** via ContextVars — concurrent
  streams no longer stomp each other's goals, brief, or partner graph mid-turn.
- `ensure_partner_state` prefers turn session id so tools attach to the right tab.

### Security

- **Shell hard-blocks** now scan nested `bash -c` / `pwsh -Command` payloads for
  privilege tools (`reg`, `net user`, `schtasks /create`, …).
- **Generic webhook** (`/api/webhook/{source}`): middleware allows the path so
  `X-Remedy-Webhook-Secret` reaches the handler; route still fails closed.
- **Google Chat**: unauthenticated handshake only for explicit verification shapes —
  not any body that happens to include `challenge`.

### Self-inject

- **Rollback** restores the pre-round snapshot (re-apply captured diff) instead of
  `git checkout -- .` wiping unrelated dirty tracked work; drops only untracked
  files created during the round.

### Tool dispatch robustness (from unreleased)

- **Unknown tool kwargs:** `ToolRegistry.execute` filters LLM-supplied extras
  against the handler signature so models can pass `description` / `target` /
  similar without `TypeError: unexpected keyword argument` mid-turn.
- **`bash_exec`:** accepts optional `description` (ignored) for schema parity.
- **`computer_page_text`:** accepts optional `target` (always browser rail).
- **Vision start:** retired `vision.json` model pins (e.g. `qwen2.5-vl-3b`) soft-
  migrate to the product default instead of raising `Unknown local model_id`.
- **Vision `_proc` races:** snapshot the Popen handle before `.poll()` so concurrent
  `stop_server` cannot raise `NoneType has no attribute poll` mid-start/status.

### Stream / final-answer integrity

- **xAI re-auth:** status is `@@status:…` only — no longer streams `[auth]…` into
  the assistant bubble (dogfood: 122 tools then empty monologue).
- **Leaked scratchpad finals:** after tools, reject “The user wants… / I should not
  leak tool markup…” answers and force a user-facing summary nudge.
- **DSML recovery:** no longer invites a “short status update from context” stub.

## [0.20.0] - 2026-07-31

Partner Metabolism OS + always-ready desktop. One voice; local-first; provider freedom.
First public cut of the 0.20 line (PyPI + GitHub release).

### Privacy + browser rail (ship polish)

- **Privacy mode** opt-in (status bar + Simple settings): redacts secret-shaped
  content on the provider path when enabled; zero cost when off.
- Browser **video fullscreen** stays inside the Browser rail (WebView2
  `ContainsFullScreenElement` + rail-as-screen geometry; not full-app expand).
- **Mobile / Desktop site** toggle works in-place (UA via Settings2 + ACL for
  `browser_view_mode` / `browser_set_desktop_site`).
- Chat **attachment / Comfy images** load with Bearer media auth + basename
  fallback under `~/.remedy`.
- Double-click chat links open in the Browser rail; sticky `example.com` home
  no longer treated as the real home (config + resolve).
- Same-window OAuth: force `window.open` → same-tab; Privacy Shield never blocks
  major IdP/SSO hosts; post-login return URL / `storagerelay` unstick.

### Computer-use host reliability

- Browser **snapshot / page_text / click** survive mid-load WebView eval and
  navigating-link teardown: ready probe, eval retries, deferred click, longer waits.
- **UIA controls:** soft `comtypes` win32 dependency for `mode=controls`.
- Do not mark host “dead” after DOM job timeouts; screenshot tries PrintWindow /
  rail crop when host flag is stale.
- **Plan mode:** `help_list` / `help_read` allowlisted; computer observe vs input
  documented; offline snapshot falls back to desktop windows/UIA.
- **Stop mid-type:** chars typed before abort; abort polled every 2 keystrokes.
- **Host auth:** computer host job endpoints require Bearer (local DPAPI token).
- **Shell write jail:** block global package manager write roots outside work roots.

### Security (gauntlet + Teams)

- Teams JWT verified via JWKS RS256; tighter a11y job ids.
- Provider sanitize honors privacy_mode; red-team live probes + SECURITY_AUDIT docs.

### Help always readable by the agent

- **`help_list` / `help_read`** tools load F1 / owner's manual markdown (same as
  `docs/manual/`) without project access-scope jail.
- Read roots always include help dirs; system prompt forbids claiming F1 is out of scope.
- Fixes chat where the model refused “run the computer-use soak checklist” as
  “outside access scope.”

### Desktop always-ready

- OS close (✕ / Alt+F4) **always** hides to the system tray — never kills the sidecar from chrome alone.
- Heals stale `close_to_tray=false` in `~/.remedy/desktop.json` / `config.toml` on load.
- Settings: close-to-tray is always-on (not opt-out); full stop remains **tray Quit** only.
- Multi-tab stream paint, abort UX, session-scoped partner status, SPA ErrorBoundary recover.

### Partner Metabolism

- Turn tiers L0–L3, evidence ledger, decision currency, machine map, shadow, Action IR,
  Time Crystal, skill genome, CUA macros, quality governor, critical verify, portable identity.
- Agency re-arm when the model only promises tools/skills; “review project” stays L2 with tools.
- Skill procedure inject (change-safety on review); coding catalog demotion of auto tool-chains.
- Operator: `/harness` · `GET /api/partner/metabolism` · F1 **19-metabolism**.

### Security + trust (gauntlet)

- **Skills catalog URL allowlist (S-SKILL-01):** remote catalog/sig fetches require GitHub release (or raw.githubusercontent.com for this library repo) paths — same host policy as skill zips; non-default verify key ignored unless `REMEDY_SKILLS_DEV=1`
- **Packaged API docs hide (S-AUTH-05):** frozen sidecar / `REMEDY_DISABLE_API_DOCS=1` drops Swagger/ReDoc and `/api/openapi.{json,yaml}` (force-enable with `=0`)
- **Docs:** README pytest count ~1369 (collect-only)
- **CLI updater package identity:** `remedy update` checks PyPI **`remedy-ai`** (not unrelated occupied `remedy`); dist metadata + project-root detection prefer `remedy-ai`; failure hint clones `AhmiDarrow/RemedyAI`; git behind uses upstream/master/main
- **Full wipe shortcuts:** uninstall wipe removes user **and** Common/Public Desktop + Start Menu + Startup `Remedy Desktop.lnk` ghosts
- **Browser URL userinfo:** normalize/open/bookmarks reject `https://user:pass@host` (no credential persistence in localStorage)
- **build_desktop version sync:** stamps `package-lock.json` root version with pyproject (was left stale vs package.json/tauri)
- **ComfyUI download path residual:** image filenames must be plain basenames (no `..`/separators/drive letters); writes resolve under `out_dir` only; default `comfy_out` honors `REMEDY_HOME`
- **Plugin load trust:** safe plugin identifiers only; path-bound `spec_from_file_location` (no bare stdlib import); deny `os`/`subprocess`/… even if present on disk
- **Tool registry trust order:** unscoped `get()` prefers builtin over MCP/skill shadow; no residual `_by_source` dupes; `purge_mcp_server`; builtin handlers cannot be clobbered
- **job_run verify write jail:** silent `job_run`/`mission_verify` share `bash_exec` shell write jail + write-roots fail-closed (no bypass via jobs path)
- **Usage ledger session cascade:** `delete_session_events` + purge on session delete/reset so chat wipe drops token/cost rows from `usage.db`
- **Learning loop session_id:** skill nanobot/coordinator pass `session_id` for multi-session ACTIVE promotion; `skill_run` skips double feedback record
- **local_infer prompt caps:** truncate huge prompts/system + clamp `max_tokens` so ranker/router cannot flood llama-server
- **MCP residual tools:** disconnect / reader EOF / rediscover purge `mcp:{server}:*` registrations so dead servers cannot still resolve `call_tool`; pending JSON-RPC futures fail closed
- **Provider sanitize:** outbound scrub uses shared metabolism redaction (Anthropic/OpenRouter/HF/npm/Stripe/Google/JWT/PEM/DB URLs) so tool results match ledger fail-closed policy
- **Time travel restore:** refuse auth/undo paths; skip incomplete (oversized) prior bodies so truncation stubs never rewrite source; API passes `message_id` + timestamp fallback
- **Nanoswarm session residual:** `goal.clear_session` + purge pattern/goal on session delete/reset via `get_swarm` (not only rare runtime attrs)
- **Session cascade wipe:** DELETE session + full reset purge attachments, plans, checkpoints, and undo JSONL so chat delete cannot leave prior file bodies on disk
- **Computer shot TTL:** desktop PNGs age out via `purge_old` / `purge_old_shots` (S-COMP-02); opportunistic sweep after screenshot capture
- **Multi-tab job cancel:** `ComputerJob` stamps `session_id`; abort/cancel only that session’s host jobs so concurrent tabs no longer clobber sibling browser work
- **Plan native arrays + session jail:** `plan_save` accepts native steps/risks arrays; `plan_show`/`step` block cross-session `plan_id`; attachment path jail is session-scoped when `session_id` is set
- **Spread abort + web_search:** `spread_run` cancels remaining waves on turn abort; register `web_search` (DDG HTML + SSRF pin) so plan mode/skills stop missing the tool
- **Batch recovery quality:** soft tool errors / gather exceptions advance fail streaks + recovery telemetry; recovery nudges emit metrics + quality
- **Messenger token redact:** expand scrub shapes (xapp, Discord, Matrix, Bearer); packaged `skill_run` requires `scripts/`, redacts stdout, and is counted
- **Agency metrics API:** `/api/metrics` gains agency rollup
- **Time-travel message_count:** soft-delete (`revert_from`/`revert_message`) resyncs `chat_sessions.message_count` from non-reverted rows; cut on `(created_at, rowid)` so same-second bursts roll back from the chosen message
- **Vision decode model cache:** cache keys include `model_id` + `base_url` so decoder switches do not replay stale briefs
- **Desktop stream HTTP errors:** `formatApiErrorBody` flattens FastAPI validation arrays (not `[object Object]`); empty `{}` bodies fall back to status text; skills import/export + attachment upload share the same flattener
- **Stop & retry provider bind:** stop/retry and promote-queued preserve per-session provider for multi-tab multi-provider
- **Bare CLI group help:** `session`/`skill`/`memory`/… without a subcommand print usage instead of silent no-op (`settings`/`computer` still default show/status)
- **Shell write jail + auth path:** mutations into `~/.remedy/auth/**` / `$REMEDY_HOME/auth/**` refused even when home write roots contain the profile (parity with `resolve_under_roots`); regression covers home-scope Set-Content + relative auth walks
- **SSRF redirect re-validation regression:** `_pinned_fetch` 302 Location to loopback/metadata/userinfo fails closed (`SSRF_BLOCKED_REDIRECT` / `URL_USERINFO_BLOCKED`)
- **Desktop stream abort UX:** cooperative `event:aborted` completes jobs as `aborted` (not done); Stop/interrupt commit job paint with `_[Stopped]_`; `uiCommitted` prevents double-bubble race; skip listMessages wipe after abort
- **Session continuity rebound:** tab switch clears turn scratch (`_turn_tool_steps` / stream accum / mission nudge / evidence inject); session reset drops continuity brief/work-roots cache
- **Assistant privacy re-accept:** `public_status` exposes `consent_ok` / `needs_reaccept` / `current_consent_version`; Settings PA banner + Review & accept when scopes/terms bump
- **Free demo model clamp:** `normalize_llm_settings` / `validate_provider_model` for `demo` snap junk/image/foreign ids to curated allowlist (not flexible like Ollama)
- **L0 skill list names:** `list my skills` uses `manifest.name` (was UUID skill ids); hides auto-learned probation like CLI; sorted human list
- **Desktop stream paint per-job:** tokens/tools accumulate on `streamJobs` paint even when detached; reattach restores partial text + process trail (multi-tab concurrent turns)
- **Partner status session scope:** `GET /api/partner/status?session_id=` scopes lean metabolism + quality to the focused chat tab; StatusBar passes active session
- **CLI skill list docs/tests:** `remedy skill list --all` / `--learned` documented; regression tests for hide/filter
- **Agency re-arm coding verbs:** short stubs for implement/debug/fix/refactor/test re-arm tools (parity with review); false-progress also catches coding narration
- **bash_exec hard blocks with tools on:** dangerous wipe/privilege/host-kill still SECURITY_BLOCK when tools registered (L2/L3); approvals off does not bypass
- **L3 work alone:** bare `work alone` / finish-without-me peers stay L3 with tools + force_spread (not demoted by tools_enabled=False)
- **L0 model/version phrasing:** `what model are you using?` / `what is the version` / `what's the version` classify as L0 instant (local reply, no frontier)
- **metabolism_public_snapshot(lean=):** end-turn + partner status + `/harness` use counters-only (no recent lists / skill·CUA ranking sorts); full snapshot remains on `GET /api/partner/metabolism`
- **Hot path re-arm:** `_rearm_agency_tools` always restores schemas **and** `run_until_done` (agency recovery no longer soft-epoch force-answers mid review/implement)
- **Fingerprint loop patience:** unfinished / `run_until_done` turns get 8 recovery loops before force-answer (was 3 — multi-step builds exited early)
- **Tool pairing look-ahead:** multi-step epoch/re-arm inject between tool results no longer orphans real results (OpenAI 400 guard)
- **Stream concurrency abort:** abort session A leaves session B streaming; registry isolation covered by tests
- **L0 SSE without key:** `/sessions/{id}/messages/stream` no longer hard-errors before `stream_response` — list skills / model / version / whoami work with zero provider key (agent L0 short-circuit)
- **Stream abort:** cooperative `@@aborted` emits `event:aborted` (not error); client disconnect `CancelledError` calls `abort_session` (kills shell + CUA jobs); interrupt path uses `stopStreamJob`; terminal stream jobs are not revived by late `onDone`
- **run_until_done epoch:** tools-armed but never-used turns get the "Use tools now" nudge (was dead code behind always-true `coding_in_flight`)
- **Partner metabolism API:** `GET /api/partner/metabolism` exposes top-level `tier` / `evidence_units` / `decision_units` for Advanced/operator consumers
- **Coding skill catalog:** demote auto-learned tool-chain skills (`file_read-list_dir-…`) in `match_skills` so curated procedures (`write-tests`, `change-safety`, …) win on implement/refactor queries; reject trivial low-diversity traces at learn gate; trivial effort no longer elevates to VALIDATED
- **Agency re-arm coding:** short stubs like "I'll implement/fix/apply/write tests" re-arm tools (same path as skill-promise prose)
- **CLI `remedy skill list`:** hide learned probation by default (`--all` / `--learned`); session CLI remains `start`/`end` only
- **Security sweep regression:** IPv6 ULA/link-local/loopback/mapped-loopback SSRF pin-on-resolve coverage; open_app protocol-detector drive-letter vs `shell:`/`ms-*`/`data:` handlers
- **Skill auto-suggest:** `review project` (and review/coding/ship phrasing) re-ranks the catalog and injects preferred procedure (`change-safety` / `project-etiquette` / `refactor-safe`) into context without waiting for `skill_activate`
- **send_policy dual work:** `begin_turn_metabolism(pre_tier=)` reuses send_policy tier (no second classify walk); autonomous still re-classifies to L3
- **Desktop ErrorBoundary:** Continue (re-mount), Reload, Copy error, component stack — not a dead blank window
- **Agency re-arm:** content-path "Activating skill now" re-arms tools; L2 covers security audit, list files, skill activate phrasing
- **open_app hardening:** refuse URL/protocol handlers (`file:`, `javascript:`, `ms-msdt:`, `http(s):`, …), UNC shares, and shell metacharacters before `cmd start`; only existing files / PATH / simple names; `ms-settings:` only via the `settings` alias; `looks_like_url` no longer treats `file:`/`javascript:` as navigate URLs
- **L2 agency accuracy:** git/VCS verbs, package install/sync, start/stop server, `what files are here`, `find where … defined`, `tail`/`head` logs, CUA scroll/type-into, add unit test / update changelog / bump version — no longer collapse to L1 (tools stripped)
- **Project scan jail:** `/api/projects/scan` resolves under access-scope roots and refuses `auth/**` (was unrestricted absolute-path recon)
- **Media auth refuse:** `/api/media` never serves `~/.remedy/auth` even under the broad home allowlist
- **Catalog custom path:** `safe_path(user, base)` argument order fixed for custom commands/agents
- **a11y secret fields:** scrub `pwd`/`pass`/autocomplete password values, not only `type=password`
- **Navigate userinfo:** empty `https://:@host` blocked via `username is not None` (was truthy-only)
- Shell jail: pathless mutations only when cwd∈roots; bare `$var` paths; IEX/Start-Process/EncodedCommand/archive; certutil `-urlcache` / WebClient / FromBase64 / IRM `-OutFile`; `powershell -e` short form; `fsutil`/`mklink`
- Shadow: all batch paths; relative paths against work roots; opaque payload hard-block (EncodedCommand/IEX/DownloadFile)
- Evidence: slim→inject→mark order; delta-only JSONL persist; per-session metabolism throttle; ContextVar session id
- L0 works without API key; early L0 skips harness; pairing fast-path; crystal hot_block cache
- Redact: Anthropic/OpenRouter/Google/HF/npm/Stripe key shapes; structured+text log formatters
- Action IR / CUA / machine map: strip URL userinfo+query; IR body-less for write tools
- web_fetch SSRF: block userinfo + CGNAT (`not is_global`); UI `@@tool_result` preview scrub; computer audit/host result redact fail-closed
- Identity: HMAC required on import; export+import rate limits; export path jail under `home/exports`
- Atomic writes: skill_genome, cua_macros, xAI auth store
- Perf: precompiled hard/soft/self-kill security patterns; shell jail `_norm_roots` once per check
- Hot path: L2 agency phrasing accuracy; browse/pure-action before lean snapshot; decision tier-on-change; L1 keeps tools when brief tasks open
- Hot path: lean snapshot skips library/pattern/goal on L0/L1 chat; O(1) quality snapshot; governor decision thrash; skip re-offload of handles; precompiled CUA ref regex
- Hot path: project profile mtime cache; tier greeting/path early exits; warm skills catalog; cached OpenAI tool schemas; single SessionQuality handle + `remedy_turn_tier_total`
- Hot path: Action IR steps cap 96; CUA macros `MAX_CUA_MACROS=64` verified; evidence units 240/lean 64 + seen_fps bound; L0 skips full organ snapshots; intent router cache; hot-path debug gated on operator DEBUG
- Hot path: time crystal `MAX_CRYSTAL_FACTS=128` + hot_block rev on hit; skill genome `MAX_PHENOTYPES=128` prune; governor `MAX_GOVERNOR_DECISIONS=40` named; L0 begin_turn skips map/crystal/gov warm; L3 false-positive fixes (`go over` / `step away from` / `review all options` / conceptual compare)
- Hot path: partner memory `MAX_HOT_FACTS=12` / `MAX_HOT_TRAITS=8`; UI `@@tool_result` preview `UI_TOOL_RESULT_PREVIEW_CHARS=8k` (was 500k); drop epoch-roll double `slim_messages_mid_turn`; snapshot reuses send_policy `turn_tier` (no second classify for force_spread)
- Docs: README pytest count ~1353 (collect-only)
- **Desktop usage CSV export:** failure path uses `formatApiErrorBody` for JSON (same FastAPI flatten as skills/attachments); plain-text error bodies still surface
- Gateway: `gateway serve` installs local API token; Teams JWT `aud`/`exp` fail-closed; secret cache `mtime_ns`+size; generic webhook 503 without secret
- Plan mode: exclude `computer_act`; CUA mutations go through Ask approvals; skill learning path jail; MCP env scrub; zip rejects symlinks
- **Plan mode:** drop `computer_act` (and keep allowlist = research tools ∪ `COMPUTER_PLAN_MODE_TOOLS` only — no click/type/app)
- **Approvals:** computer mutation tools (`click`/`type`/`key`/`drag`/`act`/`app`) require Ask approval like shell
- **Learning loop:** skill dir names path-jailed (`is_safe_skill_name` + slug); reflection tool prefix slugified
- **MCP client:** scrub child env of provider secrets; strip `_mcp_server` from tools/call args
- **Zip import:** reject Unix symlink members; **provider sanitize:** redact secret-like computer type payloads
- **Attachments path jail:** client `AttachmentRef` paths must resolve under the attachments tree before inject/vision; SVG refused as vision payload; raster magic-byte check
- **Partner memory:** `force=True` never bypasses secret guard; `memory_fact` refuses credentials
- **open_url:** http(s)-only (blocks `file://` / bare paths); CLI host UI navigate re-validates URL
- **Uninstall wipe:** refuse non-`.remedy` / drive-root / system-path wipe roots; GET attachment uses `relative_to` not `startswith`
- **Auth path jail:** `~/.remedy/auth/**` (and `$REMEDY_HOME/auth/**`) blocked in `resolve_under_roots` even under `access_scope=full`, including symlink/junction resolve into auth
- **Stream/API errors:** SSE `event: error` and LLM provider error bodies redacted before client yield / logs
- **Session export:** portable `.txt`/`.md` export redacts secret-shaped content; import caps size/messages and refuses auth paths
- **open_url userinfo:** refuse `https://user:pass@host` (comment was incomplete)
- Docs: README pytest count ~1174

### Security/perf: metabolism hardening pass

- Shared secret redaction (`metabolism/redact.py`) for ledger, IR, crystal, macros, UI tool args, partner-state previews
- Identity export: HMAC-authenticated packages; export path constrained under `home/exports` when home set
- Action IR: never stores full `file_write`/`bash_exec` bodies (path + content/command hashes only)
- Shell write jail: fail-closed on `-EncodedCommand`, Expand-Archive, tar extract, certutil -decode, BITS
- Hot path: early L0 before harness; evidence inject **before** mark_model_call (delta was empty); no double metabolism inject; mid-turn slim skipped for L0/L1 + cheap char gate; L0 begin_turn skips governor/map/IR
- Evidence parse capped at 16k chars for huge dumps

### Feat: Partner Metabolism OS (speed · accuracy · trust) — full program

Local silent metabolism so any provider model acts like a durable partner —
**one voice**, no multi-agent theater, no Remedy cloud required.

| Capability | What it does |
|------------|----------------|
| **Turn Cost Compiler (L0–L3)** | L0 local answers; L1 lean (tools off); L2 agency; L3 deep/work-alone + force-spread |
| **Evidence Ledger + Decision currency** | EU/DU + waste; mid-turn delta inject; harness + governor consume |
| **Machine Map** | Browser URL/settle, windows, file touches from tools |
| **Shadow rehearsal** | High-blast dry-run before commit (on top of write jail) |
| **Action IR** | Redacted L2/L3 traces under `~/.remedy/action_ir/` |
| **Spread muscle** | Force-spread policy; `spread_run` merge → ledger |
| **Time Crystal** | Multi-horizon; `/pin` → life; secrets never promote |
| **Skill genome** | Ranks on skill_activate/run; protected multi-win |
| **CUA macros** | Successful computer chains → hints (no typed secrets) |
| **Quality Governor** | Stuck/waste/re-explain remedies; compress_earlier lowers harness % |
| **Critical verify** | False-green / secret-risk; next-turn silent remedy |
| **Portable identity** | Encrypted export/import merges memory+crystal+projects — never keys/OAuth/IR |

Operator: `/harness` · `GET /api/partner/metabolism` · F1 **19-metabolism**.

Security: redaction at ledger/IR/export; shadow fail-closed; L0/L1 skip shadow/verify on hot path.

### Fix: only one Remedy at a time

- **Desktop** (Windows): named mutex `Local\RemedyDesktop-SingleInstance` — second
  launch focuses the existing window and exits.
- **API serve**: exclusive `~/.remedy/locks/remedy_serve.lock` so a second
  `remedy serve` / sidecar cannot start while another API is up.

### Feat: default Dark Forest + Dark Purple (alien) theme

- **Default theme** is **Dark Forest** (`forest`) for first run (no saved preference).
- New theme **Dark Purple** (`alien`): deep void base + electric alien-purple accent (`#b026ff`).
- Existing Amethyst / classic Dark unchanged; saved user theme preference still wins.

### Chore: remove product Qwen VLM — SmolVLM2 only

- Local vision / nano / helper catalog is **SmolVLM2 2.2B only** (`smolvlm2-2.2b`).
- Defaults, Setup Wizard, Settings, tools, manuals, and README no longer advertise Qwen2.5-VL 3B.
- Retired config / `vision.json` ids (`qwen2.5-vl-3b`, …) migrate to SmolVLM2 at load time.
- Ollama **chat** model list still may include “Qwen 2.5” (third-party chat models, not the local VLM).

### Security: P0 trust audit fixes (user data + computer-use)

- Browser snapshot **redacts password/OTP/sensitive input values** (`[filled]`) so secrets
  do not flow into tool results → cloud LLM.
- `assistant_brief` requires **`consent_ok`** before loading Gmail/Calendar; mail rows use
  the same clip/redact path as list tools; **consent version** stamped on accept.
- Google OAuth **state is single-use** (PKCE verifier consumed under lock; TTL purge).
- Provider sanitization is **fail-closed** in the ReAct loop (no `suppress` around sanitize).
- Optimistic rail navigate marks `ready_for_input=false`; type/click/page_text wait for settle.
- `/api/computer/a11y/*` is **loopback-only** when API auth is on (not fully public).
- `page_text` default cap lowered (12k → 8k); sanitize fast-path avoids deepcopy for plain messages.

### Security: medium follow-ups (storage + Privacy Shield)

- Google **tokens_encoding** (`dpapi`/`plain`) in public status + Settings warning when plain.
- Privacy Shield: **SHA-256 integrity** for EasyList/EasyPrivacy after download; reject HTML/
  short bodies; **scriptlet inject off** by default (CSS hide only).
- Session `tool_results` **capped + scrubbed** on save; computer job JSON text capped;
  job purge default **15 minutes** (was 1 hour).

### Feat: in-house computer use (browser rail + desktop) — local branch

Work lives on **`feature/computer-use`** (do not ship/push until soak solid).

- Provider-agnostic tools: `computer_screenshot`, `computer_click`, `computer_type`,
  `computer_key`, `computer_scroll`, `computer_navigate`, `computer_windows`, `computer_drag`
- Hybrid router: web/URL → in-app browser; native/desktop hints → OS control
- Desktop path: Win32 capture + SendInput (no vendor computer-use API)
- Browser path: job queue + Desktop host poller → WebView2 navigate/input
- HTTP: `/api/computer/host/*`, `/api/computer/jobs/*`, `POST /api/computer/capture`
- Region capture for browser-rail crops; host reports bounds on hello
- System addendum teaches models the computer tool surface (any provider)
- Desktop: auto-open Browser rail on agent navigate; status **PC host** chip
- `computer_snapshot` + click-by-`ref` (browser eN + desktop window wN)
- `computer_monitors` + screenshot `monitor=` index; Stop cancels jobs / mid-type
- Session abort cancels computer jobs; soak checklist `docs/manual/computer-use-soak.md`
- UIA control-tree snapshot (optional comtypes) → refs c1…; PrintWindow WebView/window capture
- Plan mode: see/navigate/list only (no click/type); agency manual section

### Fix: Plan banner lifecycle (cancel / no sticky done)

- **Cancel plan** on the Plan banner persists `status=cancelled` (no cosmetic Hide).
- **Approve → Build** now writes `approved` via the status API before leaving Plan mode.
- Terminal plans (`done` / `cancelled`) no longer stick as “Plan ready” in Build mode.
- `GET /api/plans/latest?actionable=1` skips terminal plans for the banner.
- `plan_save` / `PlanStore.create`: fresh saves with all-pending steps cannot claim `done`;
  new saves supersede prior draft/approved/active plans in the same session.
- Slash: `/plan cancel` (optional id).

## [0.19.0] - 2026-07-28

### Feat: full parallel multi-provider turns

- Turns no longer hold a **whole-turn** LLM lock — only a short lock while
  resolving credentials into a frozen per-turn binding.
- **ContextVar `LlmBinding`** (`llm_binding.py`): each coroutine keeps its own
  provider / model / base_url / api_key for every HTTP call (Grok + DeepSeek
  can stream at the same time on one runtime).
- **Per-turn plan_mode + tool step traces** via `turn_context` (no shared
  mutable lists across concurrent tabs).
- ReAct loop, `post_chat`, tool batch, checkpoints, and plan-mode tool gate
  read binding / plan mode from context (not `runtime._provider` mid-flight).

### Feat: background concurrent turns (Phase A)

- Switching sessions **no longer aborts** a live turn — work continues in the
  background while you chat elsewhere (e.g. Grok on A, DeepSeek on B).
- Sidebar **busy pulse** on sessions with a live job; toast when a background
  turn finishes/fails.
- Soft concurrent guard: confirm before starting a **3rd** live turn.
- Modules: `sessions/streamJobs`, `useSessionStreamJobs`, `concurrentTurns`.

### Feat: sidebar reorder (↑ / ↓)

- Project folders and sessions support **up/down** reorder (hover arrows).
- Order persists in localStorage (`projectOrder` / `sessionOrder`); pure
  `orderApply` + thin `OrderButtons` (modular, no SQLite migration).
- “No project” stays first; pinned sessions still float to the top of a group.

### Feat: Dark Forest theme

- New theme **Dark Forest** (`forest`): classic Dark layout with muted moss green
  accents — no neon mint (distinct from Emerald).

### Fix: per-session provider+model bind (multi-tab)

- Client sends **`provider` + `model`** on every chat stream (not model alone).
- Server **sticky bind**: session `llm_provider`+`model` pair is not overwritten by
  a lone model id from another tab’s global picker (fixes Grok tab getting
  `deepseek-v4-flash` → HTTP 404 on wrong host).
- Persist provider whenever model is saved; infer provider from model id when
  missing (`session_llm.py`).

### Fix: provider stops after short status snippet

- Mid-task **`ok` / `sure` / `cool`** no longer force tools off when session history
  has open work (was tools=[] → force_answer → one status line and stop).
- Hard social only (`hi` / `thanks` / `bye`) still stays tool-free mid-session.
- Action kicks include **pick up / left off / resume**.
- **False-progress** status lines (“Checking…”, “Picking up…”) re-nudge up to 4×
  and re-enable tools instead of accepting narration as the final answer.

### Fix: stuck provider / DeepSeek DSML hangs

- **Incomplete DSML recovery:** truncated tool markup (e.g. `name="bash_`) no
  longer becomes an executable empty `bash_exec`; nudge for real function
  calls instead of hanging the turn.
- **SSE idle:** default provider stream idle cutover **180s** (was 900s);
  override with `REMEDY_SSE_IDLE_SECONDS` (60–900). Surfaces a short note when
  a model round ends for idle.
- **Desktop stall banner:** after **90s** with no stream activity while a turn
  is live → “Provider quiet” + **Stop** / **Stop & retry** (re-sends last prompt).

### Fix: post-test polish (status leak, archive, sidebar, themes, memory)

- **`@@status` / unknown `@@` control tokens** no longer stream into the chat bubble
  (was showing `@@status:Visual decode…`); routed as progress label instead.
- Stall banner: neutral copy — no DeepSeek branding.
- **Archive** control on the session row (hover; always visible when archived).
- Sidebar: remove false “drag onto folders” claims; ↑↓ reorder + 📁 move; `draggable` off.
- Colored themes (Emerald / Amethyst / Amber / Ocean / Neutral): calmer body text;
  Dark + Light unchanged.
- **Memory panel**: empty search returns recent notes (not blank); excludes system
  heartbeats. Empty-state copy clarifies notes vs partner facts.

### Fix: attach images/files to session (desktop)

- **Root cause vs earlier releases:** composer/API attach path was unchanged
  since 0.10.1; drops stopped reaching the Rust handler after WebviewWindow
  (`label: main`) + OS decorations — drops arrive on **WebviewEvent**, not only
  `WindowEvent`. Also a short-lived async rfd picker deadlocked paperclip.
- Handle OS drops on **both** window and webview drag-drop events (skip browser
  embed). Queue + `file-drop-ready` + poll restored as in 0.10.1.
- Paperclip: **sync** native multi-file picker (like session import) with HTML
  `<input type="file">` fallback; attach allowed while streaming.
- **Re-attach after remove:** drop dedupe was permanent — removing a chip no
  longer blocks picking/dropping the same file again (2s race window only).
- ACL: `pick_attach_files` (+ `open_text_file` for import).

### Fix: tray / minimize restore after OS decorations

- Cache main window handle; Win32 `ShowWindow(SW_RESTORE)` when Show/tray click.
- Fixes “no main window” after hide-to-tray or minimize (logged in 0.18.6 testing).

### Feat: change-safety baked into coding turns

- Bundled skill **`change-safety`** (blast radius before multi-file work).
- **Build / tool / autonomous** intent packs inject a standing change-safety
  snippet and suggest `skill_activate`.
- **`project-etiquette` v1.1** gate 0 = blast radius; pairs with change-safety.

### Perf: coding tool-batch guidance (speed without losing agency)

- System + Build intent: **batch many independent reads in one step** (4–12),
  avoid re-reading paths already returned this turn, scope to named subsystems.
- **One soft speed nudge** per turn after 3 consecutive single explore tools
  (`file_read` / `list_dir` / `repo_search`) — does not force-answer or cap tools.

## [0.18.6] - 2026-07-27

### Fix: OS window chrome (end WebView title-bar hit-test loop)

- **Min / max / close are OS decorations** again — never fake buttons inside WebView2.
- In-app strip is logo menu + version only (no CSS drag regions, no sticky hit-tests).
- Ends the recurring “title bar dead after move/maximize” class of bugs.

### Fix: built-in Browser embed

- Auto-load homepage when the Browser rail has size; wait for layout before navigate.
- Recreate stale child webview; about:blank then navigate; delayed re-paint.
- Capability includes `remedy-browser-embed`; clearer errors + system-browser fallback.

## [0.18.5] - 2026-07-27

### Fix: Telegram poller stuck after restart (false “live” PID)

- **Windows PID liveness:** use `GetExitCodeProcess` / `STILL_ACTIVE` so a *dead*
  process that still has an open kernel object no longer holds the poll lock forever.
- **Heartbeat + stale reclaim (90s):** crashed pollers free the bot without a reboot.
- **Retry every 20s** if long-poll was deferred at startup (second instance / stale lock).
- Symptom fixed: Settings shows Telegram enabled but no inbound until full reinstall.

## [0.18.4] - 2026-07-27

### Fix: messenger realtime / Telegram↔desktop sync

- **Exclusive poll lock** (`~/.remedy/locks/telegram_getupdates.lock`) so only one
  Remedy process long-polls the bot (stops HTTP 409 thrash / “realtime dead”).
- **Persist Telegram update offset** so restarts do not re-flood backlog catch-up.
- **First-run backlog drain** without replaying into sessions when no offset exists.
- **Desktop→Telegram mirror:** assistant replies in `msg:telegram:…` sessions are
  sent back to the remote chat (was inbound-only).
- **SSE sync:** do not force-reload the active thread while a local stream is live
  (avoids fighting partial text / “stuck catching up” feel).

### Fix: concurrent session LLM isolation + stream tracking

- **LLM turn lock:** streams serialize provider bind for the whole turn (no mid-stream
  host/key races across tabs/messengers).
- **Per-session streaming set** (not one global `_streaming` bool) — switch provider on
  tab B while A streams is allowed; only *this* session blocks on 409.
- **Chat turns use `llm_only` sync** — do not thrash approval/project/harness from
  config mid concurrent work.
- Session LLM bind happens **under** the lock inside `stream_response`.

### Fix: force-answer nudge once; messenger/legacy stream honor session LLM

- Do not append “Stop calling tools…” every ReAct step (context bloat / stuck feel).
- Restore full provider+model+key+url after each stream turn (safer concurrent sessions).
- Messenger + legacy chat stream apply session `llm_provider` like desktop SSE.
- Fatal: “supported API model names are …” (wrong host) hard-stops.

## [0.18.3] - 2026-07-27

### Fix: session provider switch, quit prefs, launch update check, console flash

- **Status-bar provider switch:** each chat turn uses the session’s provider + key +
  base URL (not model name alone on the old host — fixes DeepSeek 400 with `grok-4.5`).
- **Missing model / 404:** hard-stop with “switch model” instead of soft-retry spam.
- **Quit warning “Don’t show again”:** prefs saved to disk **before** process exit.
- **Update check on launch:** one check ~2s after server ready (plus 30m interval).
- **Windows console flash:** hidden spawn for `rg`/search/spread and direct `git` diff jobs.
- **Docs / What's new:** owner notes for 0.18.1–0.18.3.

## [0.18.2] - 2026-07-27

### Fix: spread_run accepts native task arrays

- **`spread_run`:** models pass `tasks` as a JSON **array** via tool_calls; the handler
  no longer assumes a string and crashes with `'list' object has no attribute 'strip'`.
- Accepts list, single object, or JSON string; schema documents `anyOf` array|string.
- Regression tests for list-arg path.

## [0.18.1] - 2026-07-27

### Fix: run-until-finished agency (no tool-limit stop) + title bar after move

- **ReAct multi-epoch:** soft epochs only checkpoint + compact context; tools stay on
  for coding / mission / open work. Absolute step ceiling is a pathological-loop
  safety net (~10k), not a “finish soon” budget — same class as a long Build session.
- **No bare tool-limit dead-end:** fingerprint loops nudge different actions instead of
  permanently stripping tools; idle pause only after many epochs with zero tool activity.
- **Prompts / work-alone:** explicit “run until finished”; epochs are not a stop signal.
- **Desktop title bar:** min / max / close no longer die after move/maximize — drag uses
  explicit `startDragging` (no sticky CSS `data-tauri-drag-region`); controls isolated
  with `no-drag` + z-index; geometry events resync chrome hit-testing.
- **Docs:** agency manual “Run until finished”; desktop title-bar developer note; help wiki sync.

## [0.18.0] - 2026-07-27

### Agency: silent spread (fan-out) + hardening

- **`spread_run`:** parallel silent workers (explore/search/verify/diff/review) return one merged digest — cover more ground without multi-agent theater.
- **Spread planner:** heuristics (+ optional local Qwen refine when llama-server already up) inject `[Spread]` continuity hints when work is partitionable.
- **Hardening:** path-jail fail-closed on `repo_search` / jobs; verify/job shell uses same Ask approvals as `bash_exec`; `APPROVAL_REQUIRED` treated as tool error; gateway skips heartbeat memory rows; skill bash env scrub; MemoryStore lock on profile/session writes; desktop interrupt/session-switch calls `abortSession`.
- **Snappy abort:** session stop kills in-flight shell process trees; approvals use turn ContextVar session id; vision.json mtime-cached; harness skips pre_prune copy when no compress.

### Skills: library skill check (soft suggest)

- Cache-only rank of signed Skills Library index on tool-ish turns; at most one Install tip (never auto-install without a click).
- Continuity `[Library]` note + SSE chip with **Install** (download + Trust for speed) and Library browse; dismiss suppress per session.
- Speculative prep refreshes catalog in background; optional `library_rerank` local job (off by default).

## [0.17.0] - 2026-07-27

### Feature: Language-agnostic coding agency

- **`repo_search`:** no exclusive file-extension allowlist — content sniff + bundled/system **ripgrep** (MIT/Unlicense, pin 15.2.0 under `third_party/ripgrep/`). Finds GDScript, Zig, Makefiles, etc. Engine reported as `bundled-rg` | `rg` | `python`.
- **Focus folder optional:** absolute multi-tree paths work anywhere in access scope; relative paths still resolve from default cwd.
- **Orientation + stack fingerprint:** `AGENTS.md` / handoff pointers and verify hints (Godot smoke scripts, pytest, cargo, …) for focus and session work roots.
- **Tools:** `bash_exec` `timeout_seconds` / `workdir` + local PATH; multi-hunk `file_edit` + `file_edit_batch`; `job_run` explore/verify/diff; `symbol=` and context lines on search.
- **Resilience:** empty-search / NOT_FOUND recovery nudges; same-path write locks; mission verify gate + git after verify; Windows reserved-name guards; non-blocking `schedule_ensure_rg`.
- **Docs:** agency manual + THIRD_PARTY for ripgrep.

### UI: Process trail (Min / Med / Full)

- **No double trail:** live progress bar without a second chip cloud when Process is shown.
- **Min:** header counts + compact grouped chips + “+N earlier”.
- **Med:** grouped runs, path one-liner, expand for short result.
- **Full:** every step with complete args/results; taller live viewport.

## [0.16.0] - 2026-07-27

### Feature: Messengers as first-class connectors

- **Settings → Messengers:** modular adapters for Telegram (live long-poll), Discord, Slack, Mattermost, Matrix, WhatsApp, Teams, Google Chat, and Signal — expandable rows, secret-store tokens (`ch:{channel}:{field}`), status ready/partial/planned.
- **Session continuity:** messenger chats map into desktop sessions (`msg:{channel}:{id}`); SSE session events keep the UI in sync; history load uses stable rowid SQL (no 500 on long threads).
- **Gateway:** Telegram poll starts on uvicorn lifespan (not a dead `asyncio.run`); hot-reload after settings save; turn abort + per-turn workspace ContextVars for concurrent streams.
- **Hardening:** webhook auth, path jail for `/api/files` (no CWD fallback on `..`), review fixes across abort/history/skills/desktop.

### UI / desktop polish

- Title bar monogram / full-height wordmark iterations; empty-state monogram only (larger, unframed).
- Skills Library smart refresh; Installed | Library tabs sit below the title bar (not clipped).
- Calm Memory Progress language; status bar shows **Memory** without checkpoint noise.
- Sidecar console noise hidden (`CREATE_NO_WINDOW` without `DETACHED_PROCESS`).

### WebUI & docs

- WebUI prefers `desktop/dist` over stale staged `webui/`; agent notes document the desync pitfall.
- README / manuals: TOC, product showcase (Files · Terminal · Browser · Scratch · messengers · local Qwen · nanoswarm), GitHub-hosted owner manual links, download points to **latest** (no version pin).
- Community skills catalog rebuilt and re-signed.

### Tests

- Suite **870** tests green; ruff/mypy clean.

## [0.15.9] - 2026-07-27

### Fix: Skills Library tab visibility + first-session chat hang

- **Skills tabs:** full-width segmented control (**My skills** | **Library**) so Library is obvious after auto-update.
- **First chat:** wait for session list after server ready; ensure model from settings before stream; surface createSession failures; re-bootstrap API token on 401 stream (post-update).

## [0.15.8] - 2026-07-27

### Feature: Skills Library (signed catalog) + Skills panel polish

- **Skills Library:** browse the public signed catalog (`AhmiDarrow/remedy-skills`), install into quarantine, Trust to activate. Client verifies Ed25519 catalog signatures and SHA-256 skill zips; downloads limited to this project’s GitHub release assets.
- **Desktop:** Skills panel **Installed / Library** tabs; compact Trust / Promote / Quarantine / Archive / Edit / Delete; Library shows install/update state.
- **API:** `GET/POST /api/skills/library/*` (catalog, search, install, updates, submit validation); `DELETE /api/skills/{name}` for user/library packs under `~/.remedy/skills/`.
- **Hardening:** final-URL allowlist, download size caps, safe skill names, force-update replaces (no `name-imported`), `/api/skills/packs` no longer shadowed by `{name}`, activate/run respect disabled/archived, library packs protected from learning merge, catalog signing key rotated (seed not in tree).
- **Library content:** 280+ brand-free workflow packs + deep **Godot 4.7.1** and **PixelLab** skills; auto-updating SKILLS.md / README list generators; CI attaches catalog + zips on release.
- **Docs:** skills manual + Help wiki updated; suite **842** tests.

## [0.15.7] - 2026-07-26

### Feature: Memory Harness v2 + continuity harden

- **Send-view enforce:** Auto harness applies soft/strong lean prune (token budget, outcome-aware tool collapse, offload) without rewriting stored chat.
- **Session Brief:** decisions with *why*, cumulative `history_thread`, auto-create on first compress; local Qwen `brief_update` jobs (non-blocking) via shared llama-server queue.
- **Quality gate:** fail-closed when no extractable facts; score pre-prune history; middle-history replace only when brief is solid.
- **Mid-turn re-slim:** re-prune between ReAct steps when context fill is high.
- **Desktop:** Plan mode per session; single Browser rail (no dual WebView2); process Min/Med/Full (Full+ removed); tool results match by `call_id`; scratch flush on session switch; Process panel rebinds collapse to mode.
- **Docs:** Memory Harness manual updated for enforce + local co-pilot.
- **Tests:** harness send policy / quality / offload coverage; suite 833.

## [0.15.6] - 2026-07-26

### Fix: images always display in chat + smoother stream finish

- **Attachments:** image files are stored/shown as markdown `![…](path)` so chat renders previews for **every** model (display is independent of provider vision).
- **Optimistic UI:** send bubble shows image previews immediately; stream finish promotes the assistant reply without a blank gap before `listMessages` returns.
- **Legacy messages:** backtick-wrapped attachment paths are linkified into images; media API always allows `~/.remedy` attachment roots.
- **Tests:** attachment markdown embed + linkify backtick unwrap.

## [0.15.5] - 2026-07-26

### Fix/Feature: popout exit chrome, embedded browser, browser homepage

- **Fullscreen/popout:** always-visible exit bar (Exit fullscreen / Close) + **Esc** for Terminal, Browser, and Scratch; portal overlay so chrome is never covered.
- **Browser:** native WebView2 embed stays inside the slide host (not over OS chrome); rail unmounts when popout so bounds/PTY are not dual-mounted; homepage defaults to **https://github.com/AhmiDarrow/RemedyAI**.
- **Settings → Project workspace:** **Browser homepage** (`browser_home_url`) — user can change the in-app Browser ⌂ target.
- **Terminal:** reliable ConPTY PowerShell path; Esc not swallowed by xterm.
- **Quit / shell:** tray quit force-exit; denser sessions; settings stay right-rail without killing chat; native window decorations.
- **Tests:** browser home normalizer; full suite green.

## [0.15.4] - 2026-07-26

### Fix: window chrome, chat shell, sessions, browser, rails

- **Title bar:** min / max / close reliable (dedicated drag region; primary window lookup; close-to-tray).
- **Chat:** composer pinned to bottom; empty-session landing restored; token/cost ticker above composer.
- **Sessions:** remove open-tab chips; force-load history on session select; project Browse… on UI thread.
- **Browser:** embed in slide (iframe + external open); no blank WebView popup.
- **Terminal:** block blinking cursor; click-to-focus.
- **Rails:** thin → icons → open on left and right.
- **About / docs:** “My name is Ahmi, I hope you enjoy my Remedy.”

## [0.15.3] - 2026-07-26

### Fix/Feature: shell layout, in-app terminal/browser, New Project, icons, images

- **Shell:** true Left | Chat | Right; outer icon rails; right collapsed by default; swap in side headers (layout prefs v2).
- **Terminal:** in-app PowerShell via ConPTY + xterm (auto-start); ACL for PTY commands.
- **Browser:** in-app WebView2 window (not iframe); single URL chrome.
- **Projects:** first-run only may seed `Documents/Remedy Projects/New Project` in config; **New Session = root** (no project) unless the user attaches one.
- **Icons:** tray/taskbar/Start from `assets/remedy_icon_original.png`.
- **Images:** bare path autolink to ChatImage; media normalize via Pillow; less flicker.
- **Files:** open path, copy path, drag-to-chat; follows session project.
- **Tests:** media attachments + Pillow normalize; New Session root contract; linkify paths; layout v2.

## [0.15.2] - 2026-07-26

### Fix: workspace harden, plan banner after approve, browser URL safety

- **Workspace layout:** coerce unknown slide ids from localStorage so `SLIDE_META` never crashes.
- **Plan banner:** keep last plan after Approve -> Build (Plan ready) until Hide/session change; poll only in Plan mode.
- **Browser:** shared URL normalizer blocks javascript/data/file schemes; bare hosts still get https://.
- **Scratch:** debounce localStorage writes while typing; flush on Save/Clear.
- **Tests:** browserUrl, archive-days default, invalid layout slides, expanded plan-mode allowlist.

## [0.15.1] - 2026-07-26

### Fix: workspace polish, archive days bug, quieter plan banner

- **Auto-archive:** missing localStorage key no longer disables age-based archive (Number(null)===0 bug).
- **Plan banner:** poll only in Plan mode (8s); clear when leaving Plan.
- **Settings:** no dual mount when slide embeds Settings.
- **Terminal:** prefer PowerShell (pwsh -> Windows PowerShell) in project cwd.
- **Browser / Scratch:** Firefox-prefer external open; scratch preview/save/clear.
- **Tests:** sessionMeta archive rules, layoutPrefs, plan research tools.

## [0.15.0] — 2026-07-26

### Feature: three-frame workspace, plan mode, image markup, sessions scale

- **Workspace:** left/right swappable slides (Sessions, Settings, Files, Terminal, Browser, Scratch) with popout; chat is the middle frame.
- **Image viewer:** fixed WebView load (no crossOrigin on blob); hover Edit/Copy/Save; Save & attach closes editor onto the prompt.
- **Icons:** rounded-plate shell icons; chat assistant uses partner initials; empty session keeps monogram.
- **Sessions:** open tabs only in Sessions slide; rule-based archive (30d) + Archive filter.
- **Project picker:** rfd (no PowerShell lag); Add project sticky above list.
- **Plan mode:** read/search tools allowed; plan banner (Approve → Build / Request changes).

## [0.14.10] — 2026-07-26

### Feature: session image viewer + Snipping-Tool markup

- Click any chat image to open a full-screen viewer (zoom, download).
- Markup tools: pen, highlighter, arrow, rectangle, text — colors + stroke sizes.
- **Attach markup to message** exports annotated PNG onto the composer attachment rail so you can point things out to Remedy.
- Attachment chips also open the same viewer; docs in chat manual.

## [0.14.9] — 2026-07-26

### Fix: chat/tray/taskbar icons + faster export/import

- **Chat monogram:** true-alpha theme-aware icons (icon-mono-light / icon-mono-dark) with cache-bust; no baked black avatar tile.
- **Tray:** bold dark-plate glyph + gold rim; iconAsTemplate off (was nearly invisible).
- **Taskbar:** regenerated alpha ICO/PNG masters; window icon re-applied at launch.
- **Export/import:** native 
fd dialogs (no PowerShell cold-start); tool dumps aggressively capped; import reads file in Rust.
- **Defender Bearfoos:** docs note update-path ML scans + WDSI submission; PE identity/minisign unchanged. Authenticode still the long-term fix.

## [0.14.8] — 2026-07-25

### Docs: project etiquette ship skill

- New bundled skill **project-etiquette**: portable gate chain
  Fix → Test → Update project → Update docs → Build → Commit → CI green → Publish.
- Root **AGENTS.md** documents the same sequence as default ship protocol.
- Manuals list the skill; Remedy appendix maps gates to this repo's commands.

## [0.14.7] — 2026-07-26

### Fix: update UX calm + install must start before exit

- **In-app update copy:** one message path — download, then “Restarting to finish
  install” (no triple “closing / another popup” spam).
- **Install host copy:** short “Updating Remedy…” status; less serious language.
- **Hard gate:** app does **not** exit until the install script logs `BOOT` /
  `Update script started` (retry schedule once). Prevents download-done → silent
  death with nothing coming back.

## [0.14.6] — 2026-07-26

### Fix: autoupdate install never ran after download + alpha logos everywhere

- **Autoupdate (0.14.4→0.14.5 failure mode):** after download, install script
  could die with the Tauri Job Object (status stuck at `closing`, no log lines).
  Now schedules install via **three paths**: breakaway PowerShell, **WScript.Shell**
  launch, and a **one-shot schtasks** run; longer post-schedule delay before exit;
  BOOT line in update log proves the script started.
- **Branding:** `setup_branding.py` regenerates Tauri icons + public logo/icon
  favicons from true-alpha masters (preserve wordmark aspect; mono light/dark
  variants in `public/`). Syncs into `desktop/dist` for local WebUI.

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

### Feature: Diff colors in chat

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

### xAI OAuth + API key (dual auth)

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
