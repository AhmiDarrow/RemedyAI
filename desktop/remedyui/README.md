# Remedy Desktop (RemedyUI)

The Remedy Desktop shell, ported to [RemedyUI](https://github.com/AhmiDarrow/RemedyUI). Product identity stays **Remedy Desktop** / `com.remedy.desktop`. This branch starts at version **1.0.0**; the first shipped build must be greater than 0.70.5 so existing Tauri clients will accept the update jump.

This is the first working surface: Studio session list, close-to-tray, single instance, Dark Forest (and every shared palette). The mind is not spawned yet; the status bar reports that honestly. Chat, settings, house-rules setup, workspace, Grove and the updater jump follow on this branch. See `PORT-CHECKLIST.md`.

- `cargo test` runs the app on the in-memory host and writes / checks previews in `tests/golden` (every palette, 1100×700). Copy those PNGs for visual review.
- `cargo remedy run` runs a window; press F12 for the inspector. `--watch` rebuilds on save. Do not pop visible windows on a machine Joe is using.
- `cargo remedy package` builds installers (`remedy.toml`). The Windows asset name, minisign key, NSIS `/S /UPDATE` and `~/.remedy` rules are unchanged; packaging is not this milestone.

The checkout of RemedyUI this crate depends on should include [PR #118](https://github.com/AhmiDarrow/RemedyUI/pull/118) (Protan / Deutan / Tritan names) and [PR #119](https://github.com/AhmiDarrow/RemedyUI/pull/119) (`Feed::Json` reads Tauri `latest.json`), merged to `main` at `3b3a026`.
