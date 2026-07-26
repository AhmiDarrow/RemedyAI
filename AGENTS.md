# Agent notes — RemedyAI

Durable facts for coding agents working in this repo. Prefer this file + `docs/` over chat memory when they conflict.

## Desktop installer / auto-update naming

**Critical for in-app updates.** The signed `latest.json` URL must match the GitHub Release asset name **exactly**.

| Item | Canonical form |
|------|----------------|
| Git tag | `v{X.Y.Z}` (e.g. `v0.14.4`) |
| Release title | `Remedy Desktop v{X.Y.Z}` |
| Installer asset name | **`Remedy.Desktop_{X.Y.Z}_x64-setup.exe`** |
| Metadata asset | `latest.json` (same release) |
| Installer URL | `https://github.com/AhmiDarrow/RemedyAI/releases/download/v{X.Y.Z}/Remedy.Desktop_{X.Y.Z}_x64-setup.exe` |

### Rules

1. **Dots, not underscores**, between product words: `Remedy.Desktop_*` — never `Remedy_Desktop_*`.
2. Tauri/NSIS may emit a **space** (`Remedy Desktop_…`); CI renames spaces → dots before upload (`.github/workflows/desktop-release.yml`).
3. `scripts/sync_version.py` stamps `scripts/latest.json` with the same `Remedy.Desktop_*` URL.
4. `platforms.windows-x86_64.url` in published `latest.json` must equal the asset’s `browser_download_url`.
5. **Easy ops fix** when update fails with  
   `Download URL does not match signed latest.json asset`:  
   **rename the GitHub Release asset** to `Remedy.Desktop_{ver}_x64-setup.exe` (and ensure `latest.json` points at that name). Do not disable the URL match check.
6. Install path in `desktop/src-tauri/src/lib.rs` **re-reads** signed `latest.json` at download time so a stale UI-held URL cannot fail after a multi-MB pull.

### Related docs

- `docs/DESKTOP.md` — full auto-update pipeline + naming table  
- `docs/WINDOWS_SIGNING.md` — minisign + Authenticode  
- `docs/manual/08-updates-and-uninstall.md` — user-facing update flow  

## Version surfaces

Keep these aligned via `python scripts/sync_version.py {X.Y.Z}`:

- `pyproject.toml`
- `desktop/package.json` (+ lock)
- `desktop/src-tauri/tauri.conf.json`
- `desktop/src-tauri/Cargo.toml` (+ lock)
- `scripts/latest.json`
