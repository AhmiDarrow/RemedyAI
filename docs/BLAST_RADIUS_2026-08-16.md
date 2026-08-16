# Blast-radius + review report — 2026-08-16

Change-safety pass (AGENTS.md §"Change-safety protocol") over the life-task P0,
Remedy Vault, voice, and Grove work, plus the bug fixes from an adversarial
review. Local-only file (`docs/AUDIT_*`/`BLAST_*` excluded from the public tree).

## 1. What changed (this review's fixes)

Adversarial review (two independent reviewers, findings repro'd) surfaced two
**P0 security** issues and several P1/P2s. All confirmed items fixed:

### Security (P0)

1. **Vault bound to a stale URL → cross-origin secret exfil.** `_expand_vault_text`
   bound domain-locked secrets to the *last explicit navigate*, not the live
   page. A click/redirect/SSO hop to an attacker origin would still decrypt an
   `amazon.com`-bound card. **Fix:** bind to a fresh `_page_probe()` of the
   actual page; fail closed (refuse) when the page can't be confirmed and a
   bound item is involved. `executor.py:_expand_vault_text` + 3 callsites.
   Tests: `test_executor_binds_to_live_page_not_last_navigate`,
   `test_executor_fails_closed_when_page_unknown`.

2. **Owner checkpoint replayable cross-site.** A resolved sensitive approval
   recorded a session fingerprint; `computer_click`/`type` summaries carry no
   URL, so one approved "Place order" silently authorized the same action on
   any later/other page. **Fix:** sensitive approvals now use a **one-shot
   grant** consumed on the next matching call — never a persisted
   session/always fingerprint. `approvals.py` (`_one_shot`, `take_one_shot`,
   `resolve`), `agent_computer_tools.py` gate. Tests:
   `test_sensitive_approval_is_one_shot_not_persisted`,
   `test_sensitive_grant_does_not_replay_cross_site`.

### Correctness / quality (P1)

3. **Payment checkpoint was text-match-only** (ref/coordinate clicks bypassed
   it). **Fix:** resolve `ref=` → element label from the last snapshot in the
   click summary so the classifier sees "Place order" on snapshot clicks
   (`_resolve_ref_label`).
4. **Commerce verbs broke wiki/search open-only kicks** ("show me the jungle
   book wiki", nested Google search). **Fix:** `_INTERACTION_RE` rewritten to
   verb-phrases only (bare `book/order/cart/post` dropped; "order of" / "in
   order to" excluded). Regression test added.
5. **`speakable_text` ReDoS** (8.4s on adversarial brackets). **Fix:** truncate
   input before regex work.
6. **Dual `BrowserSlide` mount** (Grove stage + hidden Studio rail fighting the
   native embed's bounds → flicker/hidden). **Fix:** Studio rail slides render
   only when `surface === 'studio'`.
7. **Home talkbar created two sessions** (pre-create + `handleSend`
   self-provision). **Fix:** removed the pre-create.
8. **`useVoice.stopRecording` could hang forever** if `rec.stop()` threw. **Fix:**
   resolve on inactive/catch + 4s timeout.
9. **Goal-room send race / double-create.** **Fix:** in-flight promise map per
   goal id; `sendFromGrove` awaits it before sending.
10. **ApprovalBanner ignored the backend's `summary`/`sensitive`.** **Fix:**
    banner now leads with the plain-language summary, badges payment steps,
    demotes raw command to a details expander.

### Polish (P2)

Secret-length leaks (report "a stored secret" not char count), vault error
target/action labels, stale-open job scrub for id≠filename files, TTS install
lock (concurrent-download corruption), speak() overlap/URL-leak + `getVoices()`
async fallback + markdown strip for browser TTS, storyline autoscroll,
plantGoal keeps the draft on failure, Grove aria (`aria-pressed`, live region),
Studio hotkeys gated in Grove, `prefers-reduced-motion` honored.

## 2. Surfaces classified (primary → neighbors)

| Surface | Files touched | High-risk neighbors — checked |
|---------|---------------|-------------------------------|
| **Chat / tools / computer-use** | `executor.py`, `browse_intent.py`, `agent_computer_tools.py`, `host_bridge.py`, `desktop_win.py` | Concurrent streams, session bind, messenger turns — **unaffected**: no session/stream state touched; vault/probe changes are per-call. Regex change re-verified against open-only + search + wiki + login flows. |
| **Approvals / trust** | `approvals.py` | Coding flow (`bash_exec`/`file_write`) — **verified untouched**: sensitive tier early-returns for non-`computer_*` tools before any config load; `is_approved`/`set_mode` paths unchanged for non-sensitive. New `_one_shot` is additive. |
| **Desktop shell / chrome** | `App.tsx`, `StatusBar.tsx` | Windows tray vs Linux taskbar, window controls — **untouched**. Grove render guard only gates rail *children*; WorkspaceSide/tray/close logic unchanged. |
| **Workspace rails** | `App.tsx` (render guard) | WebView2 bounds, dual PTY — **the fix removes** a dual-mount hazard (Grove no longer double-mounts Browser/Terminal). |
| **Settings / secrets** | `vault.py`, `voice/service.py`, `routes/voice.py` | Secret store, provider switch — vault reuses `secret_store` DPAPI/harden; voice is self-contained under `~/.remedy/voice`. No change to provider-key paths. |
| **Grove (new default surface)** | `grove/*`, `api/voice.ts`, `voice/*`, `api/partner.ts` | ApprovalBanner shared with Studio — extended additively (new optional fields; Studio still renders). |
| **Docs** | CHANGELOG (prior commits) | Version bump **not** required (no runtime/API version surface changed by the fixes). |

## 3. Blast-radius checklist

1. **Same SPA?** Yes — Grove + Studio share `desktop/src`. WebUI parity needs
   `npm run build` + serve restart (standing item; unchanged by these fixes).
2. **Two processes?** Sidecar + UI; no dual ownership introduced. The dual
   BrowserSlide mount (a real dual-owner bug) is **removed**.
3. **Cross-path behavior?** Approval gate is shared by desktop stream +
   messenger turns; the one-shot change applies uniformly (sensitive = payment/
   vault only). Non-sensitive approvals behave exactly as before.
4. **OS-specific?** Vault DPAPI seal is Windows; falls back to Argon2id
   (passphrase) or owner-mode file elsewhere — unchanged. Voice engines are
   cross-platform (Kokoro/whisper CPU). No new Windows-only resource paths.
5. **Hard to unit-test?** WebView2 bounds, mic permission, real TTS/STT — see
   smoke matrix below.
6. **Architecture traps?** Vault now fails **closed** on unconfirmable page
   (durable design, not a band-aid); one-shot grant is the correct model for
   "never replay a payment yes."

## 4. What CI does NOT prove (manual smoke — do together before any publish)

Both of us, on Windows, one clean instance:

| # | Smoke | Pass |
|---|-------|------|
| 1 | Launch → Grove is the default surface | Home renders; goals load |
| 2 | `✦ Grove` ⇄ `switch to Studio` | Both directions; Studio chrome intact; ✕→tray still works |
| 3 | Open a goal → Alongside | Browser rail visible in the stage, **no flicker** (dual-mount fix) |
| 4 | Drive a site in a goal room | Rail drives; captions update; Pause stops |
| 5 | Storyline tab | Moments in order; autoscrolls to newest; no raw tool JSON |
| 6 | Speak-back toggle 🔊 | Reply spoken; voice matches `agent_gender`; markdown not read aloud |
| 7 | Mic in a talkbar | Records → transcribes → sends; unplug mid-record ⇒ no wedge |
| 8 | Vault fill on the bound site | Fills; **on a different/redirected page ⇒ refuses** |
| 9 | Payment checkpoint | "Place order" asks in auto/full; approve → proceeds once; **repeat ⇒ asks again** |
| 10 | Coding flow unchanged | `bash_exec`/`file_write` in auto/full: no new prompts |

Log greps if debugging rails: `poll lock`, `browser embed`, `add_child`.

## 5. Gate status (this session)

- `uv run pytest -q`: **2386 passed**, 13 skipped, 3 failed — the 3 are
  maintainer-clone-only file-existence checks (`test_agency_battery`,
  `test_autoupdate_hooks`, `test_plugin`), present on your tree, unrelated to
  this diff.
- Desktop: `tsc -b` clean · `npm test` **169 passed** · `vite build` clean ·
  `oxlint` clean for changed files.
- Not pushed (owner rule: local until joint testing). Two prior feature
  commits + this fix commit sit ahead of `origin/master`.
