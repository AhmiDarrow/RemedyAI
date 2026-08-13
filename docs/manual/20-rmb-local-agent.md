# RMB local agent (Remedy Muscle Bridge)

**RMB** is Remedy’s optional **on-device agent host** for long coding sessions.
It runs a local **llama.cpp** server so chat and tools can stay on this PC when
you want muscle without a cloud provider.

## What it is

| Piece | Role |
|-------|------|
| **Engine** | llama.cpp (OpenAI-compatible HTTP API) |
| **Models** | Any GGUF you place under `~/.remedy/rmb/models/` (or a path you set) |
| **Endpoint** | Default `http://127.0.0.1:8787/v1` |
| **Chat** | Settings → **Use as chat provider** switches the active provider to RMB |

While RMB is running, the local **SmolVLM** visual decoder is **suspended** so
GPU memory stays exclusive to the agent host. Stop RMB to free vision again.

## When to use it

- Offline or low-bandwidth coding
- Sensitive repos you prefer not to send to a cloud LLM
- Long multi-tool sessions with a fixed local model

Cloud providers remain first-class; RMB is an optional local muscle, not a
second product personality.

Local models emit bash. The **Host Bridge** (see [Agency](18-agency.md)) rewrites
POSIX-ish `bash_exec` strings for Windows cmd, runs PowerShell via `pwsh -File`,
and prefers `host_run(argv)` so quoting does not burn the small window.

## Setup (owner UI)

1. Open **Settings → Remedy Muscle Bridge** (local models).
2. Drop a GGUF into `~/.remedy/rmb/models/` or pick a catalog / path.
3. Leave **Autofit** on (default) — Remedy sizes context, GPU layers, and KV
   cache from this PC’s VRAM/RAM so the GGUF actually loads. Or pick a fixed
   profile (agent / turbo / quality) or type a context size to lock it.
4. **Start RMB** (required — RMB does **not** auto-start when the API/serve
   process comes up). Optionally enable **auto-start** only if you want the host
   to load with every Remedy launch.
5. **Use as chat provider** if you want chat on that host.
6. Start a **new message** so the session binds to the new provider.

Refresh status after install or path changes. Restart RMB after changing
model, context, or GPU layer settings.

## Profiles (short)

- **Autofit** — default. Measures VRAM/RAM + the GGUF and starts the largest
  stable window that fits (full GPU offload when possible, quantized KV or
  fewer layers if not). If load OOMs, RMB walks the fit down and retries.
- **Agent** — fixed 8k window
- **Turbo** — fixed 4k, snappier turns
- **Quality** — fixed 16k (can OOM on small GPUs; prefer Autofit)

Exact sampling knobs live in RMB settings; Autofit / profiles only pick host
size. Prefix cache (`--cache-reuse`) is on when the runtime supports it so
tool loops do not re-process the system prompt every step.

## Safety and scope

RMB still runs as **your Windows user** with the same **filesystem scope** and
**approval** settings as cloud modes. Untrusted project scope, write jails, and
Ask/Auto approvals apply the same way.

API keys for cloud providers never leave this PC as model input; RMB has no
cloud key of its own.

## Troubleshooting

| Symptom | What to try |
|---------|-------------|
| Not ready | Confirm GGUF path + runtime present; open Settings status card |
| Start failed | Check port 8787 free; lower context / GPU layers on small machines |
| Vision missing | Stop RMB — SmolVLM is suspended while RMB owns the GPU path |
| Chat still cloud | Use **Use as chat provider**, then send a new message |

See also: [Visual decoder](14-visual-decoder.md), [Free providers](15-free-providers.md),
[Security & data](04-security-and-data.md).
