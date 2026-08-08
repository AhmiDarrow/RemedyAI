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

## Setup (owner UI)

1. Open **Settings → Remedy Muscle Bridge** (local models).
2. Drop a GGUF into `~/.remedy/rmb/models/` or pick a catalog / path.
3. Choose **profile** (agent / turbo / quality), context size, GPU layers.
4. **Start RMB**, then **Use as chat provider** if you want chat on that host.
5. Start a **new message** so the session binds to the new provider.

Refresh status after install or path changes. Restart RMB after changing
model, context, or GPU layer settings.

## Profiles (short)

- **Agent** — balanced tool use and coding
- **Turbo** — faster / shorter generations
- **Quality** — slower, stronger answers when the model supports it

Exact sampling knobs live in RMB settings; profiles only pick a sensible preset.

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
