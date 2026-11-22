# Remedy Desktop (RemedyUI)

The Remedy Desktop shell, ported to [RemedyUI](https://github.com/AhmiDarrow/RemedyUI). Product identity stays **Remedy Desktop** / `com.remedy.desktop`. This branch starts at version **1.0.0**; the first shipped build must be greater than 0.70.5 so existing Tauri clients will accept the update jump.

This is the first working surface: Studio session list, close-to-tray, single instance, Dark Forest (and every shared palette). The mind is not spawned yet; the status bar reports that honestly. Chat, settings, house-rules setup, workspace, Grove and the updater jump follow on this branch. See `PORT-CHECKLIST.md`.

- `cargo test` runs the app on the in-memory host and writes / checks previews in `tests/golden` (every palette, 1100×700). Copy those PNGs for visual review.
- `cargo remedy run` runs a window; press F12 for the inspector. `--watch` rebuilds on save. Do not pop visible windows on a machine Joe is using.
- Windows, Linux, and macOS packages are `packaging/package-windows.ps1`, `packaging/package-linux.sh`, and `packaging/package-macos.sh`. The 1.0 Windows artifact is NSIS (`Remedy.Desktop_{ver}_x64-setup.exe`), not the Inno Setup file from `cargo remedy package`. See `packaging/README.md` for `/S /UPDATE` and for how each platform is built. Those scripts do not tag or publish.

The RemedyUI pin is `main` at `7dec309` ([PR #118](https://github.com/AhmiDarrow/RemedyUI/pull/118) theme names, [PR #119](https://github.com/AhmiDarrow/RemedyUI/pull/119) `Feed::Json`, [PR #120](https://github.com/AhmiDarrow/RemedyUI/pull/120) AhmiPrint weights).
