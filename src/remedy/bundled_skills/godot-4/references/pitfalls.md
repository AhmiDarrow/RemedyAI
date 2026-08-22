# Pitfalls (Godot 4)

## Files and cache
- `.godot/` is a cache: gitignore it, never edit it, delete it if imports
  look corrupt, then `godot_import`.
- `*.import` sidecar files **are** committed; they hold import settings.
- An asset renamed outside the editor keeps its `.import` only if you moved
  that too. Move both, or let the editor do it.
- `uid://` mismatch after manual moves: `--import`, then `godot_check`. The
  editor logs "Unrecognized UID" or silently rewrites the header.
- `res://` paths are case-sensitive on Linux/Android/Web exports. Windows
  hides the bug; `repo_search` for mismatched case before export.
- Non-resource data files (`.json`, `.csv`, `.txt`) are not exported unless
  listed in the preset's `include_filter`.

## Script runtime
- `@onready` vars are `null` in `_init` and in anything called before
  `_ready`. Parent `_ready` runs after children; a child cannot read its
  parent's `@onready` values in its own `_ready`.
- `get_node("X")` is relative to the script's node; `%X` needs "Access as
  Unique Name"; `$"../X"` couples you to the tree shape.
- Signals connected both in the editor (`[connection]` in `.tscn`) and in
  code fire twice.
- `queue_free()` is safe anywhere; `free()` inside a handler of that node
  crashes.
- Changing the tree during physics callbacks: `call_deferred`,
  `set_deferred("monitoring", false)`.
- Moving physics bodies in `_process` jitters; use `_physics_process`.
- Integer division: `7 / 2 == 3`; write `7.0 / 2`.
- `Array`/`Dictionary` are reference types: `duplicate(true)` for deep copy.
  A `.tres` loaded twice is one shared object.
- `await` inside `_process` piles up a coroutine every frame.
- Poll `is_action_just_pressed` in one callback only; reading it in both
  `_process` and `_physics_process` misses or doubles presses.
- `randomize()` is automatic in 4.x; `seed()` only for determinism.

## Scenes
- Instanced-scene children are hidden unless "Editable Children" is on;
  edits there become overrides stored in the parent `.tscn`.
- A collision shape with no shape resource gives silent non-collision.
- Layer = what I am; mask = what I scan. `Area2D` with mask 0 detects nothing.
- `Control` nodes under a `Node2D` ignore anchors; keep UI in a `CanvasLayer`.
- `visible = false` does not stop `_process`;
  `process_mode = PROCESS_MODE_DISABLED` does.
- `TileMap` is deprecated from 4.3 for `TileMapLayer`; it still works.

## Headless
- First run after clone: `--import`, or textures load as null.
- No `quit()` = hangs until the timeout. Always `--quit-after` or a script.
- `DisplayServer.get_name() == "headless"` to skip audio/rendering work.
- `Input` does nothing headless; use `Input.parse_input_event` or a test
  framework's scene runner.

## Export
- Missing templates: only the owner can install them.
- Web needs COOP/COEP headers and will not run from `file://`.
- Debug export embeds the remote debugger and runs slower; ship release.
