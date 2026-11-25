# Remedy Desktop 1.0 packaging

The first RemedyUI ship is **1.0.0** (`com.remedy.desktop`), which is newer than the last Tauri build **0.70.5**. These scripts produce the artifacts that feed already understands. They do not tag a release and they do not publish one.

`cargo remedy package` writes an Inno Setup installer on Windows, a prefix tarball on Linux, and a zipped `.app` on macOS. None of those is the 0.70.5 update payload. Use the scripts in this directory.

Outputs land in `desktop/remedyui/target/packaging/` (gitignored with the rest of `target/`). The scripts read the built `remedy-desktop` binary from `$CARGO_TARGET_DIR` when that variable is set, and from `desktop/remedyui/target/` otherwise. The installer or bundle is still written under `desktop/remedyui/target/packaging/`, which is the path the package workflow uploads.

## What an installed 0.70.5 client sends

The Windows client downloads `Remedy.Desktop_{version}_x64-setup.exe`, checks the existing minisign key, writes `%TEMP%\RemedyDesktop-UpdaterOwnsRelaunch.flag`, then runs the installer with exactly:

```text
/S /NCRC /UPDATE /NOAUTOLAUNCH
```

If a previous install directory is known, `/D=<that directory>` is the last argument and it is not quoted. `/S` is NSIS silent. `/NCRC` skips the CRC check. `/UPDATE` sets `UpdateMode`. `/NOAUTOLAUNCH`, together with the flag file, tells `POSTINSTALL` in `desktop/src-tauri/windows/hooks.nsh` not to start a second copy. The waiter script is the only relaunch.

`packaging/windows/installer.nsi` is that NSIS program. It includes `hooks.nsh` unchanged. `.onInit` records `/P` and `/UPDATE`, keeps a `/D=` override, and otherwise installs to `%LOCALAPPDATA%\Remedy Desktop` or the previous `HKCU\Software\Remedy\Remedy Desktop` path. The uninstall key is `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Remedy Desktop`.

Before it copies files, the installer runs that previous uninstaller when the registry still has one: `ExecWait '"<old uninstall.exe>" /S /UPDATE _?=<old dir>'`. `/S` and `/UPDATE` skip the choices dialog. `_?=` is last so NSIS waits until the old files are gone instead of returning as soon as the uninstaller copies itself to `%TEMP%`. The hooks then write keep-choices (`config=0`, `skills=0`, `full=0`). The per-user data directory (`%USERPROFILE%\.remedy` or `~/.remedy`) is removed only when an interactive uninstall sets `full=1`. The NSIS script itself never deletes that directory.

The 1.0 client uses the same flags, the same flag file, and the same three hidden hosts the 0.70.5 client used (PowerShell with job breakaway, a hidden `wscript` launch, and a one-shot scheduled task). It does not show the old update popup.

## Feed

`latest.json` in this directory is the 1.1.0 template. The shape matches `scripts/latest.json` (Tauri `Feed::Json`). Leave `scripts/latest.json` on the 0.70.5 template. Signatures in the 1.0 template are empty on purpose. RemedyUI refuses an empty signature, so an unsigned file cannot install.

| Platform | Asset | URL |
| --- | --- | --- |
| `windows-x86_64` | `Remedy.Desktop_1.1.0_x64-setup.exe` | `.../releases/download/v1.1.0/` |
| `linux-x86_64` | `Remedy.Desktop_1.1.0_amd64.AppImage` | `.../releases/download/v1.1.0/` |
| `darwin-aarch64` | `Remedy.Desktop_1.1.0_aarch64.app.tar.gz` | `.../releases/download/runtime-v1.1.0/` |
| `darwin-x86_64` | `Remedy.Desktop_1.1.0_x64.app.tar.gz` | `.../releases/download/runtime-v1.1.0/` |

Also produced, not listed in the feed: `Remedy.Desktop_1.1.0_amd64.deb`, `Remedy.Desktop_1.1.0_aarch64.dmg`, `Remedy.Desktop_1.1.0_x64.dmg`. Those are first-install packages. A `.deb` install cannot replace itself. A DMG left under `/Volumes/` is refused; the person moves the app out of the disk image first.

The public key is the `RW` line already embedded in `desktop/src-tauri/tauri.conf.json`. Do not generate a new key.

## Windows

From `desktop/remedyui`, with NSIS 3 (`makensis`) on `PATH` and `NSISDIR` set:

```powershell
./packaging/package-windows.ps1
```

That runs `cargo build --release --jobs 4 --locked` at below-normal priority, then `makensis` on `packaging/windows/installer.nsi`. The script passes absolute backslash paths. Windows NSIS does not open `!include` or `File` paths written with forward slashes. The cargo binary is `remedy-desktop.exe`. The installer writes it as `Remedy Desktop.exe`. The Windows exe embeds `desktop/src-tauri/icons/icon.ico` at build time (`build.rs` + winresource). The running window also sets that mark as its taskbar / Alt+Tab icon. The installer icon is the same `.ico`.

A stub compile (no need to run the installer) is `cargo test nsis_script_compiles`. Do not execute the produced setup on a machine that already has Remedy installed unless you mean to. `hooks.nsh` taskkills Remedy processes.

## Linux

On an x86_64 Linux host, from `desktop/remedyui`:

```bash
./packaging/package-linux.sh
```

Needs `dpkg-deb`, `curl`, and either `appimagetool` or network access to download the continuous AppImage tool. The script passes `--jobs 4` and `nice -n 10`, and it refuses a binary that is not an x86_64 ELF. `dpkg-shlibdeps` fills the `.deb` `Depends` line when it is available. The update payload is the AppImage. When `APPIMAGE` points at a real file, the app verifies the download, copies it beside that file (so the replace stays on the same volume), and relaunches it. Otherwise it shows the GitHub releases URL.

## macOS

On a Mac, from `desktop/remedyui`:

```bash
./packaging/package-macos.sh
```

`uname -m` picks the asset arch (`arm64` → `aarch64`, `x86_64` → `x64`). The script ad-hoc signs (`codesign --force --deep --sign -`), writes a DMG with `hdiutil` from the stage directory (the volume root is `Remedy Desktop.app`, not the bundle's contents), and writes a gzip tarball whose only top-level entry is `Remedy Desktop.app`. The updater lists that tarball before extracting it and refuses a path that escapes the archive or a bundle id other than `com.remedy.desktop`. `Contents/MacOS/Remedy Desktop` is the executable (`remedy-desktop` is a symlink to it). The icon is `desktop/src-tauri/icons/icon.icns` when that file is present. Build once per arch. This Windows tree cannot produce those binaries.

## Signing

`sign-minisign.ps1` and `sign-minisign.sh` read only:

- `REMEDY_UPDATE_KEY` (path to the secret file) and optional `REMEDY_UPDATE_KEY_PASSWORD`
- or `TAURI_SIGNING_PRIVATE_KEY` (path, raw minisign text, or the base64 of that text) and optional `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

They write `<artifact>.sig` beside the file. Paste that signature text into the matching `platforms.*.signature` of `packaging/latest.json` when the release is cut. If neither variable is set, the artifact stays unsigned and the script exits 0. Do not invent a key, and do not commit the secret.

An encrypted key is unlocked from the password variable via minisign's stdin. The scripts do not print the key. If minisign insists on a real terminal, decrypt once with `minisign -C` into a temp key and point `REMEDY_UPDATE_KEY` at it.

## CI

`.github/workflows/remedyui-desktop-package.yml` is `workflow_dispatch` only, with `contents: read`. It uploads artifacts. It does not create a tag or a GitHub Release. `desktop-release.yml` stays the Tauri pipeline and is not a 1.0 publisher. The workflow installs NSIS, minisign 0.12, and the Linux link libraries (`libwayland`, `libxkbcommon`). macOS runs on `macos-15` and `macos-15-intel`, the same runners as the Tauri pipeline. `CARGO_BUILD_JOBS` and `RAYON_NUM_THREADS` are 4.

`packaging/check.py` checks the feed, the NSIS flags, the waiter, and that this workflow cannot publish. The three package scripts run it before `cargo build`.

The crate depends on the private RemedyUI git repo. The workflow sets a git `insteadOf` from `REMEDYUI_READ_TOKEN` (else `github.token`) and `CARGO_NET_GIT_FETCH_WITH_CLI=true`. Do not print that token.

## In the app

The shell checks `https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json` through `remedy_ui::update` (`Feed::Json`). `REMEDY_DESKTOP_NO_SPAWN` or `REMEDY_DESKTOP_NO_UPDATE` skips the network, which is how tests and golden previews stay offline. A newer version adds a quiet status button and changes the tray item to `Update {version}`. Choosing it downloads on a background thread. The status line and the tray item then show `Downloading N%` (or `Downloading N MB` when the length is unknown), the same facts the old update screen read from `update-progress`. There is still no popup. Idle previews have no offer and no note, so neither is in the tree.
