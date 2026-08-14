# RemedyAI (Old-Remedy) — full project review

**Date:** 2026-08-13  
**Tree:** `C:\Users\Administrator\Old-Remedy`  
**Product:** RemedyAI v0.23.2 (local `master` is 3 commits past origin `v0.23.2`)  
**Method:** Four specialist read-only reviews (security, architecture, desktop, quality) plus orchestrator verification of the highest-severity claims against current source. No product code was changed.

Specialist notes:

- [docs/_review_security.md](docs/_review_security.md)
- [docs/_review_architecture.md](docs/_review_architecture.md)
- [docs/_review_desktop.md](docs/_review_desktop.md)
- [docs/_review_quality.md](docs/_review_quality.md)

---

## Verdict

Remedy is a real product, not a chat wrapper: FastAPI + Tauri + Vite SPA, ~124k lines of Python, ~40k of TypeScript, ~7.5k of Rust, ~193 pytest modules, and a Windows-first desktop installer. Security work since the July 2026 audits is genuine — the worst prior holes (runtime-bin dest skip, uninstaller removing PyPI `remedy`, abort dropping the stream claim, Stop not persisting, Teams JWT unsigned, computer host without Bearer) are **fixed in this tree**.

It is also overgrown. The hot path is one ~3391-line ReAct loop plus process-global leftover flags. The shell write jail is still a regex extractor, so two project-bound bypasses remain. Desktop Stop/session races live in two god hooks. CI is Linux-heavy for a Windows-first agent.

**Ship posture:** do not publish this HEAD as 0.23.2. Close the two jail extraction bugs and the Stop-drain session mixup before the next tag. The rest can land as a 0.23.3 / 0.24 hardening pass.

| Domain | Grade | One line |
|--------|-------|----------|
| Local API auth / secrets | **B+** | Bearer default, DPAPI, CORS `*` refused |
| Tool / shell jail | **B−** | File jail is strong; shell dest extraction still leaks |
| SSRF / skills / updates | **A−** | Pin-on-resolve, signed catalog, minisign |
| Agent / session isolation | **B−** | Claim/ContextVar done; runtime flags not |
| Desktop / Tauri | **B** | Tray + multi-tab jobs solid; Stop/load races remain |
| Tests / CI | **B** | Many real tests; Windows job misses jail + CUA |
| Maintainability | **C+** | One real loop, too many organs, god files |

No unauthenticated remote RCE was found on the default `127.0.0.1` + auth-on path.

---

## What this product actually is

```
You → Continuity (brief · memory · skills · budget) → your model → tools
              ↑______________ learn / compress / remember ______________|
```

- **Runtime:** `src/remedy` (391 modules). `BasicRuntime` orchestrates; `react_loop/loop.py` is the only live ReAct stream.
- **API:** FastAPI on `127.0.0.1:7400`, Bearer by default, desktop sidecar + WebUI SPA.
- **Desktop:** Tauri 2 + WebView2. Close → tray. Computer-use host + PTY + browser rail in Rust.
- **Memory:** SQLite under `~/.remedy` (plaintext by design). Secrets in DPAPI.
- **Metabolism / nanoswarm:** L0–L3, evidence, governor are live. Organism/soul pulse is prompt flavor. NanoToken + pattern/skill/health are wired; router/memory bots are not dispatched in production.

Sibling path `C:\Users\Administrator\Remedy` is a **different product**. Do not merge them.

---

## Prior bugsweep vs current code

| Prior claim | Now |
|-------------|-----|
| Runtime dest skip (`python_pwned.txt`, `cmd.exe`) | **Fixed.** Helper unused in jail; dests after argv[0] are checked. |
| `remedy uninstall` removes PyPI `remedy` | **Fixed.** Only `remedy-ai`. |
| Stop / `CancelledError` drops assistant row | **Fixed** on desktop SSE. Still open on messenger cancel. |
| `abort_session` drops stream claim | **Fixed.** Claim held until `finally`; epoch-guarded. |
| Computer host unauthenticated | **Fixed.** Bearer required; a11y is loopback + 32-char `job_id`. |
| Teams JWT claims-only | **Fixed.** RS256 + JWKS. |
| Rust `normalize_url` accepts any IPv4 | **Mostly fixed.** Loopback + RFC1918 only. IPv6-mapped IMDS still open. |

---

## Top issues (verified)

Severity here is product language: **bug** = correctness/security/breakage, **suggestion** = important residual, **nit** = hygiene.

### Security

1. **bug** `shell_write_jail.py:278` — `_ABS_PATH_RE` only matches `C:\…`, not `C:/…`. Project-bound `echo pwn > C:/Users/Public/pwn.txt` and mixed `python -c` decoys take the “no proven path + cwd in-root → allow” branch.
2. **bug** `shell_write_jail.py:58` — `$env:USERPROFILE` is opaque; PowerShell `$HOME` / `$USERPROFILE` are not. `'pwn' > "$HOME\Desktop\pwn.txt"` slips both the jail and `host_script` source scan.
3. **bug** `browser_host.rs:637` — Rust URL gate lags Python: userinfo, `metadata.*` labels, `[::ffff:169.254.169.254]`.
4. **suggestion** Teams JWKS: any `https://` `jwks_uri` from the OpenID doc is trusted; no host allowlist.
5. **suggestion** Catalog HTTP follows redirects without a final-URL check (SSRF-read; signature still blocks install).

### Agent / isolation

6. **bug** `react_loop/loop.py:292` — `_force_tool_choice`, `_turn_tier`, `_action_ir`, `_shadow_strict` still live on the process singleton. Tab A’s unfinished-work drive can force Tab B’s next POST to `tool_choice=required`.
7. **bug** `agent_react_preamble.py:510` — build protocol is consumed *before* `begin_build_turn`, so it injects into whichever session starts next.
8. **bug** `agent_computer_tools.py` + `executor.py` — `async` wrappers call `time.sleep` on the asyncio loop. One `computer_wait` freezes sibling SSE, abort, messengers.
9. **bug** `executor.py:31` — singleton `_active_session_id` + process-global navigate-settle (latent until Issue 8 is fixed).
10. **bug** `react_loop/loop.py:723` — abort checked only between ReAct steps, not mid-SSE (up to 900s sock_read).
11. **bug** `gateway/session_bridge.py:289` — messenger `except Exception` misses `CancelledError`; assistant row never persists.

### Desktop

12. **bug** `useMessages.ts:1113` — Stop `finally` drains `sessionIdRef.current` (focused tab), not the stopped session. Switch during Stop can start B’s queue.
13. **bug** `lib.rs:3646` — single-instance reclaim `taskkill /F /IM app.exe` (generic image; pre-0.23.2 leftover).
14. **bug** `useMessages.ts:249` — session-switch `load()` has no generation token; a stale `listMessages` can wipe a just-finished assistant bubble.
15. **suggestion** Stop still abort-fetch-first (`streamJobs.ts:306`). Persist works now; invert to `POST /abort` then abort fetch.

### Quality / CI

16. **bug** `tests/test_e2e_simple_c_rmb.py:69` — live e2e writes the real `~/.remedy` memory DB.
17. **bug** Several `test_computer_use.py` cases `return` on Linux (vacuous pass); Windows CI does not run write-jail or computer-use files.
18. **suggestion** CHANGELOG `[Unreleased]` empty while local master is 3 commits past tagged 0.23.2.
19. **suggestion** mypy excludes the ReAct loop and all HTTP routes; CI “type-check” does not cover the hot path.
20. **suggestion** `Users/` and `~/` at repo root are not gitignored (`git add .` would stage them).

---

## What is strong (keep)

- Bearer + constant-time compare; CORS `*` refused when auth is on; loopback bind; HTTP bootstrap off for packaged sidecar.
- Provider keys + local API token DPAPI-sealed; `config.toml` scrubbed of secrets.
- File write jail + protected `~/.remedy/auth/**` even at `access_scope=full`.
- `web_fetch` pin-on-resolve, fail-closed DNS, redirect revalidation.
- Signed skills catalog, zip-slip/bomb limits, quarantine until Trust.
- Stream claim/epoch, ContextVar session/LLM bind/workspace, per-session CUA cancel.
- Close-to-tray, per-session stream job registry, token in renderer memory (not localStorage).
- Version surfaces aligned at 0.23.2; `scripts/sync_version.py` + `check_docs.py` are real gates.
- ~1991 collected tests; jail/auth/SSRF/secret-store tests assert outcomes, not `assert True`.

---

## Architecture truth (real vs ornamental)

**On the hot path:** `BasicRuntime` → `stream_response` → L0 or `call_llm_stream` → tool batch → persist. Memory harness, partner memory, L0/L3 tiers, evidence, build engine, NanoToken.

**Mostly prompt / telemetry:** organism pulse, soul mood, skill “genome”, nanoswarm router/memory bots (`message_added` never dispatched from production).

**Already split, do not fork again:** `agent_react_loop.py` is a shim. `react_turn.py` is helpers. Next work is moving leftover `runtime._*` onto `TurnState` / ContextVars — not a third loop.

---

## By-design residuals (not defects)

- Same Windows user owns loopback, bootstrap, DPAPI, and plaintext `memory.db`.
- No project folder ⇒ `access_scope=full` (shell/file jails off except auth tree).
- Shell is a **write** jail, not an execution jail. `python game.py` inside the project can then write anywhere.
- Messenger `allow_all` turns that platform into a remote agent console.
- Cloud LLM egress: secret-regex sanitize always on; PII privacy mode is opt-in.
- Unattended self-inject defaults **on** after 300s idle (path jail + test gate + rollback exist; packaged default should be off).

---

## Recommended next sequence

1. Fix jail Issues 1–2 + add those vectors to `test_project_write_jail.py` / `test_shell_jail_fuzz.py`. Run them on the Windows CI job.
2. Capture `stoppedSid` in Stop drain; add `loadGenRef` on session switch.
3. Stop `taskkill /IM app.exe`; reclaim by recorded PID only.
4. Move `_force_tool_choice` / `_turn_tier` / `_action_ir` / build-protocol onto turn-local state.
5. `asyncio.to_thread` computer `run()`; check abort during SSE read.
6. Messenger `CancelledError` persist (copy desktop path).
7. Default self-inject off for packaged builds.
8. Then version-bump; do not retag 0.23.2 from this HEAD.

---

## Size snapshot

| Tree | Files | Lines |
|------|------:|------:|
| `src/remedy` Python | 391 | 124,040 |
| `tests` Python | 195 | 37,297 |
| `desktop/src` TS/TSX | 168 | 40,210 |
| `desktop/src-tauri/src` Rust | 5 | 7,517 |

Largest modules: `runtime/rmb/service.py` (3538), `react_loop/loop.py` (3391), `App.tsx` (1951), `react_policy.py` (1913), `computer/executor.py` (1581).
