# Save systems

## Rules
- Saves go to `user://` (`OS.get_user_data_dir()`), never `res://` (read-
  only in exports).
- Save **data**, not nodes. A save is a dictionary of plain values; the
  game rebuilds the scene from it.
- Never load saves with `ResourceLoader`/`.tres`: a crafted file can carry
  a script path. Use JSON or `FileAccess.store_var(v, false)` (no objects).
- Version every save; migrate forward; never crash on an old one.
- Write to a temp file, then rename over the real one; keep one backup.
- Autosave on level complete and on quit; manual slots only if the GDD
  asks for them.

## Minimal JSON implementation (autoload `Game`)
```gdscript
extends Node

const SAVE_PATH := "user://save.json"
const SAVE_VERSION := 1

var data: Dictionary = _defaults()

func _defaults() -> Dictionary:
    return {"version": SAVE_VERSION, "level": 1, "coins": 0,
            "settings": {"music": 0.8, "sfx": 1.0}}

func save_game() -> bool:
    var tmp := SAVE_PATH + ".tmp"
    var f := FileAccess.open(tmp, FileAccess.WRITE)
    if f == null:
        push_error("save: %s" % error_string(FileAccess.get_open_error()))
        return false
    f.store_string(JSON.stringify(data, "\t"))
    f.close()
    if FileAccess.file_exists(SAVE_PATH):
        DirAccess.copy_absolute(SAVE_PATH, SAVE_PATH + ".bak")
    return DirAccess.rename_absolute(tmp, SAVE_PATH) == OK

func load_game() -> void:
    if not FileAccess.file_exists(SAVE_PATH):
        data = _defaults(); return
    var text := FileAccess.get_file_as_string(SAVE_PATH)
    var parsed: Variant = JSON.parse_string(text)
    if typeof(parsed) != TYPE_DICTIONARY:
        push_warning("save corrupt; using defaults"); data = _defaults(); return
    data = _migrate(parsed)

func _migrate(d: Dictionary) -> Dictionary:
    var v := int(d.get("version", 0))
    # if v < 2: d["new_field"] = default; v = 2
    var merged := _defaults()
    merged.merge(d, true)
    merged["version"] = SAVE_VERSION
    return merged
```
JSON turns ints into floats: cast with `int()` on read. `Vector2` and
friends are not JSON; store `[x, y]` or use `var_to_str`/`str_to_var`
(strings, safe) for engine types.

## What to save
- Progress: current level/room id, unlocked flags, collectibles by id.
- Settings: volumes, fullscreen, input remaps (action → event strings).
- Stats: playtime, deaths — cheap, useful for playtests.
Not: node positions mid-level (unless the design needs mid-level saves),
transient state, anything derivable.

## Checkpoints vs full saves
Checkpoint = in-memory snapshot restored on death (`data.duplicate(true)`
at the checkpoint, reassign on respawn). Full save = disk on level end.
Do not write to disk on every coin.

## Testing
A diag script: write defaults, reload, compare; feed a version-0 file and
check migration; feed garbage and check defaults without error. Run it
when save code changes. Delete `user://save.json` between playtests when
progress affects the test; note it in the log.
