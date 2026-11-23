# Game dev — Remedy as a studio

Point Remedy at a game project and she works like a small studio: she knows
the engine, verifies every change with the engine itself, plays the build,
and keeps a design document honest. Nothing to set up — tell her where the
engine is if she cannot find it.

## Engines

| Engine | Depth | How Remedy verifies | Knowledge pack |
|---|---|---|---|
| **Godot 4** (GDScript, C#) | full — detection, headless runs, scene/script checks, export, playtest | `godot --headless … --quit-after 1` or your `tools/smoke_*.gd`, `--check-only` on every edited script, scene references checked offline | `godot-4` |
| **Phaser / PixiJS** | full | `npm run build`, `vitest` for logic, dev server in the background + browser playtest | `web-games` |
| **Bevy** | full | `cargo check` (fast oracle), `cargo run` in the background | `bevy` |
| **Pygame / Arcade** | full | `python -m pytest` for logic, `py_compile`, windowed launch in the background | `pygame-arcade` |
| **Love2D** | full | `luac -p`, `busted`, `love .` in the background | `love2d` |
| **Unity** | knowledge | batchmode / Test Runner CLI when an editor is installed — the editor does most of the work | `unity` |
| **Unreal** | knowledge | `RunUAT BuildCookRun`, automation tests — long builds, generous timeouts | `unreal` |

Remedy recognises the project from its files (`project.godot`, `main.lua`,
`package.json` with `phaser`, `Cargo.toml` with `bevy`, a `pygame` import,
`ProjectSettings/ProjectVersion.txt`, `*.uproject`). The status line of every
turn shows what she found, e.g. `engine: godot 4.3 (gdscript) — Godot_v4.3_console.exe`.

## Finding the engine

In order: an environment variable (`GODOT`, `GODOT4_BIN`, `LOVE`,
`UNITY_EDITOR`, `UE_ROOT`), your PATH, a `Godot*.exe` placed in the project
folder (the console build is preferred because it prints), then the usual
install locations. If none of those hit, she says so — answer with the path
and she carries on. There is no wizard.

## What she actually does

- **Headless verification after every change.** Edited `.gd` files go through
  `--check-only`; scenes are parsed for broken `res://` references; then the
  smoke line runs. A windowed launch is never mistaken for a test.
- **Playtest.** `godot_run` with a window plus `game_playtest` — she launches,
  takes screenshots on an interval, presses keys you ask for, and can ask the
  local vision model a question about the last frame. You can also just play
  and tell her what felt wrong.
- **Export.** `godot_export` lists the presets in `export_presets.cfg` and
  runs one; the output must land inside the project (or a folder you allowed).
- **Design.** The `game-dev-studio` pack keeps a GDD, a vertical slice first,
  a cut list, and a playtest protocol — one mechanic deep beats five shallow.
- **Assets.** With ComfyUI running, `game-assets` generates backgrounds,
  seamless tiles, UI panels and single sprites, then slices / palette-quantizes
  / alpha-keys them with a small Pillow script. It is honest that consistent
  multi-frame animation sheets are not something txt2img does well.

## Live editor bridge (optional)

Remedy's native tools cover everything reproducible. For live editor state —
inspecting the open scene tree, editing nodes while the editor is up — you can
list a community MCP server in `config.toml`:

```toml
mcp_servers = ["godot=npx -y <godot-mcp-package>"]
```

Its tools appear as `mcp_godot_*`; `mcp_status` shows whether it connected.
MCP results are treated as third-party data and never replace the headless
verify. See the `engine-mcp-bridge` pack.

## Learning

Remedy still learns procedures from her own work. Game-dev procedures she
picks up are now graded by how the turn went and promoted or retired on that
record — see [Skills](07-skills.md). The Settings toggle *Allow skill creation*
really does stop new ones being written.
