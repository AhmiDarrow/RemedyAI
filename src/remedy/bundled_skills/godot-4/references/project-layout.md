# Project layout

## Recommended tree
```text
project.godot
export_presets.cfg
.gitignore
.gitattributes
addons/            # third-party plugins (gut/, gdUnit4/, ...)
autoload/          # game.gd, events.gd, audio.gd
scenes/
  main.tscn        # run/main_scene
  ui/              # hud.tscn, menus
  levels/          # l1.tscn ...
player/            # player.tscn + player.gd together
enemies/
  slime/           # slime.tscn, slime.gd, slime.png
data/              # Resource scripts + .tres (weapons/, levels/)
assets/
  sprites/ tiles/ audio/ fonts/ shaders/
tools/             # smoke_boot.gd, diag_*.gd, editor scripts
test/              # unit/ integration/ (GUT or gdUnit4)
build/             # export output (gitignored)
```
Feature folders (`player/`, `enemies/slime/`) keep a scene, its script and
its art together; `assets/` holds shared material. Either pure by-type or
pure by-feature works; a mix makes `repo_search` guesswork. Follow whatever
the existing project already does.

## .gitignore
```text
.godot/
.import/
build/
export/
*.tmp
.mono/
```
Keep `*.import` sidecar files **tracked**.

## .gitattributes
```text
*.png binary
*.wav binary
*.ogg binary
*.ttf binary
*.tscn text eol=lf
*.tres text eol=lf
*.gd text eol=lf
```
LF endings keep scene diffs readable across OSes.

## project.godot fields you will read
```ini
[application]
config/name="Game"
run/main_scene="res://scenes/main.tscn"
config/features=PackedStringArray("4.3", "GL Compatibility")

[display]
window/size/viewport_width=640
window/size/viewport_height=360
window/stretch/mode="canvas_items"
window/stretch/aspect="keep"

[rendering]
textures/canvas_textures/default_texture_filter=0   # 0 = nearest (pixel art)
renderer/rendering_method="gl_compatibility"

[input]
jump={ "deadzone": 0.5, "events": [ ... ] }

[layer_names]
2d_physics/layer_1="world"
2d_physics/layer_2="player"

[autoload]
Game="*res://autoload/game.gd"

[editor_plugins]
enabled=PackedStringArray("res://addons/gut/plugin.cfg")
```
Hand-editing `project.godot` is fine for autoloads, layer names and
rendering flags. Input events are verbose: add them in the editor, or add
actions at runtime with `InputMap.add_action` in an autoload if you must.

## Naming
- Files and folders: `snake_case`. A scene and its script share a stem.
- Node names: `PascalCase`. Signals/methods/vars: `snake_case`.
- Classes: `class_name PascalCase`; one per file, stem matches.
- Input actions: `snake_case` verbs (`move_left`, `jump`, `interact`).
- Groups: plural nouns (`enemies`, `pickups`).
