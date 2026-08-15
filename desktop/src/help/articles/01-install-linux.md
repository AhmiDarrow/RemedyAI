# Install (Linux)

Remedy Desktop now runs on Linux (including WSLg) as well as Windows. Same partner, same `~/.remedy` home, same local API on `127.0.0.1:7400`.

## Requirements

- 64-bit Linux with a desktop session (GNOME, KDE, XFCE, or **WSL2 + WSLg**)
- WebKitGTK (Tauri) and a working local Python/`uv` for the sidecar
- Network for cloud providers, or a local model (Ollama / RMB)

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

Need WebKitGTK on the machine (`libwebkit2gtk-4.1-0` on Ubuntu 22.04+). The AppImage bundles more; the `.deb` expects those system libs.

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

## See also

- [Install (Windows)](01-install-windows.md)
- [First run](02-first-run.md)
- [Troubleshooting](09-troubleshooting.md)
