---
name: dogfood-isolated
description: >
  Run Remedy release and isolated tauri:dev side-by-side so the product can
  develop herself — release on :7400 + ~/.remedy as partner; WIP on :7410 +
  ~/.remedy-dev. Use when dogfood, dual instance, isolated dev, develop
  Remedy while using Remedy, or "work on herself".
version: 1.0.0
author: Remedy
tags: [remedy, dogfood, desktop, ports, self-dev, tauri]
---

# Dogfood isolated (release + WIP)

## Goal

Keep the **installed release** as the daily partner (memory, messengers, project
focus) while **`tauri:dev:isolated`** runs the build under test — no port/home war.

## Profiles

| Profile | How | API | Home | Vite |
|---------|-----|-----|------|------|
| **Release (partner)** | Installer / start menu | `127.0.0.1:7400` | `~/.remedy` | n/a |
| **Isolated dev** | `cd desktop && npm run tauri:dev:isolated` | `127.0.0.1:7410` | `~/.remedy-dev` | `localhost:5174` |

Env set by the isolated script: `REMEDY_HOME`, `REMEDY_API_PORT=7410`,
`REMEDY_PROFILE=dev`, `VITE_REMEDY_API`, `VITE_PORT=5174`, `REMEDY_DEV_ROOT`.

Full notes: repo `docs/DESKTOP.md` → **Dual instance**.

## When to use

- User wants to **use** Remedy while **building** Remedy
- Port 7400 / serve lock / dual `app.exe` fights
- Phrases: dogfood, dual instance, isolated dev, work on herself

## Steps

### 1. Leave release alone

- Do **not** kill `:7400` or `~/.remedy` processes unless the user asks to quit the partner.
- Messengers (Telegram, etc.) stay on **release only** — never enable two pollers.

### 2. Start isolated WIP

From repo root (or `desktop/`):

```powershell
cd desktop
npm run tauri:dev:isolated
```

Expect window title **`Remedy Desktop (dev · :7410)`**.

### 3. Point checks at the right API

| Check against | Base |
|---------------|------|
| Release partner / live soak of production | `http://127.0.0.1:7400` · `REMEDY_HOME=~/.remedy` |
| Isolated WIP | `http://127.0.0.1:7410` · `REMEDY_HOME=~/.remedy-dev` |

Scripts that honor env:

```powershell
$env:REMEDY_API = "http://127.0.0.1:7410"
$env:REMEDY_HOME = "$env:USERPROFILE\.remedy-dev"
```

### 4. Optional: seed provider keys (not whole brain)

Copy **provider keys / config snippets** into `~/.remedy-dev` once if needed.
Do **not** share live messenger state or dual-write `memory.db`.

### 5. Develop loop

1. Partner (release) edits the repo / drives tasks.  
2. Verify UI/API in **dev** window.  
3. Unit tests from repo: `uv run pytest -q`, `cd desktop && npm test`.  
4. Ship when ready → **project-etiquette** + **change-safety**.

## Safety

| Do | Don't |
|----|--------|
| Use `tauri:dev:isolated` for dogfood | Plain `tauri:dev` while release holds `:7400` (conflict) |
| Kill only own port when stopping isolated | `taskkill` all remedy / free 7400 casually |
| One messenger owner | Two Telegram long-pollers |

## Related skills

- **self-dev-loop** — full self-dev orchestration (dogfood → gauntlet → soak → stress → ship)  
- **project-etiquette** — ship gates  
- **change-safety** — blast radius before multi-file work  
- **gauntlet-security** · **soak-product** · **stress-suite**
