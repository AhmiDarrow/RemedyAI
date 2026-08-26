---
name: rmb-muscle
description: >
  Configure and run RMB (Remedy Muscle Bridge) — her own on-device llama.cpp
  host. Use when the owner asks to start/stop a local model, pull a GGUF,
  switch chat onto RMB, fix RMB, or mentions llama.cpp / GGUF / local muscle.
version: 1.0.0
author: Remedy
tags: [rmb, local-model, llama, house, organism]
triggers:
  - \brmb\b
  - muscle bridge
  - local model
  - llama\.cpp
  - \bgguf\b
  - on-device (model|agent|llm)
---

# RMB — her muscle

RMB is **not** a second product. It is Remedy's on-device llama.cpp host
(`http://127.0.0.1:8787/v1`). The world map (`[House] RMB=…`) already tracks
whether it is up. Drive it with the **`rmb`** tool. Do not only point the
owner at Settings.

## Do this

1. `rmb action=status` — running, ready, GGUF, autofit, vision suspended.
2. No weights → `rmb action=catalog` or `rmb action=search query=…`.
   Same weights often live under more than one Hugging Face account.
   **Do not guess the host.** List repos, pick with the owner, then
   `rmb action=files repo=owner/repo`, then
   `rmb action=pull repo=… filename=….gguf`.
3. Host down → `rmb action=start` (Ask mode will checkpoint). Autofit
   sizes context / GPU layers from this PC. Leave profile=`autofit`.
4. Chat on it → `rmb action=use` (starts + binds this session). Ask them
   to send a **new message** after the bind.
5. Free the GPU for SmolVLM → `rmb action=stop`.

## Do not

- `list_dir` / `where` / crawl disk for GGUFs — status/catalog/search.
- Invent `owner/repo`. Search, then pick.
- Restart RMB **this turn** if the current provider is already RMB
  (that cuts the reply). Use a cloud chat or a new session.
- Claim the page/model is loaded from a Settings screenshot. Read `rmb status`.
- Treat RMB as Ollama. Different door (8787 vs 11434).

## House

First-home stretch already maps door `rmb` on port 8787. Machine map
injects `[House] RMB=stopped|up|ready · <gguf> · autofit`. Believe that
line; refresh with `rmb status` when it is stale.
