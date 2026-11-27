# Remedy Desktop

Remedy Desktop is the main way to use Remedy. It is a Rust app built on
RemedyUI. It lives in `desktop/remedyui`. Its app id is `com.remedy.desktop`.
The current version is 2.0.0.

The old Tauri and React desktop was replaced. Some of its folders are still in
the tree (`desktop/src`, `desktop/src-tauri`), but they are not the shipped app.

## How it fits together

- The desktop app talks over HTTP and SSE to the Go runtime, `remedy-runtime`,
  on `127.0.0.1:7400`.
- The runtime uses the Zig core, `remedy_core`, for its low-level pieces.
- Voice runs in the Rust helper `remedy-voice` (`native/voice`).
- Closing the window hides it to the tray. Tray Quit exits the app.
- Settings, sessions and memory live in `~/.remedy`. They carry over from the
  older desktop.

No Python is installed for the app. The only Python Remedy ever installs is for
ClaimIDX, inside `~/.remedy/claimidx/python`.

## Build and test

From `desktop/remedyui`:

```bash
cargo test            # runs the app on an in-memory host and checks golden images
cargo remedy run      # opens a window; F12 opens the inspector
```

Packages (Windows installer, Linux AppImage and `.deb`, macOS app) are made by
the scripts in `desktop/remedyui/packaging`. Read
[`packaging/README.md`](../desktop/remedyui/packaging/README.md) before you run
them. They do not tag or publish a release.

## Updates

The installed app checks `latest.json` on the GitHub release page, downloads the
file for its system, and verifies the minisign signature. An unsigned file is
refused. On Windows the installer runs silently and the app starts again by
itself. A `.deb` cannot replace itself. The asset names and the exact installer
flags are in the packaging README.

## Local API

The routes the desktop uses most:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` / `POST` | `/api/sessions` | List or create sessions |
| `GET` / `PATCH` / `DELETE` | `/api/sessions/{id}` | Read, rename or delete one |
| `POST` | `/api/sessions/{id}/messages/stream` | Send a message and stream the reply (SSE) |
| `POST` | `/api/sessions/{id}/steer` | Add a note to a turn that is running |
| `POST` | `/api/sessions/{id}/abort` | Stop the reply |
| `POST` | `/api/sessions/{id}/command` | Run a slash command |
| `POST` / `GET` | `/api/sessions/{id}/attachments` | Upload or fetch an attachment |
| `GET` | `/api/status` | Health, version and provider status |
| `GET` | `/api/models` | Models and the default |
| `GET` | `/api/providers` | Provider catalog |
| `GET` | `/api/skills` | List skills |

The slash commands are listed in the manual: [Slash commands](manual/11-reference-commands.md).

## Windows signing

Updates are verified by minisign. A first download from the browser still needs
Authenticode signing to avoid the SmartScreen warning. See
[WINDOWS_SIGNING.md](./WINDOWS_SIGNING.md).
