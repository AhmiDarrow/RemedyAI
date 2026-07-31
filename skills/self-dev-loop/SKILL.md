---
name: self-dev-loop
description: >
  Meta-loop for Remedy developing herself — dogfood dual instance, change-safety,
  unit tests, security gauntlet, product soak, stress, then ship gates. Use when
  self-dev, dogfood loop, work on RemedyAI monorepo end-to-end, or "run the full
  quality loop on ourselves".
version: 1.0.0
author: Remedy
tags: [self-dev, dogfood, qa, soak, stress, gauntlet, remedy, meta]
---

# Self-dev loop (Remedy on Remedy)

## Goal

One **ordered** playbook when the user wants Remedy to improve **this** monorepo
while staying a trustworthy partner. Prefer **activating sub-skills** rather than
re-inventing commands.

## When to use

- Working **in** the RemedyAI repo as the project  
- User says self-dev, dogfood loop, full quality loop, “run gauntlet then soak”  
- Multi-hour sessions that must not skip security or ship discipline  

## Default sequence

Execute **in order**. Skip a stage only if the user explicitly narrows scope
(e.g. “unit tests only”) or the stage does not apply (docs-only change).

```text
0. Orient          → project-overview / AGENTS.md / git status
1. Dogfood layout  → dogfood-isolated (release partner + isolated WIP if needed)
2. Blast radius    → change-safety
3. Implement       → write-tests, refactor-safe as needed
4. Unit gates      → pytest + desktop npm test/build + check_docs
5. Gauntlet        → gauntlet-security (if security surface touched or user asked)
6. Soak            → soak-product (if desktop/API/CUA/stream touched or user asked)
7. Stress          → stress-suite (optional; after soak green or user asked)
8. Ship            → project-etiquette (test → docs → build → commit → CI → publish)
```

### Activate helpers

```
skill_activate(skill="dogfood-isolated")
skill_activate(skill="change-safety")
skill_activate(skill="gauntlet-security")
skill_activate(skill="soak-product")
skill_activate(skill="stress-suite")
skill_activate(skill="project-etiquette")
```

## Stage details (short)

### 0. Orient

```powershell
git status -sb
git log -5 --oneline
```

Read root `AGENTS.md` for ship gates, installer naming, smoke matrix.

### 1. Dogfood layout

- **Partner:** release on `:7400` + `~/.remedy` (this chat, if applicable).  
- **WIP UI:** `cd desktop && npm run tauri:dev:isolated` when validating desktop changes.  
- Never kill release when stopping isolated.

### 2–3. Change + implement

- Name blast radius (chat, gateway, rails, settings, packaging).  
- Prefer durable fixes; keep tree runnable.  
- Pair with **write-tests** for behavior changes.

### 4. Unit gates (minimum always before ship)

```powershell
uv run pytest -q
cd desktop; npm test; npm run build
uv run python scripts/check_docs.py
```

### 5–7. Live quality (when scope needs it)

| Stage | Skill | Trigger |
|-------|--------|---------|
| Gauntlet | **gauntlet-security** | auth, jail, SSRF, host, sanitize |
| Soak | **soak-product** | CUA, browser, stream, multi-tab, “soak” |
| Stress | **stress-suite** | concurrency, multi-provider, “stress/break” |

Set `REMEDY_API` / `REMEDY_HOME` to the profile under test.

### 8. Ship

Follow **project-etiquette** exactly: no publish on red CI; version only for real
releases; desktop tag `vX.Y.Z` for installers.

## Reporting template

| Stage | Result | Evidence |
|-------|--------|----------|
| Dogfood | release up / isolated up / n/a | ports |
| Blast radius | surfaces listed | |
| Unit | pass/fail | commands |
| Gauntlet | pass/fail/skip | |
| Soak | pass/fail/skip | `docs/_full_product_soak_results.json` |
| Stress | pass/fail/skip | |
| Ship | commit / CI / publish | |

## Anti-patterns

- Stress before unit green  
- Gauntlet against wrong port during dual-instance  
- Shipping without **project-etiquette** when user asked for release  
- Dual messenger pollers “for testing”  
- Claiming self-dev done when only the chat replied and no commands ran  

## Related skills

- **dogfood-isolated** · **gauntlet-security** · **soak-product** · **stress-suite**  
- **change-safety** · **project-etiquette** · **github** · **session-handoff**  
