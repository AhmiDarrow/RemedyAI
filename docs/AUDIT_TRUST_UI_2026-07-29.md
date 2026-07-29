# Remedy whole-product audit — Security · Documentation · UI

**Date:** 2026-07-29  
**Branch context:** `feature/computer-use` (PA, computer-use, SmolVLM2, Simple/Advanced)  
**Scope:** Local desktop product (Tauri + Python API), user data, connections, manuals, UI feel  
**Not:** External penetration test, SOC2, or legal compliance certificate  

**Product feel bar (UI):** *easy · sleek · beautiful · familiar · powerful*

---

## Executive summary

| Domain | Grade | Verdict |
|--------|-------|---------|
| **Security (local-first foundations)** | **B+** | Strong loopback auth, DPAPI keys, SSRF, approvals, skill quarantine |
| **Security (user data → cloud LLM)** | **C+** | Honest design, but tool results (mail, page text, files) can leave the machine with limited user-visible controls |
| **Security (PA / OAuth)** | **B** | Consent gates + token sealing; no formal redaction-before-provider pipeline |
| **Documentation accuracy** | **C** | Core security map good; several chapters still say “optional Qwen 3B” after SmolVLM2 pin |
| **Documentation completeness** | **C+** | No first-class PA chapter; computer-use privacy thin; help sync lag risk |
| **UI vs feel bar** | **B−** | Simple/Advanced started; Settings still dense; dual mode controls need polish; feel bar documented in AGENTS |

**User trust bottom line:** Remedy is **owner-powerful and local-first**, not “zero data leaves your PC.” Trust requires **clear defaults, consent, and minimize**—partially built for PA, incomplete product-wide.

---

# Part 1 — Security audit

## 1.1 Data inventory (what exists on disk)

| Location | Contents | Sensitivity | Controls today |
|----------|----------|-------------|----------------|
| `~/.remedy/config.toml` | Provider, model, persona, paths, flags | Low–med | Should be non-secret (scrub on save) |
| `~/.remedy/auth/` | API token, provider keys, xAI OAuth, Google OAuth | **Critical** | DPAPI (Windows) + ACL harden |
| `~/.remedy/memory.db` | Sessions, messages, memories, tool traces | **High** | Local SQLite; no encryption-at-rest beyond OS user |
| `~/.remedy/assistant.json` | Budget/debts/bills, prefs, account status | Med–high | Local file; not DPAPI-sealed (prefs only; no OAuth tokens) |
| `~/.remedy/skills/` | User/learned skills | Med | Quarantine/trust for imports |
| `~/.remedy/vision/` | Local model weights + runtime | Low | Download integrity (SHA when set) |
| Browser rail / captures | Page content, jobs | High (ephemeral) | Loopback host; content can enter chat tools |

**Gap:** No product-level **encrypted memory.db**; compromise of Windows user = full chat history. Acceptable for “owner power” products, must be **stated clearly**.

## 1.2 What leaves the machine

| Path | Destination | Trigger | Mitigations |
|------|-------------|---------|-------------|
| Chat + tool results | Configured LLM provider (xAI, OpenAI, …) | Every agent turn | User chose provider; Ollama stays local |
| Mail/calendar fields | LLM as tool JSON | PA tools after Connect | Consent + snippet caps + secret redaction |
| Web fetch | Public HTTP | Opt-in `web_tools_enabled` | SSRF + IP pin |
| Update check | GitHub Releases | Update UI | Metadata/installer |
| Messenger platforms | Telegram/Discord/… | Enabled channels | Secrets in auth store; webhooks own auth |
| Google APIs | Google | PA OAuth tools | Official OAuth; tokens local |
| Local VL | Loopback llama-server only | Images / nano | Not Remedy cloud |

**No Remedy multi-tenant cloud mailbox** — correct and important for trust messaging.

## 1.3 API & connection surface

### Strengths

- Bearer auth default when token present; constant-time compare  
- CORS `*` refused when auth on  
- Bootstrap loopback-only; optional `http_bootstrap` off  
- Computer-use host routes: **loopback without bearer** (necessary for host race); same-user trust boundary documented  
- Google OAuth callback public but **state** one-time; no token in URL response HTML beyond email label  

### Risks / findings

| ID | Severity | Finding | Recommendation |
|----|----------|---------|----------------|
| S-API-1 | **Med** | Computer host `/api/computer/*` unauthenticated on loopback | Document clearly; optional shared host secret for multi-user machines |
| S-API-2 | **Med** | `/api/auth/local-bootstrap` any same-user process | Already owner-boundary; keep `http_bootstrap` off for stricter desktop-only |
| S-API-3 | **Low** | Public `/api/status`, `/docs` | Fine for local; don’t bind 0.0.0.0 without auth |
| S-API-4 | **Med** | Webhooks `/api/webhooks/*` unauthenticated at gateway | Rely on platform HMAC/verify tokens — audit each adapter regularly |
| S-API-5 | **Low** | OpenAPI/docs exposure on loopback | Disable docs in production desktop builds if desired |

## 1.4 Secrets handling

### Strengths

- Provider keys via `secret_store` + DPAPI  
- xAI / Google tokens sealed similarly  
- Memory tool refuses secret-like content  
- Messenger secrets not echoed in settings GET  

### Gaps

| ID | Severity | Finding | Recommendation |
|----|----------|---------|----------------|
| S-SEC-1 | **High** | Tool results with mail/page/file content go to cloud LLM **without a second redaction layer** at the provider HTTP boundary | Central `sanitize_for_provider(messages)` before every completion |
| S-SEC-2 | **Med** | Session history may retain past tool bodies (mail snippets) forever in `memory.db` | Retention controls, “forget last tools,” export/wipe UX |
| S-SEC-3 | **Med** | `assistant.json` plaintext JSON (budget/debt) | Optional seal or store under auth with DPAPI |
| S-SEC-4 | **Low** | Google DPAPI fallback to ACL-only file if protect fails | Surface warning in Settings when fallback used |
| S-SEC-5 | **Med** | Logging of tool bodies not fully audited for DEBUG leaks | Policy: never log tool content at INFO; redact at DEBUG |

## 1.5 Personal assistant / OAuth

### Strengths

- Consent required (`privacy_ai_accepted`, `account_access_accepted`) before OAuth start and account tools  
- Scopes: gmail.readonly + compose (drafts) + calendar.events — no silent send  
- Disconnect path clears local tokens  
- Privacy copy explains AI + tokens  

### Gaps

| ID | Severity | Finding | Recommendation |
|----|----------|---------|----------------|
| S-PA-1 | **Med** | Consent lives in Connect dialog; Settings Simple may hide security context | First-class **Privacy & security** surface (Help article + thin Settings link) |
| S-PA-2 | **Low** | Product Google Client ID still often missing in dev builds | Ship build-time client for end users |
| S-PA-3 | **Med** | Re-connect may not force re-consent if flags already true after update of scopes | Bump consent version when scopes expand |
| S-PA-4 | **Low** | Microsoft/Yahoo planned — no security model yet | Design Graph/Yahoo with same consent + sealed tokens |

## 1.6 Computer-use

### Strengths

- DOM/UIA first; vision last (reduces screenshot spam to models)  
- Host loopback-only  
- Guidance steers away from vision thrash  

### Gaps

| ID | Severity | Finding | Recommendation |
|----|----------|---------|----------------|
| S-CU-1 | **High** | Page text / snapshots can become tool results → cloud LLM | Treat like mail: consent/minimize; optional “local-only browse” mode |
| S-CU-2 | **Med** | Screenshots may hit local VL then brief enters chat context | Document; allow disable local brief injection |
| S-CU-3 | **Low** | Jobs complete without bearer on loopback | Host secret for shared machines |

## 1.7 Messengers

### Strengths

- Secrets resolved from secure store  
- Public catalog scrubbed  
- Hot-reload without putting tokens in config.toml  

### Gaps

| ID | Severity | Finding | Recommendation |
|----|----------|---------|----------------|
| S-MSG-1 | **Med** | Inbound messages join agent context → may hit cloud LLM | Same provider-sanitize + per-channel retention |
| S-MSG-2 | **Med** | Webhook endpoints public (by necessity) | Continuous review of signature verification per platform |

## 1.8 Approvals & filesystem

### Strengths

- Ask vs Auto clear; untrusted always asks  
- Dangerous command checks  
- Access scopes (project/home/full/untrusted)  

### Gaps

| ID | Severity | Finding | Recommendation |
|----|----------|---------|----------------|
| S-APP-1 | **Low** | Auto mode is very powerful by design | Keep; reinforce in first-run copy |
| S-APP-2 | **Med** | Empty project → full access | Good for power; surface warning more visibly |

## 1.9 Security scorecard (user data focus)

```
Local secrets at rest ........ B+ (DPAPI + ACL)
Network auth surface ......... B
Data minimization to LLM ..... C+ (PA snippets yes; global pipeline no)
Consent & transparency ....... B- (PA yes; product-wide no)
Connection hygiene ........... B
Computer-use privacy ......... C+
Messenger privacy ............ C+
Incident / wipe UX ........... B- (uninstall wipe exists; granular forget weak)
```

---

# Part 2 — Documentation audit

## 2.1 Coverage map

| Topic | Manual chapter | Status |
|-------|----------------|--------|
| Install / first-run | 01, 02 | OK |
| Providers / xAI OAuth | 03 | OK |
| Security & data | 04 | **Good skeleton**; needs computer-use + messengers + wipe matrix |
| Chat / sessions | 05 | OK |
| Memory | 06 | OK |
| Skills | 07 | OK |
| Updates / uninstall | 08 | OK |
| Local vision | 14 | **STALE** (still Qwen 3B) |
| Nano swarm | 17 | **STALE** (optional Qwen) |
| Continuity | 16 | **STALE** (optional Qwen) |
| Personal assistant | — | **MISSING** dedicated chapter |
| Computer-use privacy | soak only | **Thin for users** |
| Simple/Advanced UI | overview one-liner | **Under-documented** |

## 2.2 Accuracy findings (must-fix)

| ID | Severity | Doc | Issue |
|----|----------|-----|-------|
| D-1 | **High** | `14-visual-decoder.md` | Still **SmolVLM2 2.2B**; product default is **SmolVLM2 2.2B Apache** |
| D-2 | **Med** | `00-overview.md` | Still mentions optional Qwen in architecture sketch lines |
| D-3 | **Med** | `16-continuity`, `17-nanoswarm` | “Optional local SmolVLM2” |
| D-4 | **Med** | `04-security` paths | Missing `assistant.json`, vision dir, computer jobs |
| D-5 | **Med** | Help wiki sync | Desktop help may lag manuals if sync not run |
| D-6 | **Low** | README product surfaces | Verify release README vs SmolVLM2 |

## 2.3 Completeness gaps

1. **“What leaves my machine?”** one-pager for non-engineers (screenshot-friendly)  
2. **Personal assistant** user guide: Connect dialog, scopes, disconnect, fast path  
3. **Computer-use** user privacy: rail vs system browser, what agent can read  
4. **Simple vs Advanced** (bar + Settings) explained once  
5. **Data wipe matrix:** uninstall full wipe vs clear session vs disconnect Google  

## 2.4 Documentation grade

| Criterion | Grade |
|-----------|-------|
| Security story coherence | B |
| Currency with `feature/computer-use` | C |
| New-user trust clarity | C+ |
| Operator/API completeness | B |

---

# Part 3 — UI audit (feel bar)

**Bar:** easy · sleek · beautiful · familiar · powerful  

## 3.1 What already matches

| Control | Feel | Notes |
|---------|------|--------|
| Chat-first layout | Familiar · Powerful | Standard assistant chrome |
| Status bar **Simple / Advanced** | Easy · Powerful | Right place for chrome density |
| Settings **Simple \| Advanced** | Easy · Powerful | Separate from UI mode (good) |
| PA Connect **dialog** | Easy · Sleek | Privacy out of settings wall |
| Theme system | Beautiful · Familiar | Token-based |
| Hide PC host badge | Sleek | Reduced jargon |

## 3.2 UI findings vs bar

| ID | Severity | Area | Issue vs bar | Direction |
|----|----------|------|--------------|-----------|
| U-1 | **High** | Settings Simple | Still many sections once Advanced; Simple list can feel long (messengers expandables) | Collapse messengers by default; one-line summaries |
| U-2 | **High** | Dual Simple/Advanced | Two modes (UI bar + Settings) without shared language | Label clearly: **“UI: Simple”** vs Settings **“Show advanced settings”** |
| U-3 | **Med** | Status bar Advanced | Still dense (Think / thumbs / Min-Med-Full) | Group under one “More…” or icon row |
| U-4 | **Med** | PA section | Good lean path; Connected state OK | Keep Connect dialog primary for privacy |
| U-5 | **Med** | Local model (Advanced) | Enable/disable + many buttons can feel heavy | Dependency: Install / Running / Retry — fewer toggles |
| U-6 | **Med** | Provider + model selects always visible | Powerful but busy in Simple | Simple: model only; Advanced: provider + model |
| U-7 | **Low** | Inconsistent density | Cozy/compact vs Simple/Advanced | Map Simple → slightly calmer spacing |
| U-8 | **Med** | Trust not a destination | No Privacy & security home in UI | Thin entry: Settings link or Help article, not a wall |
| U-9 | **Low** | Setup wizard | Long; powerful first-run | Keep progressive disclosure |
| U-10 | **Med** | Stale vision copy in Help if not synced | Breaks beautiful/trust | Docs sync gate |

## 3.3 Feel scorecard

| Quality | Grade | Comment |
|---------|-------|---------|
| **Easy** | B− | Simple modes help; two modes need naming; Settings still deep |
| **Sleek** | B− | Improved; Advanced chrome still button-heavy |
| **Beautiful** | B | Themes solid; avoid more chips/badges |
| **Familiar** | B+ | Chat + bottom bar is right |
| **Powerful** | A− | Owner power preserved; Advanced unlocks almost everything |

## 3.4 UI pass principles (implementation checklist)

When changing UI, require:

1. **Default Simple** does the common path in ≤3 visible primary actions.  
2. **No new permanent bottom-bar buttons** without Advanced-only or overflow.  
3. **Multi-step trust/auth** → modal or wizard, not Settings accordion spam.  
4. **One mental model for modes:**  
   - Bottom bar: *how busy is the app chrome?*  
   - Settings: *how many knobs?*  
5. **Copy** matches product: SmolVLM2, not optional Qwen 3B.  
6. **Beauty:** accent sparingly; prefer text links for secondary actions.

---

# Part 4 — Prioritized remediation roadmap

## P0 — Trust & accuracy (do next)

1. **Docs sync:** rewrite `14-visual-decoder.md`, overview architecture, nanoswarm/continuity for **SmolVLM2 2.2B Apache**; run help manual sync.  
2. **Provider-boundary sanitizer:** before every LLM HTTP call, strip secrets + optionally truncate tool payloads.  
3. **Security data map update:** `assistant.json`, vision path, computer-use in ch.04.  
4. **UI mode labeling:** “Simple UI” / “Advanced UI” on bar; Settings keep “Simple | Advanced” with subtitle *Settings detail*.

## P1 — Trust productization

5. User-facing **Privacy & data** Help article + link from Connect dialog and Settings Security.  
6. Consent **version** bump when OAuth scopes change.  
7. Session **tool-result retention** settings (keep / 7d / strip on send).  
8. Computer-use privacy one-pager + minimize page text size to model.

## P2 — UI feel polish

9. Simple UI: provider/model → model-first; hide Think/approval/process (already started).  
10. Settings Simple: messengers collapsed; fewer open sections by default.  
11. Local model Advanced: dependency status card (Install · Ready · Running).  
12. Reduce Advanced status-bar control count (overflow menu).

## P3 — Hardening

13. Optional DPAPI for `assistant.json` / memory at rest.  
14. Host shared secret for computer-use on multi-user PCs.  
15. Webhook adapter signature audit checklist in CI.  
16. External review of OAuth + webhook surface.

---

# Part 5 — What “good” looks like for user trust

Users should be able to answer in 30 seconds:

1. What stays on my PC?  
2. What can leave, and when?  
3. How do I disconnect accounts / wipe data?  
4. Am I talking to a cloud AI right now?  

**Today:** (1) partial, (2) partial for PA, weak product-wide, (3) uninstall yes / granular weak, (4) only via provider settings.

---

# Appendix A — Out of scope / honesty

- No third-party penetration test was performed.  
- No formal threat-model workshop with red team.  
- Grades are engineering judgment from codebase + docs review on 2026-07-29.  

# Appendix B — Related commits / code

- PA privacy: `7e57d35`, `privacy.py`, Connect dialog  
- Local model Apache pin: `ef1f20d` SmolVLM2  
- UI modes: `settingsMode` + `uiMode` + status bar toggle  
- Feel bar: `AGENTS.md` Product feel section  

---

*End of audit report.*
