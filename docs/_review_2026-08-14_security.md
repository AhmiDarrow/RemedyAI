# RemedyAI ~v0.24 — security review (2026-08-14)

**Date:** 2026-08-14  
**Tree:** `C:\Users\Administrator\Old-Remedy` (product version 0.24.0 in `desktop/src-tauri/tauri.conf.json`)  
**Method:** Source review of current files + named tests. No live exploit, no code changes.  
**Threat model:** Local-first Windows desktop agent. Same Windows user ≈ full access is accepted. In scope: remote/LAN/webhook exposure, agent tool jail escapes, auth bypass, secret leakage, SSRF, path traversal, supply-chain, and the new Hugging Face GGUF pull (`src/remedy/runtime/rmb/hf.py` + `src/remedy/interfaces/routes/rmb.py`).

Prior OPEN claims from `docs/_review_security.md` (2026-08-13) checked against **this** tree:

| Prior claim | Current verdict |
|-------------|-----------------|
| 1. `shell_write_jail._ABS_PATH_RE` misses `C:/` dests | **Fixed.** Drive class is `[A-Za-z]:[\\/]`. Tests cover the three 2026-08-13 vectors. |
| 2. `$HOME` / `$USERPROFILE` PowerShell redirects slip the jail | **Fixed.** Opaque-path + script-home regexes include `$HOME`/`$USERPROFILE`/`$HOMEPATH`/`${…}`; `_REDIRECT_DOLLAR_DEST_RE` fail-closes `> $…`. |
| 3. Rust `normalize_url` lags Python (userinfo, `metadata.*`, IPv4-mapped IPv6) | **Partially fixed.** Userinfo and IPv4-mapped IMDS/public IPs are rejected. **`metadata` as a DNS label is still missing** (`http://metadata.nicob.net/` still allowed in Rust). |
| 4. Teams JWKS: any `https://` `jwks_uri` trusted | **Still open.** No host allowlist; `urlopen` still follows redirects. |
| 5. Skills catalog HTTP follows redirects without final-URL check | **Still open.** Zip download checks `resp.url`; catalog `_http_get` does not. |

---

## Summary

Default bind is loopback, Bearer is on (`ensure_local_api_token`), CORS `*` is refused when a token exists, provider keys and the local API token are DPAPI-sealed on Windows, and `/api/self-improve` is no longer public. The 2026-08-13 shell-jail holes (`C:/…`, `$HOME`/`$USERPROFILE` redirects) are closed and unit-tested.

The new HF GGUF pull is **not** an unauthenticated remote RCE/SSRF path on the default `127.0.0.1` + auth-on configuration: `/api/rmb/hf/*` is outside `_AUTH_PUBLIC`, inputs are rebuilt onto `https://huggingface.co/{repo}/resolve/{rev}/{file}`, dests flatten to `~/.remedy/rmb/models/{basename}.gguf`, and redirects are host-checked (HF / `*.hf.co` only). Residual HF risk is authenticated disk-fill (no download cap) and API JSON `read()` without a byte limit.

Dominant leftover from the prior review: **Teams JWKS will ingest any `https://` `jwks_uri`** (and follow redirects there), **catalog fetch still SSRF-reads on a 302 off the allowlist**, and the **native Browser-rail URL gate still allows `metadata.<tld>`**. No new Host Bridge (`host_run` / `host_script` / `host_mkdir`) jail escape was found beyond the documented “write jail, not execution jail” residual.

---

## Prior issues re-verified

### 1. `C:/` dests — **fixed**

`_ABS_PATH_RE` now treats POSIX-slash drive paths as absolute:

```312:318:src/remedy/core/shell_write_jail.py
_ABS_PATH_RE = re.compile(
    r"(?:"
    r'(?:[A-Za-z]:[\\/]|\\\\)[^\s\'"<>|;,&]+'  # C:\… C:/… or UNC
    ...
```

`_QUOTED_PATH_RE` uses the same `[A-Za-z]:[\\/]` class (line 332).  
`tests/test_project_write_jail.py` (`test_shell_write_jail_blocks_forward_slash_drive_and_ps_home`, `test_extract_forward_slash_drive_paths`) and `tests/test_shell_jail_fuzz.py` include `echo pwn > C:/Users/Public/pwn.txt`, `Set-Content C:/…`, and the in-root decoy + `C:/` oneshot.

### 2. `$HOME` / `$USERPROFILE` redirects — **fixed**

- `_OPAQUE_PATH_HINT_RE` line 65: `\$\{?(?:HOME|USERPROFILE|HOMEPATH)\}?\b`
- `_SCRIPT_HOME_PATH_RE` line 519: same automatic variables
- `_REDIRECT_DOLLAR_DEST_RE` line 339 + check at line 683: any `>` / `>>` dest starting with `$` is fail-closed

The 2026-08-13 vectors (`'pwn' > "$HOME\Desktop\pwn.txt"`, `echo pwn > $USERPROFILE\Desktop\pwn.txt`) are in the jail tests.

### 3. Rust `normalize_url` parity — **partially fixed / still open**

Current `desktop/src-tauri/src/browser_host.rs`:

- Userinfo: authority `@` check at 676–682; tests `rejects_userinfo` (740–743).
- IPv4-mapped IPv6: unwrap via `to_ipv4_mapped()` at 690–697; `http://[::ffff:169.254.169.254]/` rejected (746–748). Mapped public IPv4 also hits the RFC1918/loopback gate (722–730).
- **Still missing:** Python `_is_blocked_metadata_host` (`src/remedy/core/computer/router.py:38`) blocks any label `metadata`. Rust only exact-matches `metadata` / `metadata.google.internal` / `metadata.goog` (709–711). `http://metadata.nicob.net/` is still allowed in the native loader. SPA `desktop/src/utils/browserUrl.ts` also has no metadata-label check (userinfo only).

See Issue 1 below.

### 4. Teams JWKS any `https` `jwks_uri` — **still open**

`src/remedy/gateway/channels/jwt_rs256.py:196–198` still appends every `jwks_uri` that merely `startswith("https://")`. `_http_get_json` (134–141) uses `urllib.request.urlopen`, which follows redirects with no host allowlist.  
`iss` is still a substring check (`teams.py:103–107`: `s in iss_l` against `sts.windows.net` / `login.microsoftonline.com` / …), so `https://evil.com/sts.windows.net` still passes claims.

See Issue 2 below.

### 5. Skills catalog redirects — **still open**

`src/remedy/skills/library/catalog.py:99–111` (`_http_get`) uses aiohttp default redirect following and never inspects `resp.url`.  
Contrast zip install (`src/remedy/skills/library/install.py:67–70`), which requires `is_allowed_download_url(final, allow_cdn_redirect=True)`.

See Issue 3 below.

### Other 2026-08-13 leftovers

- **Issue 6 (unbounded skill-zip upload):** still open — `memory.py:625` `await upload.read()` with no cap. See Issue 4.
- **Issue 7 (`/api/status` + `/api/self-improve` public):** **partially fixed.** `/api/self-improve` is no longer in `_AUTH_PUBLIC`; `tests/test_api_auth.py:108` (`test_self_improve_requires_bearer`) expects 401. `/api/status` remains public (`api.py:376`, `test_status_public`). See Issue 5.
- **Issue 8 (fuzz gaps):** jail vectors added. Rust/SPA still lack `metadata.nicob.net`. See Issue 1 / Issue 8.
- **Issue 9 (a11y comment “≥16 chars”):** still stale (`api.py:407` vs `computer.py:234` `len(jid) < 32`). See Issue 9.

---

## Issues

### Issue 1 -- Severity: bug
- File: desktop/src-tauri/src/browser_host.rs:709
- Description: Rust `normalize_url` still does not treat `metadata` as a blocked **label**. Python (`src/remedy/core/computer/router.py:38`, `if "metadata" in labels`) and `tests/test_computer_use.py` reject `http://metadata.nicob.net/`. Rust only equals `metadata`, `metadata.google.internal`, `metadata.goog`. Userinfo and IPv4-mapped IMDS/public IPs are fixed; this is the remaining native-rail gap. Agent `computer_navigate` is filtered in Python first; a job JSON / Rust `only=navigate` poller / IPC that calls `normalize_url` is the last gate into WebView2.
- Suggestion: Reject any host label `metadata` (and keep the existing `.internal` / nip.io / `169.254.` rules). Add `http://metadata.nicob.net/` to `normalize_url_tests`.
- Status: open

### Issue 2 -- Severity: suggestion
- File: src/remedy/gateway/channels/jwt_rs256.py:196
- Description: After fetching the hardcoded Microsoft OpenID documents, any `https://` `jwks_uri` is ingested and those RSA keys become trusted for Teams inbound JWT. `urlopen` follows redirects off Microsoft. `/api/webhooks/teams` is Bearer-exempt (platform JWT only). A poisoned OpenID document or an HTTPS redirect off the discovery URL would let an attacker install keys and forge Bot Framework activities. `iss` validation (`src/remedy/gateway/channels/teams.py:106`) is still `substring in iss`, so `evil.com/sts.windows.net` passes structure checks.
- Suggestion: Allowlist `jwks_uri` hosts (`login.botframework.com`, `login.microsoftonline.com`, documented BF key hosts). Refuse redirects off that set. Parse `iss` as a URL and require hostname suffix (not substring). Add JWKS-URI host tests.
- Status: open

### Issue 3 -- Severity: suggestion
- File: src/remedy/skills/library/catalog.py:99
- Description: Catalog/sig fetch allowlists the **initial** URL (`is_allowed_catalog_url` at 161–163) then uses aiohttp’s default redirect follow, without checking the final URL. A 302 from an allowlisted GitHub/raw URL to a LAN/IMDS target would be fetched (up to 8 MiB) before Ed25519 verify fails. Signature still prevents a fake catalog from installing, so this is SSRF-read, not supply-chain execute. Zip path already does the right check.
- Suggestion: Mirror `install.py:67–70` (final-URL allowlist; `allow_cdn_redirect` only for GitHub release CDNs). Cap hops.
- Status: open

### Issue 4 -- Severity: suggestion
- File: src/remedy/interfaces/routes/memory.py:625
- Description: `POST /api/skills/import` still `await upload.read()` with no request-body size cap before writing `pack.zip`. Extract later enforces 500 files / 5 MiB per member / total budget, but a multi-hundred-MB upload is already in RAM. Route requires Bearer (not a remote unauth DoS on the default path). User-uploaded packs are quarantined, not catalog-signed.
- Suggestion: Reject uploads over `MAX_SKILL_ZIP_BYTES` (50 MiB) while streaming.
- Status: open

### Issue 5 -- Severity: suggestion
- File: src/remedy/interfaces/api.py:376
- Description: `/api/status` remains in `_AUTH_PUBLIC`. It returns version, gateway channel stats, memory/session/skill counts (`src/remedy/interfaces/routes/status.py:147–155`). Harmless on `127.0.0.1`; if the owner binds `0.0.0.0` (allowed after the insecure-bind warning when a token exists), any LAN client can fingerprint messengers without Bearer. `/api/self-improve` is fixed (Bearer required).
- Suggestion: Keep `/api/ping` public. Return a stripped `{status, version}` on `/api/status` without Bearer, or require Bearer for the detailed payload.
- Status: open

### Issue 6 -- Severity: suggestion
- File: src/remedy/runtime/rmb/hf.py:510
- Description: Authenticated HF pull has **no download size cap**. `_download_one` streams 256 KiB blocks until EOF (`510–515`). `expected_size` is optional (client-supplied; `0` skips the ±2% check at 535). `_sibling_parts` will fetch up to 64 shards (`552`). `_hf_json` (`299–303`) does `resp.read()` with no max bytes (search / tree listing). Dest flattening (`dest_path` 451–460) is path-safe (basename only; `sanitize_filename` 207–216 rejects `..` and non-`.gguf`). This is not unauth (see Issue 7) and same-user can already fill the disk, but a stolen Bearer or a bound `0.0.0.0` caller can fill the volume and auto-`load=True` the file as the chat model (`rmb.py:93`, `hf.py:672–690`).
- Suggestion: Enforce a configurable max (e.g. free-disk minus reserve, or a hard 100 GiB). Cap `_hf_json` reads (e.g. 8 MiB). Require `expected_size` from the tree listing when present. Consider refusing `load=True` unless the dest basename does not clobber an existing different-size GGUF.
- Status: open

### Issue 7 -- Severity: nit
- File: src/remedy/runtime/rmb/hf.py:136
- Description: Redirect gate allows `http` as well as `https`, and `_host_allowed` (`108–112`) inspects `urlparse(newurl).netloc` (userinfo+port) rather than `hostname`. Constructed downloads always start at `https://huggingface.co/...` (`HF_API` + `resolve_url` 226–231). Off-list hosts (LAN, IMDS, `example.com`) are rejected — `tests/test_rmb_hf.py:74–82`. Residual: an HF 302 to `http://*.huggingface.co` / `http://*.hf.co` is followed (TLS downgrade on that hop). `user:pass@cdn-lfs.huggingface.co` is false-rejected (`split(":")[0]` → `user`); `user@cdn-lfs.huggingface.co` suffix-matches `.huggingface.co`. Not a demonstrated SSRF to non-HF IPs.
- Suggestion: Follow `https` only; allowlist `parsed.hostname` (not `netloc`). Add a unit test that a 302 to `http://169.254.169.254/` raises `HfError`.
- Status: open

### Issue 8 -- Severity: nit
- File: tests/test_rmb_hf.py:74
- Description: HF tests cover non-HF / loopback / IMDS **parse** rejection, dest flattening, and `..` filenames. They do not exercise `_HfRedirectHandler`. Rust `normalize_url_tests` still omit `metadata.nicob.net` / IPv4-mapped `8.8.8.8`.
- Suggestion: Add a fake-redirect test on `_urlopen` and a Rust case for `http://metadata.nicob.net/`.
- Status: open

### Issue 9 -- Severity: nit
- File: src/remedy/interfaces/api.py:407
- Description: Comment still says a11y `job_id` is “≥16 chars”. Implementation is `len(jid) < 32` reject + `jid.isalnum()` (`src/remedy/interfaces/routes/computer.py:234`). Stale comment in `computer.py:228` also claims “Bearer not required on loopback host routes”; middleware (`api.py:401–404`) **does** require Bearer for `/api/computer/host|jobs|ui`. Only `/api/computer/a11y/` is loopback + job_id.
- Suggestion: Align comments with the 32-char alnum check and Bearer-on-host-routes behavior.
- Status: open

### Issue 10 -- Severity: suggestion
- File: desktop/src-tauri/src/lib.rs:2793
- Description: Custom desktop update path fetches `latest.json` from the pinned GitHub release URL, requires a non-empty `signature` string and `is_trusted_download_url` (2623–2638: this repo’s GitHub release prefix **or any** `objects.githubusercontent.com` / `release-assets.githubusercontent.com` URL). It writes `temp.exe.sig` (2800–2802) and logs “Verifying release signature…” but **never verifies** the blob against the minisign pubkey in `tauri.conf.json:84`. Trust is “HTTPS GitHub latest.json + MZ header + ≥512 KiB”. Tauri’s updater plugin is registered separately (`lib.rs:3846`) and is not what `start_desktop_update` runs. Compromised `latest.json` (or a redirect of that fetch) plus any GitHub-CDN URL would install without a crypto check.
- Suggestion: Verify minisign against the pinned pubkey before spawning the NSIS installer, or only install via `tauri_plugin_updater`. Restrict CDN URLs to assets named `Remedy.Desktop_*_x64-setup.exe`.
- Status: open

---

## Fresh inspection notes (not extra issues)

### Hugging Face GGUF pull

| Check | Result |
|--------|--------|
| Host allowlist | Initial parse: `huggingface.co` / `hf.co` / `*.huggingface.co` / `*.hf.co` (`hf.py:37–44`, `108–112`). Non-HF / `127.0.0.1` / `169.254.169.254` rejected. |
| URL rebuild | `resolve_url` always `https://huggingface.co/{repo}/resolve/{rev}/{quoted file}` — user URL is not fetched as-is. |
| Redirects | `_HfRedirectHandler` (127–142) refuses non-http(s) or off-allowlist `Location`. Unlike `web_fetch`, no DNS pin-on-resolve (acceptable if hops stay on HF hosts). |
| Filename / traversal | `sanitize_filename` requires `.gguf`, charset, no `..` segments. `dest_path` uses `Path(...).name` under `models_dir`. |
| Repo / rev | `sanitize_repo` blocks `datasets/` `spaces/` `models/` prefixes and `..`. `sanitize_revision` blocks `..`. |
| Token in logs | `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` only go into `Authorization`. Progress / `HfError` strings are filenames and HTTP codes. `logger.exception` on pull/load failure does not dump request headers. |
| Unauthenticated pull | **No** on default path. `/api/rmb/hf/search|files|pull|progress|cancel` are not in `_AUTH_PUBLIC` (`api.py:374–400`). Middleware 401s without Bearer when `api_key` is set (`cmd_runtime.py` always calls `ensure_local_api_token` unless `REMEDY_API_AUTH=0`). |
| Auto-load | `RmbHfPull.load` defaults `True` — after download, `apply_rmb_settings` enables RMB as chat provider. Owner-power given Bearer. |
| vs `web_fetch` | `agent_web_tools._pinned_fetch` still pin-on-resolve + redirect revalidation + userinfo block. HF is a separate, host-allowlisted opener. |

### API auth / computer host / CORS / bind / bootstrap

- Computer host/jobs/ui require Bearer; Rust poller DPAPI-loads `local_api_token` and sets `Authorization` (`browser_host.rs:1378–1410`).
- a11y push: loopback + 32-char alnum `job_id` only (no `*` CORS, no Private-Network).
- CORS `*` refused when `api_key` is set (`api.py:346–363`).
- Non-loopback bind refused without auth; warn when auth-on (`cmd_runtime.py:161–176`).
- HTTP bootstrap loopback-only + `http_bootstrap` toggle (`api.py:474–507`, `local_auth.http_bootstrap_enabled`). Desktop sidecar defaults bootstrap off.

### Secret store / DPAPI

- `secret_store.py` user-scoped `CryptProtectData` (176–185), ACL harden user+Administrators+SYSTEM, never Everyone:F on restore (126–136).
- `local_api_token` same DPAPI envelope (`local_auth.py:242–255`). Same Windows user can decrypt — accepted.

### Host Bridge (`host_run` / `host_script` / `host_mkdir`)

- `host_run` jails the joined argv then execs the raw list (`shell.py:919–945`, `_join_argv_for_jail` 74–85).
- `host_mkdir` uses `resolve_tool_path(..., for_write=True)` per path (947–985). No shell.
- `host_script` runs `check_shell_write_jail` on the body, writes a scratch file, `scan_script_source_for_outside_writes`, then `pwsh -File` / `cmd /c` / `run_python_file` (1009–1126). No `-Command`.
- No new 0.24 escape found. Residual remains: a process that starts inside the project can then write anywhere (execution jail not claimed).

### Skill zip install (catalog)

- Signed catalog + checksum; zip final-URL allowlist + 50 MiB stream cap (`install.py:18, 67–77`). Import route size cap still missing (Issue 4).

---

## Residual by-design risks

Unchanged product boundaries (not counted as issues):

- Same Windows user owns the box (loopback bootstrap when enabled, DPAPI, plaintext memory/logs).
- No project folder ⇒ `access_scope=full` (auth tree still protected).
- Shell/Host Bridge is a write jail, not an execution jail.
- Messenger `allow_all`, `REMEDY_*` debug bypasses, cloud LLM egress, trusted-skill OS rights.
- a11y push remains loopback + 32-char `job_id` capability.
- Loading an untrusted GGUF is inherent supply-chain risk (llama.cpp parser). Pull does not auto-pick a repo; search returns a list.

**Held (re-checked, not issues):** file-tool write jail + `refuse_protected_secret_path`; `_write_config` / `scrub_config_secrets`; zip Zip-Slip/bomb caps; `web_fetch` pin-on-resolve; uninstaller wipe refuses non-`.remedy` trees; HF dest flatten + repo/file sanitizers; RMB settings patch has no `host` field (llama-server stays `127.0.0.1` unless `rmb.json` is edited on disk).
