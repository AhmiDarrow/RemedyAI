---
name: comfyui
description: >
  Operate a local ComfyUI instance for AI image generation — health check,
  queue workflows, poll history, download outputs. Defaults to
  http://127.0.0.1:8188 (Flux.2 Klein + Qwen3-ready).
version: 1.0.0
author: Remedy
tags: [image, generation, comfyui, art, flux]
requires: []
tools: [comfyui, local_discover]
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

# ComfyUI

Local image generation via [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
REST API. Use this skill whenever the user wants images, portraits, concept art,
textures, or batch generation from a running ComfyUI server.

## Defaults (portable — any machine)

| Item | Value |
|------|--------|
| Base URL | `http://127.0.0.1:8188` (also probes 8189, 8190, …) |
| Env | `COMFYUI_URL`, `COMFYUI_HOME`, `COMFYUI_PORT` (or `REMEDY_*`) |
| Config | `comfyui_url` / `comfyui_home` / `comfyui_port` in `~/.remedy/config.toml` |
| Side file | `~/.remedy/comfyui.json` → `{"url":"...","home":"..."}` |
| Discovery | `comfyui` action=`locate` (API ports + process + bounded home search) |

**Do not** `list_dir` the whole disk. Discovery is built into the tool for every OS/user.

## Prerequisites

1. ComfyUI installed and running:
   ```
   cd ComfyUI
   python main.py --listen
   ```
2. Models present for the workflow you queue (see Flux.2 Klein below).

## When to use

- User asks to generate / iterate on images with ComfyUI
- Check whether ComfyUI is up and which system stats it reports
- Queue a workflow JSON, wait for completion, save the PNG

## Safety

- Talks only to the local ComfyUI host (default loopback).
- Does **not** delete models, overwrite ComfyUI installs, or change server config.
- Write outputs under the project `assets/` or a user-chosen folder — never silent deletes.

## Preferred: built-in `comfyui` tool (desktop agent)

In Remedy chat, **always** use the native tool (not curl, not invented bash):

1. `comfyui` with `action="status"` — is the server up?
2. `comfyui` with `action="generate"` and a `prompt` — runs Flux.2 Klein txt2img,
   downloads the PNG, **attaches it to the current session**, and returns markdown.
3. Paste the markdown image lines from the tool result into your **final answer**
   so the user sees the picture in the bubble.

Do **not** print tool XML / DSML as chat text. Do **not** use `curl` on Windows
for this — the `comfyui` tool handles HTTP.

## CLI helper (optional / scripts)

From the skill directory:

```bash
python scripts/comfy_client.py status
python scripts/comfy_client.py queue path/to/workflow.json
python scripts/comfy_client.py wait <prompt_id>
python scripts/comfy_client.py run path/to/workflow.json --out ./assets/comfy
python scripts/comfy_client.py history
```

Env: `COMFYUI_URL=http://127.0.0.1:8188` (optional).

## API pattern (if scripting inline)

### 1. Queue

```python
payload = json.dumps({"prompt": workflow}).encode()
req = urllib.request.Request(
    f"{base}/prompt", data=payload,
    headers={"Content-Type": "application/json"},
)
prompt_id = json.loads(urllib.request.urlopen(req).read())["prompt_id"]
```

### 2. Poll history

```python
while time.time() - start < timeout:
    hist = json.loads(urllib.request.urlopen(f"{base}/history/{prompt_id}").read())
    if prompt_id in hist:
        break
    time.sleep(1.5)
```

### 3. Download outputs

From history outputs → `SaveImage` (or similar) → `filename` / `subfolder` / `type`,
then `GET {base}/view?filename=…&subfolder=…&type=output`.

### 4. Health

`GET {base}/system_stats` — if it fails, tell the user to start ComfyUI.

## Workflow JSON format

ComfyUI API workflows are objects keyed by node id:

```json
{
  "3": {
    "class_type": "KSampler",
    "inputs": {
      "seed": 42,
      "steps": 20,
      "cfg": 3.5,
      "sampler_name": "euler",
      "scheduler": "simple",
      "denoise": 1.0,
      "model": ["1", 0],
      "positive": ["6", 0],
      "negative": ["7", 0],
      "latent_image": ["5", 0]
    }
  }
}
```

Export API-format JSON from the ComfyUI UI (**Save (API Format)**), or use the
bundled starter under `scripts/workflows/txt2img_flux2_klein.json` after models exist.

## Recommended models: Flux.2 Klein

| Role | File | ComfyUI folder |
|------|------|----------------|
| UNET | `flux-2-klein-base-9b-fp8.safetensors` (or 4B if VRAM-limited) | `models/diffusion_models/` |
| Text encoder | `qwen_3_8b_fp8mixed.safetensors` | `models/text_encoders/` |
| VAE | `flux2-vae.safetensors` | `models/vae/` |

**Critical for Klein:**

- Use `CLIPLoader` with `type: "flux2"` — **not** `DualCLIPLoader`
- Qwen3 lives in `text_encoders/`, not `clip/`
- Typical sampler: `euler` + `simple`, steps **20**, CFG **3.5**, denoise **1.0**

### Prompt hygiene

- Describe subject, style, lighting, framing explicitly
- For portraits: bust / headshot framing + “no text, no watermark”
- Keep resolution modest on first pass (e.g. 512–1024) to avoid OOM

## Agent procedure

1. Call **`comfyui` / action=status**. If down, tell the user to start ComfyUI
   (`python main.py --listen` in the ComfyUI install); do not pretend success.
2. Call **`comfyui` / action=generate** with a concrete prompt (invent one if the
   user said “you decide”).
3. In the **final chat reply**, include the markdown image from the tool result
   so it appears in the session UI.
4. Iterate with a new generate call (new seed/prompt) if the user wants changes.

## Troubleshooting

| Symptom | Likely fix |
|---------|------------|
| Connection refused | Start ComfyUI; check port (`--port 8189` → set `COMFYUI_URL`) |
| `CLIPLoader` missing `flux2` | Update ComfyUI to a Flux.2-capable build |
| Black / empty images | Wrong VAE or text-encoder mismatch |
| OOM / killed | Use 4B Klein, lower resolution, close other GPU apps |
| Timeout | Raise `--timeout`; check GPU load / queue backlog |
| 400 on `/prompt` | Workflow still UI-format (needs API format) or missing models |

## Out of scope

- Installing CUDA / drivers
- Hosting a public ComfyUI without explicit user request
- Paying cloud image APIs (use provider keys / other skills for those)
