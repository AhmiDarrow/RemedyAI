# Vision & nano swarm (local model)

When your **chat model cannot see images** (for example DeepSeek Chat, Codestral, or many Ollama text models), Remedy uses a **local model on this PC** for screenshots, documents, and photos — and the same model powers **nano swarm** assist.

## What it is

- A small **vision language model** runs on **this PC only** (loopback).
- **One model for every local role:** vision decode, nano swarm, and (later) helper bot.
- Model: **Qwen2.5-VL 3B** (quantized GGUF) — **same id and files on every PC** for a given Remedy release (pinned catalog + checksums).
- Runtime: **llama.cpp** `llama-server` — CPU by default; CUDA when NVIDIA is detected (same Qwen weights).
- Vision turns each image into a structured **text brief**. That text is injected into chat so your main model can reason without multimodal APIs.

It is **not** a second chat partner and does **not** replace your configured provider.

## Delivery (not in the installer)

To keep the installer small, Qwen is **downloaded once** on first run (Setup Wizard or Settings), not packed into the `.exe`.

| Step | What happens |
|------|----------------|
| First run | Download pinned GGUF + mmproj + llama-server (~2.8 GB+) |
| Verify | SHA256 / size from Remedy’s catalog |
| After install | **Server starts automatically** and **starts with Remedy** on every launch |
| Later updates | No re-download unless the catalog model pin changes |

## Setup

1. **Setup Wizard** → Local vision & nano swarm → leave install on → Finish.  
2. Or **Settings → Vision & nano swarm** → **Download & install local model**.  
3. Watch the status dock for download progress.  
4. When ready, llama-server **auto-starts** (no manual Start required for normal use).

Path: `~/.remedy/vision/` (models, runtime, `vision.json`).

## Starts with Remedy

Once installed and **enabled**:

- API/desktop boot runs **auto-start** (`vision.auto_start = true` by default).  
- Full quit stops the local server (same as today).  
- Hide-to-tray keeps the server up.  
- Settings still offers Start/Stop for power users; default is **on with the app**.

## Nano swarm

Deterministic bots (Token · Pattern · Memory · Skill) run without the local LLM.  
Vision and optional local Router assist need the server — which is why auto-start matters after install.

`GET /api/nanoswarm/status` · Settings swarm panel · `/harness` in chat.

## When it runs (images)

| Chat model | Images | Behavior |
|------------|--------|----------|
| Vision-capable | Yes | Native multimodal by default |
| Vision-capable + prefer local | Yes | Local decode → text brief |
| Text-only + local ready | Yes | Local decode → text brief |
| Text-only + not installed | Yes | Path-only + Settings hint |

## Privacy

- Decoder API binds to **127.0.0.1** only.  
- Images stay under `~/.remedy/attachments/`.  
- No Remedy cloud vision service.

## Troubleshooting

| Symptom | Try |
|---------|-----|
| Download stuck | Check disk/network; Cancel then Resume in Settings |
| Not starting with Remedy | Settings → enable local model; confirm status Ready |
| Slow on CPU | Normal without NVIDIA; Use CUDA when available |
| Weak OCR | Clearer crop; re-attach image |

## Related

[Security & data](04-security-and-data) · [Chat & sessions](05-chat-and-sessions)
