# Local vision & on-device Qwen

When your **chat model cannot see images** (DeepSeek Chat, many Ollama text models, etc.), or when you want to **spend fewer paid vision tokens**, Remedy uses a **local model on this PC**.

This is Remedy’s **local brain for efficiency** — not a second chat partner.

## Contents

- [What it is](#what-it-is)
- [Why it matters](#why-it-matters)
- [Delivery](#delivery-not-in-the-installer)
- [Setup](#setup)
- [Starts with Remedy](#starts-with-remedy)
- [When it runs (images)](#when-it-runs-images)
- [Continuity assist](#continuity-assist)
- [Privacy](#privacy)
- [Troubleshooting](#troubleshooting)

---

## What it is

| Piece | Detail |
|-------|--------|
| **Model** | **Qwen2.5-VL 3B** (quantized GGUF) — pinned id + checksums per release |
| **Runtime** | **llama.cpp** `llama-server` · loopback only · CPU default · CUDA when NVIDIA is present |
| **Visual decoder** | Each image → structured **text brief** injected so any chat model can reason about it |
| **Same weights** | Vision decode, harness brief assist, and nano refine share **one** local server |

It does **not** replace your configured Grok / Claude / GPT / Ollama chat model.

---

## Why it matters

| Benefit | How |
|---------|-----|
| **Any chat model + images** | Text-only providers still get OCR/layout/scene text from the brief |
| **Lower cloud cost** | **Prefer local** can decode here first and skip expensive provider vision |
| **Privacy** | Decode stays on **127.0.0.1**; no Remedy cloud vision |
| **Harness efficiency** | Session Brief can refresh in the background without another paid API call |

---

## Delivery (not in the installer)

The Windows installer stays small: Qwen is **downloaded once** (Setup or Settings), not packed into the `.exe`.

| Step | What happens |
|------|----------------|
| First run | Download pinned GGUF + mmproj + llama-server (~2.8 GB+) |
| Verify | SHA256 / size from Remedy’s catalog |
| After install | Server **starts automatically** and **starts with Remedy** on every launch |
| Later updates | No re-download unless the catalog model pin changes |

Path: `~/.remedy/vision/` (models, runtime, `vision.json`).

---

## Setup

1. **Setup Wizard** → Local vision / nano swarm → leave install on → Finish.  
2. Or **Settings → Vision & nano swarm** → **Download & install local model**.  
3. Watch the status dock for download progress.  
4. When ready, llama-server **auto-starts** (no manual Start for normal use).

---

## Starts with Remedy

Once installed and **enabled**:

- API/desktop boot runs **auto-start** (`vision.auto_start = true` by default).  
- Full quit stops the local server.  
- Hide-to-tray keeps the server up.  
- Settings still offers Start/Stop for power users.

---

## When it runs (images)

| Chat model | Images | Behavior |
|------------|--------|----------|
| Vision-capable | Yes | Native multimodal by default |
| Vision-capable + prefer local | Yes | Local decode → text brief (saves provider vision) |
| Text-only + local ready | Yes | Local decode → text brief |
| Text-only + not installed | Yes | Path preview + Settings hint to install |

Chat **display** of images (markdown preview in bubbles) works for every model; **understanding** uses provider vision or this decoder.

---

## Continuity assist

Deterministic continuity workers (Token · Pattern · Memory · Skill · Router) do **not** need Qwen on the hot path.

When llama-server is up, the same Qwen may:

- Refresh **Session Brief** in the background  
- Optionally refine router labels (never blocks a turn waiting on the local model)

They never appear as separate chat partners. Operators: [Continuity workers](17-nanoswarm).

`GET /api/nanoswarm/status` · `/harness` in chat (diagnostics).

---

## Privacy

- Decoder API binds to **127.0.0.1** only.  
- Images stay under `~/.remedy/attachments/`.  
- No Remedy cloud vision service.

---

## Troubleshooting

| Symptom | Try |
|---------|-----|
| Download stuck | Disk/network; Cancel then Resume in Settings |
| Not starting with Remedy | Settings → enable local model; status Ready |
| Slow on CPU | Normal without NVIDIA; enable CUDA when available |
| Weak OCR | Clearer crop; re-attach image |
| Status bar frozen historically | Fixed builds use cheap `/api/ping` (not blocking vision health) |

---

## Related

[Security & data](04-security-and-data) · [Chat & sessions](05-chat-and-sessions) · [Memory & harness](06-memory-and-harness) · [Continuity philosophy](16-continuity-philosophy)
