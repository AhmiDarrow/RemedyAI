# Updates & uninstall

## Check for updates

| Where | Action |
|-------|--------|
| **On launch** | One automatic check after the local server is ready (silent unless an update exists) |
| Logo menu | **Check for updates…** |
| Settings → About | **Check for Updates** |
| Tray | Updates entry |
| Status bar | **Update** badge when available |
| Background | Re-checks about every **30 minutes** while the app is open |

The app queries **GitHub Releases** for this project only. Installs use the signed
`latest.json` asset URL (must match) and a non-empty release signature field.
Published desktop builds are **minisign**-signed in CI.

**Installer filename on the release:** `Remedy.Desktop_{version}_x64-setup.exe`
(dots where the product name has spaces — e.g. `Remedy.Desktop_0.14.4_x64-setup.exe`).
That name must match the URL inside `latest.json` exactly.

## Switch to WebUI (not an uninstall)

Logo menu / status bar **WebUI** / Settings → **Switch to WebUI**:

1. Desktop window hides to the **tray** (server keeps running).  
2. Browser opens `http://127.0.0.1:7400/` (same chat + local API).  
3. Tray → **Show Remedy** returns to the desktop shell.  

**Windows title-bar ✕** hides to the tray (server stays up). Full **Quit** from
the tray (or app menu) stops the server — the WebUI will stop working.

**Linux / WSLg ✕** minimizes to the taskbar (there is no tray). Quit from the
app menu or the quit dialog. In-app Update is Windows-only; Linux installs
come from the GitHub Release `.deb` or AppImage on the same tag.

## Install an update

Two-stage progress (by design):

1. **In-app** update screen shows **download** progress only.  
2. When download finishes, **Remedy closes** (that screen closes with it).  
3. A **new** **Remedy Install Progress** popup appears for silent install + relaunch  
   (so you are not left on a blank desktop after the app exits).  
   The host is a hidden PowerShell process — you should **not** see black CMD windows.  
4. Update-mode uninstall keeps your **user data** (`/UPDATE` path).  
5. App **relaunches once** when the pipeline succeeds (the installer does **not** also
   auto-start - that used to open two Remedy windows); the install popup closes.  

If download fails, use the release page manually:  
https://github.com/AhmiDarrow/RemedyAI/releases

## What updates preserve

| Kept | Removed / replaced |
|------|---------------------|
| `~/.remedy` config, memory, skills, auth | Old app binaries under install dir |
| Your provider settings | Previous EXE/sidecar versions |

Silent auto-update uninstalls **do not** show the wipe dialog and **keep** data.

## Uninstall

1. Windows **Apps & features** → Remedy Desktop → Uninstall  
   *or* the uninstaller from the install folder.  
2. Interactive uninstall shows **data options**:

| Option | Effect |
|--------|--------|
| Keep all (default on cancel/error) | Remove app only |
| Remove config | Deletes config / desktop prefs / auth **and** the local visual decoder (`~/.remedy/vision` — llama-server + GGUF models) |
| Remove skills | Deletes user skills tree |
| Full wipe | Removes entire `~/.remedy` (including vision, memory, sessions) and related app data |

Uninstall **stops `llama-server`** before deleting vision files so large model weights are not left locked.

3. Cancel aborts uninstall.  
4. Dialog/script errors **soft-fail**: app still uninstalls, data kept.

## After uninstall

- Reinstalling without wipe restores a clean app against existing `~/.remedy`.  
- Full wipe + reinstall behaves like a true first run (Setup wizard).  

## Defender / Startup notes

- Autostart uses **Startup folder** shortcuts only (not registry Run).  
- Older builds that wrote Run keys are cleaned on install.  
- See [Troubleshooting](09-troubleshooting) if Defender flagged a legacy build.

## Related

- [Install](01-install-windows) · [Security & data](04-security-and-data) · [What’s new](13-whats-new)
