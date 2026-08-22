# GDScript 2 idioms (Godot 4.x)

## Declarations
```gdscript
class_name Player extends CharacterBody2D

@export var speed: float = 220.0
@export_range(0.0, 1.0) var friction: float = 0.2
@export var bullet_scene: PackedScene
@onready var sprite: Sprite2D = $Sprite2D
@onready var anim: AnimationPlayer = %Anim   # unique name (editor: "Access as Unique Name")

signal died(cause: String)
enum State { IDLE, RUN, AIR }
var state: State = State.IDLE
const GRAVITY: float = 980.0
```

Static typing everywhere (`var x: int`, `-> void`). It turns runtime errors
in the owner's hands into parse errors caught by `godot_check`.

## Movement
```gdscript
func _physics_process(delta: float) -> void:
    var dir := Input.get_axis("move_left", "move_right")
    velocity.x = dir * speed
    if not is_on_floor():
        velocity.y += GRAVITY * delta
    elif Input.is_action_just_pressed("jump"):
        velocity.y = -420.0
    move_and_slide()
```
`move_and_slide()` takes no arguments in 4.x and uses `velocity` directly.
3D: `CharacterBody3D`, same shape with `Vector3` and `up_direction`.

## Signals and callables
```gdscript
died.connect(_on_died)                 # typed, preferred
died.connect(_on_died.bind("extra"))   # bind extra args
died.emit("spikes")
button.pressed.connect(func() -> void: print("hi"))
if died.is_connected(_on_died): died.disconnect(_on_died)
```

## Await
```gdscript
await get_tree().create_timer(0.4).timeout
await anim.animation_finished
var result: int = await some_coroutine()   # coroutine = func that awaits
```
A function containing `await` returns a coroutine; callers `await` it or
fire-and-forget. `yield` does not exist.

## Instancing and tree
```gdscript
var b := bullet_scene.instantiate() as Area2D
b.global_position = muzzle.global_position
get_tree().current_scene.add_child(b)   # not self, or it moves with the shooter
queue_free()                             # never free() a node mid-callback
get_tree().change_scene_to_packed(preload("res://levels/l2.tscn"))
get_tree().reload_current_scene()
```

## Tween
```gdscript
var t := create_tween()
t.tween_property(self, "scale", Vector2(1.2, 0.8), 0.06)
t.tween_property(self, "scale", Vector2.ONE, 0.12).set_trans(Tween.TRANS_BACK)
t.tween_callback(queue_free)
```
Tweens are not nodes; they die with their creator and must be recreated.

## Renames from Godot 3 (do not write these)
`onready` → `@onready`; `export` → `@export`; `yield` → `await`;
`instance()` → `instantiate()`; `KinematicBody2D` → `CharacterBody2D`;
`move_and_slide(velocity)` → `move_and_slide()`; `connect("sig", obj, "m")`
→ `sig.connect(obj.m)`; `Spatial` → `Node3D`; `empty()` → `is_empty()`;
`rand_range` → `randf_range`; `OS.get_ticks_msec` → `Time.get_ticks_msec`;
`Tween` node → `create_tween()`; `PoolStringArray` → `PackedStringArray`.
