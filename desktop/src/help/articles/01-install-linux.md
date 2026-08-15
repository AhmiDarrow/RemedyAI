# Install (Linux)

Remedy Desktop now runs on Linux (including WSLg) as well as Windows. Same partner, same `~/.remedy` home, same local API on `127.0.0.1:7400`.

## Requirements

- 64-bit Linux with a desktop session (GNOME, KDE, XFCE, or **WSL2 + WSLg**)
- WebKitGTK (Tauri) and a working local Python/`uv` for the sidecar
- Network for cloud providers, or a local model (Ollama / RMB)

## Ways to run

| Path | Who it is for |
|------|----------------|
| **Dev / source** | `uv run remedy serve` + Linux Tauri build from this repo |
| **Packaged `.deb` / AppImage** | GitHub Releases when a Linux desktop asset is published |
| **PyPI CLI** | `pip install remedy-ai` / `uv tool install remedy-ai` then `remedy serve` |

Until a Linux installer is on the same Release as Windows, use **source or PyPI** and the Linux desktop binary from this tree.

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
