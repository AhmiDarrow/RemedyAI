# Input maps

## Actions, not keys
Code reads actions (`Input.is_action_pressed("jump")`), never keycodes.
Define actions in `project.godot` `[input]` (editor: Project Settings →
Input Map). Name them as `snake_case` verbs: `move_left`, `move_right`,
`move_up`, `move_down`, `jump`, `attack`, `dash`, `interact`, `pause`,
`ui_accept`/`ui_cancel` are built in — reuse them for menus.

## Default bindings (bind both from day one)
| Action | Keyboard | Gamepad |
|--------|----------|---------|
| move_* | WASD + arrows | left stick + d-pad |
| jump | Space / Z | A (bottom face) |
| attack | X / J | X (left face) |
| dash | Shift / C | RB or B |
| interact | E / Up | Y (top face) |
| pause | Esc | Start |

Godot uses generic SDL-style button names (`JOY_BUTTON_A` = bottom face)
so Xbox/PlayStation/Switch map consistently; label by position in UI or
read `Input.get_joy_name(0)` to pick icons.

## Reading input
```gdscript
var axis := Input.get_axis("move_left", "move_right")          # -1..1
var vec := Input.get_vector("move_left", "move_right", "move_up", "move_down")
if Input.is_action_just_pressed("jump"): ...
if Input.is_action_just_released("jump"): ...                  # variable jump
```
`get_vector` applies the action deadzone and normalises; set deadzone
0.2–0.3 on stick actions in the Input Map (default 0.5 is too high for
movement). Read input in `_physics_process` for movement; `_unhandled_input`
for one-shot UI/global actions (pause) so UI can consume events first.

## Buffering and forgiveness
Keep timers for jump buffer and coyote time in the player script (see
juice-and-feel.md). Do not try to buffer via the InputMap.

## Remapping UI
Minimal rebind flow:
```gdscript
func rebind(action: String, event: InputEvent) -> void:
    for old in InputMap.action_get_events(action):
        if (old is InputEventKey) == (event is InputEventKey):
            InputMap.action_erase_event(action, old)
    InputMap.action_add_event(action, event)
    Game.data.settings["binds"][action] = var_to_str(event)   # safe string
```
Restore at boot with `str_to_var`. Offer a "reset to defaults" that reloads
from `ProjectSettings.get_setting("input/" + action)`.

## Touch (only if the GDD says so)
`TouchScreenButton` nodes under a `CanvasLayer`, each with an `action`
set, so the same code path works. Enable `input/pointing/emulate_touch_from_mouse`
for desktop testing. Virtual sticks are a last resort; tap-to-move or
swipe gestures usually fit a phone better.

## Headless and playtest note
`Input` does nothing in `--headless`; verify input maps exist with a diag
script (`InputMap.has_action`), and exercise bindings in the windowed
playtest via `game_playtest` keys. Gamepad cannot be scripted by
`game_playtest`; ask the owner for a controller pass before shipping.
