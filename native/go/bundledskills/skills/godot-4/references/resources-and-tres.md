# Resources and .tres

## Custom Resource classes
```gdscript
# data/weapon_data.gd
class_name WeaponData extends Resource

@export var display_name: String = "Sword"
@export var damage: int = 3
@export var cooldown: float = 0.4
@export var icon: Texture2D
@export var projectile: PackedScene
```
Create instances in the editor (FileSystem → New Resource → WeaponData) and
save as `res://data/weapons/sword.tres`. Use from scripts:
```gdscript
@export var weapon: WeaponData
func attack() -> void:
    if weapon == null: return
    hit(weapon.damage)
```
Resources are shared by reference: two enemies using the same `.tres` share
one object. Mutate a copy (`weapon.duplicate()`) for per-instance state, or
keep runtime state on the node, not the resource.

## .tres anatomy
```text
[gd_resource type="Resource" script_class="WeaponData" load_steps=2 format=3 uid="uid://bq1example"]
[ext_resource type="Script" path="res://data/weapon_data.gd" id="1_x"]
[resource]
script = ExtResource("1_x")
display_name = "Sword"
damage = 3
```
Hand-editable and diff-friendly; prefer over `.res` (binary) for anything
under version control.

## preload vs load
- `preload("res://...")` resolves at parse time; the path must be a literal;
  the resource loads with the script. Use for things always needed.
- `load(path)` at runtime; accepts variables. Returns `null` on failure —
  check it.
- `ResourceLoader.load_threaded_request(path)` + `load_threaded_get` for big
  scenes behind a loading screen.
- `ResourceLoader.exists(path)` before `load` when the path comes from data.

## uid:// behaviour
Every imported/saved resource gets a stable `uid://` stored in its `.import`
sidecar or `.tres`/`.tscn` header and cached in `.godot/uid_cache.bin`.
References in scenes carry both `uid` and `path`; load tries uid first, path
second. Consequences:
- Moving a file inside the editor keeps uid and updates path: safe.
- Moving a file outside the editor (git mv, `file_write`) keeps the uid in
  the file but the cache is stale until the next import: run `godot_import`
  (`--import`) then `godot_check`.
- Deleting `.godot/` is safe; it rebuilds.
- Deleting a file and creating a new one with the same name gets a new uid;
  old references fall back to path and work, but the editor warns and
  rewrites them on save.
- Never hand-write a `uid://` you invented; omit the attribute and let the
  editor assign one.

## Saving game data
Do not use `ResourceSaver.save` + `load` for player saves: a `.tres` can
embed a script path and run code. Use JSON or `FileAccess.store_var(data,
false)` for untrusted files. See game-dev-studio `references/save-systems.md`.
