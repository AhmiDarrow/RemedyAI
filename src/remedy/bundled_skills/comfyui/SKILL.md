---
name: comfyui
description: >
  Local ComfyUI image generation end-to-end: from a blank machine (download,
  install, start, fetch models, workflows) through queue/poll/download and
  chat embeds. Defaults to http://127.0.0.1:8188 (Flux.2 Klein + Qwen3-ready).
  Use when the user wants images and has Remedy + a chat provider — even if
  ComfyUI is not installed yet.
version: 1.1.0
author: Remedy
tags: [image, generation, comfyui, art, flux, install, bootstrap]
requires: []
tools: [comfyui, local_discover, bash_exec, file_write, file_read]
# Portable local discovery — any machine, no hard-coded user paths.
local:
  services:
    - id: comfyui
      ports: [8188, 8189, 8190, 8000]
      path: /system_stats
      env_url: [COMFYUI_URL, REMEDY_COMFYUI_URL]
      config_url: [comfyui_url]
      env_home: [COMFYUI_HOME, REMEDY_COMFYUI_HOME]
      config_home: [comfyui_home]
      dir_names: [ComfyUI, comfyui, comfy, ComfyUI_windows_portable]
      entry: [main.py]
---

# ComfyUI (from zero → first image)

Local image generation via [ComfyUI](https://github.com/Comfy-Org/ComfyUI)
REST API, driven by Remedy’s built-in `comfyui` tool.

**You can start with nothing installed.** If the user asks for images and
ComfyUI is missing, follow **From scratch bootstrap** below (with their approval
for downloads / shell). Then generate with the native tool.

This skill pairs with Remedy Desktop (or CLI) **connected to any LLM provider**
(xAI, OpenAI, Ollama, …). The provider runs the agent; ComfyUI runs **on the
user’s GPU/CPU** for pixels.

## Defaults (portable — any machine)

| Item | Value |
|------|--------|
| Base URL | `http://127.0.0.1:8188` (also probes 8189, 8190, 8000, …) |
| Env | `COMFYUI_URL`, `COMFYUI_HOME`, `COMFYUI_PORT` (or `REMEDY_*`) |
| Config | `comfyui_url` / `comfyui_home` / `comfyui_port` in `~/.remedy/config.toml` |
| Side file | `~/.remedy/comfyui.json` → `{"url":"...","home":"..."}` |
| Discovery | `comfyui` action=`locate` (API ports + process + bounded home search) |
| Starter workflow | `scripts/workflows/txt2img_flux2_klein.json` (API format) |

**Do not** `list_dir` the whole disk hunting for ComfyUI. Use `action=locate` /
`status`. Discovery is built in for every OS/user.

---

## Agent decision tree (always start here)

1. Call **`comfyui` / `action=status`** (or `locate`).
2. **API up** → jump to **Generate images**.
3. **Install found, API down** → **Start ComfyUI** (use `start_hint` from the tool).
4. **Nothing found** → **From scratch bootstrap** (this is expected on a fresh PC).
5. After models are in place and the server is up → **Generate images**.

Never invent success. Never paste DSML/tool XML into chat. Prefer the `comfyui`
tool over raw `curl` (especially on Windows).

---

## From scratch bootstrap (fresh install)

Goal: machine has **no** ComfyUI → user can generate a PNG from Remedy chat.

**Ask once** before large downloads (disk: tens of GB for full Flux.2 Klein;
smaller if using 4B). Confirm GPU type: **NVIDIA** (default), AMD, Intel, or CPU-only.

### Phase A — Install ComfyUI

#### Windows (recommended: official portable)

Docs: https://docs.comfy.org/installation/comfyui_portable_windows  
Releases: https://github.com/Comfy-Org/ComfyUI/releases

| GPU | Download (latest) |
|-----|-------------------|
| NVIDIA (modern RTX) | `ComfyUI_windows_portable_nvidia.7z` |
| NVIDIA (older / CUDA 12.6) | `ComfyUI_windows_portable_nvidia_cu126.7z` |
| AMD | `ComfyUI_windows_portable_amd.7z` |
| Intel | `ComfyUI_windows_portable_intel.7z` |

Direct patterns (resolve via “latest” on GitHub Releases):

```text
https://github.com/Comfy-Org/ComfyUI/releases/latest/download/ComfyUI_windows_portable_nvidia.7z
https://github.com/Comfy-Org/ComfyUI/releases/latest/download/ComfyUI_windows_portable_nvidia_cu126.7z
https://github.com/Comfy-Org/ComfyUI/releases/latest/download/ComfyUI_windows_portable_amd.7z
https://github.com/Comfy-Org/ComfyUI/releases/latest/download/ComfyUI_windows_portable_intel.7z
```

Agent steps (with approval):

1. Pick install root — prefer a short path with space, e.g.  
   `%USERPROFILE%\ComfyUI_windows_portable` or `D:\AI\ComfyUI_windows_portable`.
2. Download the matching `.7z` (browser, `curl -L -o …`, or PowerShell  
   `Invoke-WebRequest -Uri … -OutFile …`). File is large — warn the user.
3. Extract with **7-Zip** (https://7-zip.org/). Nested archives need full extract.
4. Expected layout after extract:

```text
ComfyUI_windows_portable/
  ComfyUI/                 ← main.py lives here (this is COMFYUI_HOME)
  python_embeded/
  run_nvidia_gpu.bat       ← or run_amd_gpu.bat / run_intel_gpu.bat / run_cpu.bat
  update/
  README_VERY_IMPORTANT.txt
```

5. Persist location for Remedy:

```json
// ~/.remedy/comfyui.json
{
  "home": "C:\\Users\\<you>\\ComfyUI_windows_portable\\ComfyUI",
  "url": "http://127.0.0.1:8188"
}
```

Or set env `COMFYUI_HOME` / `COMFYUI_URL`, or `comfyui_home` / `comfyui_url` in
`~/.remedy/config.toml`.

#### Windows alternative: git + venv (advanced)

```bash
git clone https://github.com/Comfy-Org/ComfyUI.git
cd ComfyUI
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# GPU torch: follow ComfyUI / pytorch.org for the right CUDA wheel
python main.py --listen 127.0.0.1 --port 8188
```

#### macOS / Linux (git)

```bash
git clone https://github.com/Comfy-Org/ComfyUI.git
cd ComfyUI
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Apple Silicon / CUDA extras per ComfyUI docs
python main.py --listen 127.0.0.1 --port 8188
```

### Phase B — Start ComfyUI

| Install type | How to start |
|--------------|--------------|
| Windows portable NVIDIA | Double-click `run_nvidia_gpu.bat` (leave the console open) |
| Windows portable AMD/Intel/CPU | Matching `run_*_gpu.bat` / `run_cpu.bat` |
| Git install | `python main.py --listen 127.0.0.1 --port 8188` |

Success line in the console:

```text
To see the GUI go to: http://127.0.0.1:8188
```

- Leave that window open while generating.
- Verify from Remedy: `comfyui` / `action=status` → `ok: true`.
- If port differs, set `COMFYUI_URL=http://127.0.0.1:<port>`.

Optional: bind API only without auto-opening the browser still uses the same
`main.py --listen 127.0.0.1 --port 8188`.

### Phase C — Get models (Flux.2 Klein — matches bundled workflow)

Create folders if missing (under **ComfyUI root**, i.e. folder that contains `main.py`):

```text
models/diffusion_models/
models/text_encoders/
models/vae/
```

| Role | Filename (as used by starter workflow) | Folder |
|------|------------------------------------------|--------|
| UNET | `flux-2-klein-base-9b-fp8.safetensors` (or a **4B** Klein fp8 if VRAM-limited) | `models/diffusion_models/` |
| Text encoder | `qwen_3_8b_fp8mixed.safetensors` | `models/text_encoders/` |
| VAE | `flux2-vae.safetensors` | `models/vae/` |

**How to obtain them (agent guidance):**

1. Prefer **Hugging Face** / official Comfy model docs for current Flux.2 Klein
   and Qwen3 encoder packages. Filenames must match the table (or edit the
   workflow / tool defaults if the user downloads alternate names).
2. ComfyUI Manager (if installed) or browser download → save into the folders above.
3. `huggingface-cli download …` is fine when the user has HF access and disk.
4. After downloads, **restart ComfyUI** so it rescans models.

**VRAM guidance (agent should ask / recommend):**

| Hardware | Suggestion |
|----------|------------|
| ≤8 GB VRAM | 4B Klein (if available), 512×512, close other GPU apps |
| 12–16 GB | 9B fp8 often OK at 512–768 |
| 24 GB+ | 9B fp8 comfortable; higher res later |

Rough disk: **several GB to 20+ GB** depending on UNET size — state this before download.

If the user only wants a smoke test and already has SD1.5/SDXL checkpoints, you
may queue a **different** API-format workflow they provide — but Remedy’s built-in
`generate` path expects the Klein filenames above unless you customize workflow JSON.

### Phase D — Workflows (write / adapt)

#### Use the bundled starter (default)

Path (inside this skill):

`scripts/workflows/txt2img_flux2_klein.json`

Or let the tool build it: `comfyui` / `action=generate` with a `prompt`
(internally uses the same graph + injects the text prompt).

#### Write / edit a workflow

1. **Preferred for custom graphs:** open `http://127.0.0.1:8188` → build in UI →
   **Save (API Format)** → save `.json` under the project (e.g. `assets/workflows/`).
2. API format is a dict keyed by **string node ids**, each with `class_type` + `inputs`.
   Links are `[node_id, output_index]` pairs — not the pretty UI format.
3. Edit with `file_write` / editor; keep filenames in loaders aligned with disk.
4. Queue via CLI helper or POST `/prompt` (see below). For chat UX, stick to
   `action=generate` when the Klein graph is enough.

Minimal structure reminder:

```json
{
  "1": {
    "class_type": "UNETLoader",
    "inputs": { "unet_name": "flux-2-klein-base-9b-fp8.safetensors", "weight_dtype": "fp8_e4m3fn" }
  },
  "6": {
    "class_type": "CLIPTextEncode",
    "inputs": { "text": "your prompt here", "clip": ["2", 0] }
  }
}
```

**Critical for Klein:**

- `CLIPLoader` with `type: "flux2"` — **not** `DualCLIPLoader`
- Qwen3 lives in `text_encoders/`, not `clip/`
- Sampler: `euler` + `simple`, steps ~**16–20**, CFG ~**3.5**, denoise **1.0**
- First pass resolution **512×512** (or 768 if VRAM allows)

### Phase E — Wire Remedy and prove it

1. `comfyui` / `action=locate` — install path + live endpoint.
2. `comfyui` / `action=status` — `ok: true`.
3. `comfyui` / `action=generate` with a short concrete prompt  
   (e.g. “matte clay fox figurine, soft studio light, no text”).
4. Paste the **markdown image** from the tool result into the final reply so the
   desktop bubble shows the picture.
5. On failure, use **Troubleshooting** — do not claim an image was made.

---

## Generate images (steady state)

Preferred native tool (desktop / agent):

1. `comfyui` `action=status`
2. `comfyui` `action=generate` + `prompt` (+ optional size/steps if the tool exposes them)
3. Include tool markdown images in the **final** assistant message

Do **not** use PowerShell/curl for routine generates — the tool queues, waits,
downloads, and attaches to the session.

### Prompt hygiene

- Subject, style, lighting, framing explicit
- Portraits: bust/headshot + “no text, no watermark”
- Keep res modest on first pass

---

## CLI helper (optional / scripts)

From this skill directory:

```bash
python scripts/comfy_client.py status
python scripts/comfy_client.py queue path/to/workflow.json
python scripts/comfy_client.py wait <prompt_id>
python scripts/comfy_client.py run path/to/workflow.json --out ./assets/comfy
python scripts/comfy_client.py history
```

Env: `COMFYUI_URL=http://127.0.0.1:8188` (optional).

### API pattern (inline scripting)

```python
# Queue
payload = json.dumps({"prompt": workflow}).encode()
req = urllib.request.Request(
    f"{base}/prompt", data=payload,
    headers={"Content-Type": "application/json"},
)
prompt_id = json.loads(urllib.request.urlopen(req).read())["prompt_id"]

# Poll /history/{prompt_id} until present
# Download via GET /view?filename=…&subfolder=…&type=output
# Health: GET /system_stats
```

---

## Safety

- Talks to **local** ComfyUI (loopback by default). Do not `--listen 0.0.0.0`
  unless the user explicitly wants LAN access.
- **Do not** delete model packs or overwrite installs without explicit ask.
- Large downloads need **user consent** (disk + time).
- Write outputs under project `assets/`, `~/.remedy/comfy_out`, or a path the
  user chose — never silent deletes.
- Approvals: high-impact shell/file ops still follow Remedy’s Ask/Auto mode.

---

## Troubleshooting

| Symptom | Likely fix |
|---------|------------|
| Connection refused | Start ComfyUI; leave bat/console open; re-check `status` |
| No install found | Run **From scratch bootstrap**; set `COMFYUI_HOME` / `comfyui.json` |
| Wrong port | `--port 8189` → `COMFYUI_URL=http://127.0.0.1:8189` |
| 400 on `/prompt` | Missing models or UI-format JSON (need **API format**) |
| Model filename not found | Rename file or edit loader inputs to match disk |
| `CLIPLoader` missing `flux2` | Update ComfyUI to a Flux.2-capable build (`update/` bats on portable) |
| Black / empty images | Wrong VAE or text-encoder; wrong clip type |
| OOM / killed | 4B Klein, 512², fewer steps, free VRAM |
| Timeout | GPU busy / queue; raise wait timeout; check Comfy console |
| Extract failed | Use 7-Zip full extract; free disk space |
| CUDA / driver errors | Update GPU driver; correct portable package (cu126 vs modern); CPU bat as last resort |

---

## Out of scope (unless user insists)

- Installing GPU **drivers** / full CUDA Toolkit from scratch (point them to NVIDIA/AMD installers)
- Hosting ComfyUI on the public internet
- Paid cloud image APIs (use the configured chat **provider** for non-local art tools)
- Silently downloading 20+ GB without asking

---

## Quick reference — agent checklist

```text
[ ] status/locate
[ ] if missing → portable download → extract → comfyui.json
[ ] start bat / main.py --listen
[ ] status ok
[ ] models in diffusion_models / text_encoders / vae
[ ] generate + paste markdown image
[ ] iterate prompt/seed as requested
```
