---
name: gauntlet-security
description: >
  Security gauntlet for Remedy — unit jail/SSRF/auth suite plus controlled live
  red-team probes. Use when security audit, red-team, gauntlet, harden, jail,
  SSRF, auth bypass, or before ship after security-sensitive changes.
version: 1.0.0
author: Remedy
tags: [security, gauntlet, redteam, ssrf, jail, auth, remedy]
---

# Security gauntlet

## Goal

Prove **jails, auth, SSRF, and host boundaries** still hold after changes.
Threat model: same Windows user ≈ owner power; harden against misconfig and
**agent tool abuse**, not full user compromise.

Artifacts: `docs/REDTEAM_*.md`, `docs/SECURITY_AUDIT_*.md`,
`docs/_redteam_live_results.json`, `scripts/_redteam_live_probes.py`.

## When to use

- Security-sensitive PR (auth, shell, paths, media, computer host, webhooks)
- User says gauntlet / red-team / harden / jail check
- Before release if security surface moved

## Preflight

1. Repo root = RemedyAI; venv ready (`uv sync` / `.venv`).  
2. Know which API is under test (release `:7400` vs isolated `:7410`).  
3. Set env if non-default:

```powershell
$env:REMEDY_API = "http://127.0.0.1:7400"   # or 7410
$env:REMEDY_HOME = "$env:USERPROFILE\.remedy"
```

## Gate A — Unit security suite (always)

From repo root:

```powershell
uv run pytest -q --tb=short `
  tests/test_api_auth.py `
  tests/test_web_fetch_ssrf.py `
  tests/test_project_write_jail.py `
  tests/test_project_scan_jail.py `
  tests/test_zip_import_security.py `
  tests/test_secret_store.py `
  tests/test_secret_acl_no_everyone.py `
  tests/test_access_scope.py `
  tests/test_agency_hardening.py `
  tests/test_tool_policy.py `
  tests/test_provider_sanitize.py
```

**Pass:** all green. **Fail:** fix before live probes.

Also useful neighbors when you touched them:

- `tests/test_hidden_process.py`, `tests/test_computer_use.py` (host auth)
- `tests/test_media_api.py`, `tests/test_uninstall_wipe_paths.py`

## Gate B — Live probes (API must be up)

Start release or isolated API first. Then:

```powershell
.\.venv\Scripts\python.exe scripts/_redteam_live_probes.py
```

Expect JSON summary on stdout; often writes `docs/_redteam_live_results.json`.

**Safe by design:** no disk wipe, no real destructive shell; probes auth, path
jail, SSRF helpers, computer host shape, webhooks.

## Gate C — Shell write jail spot checks

```powershell
.\.venv\Scripts\python.exe scripts\_prove_write_jail.py
# optional multi-run:
.\.venv\Scripts\python.exe scripts\live_project_write_jail_10x.py
```

## Gate D — Report

Summarize for the user:

| Area | Result |
|------|--------|
| Unit jails / auth / SSRF | pass/fail |
| Live probes | pass/fail + notable findings |
| Residual risk | honest (owner power, loopback bootstrap, etc.) |

If new findings: document in `docs/` dated note + fix or track; do not ship known
P0 (unauth host, jail bypass).

## Anti-patterns

- Live probes without unit suite green  
- Probing production non-loopback hosts  
- Enabling dual messengers “to test security”  
- Claiming “secure” because pytest passed while live host was down  

## Related skills

- **soak-product** — broader product health after harden  
- **project-etiquette** — ship only after gates  
- **change-safety** — blast radius for security surfaces  
- **self-dev-loop** — full self-dev sequence
