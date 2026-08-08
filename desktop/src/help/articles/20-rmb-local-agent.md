# RMB — local agent host

**RMB** (Remedy Muscle Bridge) is Remedy’s **built-in local chat host** for coding and tool use. It runs on your PC with no API key.

## What it is

| | |
|--|--|
| **Brand** | RMB in Settings and the provider list |
| **Engine** | llama.cpp (`llama-server`) |
| **Default endpoint** | `http://127.0.0.1:8787/v1` |
| **Not** | The retired custom `.rmb4` lattice format (research only) |

## Setup

1. Open **Settings → RMB**
2. Put a coding GGUF (recommended: **Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf**) in `~/.remedy/rmb/models/`  
   or leave it discoverable under your models folders
3. Install **Local vision** once if `llama-server` is missing (shared runtime)
4. Click **Start RMB**, then **Use as chat provider**

## Profiles

| Profile | Context | Use |
|---------|---------|-----|
| **Agent** | 8k | Default — tool chains + coding on 12GB GPUs |
| **Turbo** | 4k | Snappier, shorter windows |
| **Quality** | 16k | More context when free VRAM allows |

## Endless coding sessions

RMB is wired for long agent work — **context is invisible**:

- **Silent harness** prune / offload / Session Brief run automatically (soft ~55% / strong ~78% of n_ctx)
- No user-facing “please compress” system chatter — Remedy just remembers and keeps going
- **Session Brief** + middleman hold intent, paths, and decisions off the hot prompt
- **Tool budgets** use a larger local `max_tokens` ceiling for multi-step tool JSON and patches
- Tool chains are **not** interrupted mid-flight for compress nudges

## Exclusive host (SmolVLM)

While **RMB is running** (or marked suspended after Start):

- **SmolVLM is unloaded** and will not auto-start until you **Stop RMB**
- Vision routes, install “start server”, and decode paths all hard-skip
- App boot prefers **RMB first** when RMB auto-start is on (no Smol thrash)

When RMB is your chat provider:

- Nano briefs and local helpers use the **same RMB host** (8787)
- Attached images are **file paths** for tools — not a separate vision decode stack

Any **GGUF** works: drop it in `~/.remedy/rmb/models/`, set path in Settings, or leave one file there and Start RMB will pick it up.

## Tips

- Prefer the **Coder** family for agent tools (file edits, multi-step calls)
- Install Local vision only when you need **cloud-text + local image decode** without RMB chat
- Chat stays on this PC
