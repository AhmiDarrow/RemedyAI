---
name: stress-suite
description: >
  Live stress and break suites for Remedy API/desktop — multi-turn storms,
  concurrent sessions, multi-provider rotation, adversarial break battery.
  Use when stress, load, break suite, hammer, continuous test, or find weak spots.
version: 1.0.0
author: Remedy
tags: [stress, load, concurrency, break, qa, remedy]
---

# Stress suite

## Goal

Push the **running** local API (and optionally desktop) hard enough to surface
races, stalls, auth flakes, and provider-bind bugs that unit tests miss.

**Not** a substitute for soak or gauntlet — run those first when security/product
paths changed.

## When to use

- User says stress, hammer, break suite, continuous test, weak spots  
- After concurrency / multi-provider / stream abort work  
- Nightly or pre-release confidence (with soak green)  

## Preflight

1. API healthy: `GET {REMEDY_API}/api/ping`  
2. Bearer token present under `$REMEDY_HOME/auth/local_api_token`  
3. Prefer **release** API for partner-grade stress; use isolated only when testing that build  

```powershell
$env:REMEDY_API = "http://127.0.0.1:7400"
$env:REMEDY_HOME = "$env:USERPROFILE\.remedy"
# optional knobs:
# $env:REMEDY_STRESS_PASSES = "20"
```

## Suites (pick by intent)

### A. Full stress battery (API)

```powershell
.\.venv\Scripts\python.exe scripts\stress_full_suite.py
.\.venv\Scripts\python.exe scripts\live_stress_remedy.py
.\.venv\Scripts\python.exe scripts\stress_desktop_api.py
```

`stress_full_suite.py`: multi-pass with **provider rotation** (demo / deepseek / xai / poe).
Honor `REMEDY_STRESS_PASSES`, `REMEDY_STRESS_ROTATE_EVERY`.

### B. Adversarial break suite

```powershell
.\.venv\Scripts\python.exe scripts\live_agent_break_suite.py
```

Abuse-shaped turns, concurrency, policy edges. Stop on first hard FAIL if debugging.

### C. Multi-turn / workout

```powershell
.\.venv\Scripts\python.exe scripts\live_10_turn_stress.py
.\.venv\Scripts\python.exe scripts\live_agent_full_workout.py
.\.venv\Scripts\python.exe scripts\live_continuous_test.py
```

### D. Desktop UI / settings matrix

```powershell
.\.venv\Scripts\python.exe scripts\live_desktop_ui_10runs.py
.\.venv\Scripts\python.exe scripts\live_settings_matrix.py
```

Needs desktop automation hooks / live UI — skip if headless CI only.

### E. Security-adjacent live chat soak

```powershell
.\.venv\Scripts\python.exe scripts\live_soak_security_chat.py
```

## How to run safely

| Rule | Why |
|------|-----|
| Start small (`REMEDY_STRESS_PASSES=5`) then scale | Find fail fast |
| One stress driver at a time | Avoid false races from two scripts |
| Watch stall banner / logs | Quiet streams ≠ always hung |
| Do not force-quit release partner mid-dogfood | Use isolated profile for WIP |

## Report format

| Suite | Passes | Fails | Weak | Notes |
|-------|--------|-------|------|-------|
| stress_full_suite | | | | |
| break_suite | | | | |
| … | | | | |

Call out **repro steps** for any FAIL (session id, provider, last request).

## Anti-patterns

- Stress before unit + soak when you just changed jails or CUA  
- Blaming providers for bugs that are session-bind races  
- Leaving 50-pass storms running overnight without log capture  
- Hitting non-loopback APIs  

## Related skills

- **soak-product** — functional green before load  
- **gauntlet-security** — auth/jail first  
- **self-inject** — test-gated auto-improve  
- **self-dev-loop** — orchestration  
