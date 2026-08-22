# Headless runs and CI

## Flags that matter
| Flag | Effect |
|------|--------|
| `--headless` | No window, no audio, dummy renderer; required on CI |
| `--path <dir>` | Project directory (the one with project.godot) |
| `-s <script.gd>` | Run a script `extends SceneTree` (or MainLoop) instead of the main scene |
| `--quit-after N` | Quit after N frames; with the main scene it is a boot test |
| `--check-only` | With `-s`: parse the script only, do not run it |
| `--import` | Import assets into `.godot/` then exit (first run after clone) |
| `--export-release <preset> <path>` / `--export-debug` / `--export-pack` | Export; template must be installed |
| `--log-file <file>` | Copy engine output to a file |
| `--verbose` / `--quiet` | More / less output |

Things that do **not** exist: `--test` for user tests (engine self-tests
only), `--lint`, a project-wide `--check-only`. Do not invent them.

## Smoke vs diag scripts
`tools/smoke_boot.gd` (in SKILL.md) answers "does the main scene boot".
Add `tools/diag_*.gd` for targeted checks, all `extends SceneTree`:
```gdscript
extends SceneTree
func _init() -> void:
    var errors := 0
    for p in ["res://levels/l1.tscn", "res://player/player.tscn"]:
        if load(p) == null:
            push_error("DIAG: cannot load " + p); errors += 1
    for a in ["move_left", "jump"]:
        if not InputMap.has_action(a):
            push_error("DIAG: missing action " + a); errors += 1
    print("DIAG OK" if errors == 0 else "DIAG FAIL %d" % errors)
    quit(errors)
```
`quit(code)` sets the process exit code; CI and `godot_run` read it. Add
`await process_frame` before `quit` when nodes were added to the tree.

## Reading the output
- `SCRIPT ERROR:` + `at: func (res://...:line)` → parse or runtime error.
- `ERROR: Failed loading resource: res://...` → missing file / case / uid.
- `WARNING:` lines are usually safe; `Condition "..." is true` are not.
- Exit 0 with no `SMOKE OK` means the script quit early; treat as failure.

## Timeouts
Headless boot of a small project: 2–10 s. Import of many textures: minutes.
Pass `timeout_seconds` to `godot_run`/`bash_exec` accordingly. A scene that
never calls `quit()` runs until killed: always `--quit-after` or a script
that quits.

## CI job shape (GitHub Actions, Linux)
```yaml
- uses: chickensoft-games/setup-godot@v2
  with: { version: 4.3.0, use-dotnet: false }
- run: godot --headless --path . --import
- run: godot --headless --path . -s tools/smoke_boot.gd
- run: godot --headless --path . -s addons/gut/gut_cmdln.gd -gexit
```
Pin the engine version to the one in `project.godot` `config/features`.
Cache nothing from `.godot/`; `--import` rebuilds it.
