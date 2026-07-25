# Troubleshooting

Work top-down: is the **local server** up, is **auth** loaded, is the **provider** valid?

## Server failed to start / “Is the server running?”

1. **Retry** on the error screen (restarts sidecar).  
2. Wait up to ~90s on first install (skill seed / cold start).  
3. Confirm nothing else owns port **7400**.  
4. Fully quit (tray → Quit) and relaunch.  
5. Reinstall the latest desktop build.  
6. **Open data folder** and read logs under `%USERPROFILE%\.remedy\logs\` (see below).

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

## SmartScreen / Unknown publisher

- **More info → Run anyway** for GitHub-sourced installers.  
- Prefer releases from `AhmiDarrow/RemedyAI` only.

## Defender Persistence.A!ml (legacy)

Older builds used HKCU Run for autostart. **0.10.22+** uses Startup folder only.

1. Update to latest.  
2. Protection history → allow if blocked.  
3. Confirm no `RemedyDesktop` under  
   `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

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
