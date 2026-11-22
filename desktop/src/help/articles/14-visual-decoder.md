# Local mind (Qwen3.5-4B)

Remedy’s **local mind** is a single Qwen3.5-4B multimodal model on this PC (text + vision). It is her inner mind — not a second chat partner. Frontier chat models are muscle: they usually get a compact **image digest**, not pixels, unless the mind asks for the image or you turn on **Always send images**.

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
| **Model** | **Qwen3.5-4B** (Q6_K, Q4_K_M autofit fallback, official mmproj-F16) — pinned id `qwen35-4b-mind` · **Apache 2.0** |
| **Runtime** | **llama.cpp** `llama-server` (b12628) · loopback only (`127.0.0.1:8740`) · CPU default · GPU build when this PC has a matching card (Windows CUDA / Linux Vulkan) |
| **Visual decoder** | Each image → a structured **JSON digest** so any chat model can reason about it. Pixels go to muscle only when needed. |
| **Same weights** | Vision, nano, helper, dreams, workers, and media digest share **one** local server |

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

Weights download automatically the first time Remedy starts (~4.2 GB for Q6_K + mmproj, or the Q4_K_M fallback on smaller machines). Same model files on every PC for a given Remedy release. The **llama-server** binary is OS-specific: Windows downloads the pinned CPU/CUDA zip; Linux downloads the pinned Ubuntu CPU/Vulkan `tar.gz` and marks it executable. Settings shows progress; **Retry download** is only if that first pull failed.

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

- A user-attached photo is digested locally first; muscle gets the digest by default.  
- If the mind is not ready and the chat model has native vision, Remedy falls back to sending the image so the turn is not left blind.  
- Computer-use still prefers DOM/UIA before screenshots (click-target pixels may still be attached).  

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
| Not installed | Wait for the first-run download, or Settings → Retry download |
| Stuck downloading | Check disk space; resume install |
| GPU runtime issues | CPU runtime still works; GPU is optional |
| Wrong model name in old docs | Product default is **Qwen3.5-4B** (`qwen35-4b-mind`). Legacy SmolVLM2 / Qwen-VL pins are migrated automatically; old files stay on disk. |

---

## Related

- [Security & data](04-security-and-data) · [Continuity workers](17-nanoswarm) · [Free providers](15-free-providers)
