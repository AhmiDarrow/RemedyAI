---
name: change-safety
description: >
  Blast-radius protocol before code changes — name the surface, list coupled
  neighbors, run paired checks, and smoke UI/chrome/messengers that unit tests
  miss. Use when implementing, fixing, refactoring, reviewing risk, or when
  the user says don't break anything / check neighbors / change safety.
version: 1.0.0
author: Remedy
tags: [quality, regression, safety, coding, review]
---

# Change-safety (blast radius)

Ship gates catch **red tests**. This skill catches **“fixed A, broke B.”**

Run this **before** multi-file work (and again before commit/ship).

## When to use

- Implement / fix / refactor / “don't break X”
- Desktop shell, messengers, browser embed, settings, concurrent chat
- Any monorepo or multi-surface app (desktop + web, agent + gateway)

## Steps (always)

### 1. Name the change

One sentence: what **user- or API-visible** behavior changes?

### 2. Classify the surface

Pick primary (+ secondary): chat/ReAct · messengers/gateway · desktop chrome ·
workspace rails (browser/terminal) · settings/secrets · docs-only · release/packaging.

### 3. Blast-radius questions

1. **Same SPA / shared frontend?** UI change may need rebuild + server restart for WebUI.  
2. **Two processes?** Sidecar + UI, dual pollers, dual port owners — avoid fights.  
3. **Cross-path?** Desktop stream vs messenger vs legacy chat — session provider/model.  
4. **OS / Windows-only?** Paths, secrets, WebView2, installers — reason about those.  
5. **Hard to unit-test?** Title bar, tray, embedded browser, live bots → **manual smoke**.  
6. **Known failure class?** Prefer architecture that removes the class (e.g. OS window
   decorations for min/max/close; exclusive poll lock for Telegram) over another patch.

### 4. Paired checks

| If you touch… | Also verify… |
|---------------|--------------|
| Gateway / Telegram | Single poller; inbound + outbound; no 409 thrash |
| Session stream / LLM | Provider switch isolation; messenger uses session LLM |
| SSE / messages UI | No force-reload mid-stream |
| Window chrome | OS min/max/close; tray; close-to-tray; quit |
| Browser rail | Load works; popout chrome clickable; external open |
| Settings / secrets | Save + reconnect; no secrets in plain config |
| Docs | Sync/check scripts the project uses |
| Version / release | All version surfaces aligned |

### 5. Manual smoke (when chrome / messengers / browser touched)

One clean instance: launch → short chat → window controls → browser rail →
messenger round-trip (if enabled) → full quit → relaunch (no dual serve).

### 6. Neighbor rule before commit

List **files you did not edit** that couple to this change and confirm tested or
safe. If unsure: targeted test or commit note `Risk: … / Smoke: …`.

### 7. What CI does not prove

CI does not click title bars, drive Telegram, or exercise multiwebview on a real
GPU. Green CI is **necessary, not sufficient** for those zones.

## After implementation

- Targeted tests for behavior you changed  
- Full suite when shipping or when user asked “test everything”  
- For **ship/release**, also activate **project-etiquette** (gate chain)

## Anti-patterns

- “Tests pass” without smoke for the surface you changed  
- Patching a recurring class of bug without changing architecture  
- Shipping desktop UI without WebUI rebuild/restart when SPA is shared  
- Dual processes fighting a single bot token or port
