# Troubleshooting

Work top-down: is the **local server** up, is **auth** loaded, is the **provider** valid?

## Server failed to start / “Is the server running?”

1. **Retry** on the error screen (restarts sidecar).  
2. Wait up to ~90s on first install (skill seed / cold start).  
3. Confirm nothing else owns port **7400**.  
4. Fully quit (tray → Quit) and relaunch.  
5. Reinstall the latest desktop build.  
6. **Open data folder** and read logs under `%USERPROFILE%\.remedy\logs\` (see below).

## Always starts minimized / only in the tray

“Start with Windows” and “Start hidden in tray” are **separate**. Older setup incorrectly
turned on **start hidden** whenever login-at-startup was enabled.

1. Open **Settings** (desktop / shell section).  
2. Uncheck **Start hidden in tray** (leave **Start with Windows** on if you want).  
3. **Save**, fully **tray → Quit**, relaunch — the main window should open normally.  
4. Tray-only *start* is only when that box is checked (`%USERPROFILE%\.remedy\desktop.json`).

**Note (0.20.0+):** clicking **✕** always hides to tray by design (keeps the server up). That
is not the same as “start hidden.” To leave Remedy, use **tray → Quit**.

## Title bar min / max / close unresponsive

**0.18.6+** uses the **OS title bar** for window controls. If buttons still misbehave,
that is Windows chrome (not WebView drag-region residue). Fully Quit and relaunch.

**0.20.0+:** ✕ should **hide to tray**, not quit. If ✕ still kills the process, install the
latest 0.20.x build (older prefs with `close_to_tray=false` are healed on load).

Older undecorated builds could lose clicks after move/maximize; install the latest.

## Built-in Browser stays blank / “does not load”

The Browser rail uses a **child WebView2** (not an iframe). Common causes:

1. **Rail too narrow** — expand Browser to a full panel (not icon-only), then press **Go**.  
2. **Stale embed** — press **Go** again, or **✕** close then **⌂** home.  
3. **WebView2 Runtime** — Edge/WebView2 must be installed (Windows 11 usually has it). Reinstall [WebView2 Evergreen](https://developer.microsoft.com/microsoft-edge/webview2/) if embed errors mention WebView2.  
4. Use **↗** to open the same URL in the system browser as a fallback.  
5. Check `%LOCALAPPDATA%\com.remedy.desktop\logs\` for `browser embed` / `add_child` lines.

## She won’t open Grove / Settings / the Terminal rail

Ask her in chat (“open Voice settings”, “go to Alongside”). She uses
**app_control** — she should not click her own chrome. If an older build
still clicks Maximize instead, install **0.31.1+**.

## “Sign in with Google / Microsoft” stuck in Browser rail

Google Identity Services (and other sized login windows) open as a **real
popup** so `window.opener` stays alive. Redirect-style SSO still completes
in the rail. Privacy Shield does not block identity hosts (Google, Microsoft,
GitHub login, Auth0, Okta, …).

1. After **Sign in with Google**, a small account window should appear —
   pick the account there; the rail stays on the site.  
2. If the rail itself shows Google’s `/gsi/transform` page, it should bounce
   back to the site. Close that tab and retry if it does not.  
3. If still stuck: toggle **Privacy Shield off**, retry, then turn it back on.  
4. Last resort: **↗** system browser for that login only.

## Mobile vs desktop layout in the Browser rail

The rail defaults to a **mobile** browser identity so sites serve compact layouts
that fit the panel. Toolbar control:

| Control | Meaning |
|---------|---------|
| **📱** | Mobile view (default) |
| **🖥** | Desktop site (full multi-column layout; may feel cramped) |

Preference is stored in `~/.remedy/browser_rail.json`. Use **↗** for a real full-window desktop browser when needed.

## Telegram / messengers not realtime / “stuck syncing”

Telegram allows **only one** `getUpdates` long-poll per bot token. Two Remedy windows, a leftover `remedy serve`, or `tauri:dev` plus an installed desktop all fighting the same bot produce HTTP **409** and feel like chat is dead or endlessly catching up.

1. Fully **Quit** every Remedy (tray → Quit) and stop any extra CLI `remedy serve`.  
2. Relaunch **one** instance only.  
3. In `%USERPROFILE%\.remedy\logs\remedy.log` look for `telegram poll lock acquired` and **no** repeated `getUpdates 409`.  
4. Second instance should log that long-poll was deferred / not started (poll lock held) — on **0.18.5+** it **retries** every ~20s if the owner dies.  
5. If 409s continue with a single Remedy, something else (another machine, webhook, or second install) is still polling that bot.  
6. **Stuck after upgrade (0.18.4):** a dead Windows PID could keep the lock. Install **0.18.5+**, fully Quit, delete `%USERPROFILE%\.remedy\locks\telegram_getupdates.lock` if it remains, relaunch.

**Desktop → Telegram:** replies you send in a `msg:telegram:…` session are mirrored outbound on **0.18.4+**. Older builds only answered messages that arrived *from* Telegram.

## Status bar flips Connected ↔ Disconnected

Usually the local API event loop was blocked (historically: visual decoder health checks against a dead `llama-server` port). Fixed builds use a cheap `/api/ping` probe and non-blocking vision status.

If it still flaps:

1. Open Settings → note whether Visual decoder is installed but not running.  
2. Check `%USERPROFILE%\.remedy\logs\debug.log` for `SLOW GET` lines (requests ≥500ms).  
3. Quit fully and relaunch so the sidecar reloads with file logging.

## Where logs live

| File | Contents |
|------|----------|
| `%USERPROFILE%\.remedy\logs\remedy.log` | Normal server log (INFO+, rotating) |
| `%USERPROFILE%\.remedy\logs\errors.log` | ERROR+ only |
| `%USERPROFILE%\.remedy\logs\debug.log` | DEBUG trail for perf / disconnect diagnosis |

Set `REMEDY_LOG_LEVEL=DEBUG` or `log_level = "DEBUG"` in `config.toml` for more verbose console output. File `debug.log` is always written at DEBUG when the server starts.

## Failed to load server config / setup won’t open

- Click **Open setup** (warms token + wizard).  
- Corrupt `config.toml` forces first-run again — complete Setup to rewrite it.  
- After wipe/reinstall, auth token is new; Retry once.

## Failed to save settings

- Ensure status is connected.  
- Open Setup and finish once (rewrites config correctly).  
- Confirm `%USERPROFILE%\.remedy` is writable.  
- Real error text (0.10.36+) appears in the wizard — note it for support.

## xAI OAuth fails / “Cannot reach local API” / “Failed to fetch”

On **0.10.37**, Sign in with xAI could show *Cannot reach local API … (/auth/xai/login)* even when the
server was up. Cause: CORS **OPTIONS** preflight was blocked by API auth (401 without CORS headers),
so the webview reported a network error. **0.10.38+** lets OPTIONS through auth; update if you hit this.

| Check | Fix |
|-------|-----|
| Still on 0.10.37 | Install **0.10.38+** (OPTIONS / CORS preflight fix) |
| Server down | Splash **Retry** until connected, then open Setup |
| Offline / proxy | Allow `auth.x.ai` and `accounts.x.ai` |
| Stale sidecar | Quit fully, relaunch latest installer |
| Browser blocked | Use verification URL + code from the wizard |
| Still stuck | Paste an xAI console API key instead of OAuth |

## Unauthorized / 401 from local API

- Token file missing after wipe → restart app.  
- Do not call the API from non-loopback hosts.  
- Advanced: `REMEDY_API_AUTH=0` only for local debugging.

## Agent says F1 / help is “outside access scope”

**0.20.0+:** That is wrong. Owner’s manual chapters (same as F1) are always
readable via tools:

1. `help_list` — article ids  
2. `help_read(id="computer-use-soak")` — full markdown  

Or `file_read` on an absolute path under `docs/manual/` when the repo is on disk.
If the model still refuses, resend “use help_read on computer-use-soak”.

## Images in chat show “Loading…” / broken / blank

Remedy does **not** depend on the LLM provider for image *display*. Models often
write markdown like `![preview](assets/previews/hero_logo_color_on_dark.png)`. The desktop loads
those through the local API (`GET /api/media`) with your project path.

| Check | Fix |
|-------|-----|
| Relative path outside project | Set **Project folder** in Settings to the repo that owns the files |
| Absolute path outside access scope | Scope is `project` / `home` / `full` — expand scope or copy files into project |
| Old build | Install **0.14.4+** (ChatImage + media route + alpha logos) |
| Still blank | Open `%USERPROFILE%\.remedy\logs\errors.log` after expanding the image |

Attachments (drag/drop screenshots) use a separate upload path and should always
preview; if only *generated* previews fail, it is almost always path/scope.

## Session Export does nothing / no file

On desktop, Export uses a **native Save dialog** (0.14.3+). If you cancel the
dialog, nothing is written (expected). Large sessions (0.14.5+) strip embedded
images and cap huge tool dumps so the machine does not freeze.

| Check | Fix |
|-------|-----|
| Old WebView-only download | Update to **0.14.4+** |
| Empty session | Export needs at least one stored message |
| UI freezes on export | Update to **0.14.5+** (capped export + async write) |
| API error toast | Confirm Connected status; check `remedy.log` for `/export` |

## Cannot send while Remedy is streaming

As of **0.14.5** you can type and send during a turn:

| Action | Effect |
|--------|--------|
| **Enter** | Queue message for after the current turn |
| **Ctrl+Enter** (or right-click Send) | Stop current turn and send immediately |
| Queue bar | Interrupt / After / Cancel / Clear all |

## Usage ticker stuck on idle / $0

Live run counts use streaming partials until the provider reports usage.
Session totals use message length estimates when token metadata is missing.
**0.14.5** fixes Grok 4.5 pricing match order and live estimates.

## Update shows black CMD windows

Fixed in **0.14.4+**: install-progress and update hosts spawn hidden PowerShell
(no cmd /c start). You should only see the install-progress popup.

If you still see consoles on an older build, update manually from GitHub Releases.

## Update downloaded but never installed / no restart

If Remedy closed after download and nothing happened (no install popup, still on
old build), the install script may have been killed with the app process
(**0.14.4–0.14.5** Job Object race).

| Check | Fix |
|-------|-----|
| Stuck at “closing” | Update to **0.14.7+** (multi-path install schedule) |
| Defender **Bearfoos** / **Wacatac** on update | ML false positive on unsigned installer/sidecar. **Allow on this device** if you installed from official GitHub Releases. Fixed PE identity since earlier 0.14.x; Authenticode still pending. |
| Defender **Execution.A!ml** on first launch | ML false positive on unsigned generic **`app.exe`**. **Allow on this device**, then relaunch. **0.23.2+** ships as `Remedy Desktop.exe`. |
| Taskbar shows old icon | Quit fully, then clear Windows icon cache (see DESKTOP.md). Rebuild embeds multi-size alpha ICO. |
| Tray icon hard to see | **0.14.9+** uses a bold dark-plate tray glyph (`iconAsTemplate` off). |
| `%TEMP%\RemedyDesktop-Update.log` has no `BOOT` / `Update script started` | Confirm **0.14.7+**; otherwise install the `.exe` from GitHub Releases |
| Close-to-tray only | Use tray → **Quit** if an old update left installers in `%TEMP%` |
| Manual recovery | Run the newest `RemedyDesktop-Update-*.exe` in `%TEMP%`, or the release installer |

## SmartScreen / Unknown publisher

- Install **only** from [RemedyAI Releases](https://github.com/AhmiDarrow/RemedyAI/releases).  
- If Windows says **Unknown publisher**: **More info → Run anyway**.  
- In-app updates are **minisign**-verified even when the first installer is not Authenticode-signed yet.

## Windows Defender false positives

Defender’s machine-learning names sometimes mislabel legitimate desktop apps
(especially new releases). Remedy is local-first software from this repo — not a
Trojan. Known historical / ML labels and status:

| Name | Status |
|------|--------|
| `Behavior:Win32/Persistence.A!ml` | **Fixed** — old builds wrote `HKCU\…\Run`; current builds use **Startup folder** only and scrub legacy keys without PowerShell. |
| `Trojan:Win32/Wacatac.B!ml` | **Mitigated** — sidecar PE now has product version/company/icon metadata; still improve further with Authenticode. |
| `Trojan:Win32/Bearfoos.A!ml` | Same class of ML hit on unsigned PyInstaller-style binaries; same mitigations. |
| `Behavior:Win32/Execution.A!ml` | **Mitigated in 0.23.2** — UI EXE is `Remedy Desktop.exe` instead of generic `app.exe`. If 0.23.1 already fired: **Allow on this device**. |

**If Windows Security quarantines Remedy after install or first run:**

1. Open **Windows Security → Virus & threat protection → Protection history**.  
2. Find the Remedy entry → **Actions → Allow on this device** (or Restore).  
3. Relaunch from Start Menu → **Remedy Desktop**.  
4. Prefer the **latest** installer from GitHub Releases (older 0.10.19–0.10.21 builds are more likely to trip Persistence).  
5. Optional: confirm no value named `RemedyDesktop` / `Remedy Desktop` / `remedy-desktop` under  
   `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (current builds never write these).

## Wrong / old taskbar icon

1. Tray → Quit.  
2. Clear icon cache (PowerShell) or reboot.  
3. Unpin and re-pin the app.  
4. Confirm you launched the new install path.

## Chat cut off / context full

- Run `/compact`.  
- Lower harness thresholds or start a **new session**.  
- Use a larger-context model if your provider offers one.

## Provider errors (401/429/model)

- Re-check API key in Settings.  
- Confirm model id exists for that provider.  
- Rate limits: wait or switch model.  
- Ollama: ensure `ollama serve` is running and the model is pulled.

## Updates won’t install

- Install manually from GitHub Releases.  
- Close all Remedy processes first.  
- Check disk space and antivirus quarantine.

## Still stuck

1. Note app version (Settings → About / F1 → What’s new).  
2. Export or screenshot the exact error.  
3. Open an issue on GitHub with OS build + steps.  

## Related

- [First run](02-first-run) · [Providers](03-providers-and-auth) · [Updates](08-updates-and-uninstall)

