---
name: soak-product
description: >
  Full product soak for Remedy — API health, computer-use, plan mode, browser
  rail, concurrency, docs check, and sign-off artifacts. Use when soak, product
  soak, e2e live, computer-use checklist, or pre-merge confidence on desktop+API.
version: 1.0.0
author: Remedy
tags: [soak, e2e, computer-use, desktop, qa, remedy]
---

# Product soak

## Goal

End-to-end confidence that **Desktop + local API** still work as a product —
not only unit tests. Produces machine-readable results under `docs/`.

## When to use

- After computer-use / browser / stream / multi-tab changes  
- User says soak, full product soak, e2e live, sign-off  
- Before merge of a large feature branch  

## Preconditions

- [ ] API up (`http://127.0.0.1:7400` or isolated `:7410`)  
- [ ] Desktop running with computer host when testing CUA  
  - Release install **or** `npm run tauri:dev:isolated`  
- [ ] Build mode (not Plan-only) for click/type  
- [ ] Token: `~/.remedy/auth/local_api_token` (or `$REMEDY_HOME/auth/…`)  

```powershell
$env:REMEDY_API = "http://127.0.0.1:7400"
$env:REMEDY_HOME = "$env:USERPROFILE\.remedy"
```

## Automated soak (primary)

From repo root with API + desktop live:

```powershell
.\.venv\Scripts\python.exe scripts\_full_product_soak.py
```

- Writes **`docs/_full_product_soak_results.json`**  
- Exit **0** only if no FAIL (SKIP allowed)  
- Covers health, computer-use, plan allowlist, providers, concurrency, docs  

Also useful:

```powershell
.\.venv\Scripts\python.exe scripts\_soak_run.py
.\.venv\Scripts\python.exe scripts\live_full_product_e2e.py
.\.venv\Scripts\python.exe scripts\computer_rail_e2e.py
```

## Computer-use checklist (manual / agent)

Open F1 or:

```
help_read(id="computer-use-soak")
```

Canonical chapter: `docs/manual/computer-use-soak.md`.

Minimum path:

1. `computer_monitors` → ≥1 display  
2. `computer_screenshot` → PNG under home `computer/shots/`  
3. `computer_snapshot` → window refs  
4. Browser: navigate in-rail, snapshot `eN`, click, stop cancels jobs  
5. Offline: navigate does not surprise-open OS browser  

## Unit neighbors (always before claiming soak)

```powershell
uv run pytest -q tests/test_computer_use.py tests/test_react_stream.py tests/test_react_policy.py
uv run python scripts/check_docs.py
cd desktop && npm test
```

## Sign-off format

Update or create `docs/full-product-soak-signoff.md` (or dated note):

| Metric | Value |
|--------|-------|
| PASS / FAIL / SKIP | counts |
| API version / port | |
| Desktop host connected | yes/no |
| Verdict | PASS or blocked + next fix |

## Anti-patterns

- Soak against dead API  
- “PASS” with FAIL in `_full_product_soak_results.json`  
- Skipping computer host when the change was CUA/browser  
- Running soak scripts against the wrong home/port during dual-instance dogfood  

## Related skills

- **dogfood-isolated** — which port/home to soak  
- **gauntlet-security** — security-focused gate  
- **stress-suite** — higher load after soak is green  
- **self-dev-loop** — sequence  
- **project-etiquette** — ship after green soak when releasing  
