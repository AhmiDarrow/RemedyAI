# RemedyAI v0.23.2 — full-project security review

**Date:** 2026-08-13  
**Tree:** `C:\Users\Administrator\Old-Remedy`  
**Method:** Source review of current files + the named test suites. No live exploit, no code changes.  
**Threat model:** Local-first owner-power desktop agent. Same Windows user ≈ full access is accepted. In scope: remote/LAN/webhook exposure, agent tool jail escapes, auth bypass, secret leakage, SSRF, path traversal, supply-chain.

Prior claims checked against **this** tree (not historical audits):

| Prior claim | Current verdict |
|-------------|-----------------|
| `is_runtime_executable_path` skips dests named `python*` / `cmd.exe` | **Refuted / fixed.** Helper is defined and unit-tested but **not used** in `check_shell_write_jail`. Dest checks run on every statement *after* argv[0]. `tests/test_project_write_jail.py` (`test_shell_write_jail_blocks_runtime_bin_as_destination`) requires `copy`/`del`/`Set-Content` onto `cmd.exe`/`python.exe` to jail. |
| `remedy uninstall` pip-uninstalls unrelated PyPI `remedy` | **Refuted / fixed.** `_pip_dists()` returns only `("remedy-ai",)`. |
| Teams webhook is claims-only (no JWT signature) | **Refuted / fixed.** `verify_inbound_auth` requires RS256 + Bot Framework / Azure AD JWKS. |
| Computer host/jobs/ui unauthenticated on loopback | **Refuted / fixed.** Bearer required; a11y push stays loopback + `job_id`. |
| Rust `normalize_url` accepts any IPv4 (incl. IMDS / 8.8.8.8) | **Mostly fixed.** Rust now allows only loopback + RFC1918 IPv4 and blocks `.nip.io` / `169.254.` prefixes. Remaining parity gaps are listed below. |

---

## Summary

Default bind is loopback, Bearer is on, CORS `*` is refused when a token exists, provider keys and the local API token are DPAPI-sealed on Windows, file writes are project-jailed even under `access_scope=full`, `web_fetch` pin-on-resolve + redirect revalidation is solid, skill zips are signed/checksummed/quarantined, and messenger webhooks fail closed (WhatsApp HMAC, Teams RS256, Google Chat bearer, generic CI webhook secret). Dominant remaining risk is **regex-based shell write-jail extraction**: Windows `C:/…` paths and PowerShell automatic `$HOME`/`$USERPROFILE` are not treated as dests, so a project-bound `bash_exec` / `host_script` can still mutate outside the focus tree. Secondary: native Browser-rail URL checks still lag Python/SPA (userinfo, `metadata.*` labels, IPv4-mapped IPv6). No unauthenticated remote RCE was found on the default `127.0.0.1` + auth-on path.

---

## Issues

### Issue 1 -- Severity: bug
- File: src/remedy/core/shell_write_jail.py:278
- Description: Path extraction only treats drive-letter paths as absolute when they use a **backslash** (`[A-Za-z]:\\`). Quoted-path extraction has the same bias (`C:\` / `\\` / `//`, not `C:/`). On Windows, `C:/Users/Public/pwn.txt` is a real absolute path. When cwd is inside write roots and no other token is extracted, the jail takes the “mutation with no proven path + cwd in-root → allow” branch (`check_shell_write_jail` around line 680).

  Project-bound bypasses that current tests do **not** cover:

  1. `echo pwn > C:/Users/Public/pwn.txt`
  2. `Set-Content C:/Users/Public/pwn.txt pwned`
  3. `python -c "open(r'C:\\proj\\ok.txt','w'); open('C:/Users/Public/x','w')"` — in-root decoy makes `candidates` non-empty so the interpreter-oneshot fail-closed path is skipped; the forward-slash dest is never extracted.

  `scan_script_source_for_outside_writes` reuses `_ABS_PATH_RE`, so `host_script` bodies with `C:/…` also slip the source scan. Fuzz coverage (`test_shell_jail_fuzz.py`) blocks bare `node -e "…C:/Users/Public/x…"` only because pathless `-e` is fail-closed, not because `C:/` is parsed.
- Suggestion: Treat `[A-Za-z]:[\\/]` as absolute in `_ABS_PATH_RE` and `_QUOTED_PATH_RE`. Add the three vectors above to `test_project_write_jail.py` / `test_shell_jail_fuzz.py`.
- Status: open

### Issue 2 -- Severity: bug
- File: src/remedy/core/shell_write_jail.py:58
- Description: Opaque-path and script-home detectors know `$env:USERPROFILE`, `%USERPROFILE%`, and `Path.home` / `expanduser`, but **not** PowerShell automatic variables `$HOME` and `$USERPROFILE` (no `env:` prefix). `_BARE_PS_VAR_PATH_RE` only fires when a cmdlet (`Set-Content`, `-Path $var`, …) is present.

  So a project-bound PowerShell body / session can write outside the focus folder with a redirect and no env: prefix:

  - `'pwn' > "$HOME\Desktop\pwn.txt"`
  - `echo pwn > $USERPROFILE\Desktop\pwn.txt`

  `host_script(lang=pwsh)` jails the body with the same helpers, then `scan_script_source_for_outside_writes` (`_SCRIPT_HOME_PATH_RE` at line 475) also misses `$HOME` / `$USERPROFILE`. Existing tests (`$env:USERPROFILE`, `%USERPROFILE%`) stay green.
- Suggestion: Add `$HOME`, `$USERPROFILE`, `$HOMEPATH`, and `${HOME}` / `${USERPROFILE}` to `_OPAQUE_PATH_HINT_RE` and `_SCRIPT_HOME_PATH_RE`. Fail closed on any `$` dest after `>` / `>>` when project-bound. Add fuzz vectors.
- Status: open

### Issue 3 -- Severity: bug
- File: desktop/src-tauri/src/browser_host.rs:637
- Description: Rust `normalize_url` comments “Mirror Python `is_valid_navigate_url`” but does not. Python (`src/remedy/core/computer/router.py:126`) and the SPA (`desktop/src/utils/browserUrl.ts:41`) reject URL userinfo and any host label `metadata` (so `http://metadata.nicob.net/` is blocked in `tests/test_computer_use.py`). Rust does not inspect `parsed.username()` / `password()`, and only matches a short host allow/deny list.

  Concrete native-loader gaps vs Python:

  1. `https://user:pass@example.com/` — allowed in Rust; rejected in Python/SPA.
  2. `http://metadata.nicob.net/` — allowed in Rust (`metadata` is only a label, not an exact host); rejected in Python.
  3. `http://[::ffff:169.254.169.254]/` and `http://[::ffff:8.8.8.8]/` — host contains `.`, `Ipv4Addr` parse fails, IPv4 RFC1918 gate never runs. Python’s hostname regex rejects these (no dotted-quad / FQDN match).

  Agent `computer_navigate` is filtered in Python first; a job JSON, Rust `only=navigate` poller, or any IPC that calls `normalize_url` directly is the last gate into WebView2.
- Suggestion: Reject non-empty userinfo (including `https://@host`); reuse the Python metadata-label + wildcard-DNS rules; unwrap IPv4-mapped IPv6 before the private-IP check. Add a small Rust unit table that is a literal subset of `test_computer_use.py`.
- Status: open

### Issue 4 -- Severity: suggestion
- File: src/remedy/gateway/channels/jwt_rs256.py:196
- Description: After fetching hardcoded Microsoft OpenID documents, **any** `https://` `jwks_uri` is ingested and those RSA keys become trusted for Teams inbound JWT. `urllib.request.urlopen` follows redirects. There is no host allowlist on `jwks_uri` and no test (`tests/` has no JWKS-URI cases). A poisoned OpenID document (or an HTTPS redirect off Microsoft) would let an attacker install their own keys and then forge Bot Framework activities into `/api/webhooks/teams` (public at the Bearer middleware; auth is JWT-only).
- Suggestion: Allowlist `jwks_uri` hosts (`login.botframework.com`, `login.microsoftonline.com`, and the documented BF key hosts). Refuse redirects off that set. Pin or re-check `iss` against the same hosts after signature verify (today `_jwt_claims_structurally_valid` only does a substring check on `sts.windows.net` / `login.microsoftonline.com` / … — `evil.com/sts.windows.net` would pass claims).
- Status: open

### Issue 5 -- Severity: suggestion
- File: src/remedy/skills/library/catalog.py:99
- Description: Catalog HTTP fetch allowlists the **initial** URL (`is_allowed_catalog_url`) then uses aiohttp’s default redirect follow, **without** checking the final URL. Zip downloads in `install.py:67` already require `is_allowed_download_url(final, allow_cdn_redirect=True)`. A 302 from an allowlisted GitHub/raw URL to a LAN/IMDS target would be fetched (up to 8 MiB) before Ed25519 verify fails. Signature still prevents a fake catalog from installing, so this is SSRF-read, not supply-chain execute.
- Suggestion: Mirror the zip final-URL check (and `allow_cdn_redirect` only for GitHub release CDNs). Do not follow more than one hop off github.com / raw.githubusercontent.com.
- Status: open

### Issue 6 -- Severity: suggestion
- File: src/remedy/interfaces/routes/memory.py:625
- Description: `POST /api/skills/import` (`await upload.read()`) has no request-body size cap before writing `pack.zip`. Zip extract later enforces 500 files / 5 MiB per member / total budget, but a multi-hundred-MB upload is already in RAM. Route requires Bearer (not a remote unauth DoS on the default path). User-uploaded packs are quarantined; they are not catalog-signed.
- Suggestion: Reject uploads over `MAX_SKILL_ZIP_BYTES` (50 MiB) while streaming. Keep quarantine-until-Trust.
- Status: open

### Issue 7 -- Severity: suggestion
- File: src/remedy/interfaces/api.py:374
- Description: `/api/status` and `/api/self-improve` are in `_AUTH_PUBLIC`. Status returns version, gateway channel list, memory/session counts. Self-improve returns idle clocks plus `read_pending_ship()` (round summary + changed paths). Harmless on `127.0.0.1`; if the owner binds `0.0.0.0` (allowed after the insecure-bind warning when a token exists), any LAN client can fingerprint messengers and pending self-improve work without Bearer. Tests (`test_status_public`, `test_self_improve_public`) lock this in as intended.
- Suggestion: Keep `/api/ping` public. Move `/api/status` details and `/api/self-improve` behind Bearer (or return a stripped `{status, version}` publicly).
- Status: open

### Issue 8 -- Severity: suggestion
- File: tests/test_shell_jail_fuzz.py:29
- Description: Coverage is strong for encoded PowerShell, `$env:` / `%VAR%`, global installs, numbered redirects, and dest-is-runtime. Gaps that would have caught Issues 1–3: `C:/Users/…` dests, PowerShell `$HOME`/`$USERPROFILE` redirects, mixed decoy+outside interpreter oneshots, Rust vs Python navigate-URL parity (userinfo / `metadata.nicob.net` / IPv4-mapped). `test_web_fetch_ssrf.py` is in good shape (userinfo, redirect, CGNAT, IPv6 ULA/mapped). `test_api_auth.py` covers Bearer, bootstrap loopback/opt-out, CORS `*`, generic webhook fail-closed, docs disable.
- Suggestion: Add the missing jail and rail-URL cases to the existing files rather than a new suite.
- Status: open

### Issue 9 -- Severity: nit
- File: src/remedy/interfaces/api.py:408
- Description: Comment says a11y `job_id` is “≥16 chars”; `computer.py:234` requires `len(jid) < 32` reject and `jid.isalnum()`. Behavior is the stricter one (good). CORS on a11y is loopback-origin echo only — no `*` and no Private-Network (prior S-COMP-01 mostly closed).
- Suggestion: Align the middleware comment with the 32-char alnum check.
- Status: open

---

## Residual by-design risks

These are product boundaries, not counted as issues:

- **Same Windows user owns the box.** Loopback bootstrap (`GET /api/auth/local-bootstrap` when `http_bootstrap` is on — default for plain `remedy serve`, off for packaged sidecar), DPAPI secrets, memory.db / undo JSONL / attachments / logs in plaintext, and computer screenshots are all readable by malware running as the owner. Disable bootstrap (`REMEDY_HTTP_BOOTSTRAP=0` / Settings) for IPC-only desktop.
- **No project folder ⇒ `access_scope=full`.** File and shell write jails are off except protected `~/.remedy/auth/**`. Documented owner-PC mode.
- **Shell is a write jail, not an execution jail (S-WS-01 residual).** `.\hello.exe` / `python game.py` inside the project is allowed (`test_shell_write_jail_allows_runtime_bin_invoke`). A process that starts can then write anywhere; `.py` launch gets a best-effort source scan only.
- **Messenger `allow_all`.** Empty allowlist + `allow_all=false` ignores inbound (Teams / Google Chat / Telegram / WhatsApp). Turning `allow_all` on makes that platform a remote agent console. WhatsApp POST is HMAC fail-closed without `app_secret`.
- **Debug env bypasses.** `REMEDY_TEAMS_SKIP_JWT`, `REMEDY_TEAMS_SKIP_JWKS`, `REMEDY_GCHAT_ALLOW_NO_AUTH`, `REMEDY_API_AUTH=0`, `REMEDY_ALLOW_INSECURE_BIND=1`, `REMEDY_SKILLS_DEV=1` are owner-power escape hatches.
- **Cloud LLM egress.** `provider_sanitize` always redacts secret-shaped values; privacy mode (email/phone/SSN, tighter caps) is opt-in. Mail/page/computer text still leaves the machine when those tools run.
- **Trusted skills and Auto approvals** run with the user’s OS rights. Catalog install is signed + quarantined; user zip import is quarantined but not signed.
- **Google OAuth callback** (`/api/assistant/google/callback`) is Bearer-exempt by necessity. State is `token_urlsafe(24)`, single-use, PKCE S256.
- **a11y push** remains loopback + 32-char `job_id` capability (no Bearer), so same-user job completion is still possible if the id leaks.

**Not issues (confirmed held):**

- File tools: `resolve_tool_path(..., for_write=True)` + `refuse_protected_secret_path` (auth tree never writable/readable via tools, including `access_scope=full`).
- `_write_config` / `scrub_config_secrets` never persist `llm_api_key` / `provider_keys` to `config.toml` (auth route assigns then scrubs).
- Zip import: Zip-Slip, symlink members, bomb caps (`exporter._safe_extract_zip`).
- `web_fetch` / `web_search`: opt-in, pin-on-resolve, fail-closed mixed DNS, userinfo, redirect revalidation.
- Uninstaller wipe: refuses non-`.remedy` trees and drive-root `.remedy`.
