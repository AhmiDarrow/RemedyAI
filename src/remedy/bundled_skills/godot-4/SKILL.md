---
name: godot-4
description: >
  Work inside a Godot 4.x project: find the engine, read project.godot, edit
  scenes and GDScript/C#, verify headless after every change, playtest
  windowed, export. Use whenever the project has project.godot or the owner
  mentions Godot, GDScript, .tscn, autoloads, GUT or gdUnit4.
version: 1.0.0
author: Remedy
tags: [game, godot, gdscript, engine, headless, export]
requires: []
tools: [game_project_info, godot_run, godot_check, godot_export, godot_import, game_playtest, local_discover, bash_exec, file_read, file_write, file_edit, repo_search, list_dir, computer_screenshot, skill_activate]
triggers:
  - "\\b(godot|gdscript|\\.tscn|\\.gd\\b|autoload|gdunit|gut\\b)\\b"
  - "\\b(export templates?|scene tree|project\\.godot|\\.tres|\\.godot/)\\b"
local:
  binaries:
    - id: godot
      names: [godot4, godot, Godot_v4, godot4-console]
      env: [GODOT, GODOT4_BIN]
---

# Godot 4 (engine loop)

Godot 4.x only. GDScript 2 syntax. If `project.godot` says `config_version=4`
or `config/features` lacks `"4.x"`, stop and tell the owner it is a Godot 3
project — do not mix idioms.

## Decision tree (start here)

1. `game_project_info(path)` → engine name/version, language, binary, verify
   command. If it reports no binary: try `local_discover`, `GODOT` /
   `GODOT4_BIN` env, `godot*` on PATH, a `Godot*.exe` in the project root
   (prefer `*_console.exe` on Windows). Still nothing → ask: "tell me where
   Godot is" and move on. Never start a setup wizard.
2. `file_read` `project.godot`: main scene (`run/main_scene`), autoloads,
   input map, window size/stretch, rendering method, physics layer names.
3. Work in scenes (`.tscn`, text) + scripts (`.gd`). Prefer editing `.gd`
   over hand-editing `.tscn`; when you must touch `.tscn`, keep `load_steps`,
   `[ext_resource]` ids and `uid://` values intact.
4. **Verify after every change** (hard rule):
   - `godot_check(paths=[edited .gd/.tscn])` → parse errors, missing refs.
   - Smoke: `godot_run(path, script="tools/smoke_boot.gd", headless=True)`
     and require `SMOKE OK` in stdout. No smoke script yet → create it
     (below) before the first edit. Fallback: `godot_run(headless=True,
     quit_after=1)` — weaker, prints only script/load errors.
5. Playtest: `godot_run(path, headless=False)` (auto-backgrounded) then
   `game_playtest(pid, seconds, interval, keys, question)` or
   `computer_screenshot`. Read the engine log for `SCRIPT ERROR`, `E 0:00`.
6. Export: `godot_export(list=True)` → pick preset → `godot_export(preset,
   output, debug=False)`. Templates missing → tell the owner to install them
   from Editor → Manage Export Templates; you cannot download them silently.

Read `references/INDEX.md` and pull what you need with `file_read`.

## Canonical smoke script — `tools/smoke_boot.gd`

```gdscript
extends SceneTree

func _init() -> void:
    var path: String = ProjectSettings.get_setting("application/run/main_scene", "")
    if path.is_empty():
        push_error("SMOKE FAIL: no main scene set")
        quit(1)
        return
    var packed := load(path) as PackedScene
    if packed == null:
        push_error("SMOKE FAIL: cannot load %s" % path)
        quit(1)
        return
    var inst := packed.instantiate()
    root.add_child(inst)
    await process_frame
    await process_frame
    print("SMOKE OK ", path)
    quit(0)
```

`_init` on a `SceneTree` script runs before any scene; `await process_frame`
lets `_ready` fire so `@onready` null errors surface. Extend with
`assert`s on autoloads (`root.get_node_or_null("Game")`).

## Headless command lines (what the tools run)

```text
godot --headless --path . -s tools/smoke_boot.gd
godot --headless --path . --quit-after 2
godot --headless --path . --check-only -s path/to/script.gd
godot --headless --path . --import
godot --headless --path . --export-release "Windows Desktop" build/game.exe
godot --headless --path . --log-file logs/godot.log
godot --headless --path . -s addons/gut/gut_cmdln.gd -gexit
godot --headless --path . -s addons/gdUnit4/bin/GdUnitCmdTool.gd --add res://test
```

`--check-only` parses one script; it is not a whole-project lint. First run
after a clone or new assets needs `--import` (or `godot_import`) or the
`.godot/` cache is empty and `load()` of textures fails headless. Headless
runs need a bounded `timeout_seconds`; a scene that never calls `quit()`
hangs otherwise.

## GDScript idioms (use these, not Godot 3 forms)

- `@export var speed: float = 200.0`, `@onready var sprite: Sprite2D = $Sprite2D`
- `signal died(cause: String)` → `died.emit("fall")`; connect with
  `died.connect(_on_died)` or `connect("died", Callable(self, "_on_died"))`
- `await get_tree().create_timer(0.5).timeout`; `await anim.animation_finished`
- `Input.get_axis("move_left", "move_right")`, `Input.get_vector(...)`
- `CharacterBody2D`: set `velocity`, then `move_and_slide()` (no args)
- `preload("res://x.tscn").instantiate()`; `load()` for dynamic paths
- `create_tween().tween_property(self, "scale", Vector2.ONE, 0.2)`
- Groups: `add_to_group("enemies")`, `get_tree().call_group("enemies", "stun")`
- Typed arrays `var items: Array[Item] = []`; `class_name Item extends Resource`
- `String.is_empty()`, `randf()`, `randi_range()`, `Vector2.ZERO`, `PI`

## Pitfalls (check before claiming done)

- `.godot/` must be in `.gitignore`; never commit it, never edit it.
- Renaming/moving files changes nothing in `uid://`, but stale `uid://` in
  `.tscn`/`.tres` after deleting `.godot/` fall back to path; a wrong path +
  wrong uid = broken ref. Fix with `godot_import` then `godot_check`.
- `res://` paths are case-sensitive on export (Linux/Android) even if Windows
  editor runs fine. `repo_search` for case mismatches before export.
- `@onready` vars are null inside `_init` and before `_ready`.
- `get_node("Player")` is relative; use `%UniqueName` or `$"../Player"`.
- Physics bodies moved in `_process` jitter; use `_physics_process`.
- Signals connected in the editor and in code fire twice.
- C# projects need the `.NET`/Mono build of Godot and `dotnet build`
  before headless runs; see `references/csharp-godot.md`.

When the engine is not on the machine you can still edit scripts, but say
plainly that nothing was verified.
