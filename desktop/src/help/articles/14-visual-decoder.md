# Visual decoder

When your **chat model cannot see images** (for example DeepSeek Chat, Codestral, or many Ollama text models), Remedy can still help with screenshots, documents, and photos using a **local visual decoder**.

## What it is

- A small **vision language model** runs on **this PC only** (loopback).
- Default model: **Qwen2.5-VL 3B** (quantized GGUF).
- Runtime: **llama.cpp** `llama-server` (not bundled until you opt in).
- The decoder turns each image into a structured **text brief** (scene, OCR, UI notes, design cues). That text is injected into the chat so your main model can reason without multimodal APIs.

It is **not** a second chat partner and does **not** replace your configured provider.

## Setup

1. Open **Settings → Visual decoder**.
2. Click **Install visual decoder** (or enable it during first-run setup).
3. Wait for the download (model + projector + llama-server — several GB).
4. Leave **Enable for text-only chat models** on.

Install path: `~/.remedy/vision/` (runtime, models, `vision.json`).

## When it runs

| Chat model | Images attached | Behavior |
|------------|-----------------|----------|
| Vision-capable (GPT-4o, Claude, Gemini, …) | Yes | Native multimodal by default |
| Vision-capable + **Prefer local decoder** | Yes | Local decode → text brief (saves provider image tokens) |
| Text-only + decoder ready | Yes | Local decode → text brief |
| Text-only + decoder off | Yes | Path-only + Settings hint |

**Prefer local decoder even if chat model has vision** (Settings): always use the local brief when the decoder is ready. If the decoder is not ready, Remedy falls back to the provider’s native vision so you are not left blind.

Provider APIs usually charge **more tokens for raw images** than for a short text description of the same image, so prefer-local can reduce cost and context size. Tradeoff: the chat model only sees what the local decoder wrote (possible OCR gaps).

The composer shows a banner when images use the local path.

## Privacy

- Decoder API binds to **127.0.0.1** only.
- Images stay under `~/.remedy/attachments/` and are read locally.
- No Remedy cloud vision service.

## Uninstall

Settings → Visual decoder → **Uninstall** removes managed files under `~/.remedy/vision/`. You can reinstall later.

## Install cancel & resume

- **Cancel install** stops the download; `.partial` files stay under `~/.remedy/vision/` so **Resume install** continues without re-fetching finished bytes.
- Settings shows **CPU vs GPU**, free disk, and RAM warnings before/during install.
- **Switch to CUDA** (when NVIDIA is detected) reinstalls only the llama-server runtime and keeps model weights.
- Uninstalling Remedy with **config wipe** or **full wipe** also removes `~/.remedy/vision/` (large models).

## Troubleshooting

| Symptom | Try |
|---------|-----|
| Install stuck | Check disk space; Cancel then Resume; Refresh status |
| Slow decode | CPU runtime is normal on non-NVIDIA machines; CUDA install is faster when available |
| Low RAM warning | Close other apps; decoder needs ~6 GB+ free |
| Server won’t start | GPU busy / antivirus; try Start server; see runtime under `~/.remedy/vision/runtime` |
| Weak OCR | Re-attach a clearer crop; ask the agent to re-check specific text |
| Chat still “can’t see” | Confirm decoder **enabled** and **ready**; confirm chat model is text-only path |

## API (power users)

- `GET /api/vision/status`
- `POST /api/vision/install`
- `POST /api/vision/uninstall`
- `POST /api/vision/start` / `stop`
