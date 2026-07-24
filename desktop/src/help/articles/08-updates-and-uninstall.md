# Updates & uninstall

## Check for updates

| Where | Action |
|-------|--------|
| Logo menu | **Check for updates…** |
| Settings → About | **Check for Updates** |
| Tray | Updates entry |
| Status bar | **Update** badge when available |

The app queries **GitHub Releases** for this project only. Installs use the signed
`latest.json` asset URL (must match) and a non-empty release signature field.
Published desktop builds are **minisign**-signed in CI.

## Switch to WebUI (not an uninstall)

Logo menu / status bar **WebUI** / Settings → **Switch to WebUI**:

1. Desktop window hides to the **tray** (server keeps running).  
2. Browser opens `http://127.0.0.1:7400/` (same chat + local API).  
3. Tray → **Show Remedy** returns to the desktop shell.  

Full **Quit** stops the server — the WebUI will stop working.

## Install an update

1. When an update is available, open the update screen.  
2. Download / run the installer (may request elevation).  
3. Update-mode uninstall keeps your **user data** (`/UPDATE` path).  
4. App relaunches after install when the pipeline succeeds.  

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
| Remove config | Deletes config / desktop prefs / auth |
| Remove skills | Deletes user skills tree |
| Full wipe | Removes `~/.remedy` and related app data |

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
