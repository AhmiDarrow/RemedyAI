# Local model resources (optional / offline only)

## Production (default)

**Remedy does not package Qwen or llama-server in the installer.**

| What | Where |
|------|--------|
| Installer size | Small (app + sidecar only) |
| Model delivery | **First-run download** in Setup Wizard / Settings |
| Install path | `~/.remedy/vision/` (models + runtime + `vision.json`) |
| After install | Local server **starts with Remedy** |

Pinned model (every PC, same bytes after download):

- id: `qwen2.5-vl-3b`
- `Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf`
- `mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf`

Tauri **does not** map this folder into the NSIS bundle (`tauri.conf.json` has no `resources/local` entry).

## Offline / air-gap (optional)

If you must skip the network download:

1. Install once on a machine with internet, **or** copy verified GGUFs + runtime.
2. Stage into this folder (gitignored — safe to fill locally):

   ```bash
   python scripts/stage_local_bundle.py --from-vision-home
   ```

3. Run the app with:

   ```bat
   set REMEDY_LOCAL_BUNDLE=C:\path\to\RemedyAI\desktop\resources\local
   ```

4. Or copy staged files into the user’s `~/.remedy/vision/` and use **Settings → Use existing files**.

Expected layout after staging:

```text
local/
  models/qwen2.5-vl-3b/
    Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf
    mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf
  runtime/cpu/     # llama-server + DLLs
  runtime/cuda/    # optional NVIDIA runtime
```

## Do not

- Commit `*.gguf` or llama-server binaries to git  
- Re-add `../resources/local` to `tauri.conf.json` unless you intentionally want a multi‑GB installer  
