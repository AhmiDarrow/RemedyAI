# RemedyAI — End-to-End Security Audit

**Date:** 2026-07-30  
**Codebase:** `C:\Users\Administrator\RemedyAI` · branch context `feature/computer-use`  
**Version series:** 0.19.x at audit time (product line advanced to **0.20.0** afterward)  
**Method:** Static code review of current sources + tests (not a live pentest, SOC2, or legal cert)  
**Threat model:** Local-first **owner-power** desktop agent. Same Windows user compromise ≈ full access is accepted and must be stated honestly. Remote / LAN / webhook exposure is in scope.

**Prior art:** `docs/AUDIT_TRUST_UI_2026-07-29.md` (trust/UI/docs). This document is a **security-first refresh** including post–computer-use and write-jail work.

---

## Executive summary

| Domain | Grade | Verdict |
|--------|-------|---------|
| **Local API auth (main `remedy serve` path)** | **B+** | Bearer default, constant-time compare, CORS `*` refused with auth, loopback bind |
| **Secrets at rest (provider / OAuth)** | **B+** | Out of `config.toml`; DPAPI + ACL harden; public settings never echo keys |
| **Secrets at rest (user content)** | **C** | `memory.db`, undo, attachments, logs are **plaintext** for the OS user |
| **Tool / path jail** | **B−** | Strong **file** write jail when project bound; **shell can still write outside** |
| **SSRF / web_fetch** | **A−** | Opt-in, pin-on-resolve, fail-closed DNS, redirect recheck |
| **Skills / zip** | **A−** | Signed catalog, checksum, Zip-Slip/bomb limits, quarantine until Trust |
| **Computer-use** | **C+** | Loopback host unauthenticated by design; page/shot content → cloud LLM risk |
| **Messengers** | **B−** | Allowlists fail-closed; WhatsApp HMAC solid; **Teams JWT not signature-verified** |
| **Desktop shell / CSP / update** | **B** | Minisign updates; no Authenticode on first download; CSP connect-src broad |
| **Data → cloud LLM** | **C+** | Secret-pattern sanitize + caps; **not** full PII/mail/page redaction |

### Bottom line (user trust)

Remedy is **powerful and local-first**, not “nothing leaves your PC” and not “encrypted vault for all chat history.”

- **Strengths:** Owner-bound loopback API, DPAPI secrets, SSRF, skill signing, project write jail for file tools, fail-closed provider sanitize, messenger allowlists.
- **Highest residual risks:** (1) unauthenticated legacy `gateway serve` path, (2) Teams webhook JWT without signature verify, (3) shell bypass of project write jail, (4) plaintext high-value data at rest, (5) tool results (mail/page/files) to cloud LLMs with only secret-regex scrubbing.

**No Critical remote zero-auth RCE found** on the **default** path (`remedy serve` / Desktop sidecar, bind `127.0.0.1`, auth on).

---

## 1. Attack surface map

```
┌──────────────────── User / OS ────────────────────┐
│  Desktop (Tauri WebView2)  ·  Browser WebUI        │
│  Messengers  ·  Shell tools  ·  Computer host      │
└───────────────┬───────────────────────────────────┘
                │ HTTP 127.0.0.1:7400 (+ webhooks if exposed)
                ▼
┌──────────── Remedy API (FastAPI) ─────────────────┐
│ Auth middleware · routes · tool registry           │
│ Sessions · memory · computer jobs · gateway        │
└───────────────┬───────────────────────────────────┘
                │
    ┌───────────┼───────────┬──────────────┐
    ▼           ▼           ▼              ▼
 Cloud LLM   Google/xAI   Messenger     Disk
 providers   OAuth APIs   platforms     ~/.remedy/*
```

| Surface | Entry | Trust assumption |
|---------|-------|------------------|
| Loopback API | SPA / CLI / Desktop | Same Windows user |
| HTTP bootstrap | `GET /api/auth/local-bootstrap` | Same user + loopback |
| Computer host | `/api/computer/host|jobs|ui|a11y/*` | Loopback, **no Bearer** |
| Webhooks | `/api/webhooks/*` | Platform crypto only |
| Sidecar / tools | Agent tool_calls | Approvals + jail + danger blocks |
| Updates | GitHub + minisign | Release integrity |
| Cloud LLM | Provider HTTP | User-chosen egress |

---

## 2. Data inventory (user data)

| Location | Contents | Sensitivity | Controls |
|----------|----------|-------------|----------|
| `~/.remedy/auth/provider_keys.json` | Provider API keys | **Critical** | DPAPI (Win) + ACL; never in config.toml |
| `~/.remedy/auth/local_api_token` | Bearer for local API | **Critical** | Plaintext + ACL (not DPAPI) |
| `~/.remedy/auth/xai.json` / Google tokens | OAuth tokens | **Critical** | DPAPI preferred; plain fallback possible |
| `~/.remedy/config.toml` | Non-secret settings | Low–med | Scrub keys on write |
| `~/.remedy/memory.db` | Sessions, messages, tool results, profile | **High** | SQLite plaintext; tool_results capped on save |
| `~/.remedy/undo/*.jsonl` | Prior file bodies for time-travel | **High** | Plaintext, up to ~400k chars/entry |
| `~/.remedy/attachments/` | Uploaded files | **High** | Size cap 15 MiB; no TTL encryption |
| `~/.remedy/checkpoints/` | Task checkpoints | Med | Plain JSON |
| `~/.remedy/logs/` | remedy/errors/debug | Med–high | Rotate; **no secret scrub filter** |
| `~/.remedy/computer/jobs|shots` | Job JSON, screenshots | **High** | Job text cap + ~15m purge; shots may linger |
| `~/.remedy/skills/` | User / imported skills | Med | Quarantine until Trust |
| `~/.remedy/vision/` | Local model weights | Low | Download integrity when hashed |
| `assistant.json` / PA prefs | Budget/account flags | Med | Local file |

**What leaves the machine (by design):**

| Path | Destination | Mitigations |
|------|-------------|-------------|
| Chat + tool results | User’s LLM provider | Sanitize secret patterns + size caps; fail-closed on sanitize error |
| Mail/calendar snippets | LLM (after Connect + consent) | Consent flags; snippet caps |
| Computer page text / snapshots | LLM | Caps/redact passwords in DOM snapshot path |
| `web_fetch` | Public HTTP | Opt-in + SSRF |
| Messengers | Platforms | Allowlists; sealed secrets |
| Updates | GitHub Releases | Minisign |

There is **no Remedy multi-tenant cloud mailbox**.

---

## 3. Findings (severity-ordered)

Severity key: **Critical** remote unauth compromise · **High** significant data/agent compromise under realistic conditions · **Med** important residual · **Low** hygiene · **Info** by design / residual owner-boundary.

### 3.1 Critical / High

| ID | Sev | Surface | Finding | Evidence | Recommendation |
|----|-----|---------|---------|----------|----------------|
| **S-AUTH-01** | **High** | CLI | **`remedy gateway` → `_serve_api` calls `create_app()` without `api_key`.** Auth middleware only installs when `api_key` is truthy → fully open loopback API. | `src/remedy/gateway/cli.py` ~225–233; `api.py` `if api_key:` | Always call `ensure_local_api_token` + pass `api_key=`; deprecate/alias path to main `remedy serve`. Add regression test. |
| **S-MSG-01** | **High** | Teams webhook | **JWT payload decoded without signature verification** (structure/`aud` only). Skip flag exists. If webhook is reachable beyond strict isolation, forged Bearer can inject agent turns (allowlist still applies). | `gateway/channels/teams.py` `_jwt_payload_unverified` | Verify against Bot Framework OpenID JWKS; reject bad `exp`/`iss`; remove/gate skip-JWT. |
| **S-DATA-01** | **High** | At rest | **`memory.db` is unencrypted.** Full chat + tool traces readable by same OS user or offline disk as that user. | `memory/store.py`; manual 04 | Document as product truth; optional SQLCipher + DPAPI key; retention + “strip tool bodies.” |
| **S-WS-01** | **High → Fixed 2026-07-30** | Shell vs jail | **Was:** `bash_exec` only jailed *cwd*; PowerShell could `Set-Content` into sibling trees. **Now:** `shell_write_jail.check_shell_write_jail` blocks mutation commands targeting paths outside write roots; `update_settings(project_path=)` refuses mid-session retarget without `force_project_switch`. | `shell_write_jail.py`, `agent_workspace_tools.bash_exec`, `agent_settings_tools` | Keep tests in `test_project_write_jail.py`; restart serve to load. |

### 3.2 Medium

| ID | Sev | Surface | Finding | Recommendation |
|----|-----|---------|---------|----------------|
| **S-AUTH-02** | Med | Bootstrap | HTTP `local-bootstrap` **defaults ON** — any same-user loopback client can obtain Bearer. | Default off for desktop-only; keep IPC; explicit enable for WebUI. |
| **S-AUTH-03** | Med | Token file | `local_api_token` plaintext + ACL only (unlike provider DPAPI). | DPAPI-seal like other secrets. |
| **S-AUTH-04** | Med | Computer host | `/api/computer/host|jobs|ui|a11y/*` skip Bearer on loopback. | Optional host shared secret; document multi-user machines. |
| **S-COMP-01** | Med | a11y | a11y push loopback-exempt + permissive CORS/PNA patterns; `job_id` as weak shared secret. | Bearer or one-time nonce; tighten CORS; higher entropy job ids. |
| **S-PROV-01** | Med | Cloud LLM | Sanitize redacts key-like secrets and caps sizes; **does not** systematically redact mail/page/file PII before provider POST. | Privacy mode: aggressive tool-role strip; local-only models for sensitive tools. |
| **S-WS-02** | Med | Scope | No project path → **effective `full`** access (power default). | Surface “full machine” in UI; confirm on first use. |
| **S-DATA-02** | Med | Retention | No TTL for sessions/memories; harness prune is send-view only. | Retention settings; forget tools / purge session. |
| **S-DATA-03** | Med | Undo | Undo JSONL holds prior file contents in plaintext. | Cap, scrub, age-out; include in privacy wipe. |
| **S-DATA-04** | Med | Logs | Rotating logs without secret-redaction filters. | Handler-level redact; include in wipe. |
| **S-DATA-05** | Med | Attachments | Plain attachments; size only; weak cascade on selective wipe. | Session delete cascade; TTL; wipe checkbox. |
| **S-MSG-02** | Med | Webhooks | Platform webhooks public by necessity; quality depends on each adapter. | Continuous adapter review; fail-closed. |
| **S-MSG-03** | Med | Generic webhook | `/api/webhook/{source}` auth only when a secret is configured; empty when auth off. | Always require `REMEDY_WEBHOOK_SECRET`. |
| **S-MSG-04** | Med | Telegram | `allow_all` opt-in can open bot to world. | Hard confirm in UI; refuse without non-empty allowlist in “production” profile. |
| **S-DESK-01** | Med | CSP | `connect-src` allows broad `https:`/`http:` from webview. | Tighten to loopback API + ipc for desktop. |
| **S-SUPPLY-01** | Med | Signing | In-app minisign OK; **first-install EXE not Authenticode** (SmartScreen). | OV/EV Authenticode on NSIS + main EXE. |
| **S-BASH-01** | Med | Shell | Dangerous-command blocklist incomplete vs encoding/obfuscation (`-enc`, nested shells). | Expand hard blocks; keep approvals. |
| **S-SEC-01** | Med | DPAPI fallback | Provider/OAuth may store `encoding=plain` if CryptProtect fails. | Settings banner for all stores (not only Google). |

### 3.3 Low / Info

| ID | Sev | Finding | Recommendation |
|----|-----|---------|----------------|
| **S-AUTH-05** | Low | OpenAPI `/docs` public when server reachable | Disable in packaged desktop builds |
| **S-AUTH-06** | Low | `hmac.compare_digest` unequal length may 500 | Safe compare wrapper → always 401 |
| **S-COMP-02** | Low | Screenshots under `computer/shots/` may accumulate | TTL purge |
| **S-DESK-02** | Low | `shell:allow-open` any http(s) URL | Host allowlist / confirm |
| **S-DESK-03** | Low | Sidecar discovery by size near install dir | Prefer fixed resource path + signature |
| **S-SKILL-01** | Low | Catalog URL env override without same host allowlist as zips | Allowlist unless dev flag |
| **S-WS-03** | Low | Project scope **reads** Desktop/Docs/Downloads | Optional project-only reads |
| **S-*** | Info | Same-user malware owns loopback/bootstrap/host — stated product boundary | Keep honest docs |

---

## 4. Strengths (keep these)

1. **Main serve path:** Bearer by default; constant-time Bearer/`X-Remedy-Token`; non-loopback bind refused without auth (unless explicit insecure env).  
2. **CORS:** `*` refused when API auth enabled.  
3. **Secrets:** Not in `config.toml`; Windows DPAPI + ACL harden; no Everyone grant (tested).  
4. **Settings GET:** Booleans / fingerprints only — no raw keys.  
5. **Google OAuth:** PKCE S256; single-use state; consent flags; encoding warning when plain.  
6. **xAI OAuth:** Forces `auth.x.ai` host; sealed credentials.  
7. **web_fetch SSRF:** Opt-in; private/loopback/metadata blocked; pin-on-resolve; redirect revalidation; body caps.  
8. **Skills:** Ed25519 catalog, checksum, URL allowlist, Zip-Slip/bomb limits, quarantine until Trust.  
9. **File write fidelity:** Execute path full args (`coerce_tool_arguments_json`); history stubs refused on write; provider history summarized safely.  
10. **Project write jail (files):** Bound project → file_write/file_edit roots project-only even when `access_scope=full` (for_write path).  
11. **Approvals:** Ask default; Auto = owner power; untrusted always asks.  
12. **Hard danger patterns + host self-kill** for wipe/suicide commands.  
13. **Messenger allowlists** fail-closed when empty; WhatsApp HMAC fail-closed; Telegram dual-poll lock.  
14. **Updates:** Minisign-verified; autostart via Startup folder only (no HKCU Run).  
15. **Full uninstall wipe** of `~/.remedy` + AppData leftovers (tested path list).  
16. **Provider sanitize fail-closed** in ReAct loop.

---

## 5. Exploit / abuse scenarios (conceptual — no PoCs)

| Scenario | Preconditions | Impact | Mitigated by |
|----------|---------------|--------|--------------|
| Local process steals Bearer via bootstrap | Bootstrap on, same user | Full agent API | Bootstrap off / IPC only |
| Local process drives computer host | Loopback | Jobs/UI/a11y | Host secret (missing) |
| Shell writes outside project | Project scope, Auto approvals | Escape file jail | File tools only; shell gap open |
| Cloud LLM sees mail/page | PA/computer tools used | Confidentiality to provider | Consent + size caps; incomplete redaction |
| Forged Teams webhook | Public bind/tunnel + weak JWT | Inbound agent abuse | Signature verify (missing) |
| Malicious skill zip | User Trusts after quarantine | Arbitrary code as user | Quarantine + signing (if catalog) |
| `gateway serve` open API | Operator uses legacy path | Unauth local API | Fix S-AUTH-01 |
| Disk theft of laptop | No FDE | Full memory.db | OS disk encryption + optional DB crypto |

---

## 6. Scorecard

```
Local secrets (provider/OAuth) ..... B+
Local API (default serve) .......... B+
Legacy / alternate serve paths ..... D  (S-AUTH-01)
File path jail ..................... B
Shell containment .................. C-
SSRF ............................... A-
Skills supply chain ................ A-
Computer-use host .................. C+
Computer-use data → LLM ............ C
Messengers (Telegram/WhatsApp) ..... B
Messengers (Teams JWT) ............. D+
Data at rest (chat/undo/logs) ...... C
Egress privacy (cloud LLM) ......... C+
Desktop CSP / first-run trust ...... B-
Wipe / uninstall ................... B
```

**Overall security grade (default Desktop product):** **B−**  
**Overall if Teams public + gateway serve used:** **C / C−**

---

## 7. Priority remediation plan

### P0 (this week)

1. **Fix S-AUTH-01** — `gateway` serve must use `ensure_local_api_token` + `create_app(..., api_key=...)`.  
2. **Teams JWT signature verification** (or disable Teams inbound until done).  
3. **Document + UI warn:** shell can escape project write jail; recommend Auto only for trusted owners.

### P1 (near term)

4. Safer defaults: `http_bootstrap=false` for desktop-only; seal `local_api_token` with DPAPI.  
5. Computer host shared secret; tighten a11y CORS/PNA.  
6. Privacy mode at provider boundary (tool-result class redaction beyond secrets).  
7. Retention + tool-body strip settings; secret scrub on log handlers.  
8. CSP tighten for desktop webview.

### P2

9. Optional `memory.db` encryption.  
10. Authenticode on installer.  
11. Screenshot/undo/attachment TTL and selective wipe clarity.  
12. Expand shell dangerous-command / encoded PowerShell hard blocks.

---

## 8. Test coverage vs claims

| Area | Tests present | Gaps |
|------|---------------|------|
| API auth / CORS / bootstrap | `test_api_auth.py` | gateway serve unauth; compare_digest length |
| Secret store / ACL | `test_secret_store.py`, `test_secret_acl_no_everyone.py` | — |
| Google / xAI OAuth | `test_google_oauth.py`, `test_xai_auth.py` | — |
| Write jail | `test_project_write_jail.py` | Shell escape cases |
| SSRF | `test_web_fetch_ssrf.py` | IPv6 ULA edge cases |
| Zip import | `test_zip_import_security.py` | Catalog URL env |
| Provider sanitize | `test_provider_sanitize.py` | PII beyond secrets |
| Uninstall paths | `test_uninstall_wipe_paths.py` | — |
| Teams JWT | — | **Missing signature tests** |

---

## 9. What this audit is / is not

| Is | Is not |
|----|--------|
| Code-backed control review of current tree | Live network pentest |
| User-data + exploit-class risk framing | Formal CVE assignment |
| Actionable P0–P2 plan | Legal/compliance certification |
| Update to 2026-07-29 trust audit | UI/feel audit (see prior doc) |

---

## 10. Honest product statement (for users)

> Remedy runs **on your machine** with **owner-level power**. Your chat history and tool traces live under `~/.remedy` as the Windows user (not full-disk encrypted by Remedy). When you use a cloud model, **tool results can leave the PC** to that provider. Approvals (Ask vs Auto), project focus, and access scope limit *how easily* the agent acts — they are not a multi-tenant sandbox. Install only from **official GitHub Releases**; in-app updates are **minisign-verified**.

---

*Audit prepared 2026-07-30 from static review of RemedyAI sources and automated explore passes over auth, tools, and data/desktop surfaces.*
