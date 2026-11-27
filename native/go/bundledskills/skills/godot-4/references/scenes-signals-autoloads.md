# Scenes, signals, autoloads

## Scene composition
One scene = one reusable thing (Player, Enemy, Bullet, HUD, Level). Root
node type decides behaviour: `CharacterBody2D` for moving bodies, `Area2D`
for triggers/pickups, `Node2D` for plain containers, `Control` for UI.
Attach the script to the root; children are looked up with `$Name` /
`%Unique`. A scene that needs another scene instances it; it does not
reach into its parent.

## Wiring rule: signals up, calls down
- Parent calls child methods: `$HUD.set_health(hp)`.
- Child emits signals, parent listens: `player.died.connect(_on_player_died)`.
- Siblings never talk directly; the shared parent or an autoload bus mediates.

## Autoload singletons
`project.godot`:
```ini
[autoload]
Game="*res://autoload/game.gd"
Events="*res://autoload/events.gd"
```
`*` means enabled. A script autoload becomes a node under `/root` named
`Game`; access it as `Game.score` from anywhere. Event bus pattern:
```gdscript
# autoload/events.gd
extends Node
signal coin_collected(value: int)
signal level_finished
```
```gdscript
Events.coin_collected.emit(5)             # anywhere
Events.coin_collected.connect(_on_coin)   # HUD
```
Keep autoloads few: Game (state/score/save), Events (bus), Audio (sfx
pool). No gameplay logic in them. In a `SceneTree` smoke script they are
at `root.get_node_or_null("Game")` once the tree has initialised.

## Groups
`add_to_group("enemies")` in `_ready` or via the editor Node tab.
`get_tree().get_nodes_in_group("enemies")`, `call_group("enemies", "freeze")`.
Good for "all of X"; bad as a replacement for references you already hold.

## Scene changing and persistence
`get_tree().change_scene_to_file("res://levels/l2.tscn")` frees the current
scene. Anything that must survive (score, inventory) lives in an autoload or
a Resource, not on the level.

## Ordering facts
- `_ready` runs children first, then parent. A parent's `_ready` can use
  `@onready` children; a child cannot assume its parent is ready.
- `_enter_tree` fires before `_ready`; `_exit_tree` on removal.
- Modifying the tree during physics callbacks needs `call_deferred` or
  `queue_free()`; otherwise "Can't change this state while flushing queries".
- Set `owner` on nodes added at runtime if `PackedScene.pack()` should
  include them.

## .tscn by hand (only when needed)
```text
[gd_scene load_steps=2 format=3 uid="uid://c8a1example"]
[ext_resource type="Script" path="res://player/player.gd" id="1_abc"]
[node name="Player" type="CharacterBody2D"]
script = ExtResource("1_abc")
[node name="Sprite2D" type="Sprite2D" parent="."]
[connection signal="died" from="." to="." method="_on_died"]
```
`load_steps` = ext + sub resources + 1. Wrong `load_steps` still loads (the
editor rewrites it); a wrong `id` fails. Run `godot_check` after any manual
edit. Do not invent `uid` values; omit the attribute and let the editor
assign one.
