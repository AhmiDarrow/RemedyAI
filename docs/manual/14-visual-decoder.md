# Local model (SmolVLM2)

When your **chat model cannot see images** (DeepSeek Chat, many Ollama text models, etc.), or when you want **local** image understanding, Remedy uses a **local model on this PC**.

This is Remedy’s **local efficiency stack** — not a second chat partner.

## Contents

- [What it is](#what-it-is)
- [Why it matters](#why-it-matters)
- [Delivery](#delivery-not-in-the-installer)
- [Setup](#setup)
- [Starts with Remedy](#starts-with-remedy)
- [When it runs (images)](#when-it-runs-images)
- [Privacy](#privacy)
- [Troubleshooting](#troubleshooting)

---

## What it is

| Piece | Detail |
|-------|--------|
| **Model** | **SmolVLM2 2.2B** (quantized GGUF) — pinned id `smolvlm2-2.2b` · **Apache 2.0** |
| **Runtime** | **llama.cpp** `llama-server` · loopback only · CPU default · CUDA when NVIDIA is present |
| **Visual decoder** | Each image → structured **text brief** so any chat model can reason about it |
| **Same weights** | Vision decode and nano/helper roles share **one** local server |

It does **not** replace your configured Grok / Claude / GPT / Ollama chat model.

---

## Why it matters

| Benefit | How |
|---------|-----|
| **Any chat model + images** | Text-only providers still get OCR/layout/scene text from the brief |
| **Lower cloud cost** | Prefer-local can decode here first |
| **Privacy** | Decode stays on **127.0.0.1**; no Remedy cloud vision |
| **Commercial-friendly weights** | Apache 2.0 (unlike research-only licenses on some other small VLMs) |

---

## Delivery (not in the installer)

Weights download on first setup or from **Settings → Advanced → Local model** (~1.6 GB). Same files on every PC for a given Remedy release.

---

## Setup

1. Open **Settings** → switch Settings detail to **Advanced** if needed.  
2. Open **Local model**.  
3. Install / start when prompted.  
4. Status should become Ready (running or idle).

---

## Starts with Remedy

When installed and enabled, the local server **auto-starts** with the desktop app so image turns are not cold-started mid-chat.

---

## When it runs (images)

- Chat model has **no** native vision → decode path preferred.  
- Prefer-local / force-decode can prefer the local brief even when the cloud model supports vision.  
- Computer-use still prefers DOM/UIA before screenshots.

---

## Privacy

- Inference binds to **loopback** only.  
- Briefs may be included in the **chat context** sent to your **configured LLM provider** for that turn.  
- OAuth and provider API keys are unrelated and stay in `~/.remedy/auth/`.

See [Security & data](04-security-and-data).

---

## Troubleshooting

| Symptom | Try |
|---------|-----|
| Not installed | Settings → Advanced → Local model → Install |
| Stuck downloading | Check disk space; resume install |
| CUDA issues | CPU runtime still works; GPU is optional |
| Wrong model name in old docs | Product default is **SmolVLM2 2.2B**, not a retired Qwen 3B pin |

---

## Related

- [Security & data](04-security-and-data) · [Continuity workers](17-nanoswarm) · [Free providers](15-free-providers)
