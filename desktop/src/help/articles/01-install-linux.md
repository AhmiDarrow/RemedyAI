# Install (Linux)

Remedy Desktop now runs on Linux (including WSLg) as well as Windows. Same partner, same `~/.remedy` home, same local API on `127.0.0.1:7400`.

## Requirements

- 64-bit Linux with a desktop session (GNOME, KDE, XFCE, or **WSL2 + WSLg**)
- Network for cloud providers, or a local model (Ollama / RMB / first-run vision)
- Packaged desktop **includes** the Python sidecar (same as Windows). You do **not**
  need a system `python` / `uv` for the `.deb` or AppImage.

## Ways to run

| Path | Who it is for |
|------|----------------|
| **`.deb` / AppImage** | [GitHub Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest) — **v0.26.0+** |
| **PyPI CLI** | `uv tool install remedy-ai` then `remedy serve` (browser WebUI) |
| **Dev / source** | This repo: `npm run tauri:dev` / `tauri:build` |

### Packaged desktop (v0.26.0+)

1. Open [RemedyAI Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest).
2. Download either:
   - **`Remedy.Desktop_*_amd64.deb`** — Debian/Ubuntu: `sudo apt install ./Remedy.Desktop_*_amd64.deb`
   - **`Remedy.Desktop_*.AppImage`** — `chmod +x` then run it
3. Launch **Remedy Desktop** from the app menu (deb) or the AppImage.

The **`.deb`** asks apt for the same class of deps Windows bundles or assumes:
`libwebkit2gtk-4.1-0`, `libgtk-3-0`, `libayatana-appindicator3-1`, `librsvg2-2`,
`xdg-utils`, `libvulkan1`, `libgomp1`. `sudo apt install ./Remedy.Desktop_*_amd64.deb`
pulls them. Computer-use hands are **Recommends** (not hard Depends): `grim`,
`xdotool` or `ydotool`, `wmctrl`, and `scrot`. Install them so click/type/scroll
and region screenshots work:

```bash
sudo apt install grim xdotool wmctrl scrot
# Wayland extra: sudo apt install ydotool
```

The **AppImage** is more self-contained (media framework included);
you still need FUSE to run it (`libfuse2` / `libfuse2t64` on Ubuntu).

The **local vision / nano** stack is **not** in the installer (same as Windows):
first use downloads pinned **SmolVLM2** weights plus **llama.cpp** `llama-server`
for this OS (`ubuntu-x64` CPU or `ubuntu-vulkan-x64` when a GPU is present) into
`~/.remedy/vision/`.

Claimidx is also downloaded and installed on first run rather than bundled. It
runs as a private loopback helper from `~/.remedy/claimidx/`; a failed or
offline download never prevents Remedy from starting.

## WSLg (Windows host, Linux UI)

The Linux build talks to the **Windows work area** of the monitor the window is on (not a fake maximize that covers the taskbar). Close (✕) **minimizes to the Windows taskbar** — WSLg has no system tray. There is no “Start with Windows” toggle on Linux.

Do **not** launch a Windows `remedy.exe` via `/mnt/c` as the sidecar. The Linux app rejects PE/`.exe` shebangs so `:7400` actually binds.

## First launch

1. Start the app (or `remedy serve` + open the WebUI).
2. Finish the setup wizard (or Skip — you can open Setup later).
3. Add a provider key or sign in with xAI from Settings. Google OAuth uses **this** sign-in, not a leftover linked account.

Data stays in `~/.remedy` on the Linux filesystem. If you share a home with Windows (`/mnt/c/Users/…/.remedy`), Linux will **not** overwrite the Windows DPAPI token; it writes `local_api_token.posix` beside it.

## Where files live

| What | Typical path |
|------|----------------|
| User data | `~/.remedy/` |
| Config | `~/.remedy/config.toml` |
| Memory DB | `~/.remedy/memory.db` |
| Auth / keys | `~/.remedy/auth/` |
| Managed Claimidx | `~/.remedy/claimidx/` |

## See also

- [Install (Windows)](01-install-windows.md)
- [First run](02-first-run.md)
- [Troubleshooting](09-troubleshooting.md)
