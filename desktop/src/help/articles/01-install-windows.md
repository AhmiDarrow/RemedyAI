# Install (Windows)

## Requirements

- Windows 10/11 **x64**
- Network for LLM providers (or local Ollama only)
- ~200 MB disk for the app; more for models if using Ollama

## Download

1. Open [RemedyAI Releases](https://github.com/AhmiDarrow/RemedyAI/releases/latest).
2. Download the **`Remedy.Desktop_*_x64-setup.exe`** installer (not source zips).
3. Prefer the latest **v0.10.x** signed release when available.

> **SmartScreen / “Unknown publisher”**  
> Solo builds may not be Authenticode-signed. Click **More info → Run anyway** if Windows warns. Always download only from this GitHub repo. In-app updates are still **minisign**-verified.

## Install steps

1. Run the setup `.exe`.
2. Choose the install location (default is under your user Local App Data).
3. On the **finish page**:
   - Optional: create a desktop shortcut
   - Optional: **Run Remedy Desktop** (recommended on first install)
4. Interactive installs **do not** auto-launch before that finish page (so you can choose).

Silent/passive/update installs may relaunch automatically.

## Where files live

| What | Typical path |
|------|----------------|
| App install | `%LOCALAPPDATA%\Remedy Desktop\` |
| Main EXE | `%LOCALAPPDATA%\Remedy Desktop\Remedy Desktop.exe` |
| Sidecar API | `remedy-desktop.exe` next to the main app (local server) |
| User data | `%USERPROFILE%\.remedy\` |
| Config | `%USERPROFILE%\.remedy\config.toml` |
| Memory DB | `%USERPROFILE%\.remedy\memory.db` |
| Auth / keys | `%USERPROFILE%\.remedy\auth\` |
| Desktop prefs | `%USERPROFILE%\.remedy\desktop.json` (and app data under `com.remedy.desktop` if present) |

## First launch

1. Splash screen waits for the local API (`http://127.0.0.1:7400`).
2. **Setup wizard** opens when setup is not completed (fresh install or wiped data).
3. Complete [First run](02-first-run), then chat.

## Always-ready (optional)

In setup finish or **Settings**:

- **Start with Windows** uses a **Startup folder** shortcut only (not the registry Run key).
- Tray: Show, Settings, Updates, About, Quit.
- Close-to-tray keeps the local server warm.

## Verify install

- Start Menu → **Remedy Desktop**
- Status bar shows connection / model once the server is up
- **F1** opens this Help wiki
- `~/.remedy` exists after first successful start

## Related

- [First run](02-first-run) · [Updates & uninstall](08-updates-and-uninstall) · [Troubleshooting](09-troubleshooting)
