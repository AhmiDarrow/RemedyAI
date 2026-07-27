---
name: godot-game-engine
version: 1.0.0
description: >
  Build and debug games in Godot Engine 4.7.1: project layout, scenes/nodes,
  typed GDScript, signals, resources, input, physics, export, and common pitfalls.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - godot
  - gdscript
  - engine
  - tooling
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - file_edit
  - repo_search
  - shell
metadata:
  source: library
  library_id: godot-game-engine
  official: true
  domain: gaming
  security_flags: []
  engine_version: "4.7.1"
---

# Godot Game Engine (4.7.1)

Practical playbook for shipping work in **Godot 4.7.1-stable** (GDScript-first).
Use when the user is creating, refactoring, debugging, or exporting a Godot project.

**Docs home:** https://docs.godotengine.org/en/4.7/  
**Download:** https://godotengine.org/download/ (pick **4.7.1** stable; Standard build for GDScript, .NET build only if they use C#)

## When to use

- New project setup or folder layout for a 2D/3D Godot game
- Scene tree design, script architecture, signals vs direct calls
- GDScript 4.x typing, resources, autoloads, input maps, physics layers
- Editor vs runtime bugs, performance profiling, export packaging
- Migrating habits from Godot 3.x / older 4.x into 4.7.x APIs

## Operating rules

- Prefer read-only exploration of `project.godot` and scenes before editing.
- Match the project’s existing style (2D vs 3D, composition patterns, naming).
- Prefer **typed GDScript**, `@export`, signals, and **Resources** over giant global scripts.
- Never invent node paths, signal names, or API that you have not verified in the project or docs.
- Ask before destructive changes (deleting scenes, rewriting `project.godot`, force-overwriting exports).
- Never print or commit secrets, API keys, or private personal data.
- Confirm before long shell commands that write outside the project folder.

## Pin the engine version

1. Confirm the user runs **4.7.1-stable** (Help → About, or binary name).
2. In `project.godot`, note `config_version` and feature tags; do not casually bump `config/features` beyond what they have installed.
3. Export templates must match the editor version (Project → Export → Manage Export Templates → **4.7.1**).
4. If they are on 4.7.0, prefer upgrading to 4.7.1 for regression fixes (maintenance release; no known 4.7 → 4.7.1 incompatibilities).

## Project layout (recommended)

Keep the repo editor-friendly and VCS-friendly:

```text
project/
  project.godot
  .gitignore          # ignore .godot/, export_presets.cfg secrets if any, *.translation cache as needed
  icon.svg
  addons/             # editor plugins (commit intentionally)
  assets/             # art, audio (or split by type)
  scenes/
    main.tscn
    player/
    ui/
    levels/
  scripts/            # shared pure scripts / helpers (optional)
  resources/          # .tres data (stats, items, themes)
  shaders/
```

Rules of thumb:

- One **main scene** set in Project Settings → Application → Run.
- Prefer **scene composition** (nested `.tscn`) over one mega-scene.
- Put reusable data in **Resource** (`.tres` / custom `Resource` scripts), not hardcoded dictionaries in nodes when the data will be shared or tuned.
- Keep `addons/` pinned and documented; treat third-party addons as dependency risk.

## Core mental model

| Idea | Practice in 4.x |
|------|------------------|
| **Scene = prefab + class** | Nested scenes compose; you can inherit/extend scenes |
| **Nodes do jobs** | Prefer the right node (`CharacterBody2D`, `Area2D`, `Control`, …) over one `Node` with everything |
| **Signals for decoupling** | Child emits; parent or systems connect — avoid deep `get_node("../../..")` chains |
| **Groups for broadcast** | `add_to_group("enemies")` for bulk queries; still prefer typed refs for hot paths |
| **Resources for data** | Stats, item defs, dialogue lines — serializable and shareable |
| **Autoload sparingly** | True globals only (audio bus helper, save service, event bus) — not “dumping ground” |

Separate **2D and 3D** stacks: pixels for 2D, meters for 3D; mix deliberately (e.g. 2D UI over 3D world).

## GDScript 4.x patterns (prefer these)

### Typed scripts and exports

```gdscript
class_name Player
extends CharacterBody2D

@export var move_speed: float = 220.0
@export var jump_velocity: float = -400.0

@onready var anim: AnimatedSprite2D = $AnimatedSprite2D
@onready var hitbox: Area2D = $Hitbox

signal died(source: Node)

func _physics_process(delta: float) -> void:
    var direction := Input.get_axis("move_left", "move_right")
    velocity.x = direction * move_speed
    move_and_slide()
```

- Use `class_name` when other scripts need the type.
- Prefer `@export` over raw inspector hacks; group with `@export_group` / `@export_subgroup` when the inspector gets noisy.
- Prefer explicit return types and typed arrays: `Array[Node]`, `Dictionary` only when structure is truly free-form.

### Signals over brittle paths

```gdscript
# child
signal health_changed(current: int, maximum: int)

func take_damage(amount: int) -> void:
    health = max(health - amount, 0)
    health_changed.emit(health, max_health)
    if health == 0:
        died.emit(self)

# parent / UI
func _ready() -> void:
    player.health_changed.connect(_on_player_health_changed)
```

Connect in the editor **or** in code; if both, document which is authoritative to avoid double-connects.

### Await and one-shot timers

```gdscript
await get_tree().create_timer(0.15).timeout
await anim.animation_finished
```

Avoid busy-wait loops in `_process` for simple delays.

### Callables and deferred work

- Use `Callable` / `.bind()` for flexible connections.
- `call_deferred("add_child", node)` when parenting during physics/query callbacks.
- Never free a node mid-iteration of its siblings without care — queue_free and deferred patterns matter.

## Scenes and composition checklist

1. **Identify the unit of reuse** (Player, Bullet, Chest, Chunk) → own `.tscn`.
2. **Root node type** matches the job (physics body vs pure data vs UI).
3. **Collision layers/masks** named in Project Settings → Layer Names (2D/3D Physics).
4. **Unique names** (`%NodeName`) for stable in-scene lookups when paths churn.
5. **Editable children** only when intentional; prefer instance + script API.
6. Keep scene files reviewable: avoid opaque binary-only assets when a text scene helps VCS (default text `.tscn` is fine).

## Input

1. Define actions in **Project → Project Settings → Input Map** (`move_left`, `jump`, `interact`, …).
2. Support keyboard + gamepad from day one when the genre needs it (`Input.get_vector`, `Input.get_axis`).
3. UI: prefer `Control` focus and `gui_input` / `_unhandled_input` split so gameplay doesn’t steal menu clicks.
4. Remapping: design data-driven action names early if players will rebind.

## Physics (common 2D/3D)

- **CharacterBody2D/3D** + `move_and_slide()` for player/enemies with kinematic feel.
- **RigidBody** for simulation; don’t fight it with manual position sets every frame.
- **Area** for triggers and hitboxes; keep hitboxes on clear layers.
- Set **safe margins**, floor snap, and motion modes deliberately for platformers.
- Debug with visible collision shapes and the editor’s debug run options.

## UI (Control)

- Root UI under a `CanvasLayer` when it must stay on top of the world.
- Use containers (`VBoxContainer`, `MarginContainer`, …) before absolute positioning.
- Theme / Theme Type Variation for consistent look; put fonts and colors in a Theme resource.
- Scale: plan for multiple resolutions (stretch mode/aspect in Project Settings → Display → Window).

## Autoloads (singletons)

Good: `SaveService`, `AudioManager`, `EventBus`, `GameState` (thin).  
Bad: thousands of lines of level logic in one autoload.

```gdscript
# Example thin event bus
extends Node
signal quest_completed(id: String)
```

Prefer scene-local state; promote to autoload only when multiple distant systems need the same service.

## Resources and saves

- Custom resources:

```gdscript
class_name ItemDef
extends Resource

@export var id: StringName
@export var display_name: String
@export var max_stack: int = 99
```

- Runtime saves: `FileAccess` + JSON or binary; write atomically (temp file then rename) when possible.
- Version your save schema (`save_version: int`) and plan migrations.
- User data path: `OS.get_user_data_dir()` — never assume a writable project folder in exported builds.

## Shaders and VFX

- Start with **VisualShader** or short `canvas_item` / `spatial` shaders; keep uniforms documented.
- Prefer GPUParticles for bulk effects; pool CPU-spawned nodes (bullets, damage numbers).
- Watch overdraw and transparent sorting on mobile targets.

## Performance habits

1. Profile before rewriting (Debugger → Profiler / Monitors).
2. Avoid per-frame `get_node` string lookups — cache with `@onready` or stored refs.
3. Avoid allocating heavy objects every frame in `_process`.
4. Use object pools for bullets/VFX if spawn rate is high.
5. Occlusion/culling and LODs matter more in 3D; batch sprites/tilemaps sensibly in 2D.
6. Large tilemaps / navigation: bake and stream by chunk when maps grow.

## Debugging workflow

1. Reproduce with a **minimal scene** when possible.
2. Check remote scene tree while the game runs (Editor debugger).
3. Print with context: `push_warning()`, `push_error()`, or temporary `print` — remove noise before ship.
4. Break on errors; watch stack traces into engine code (open-source advantage).
5. For logic races: signals fired twice, `_ready` order, and `await` across free’d nodes.

## Export and shipping

1. Install **matching** export templates for 4.7.1.
2. Configure presets (Windows, Linux, macOS, Android, Web, …) — set app name, icon, permissions.
3. Test **exported** builds early (path, fullscreen, input, performance differ from F5).
4. Feature tags / custom build options only when needed (e.g. demo vs full).
5. Web export: respect browser input and audio autoplay limits; test itch/self-host upload sizes.
6. Do not commit private keystores or store signing passwords into the repo.

## Godot 4.7.x awareness (stay current)

4.7 introduced larger product work (e.g. HDR output path, official asset store integration, drawable textures, Android/XR and Java interface work from GDScript). When advising:

- Prefer **4.7 docs** (`/en/4.7/`) over stale 3.x or early-4.0 tutorials.
- If a tutorial uses old APIs (`yield` → `await`, `KinematicBody` → `CharacterBody`, `connect("sig", self, "fn")` string form → `signal.connect(callable)`), modernize to 4.x.
- For regressions after upgrading, check the 4.7.1 maintenance notes and interactive changelog before deep rewrites.

## Suggested work sequence (any feature)

1. **Clarify** target platform(s), 2D vs 3D, and “done” for this task.
2. **Map** existing scenes/scripts that touch the feature (search `project.godot`, `.tscn`, `.gd`).
3. **Design** the smallest scene + script surface (who owns state? who emits signals?).
4. **Implement** with typed GDScript and exported tunables.
5. **Playtest** in editor; then verify export if paths or input are involved.
6. **Clean** temporary prints, dead nodes, and unused exports.
7. **Document** one short note for the user: how to tune and where the entry scene is.

## Anti-patterns to push back on

- One autoload “Game” script that owns every system
- Stringly-typed node paths without `%UniqueName` or exported `NodePath`
- Copy-pasted enemy scenes with no shared base or components
- Saving absolute filesystem paths into resources
- Ignoring collision layer names until “why doesn’t this hit?”
- Shipping without ever running an exported build

## Done when

- The user’s Godot task works in **4.7.1** (or blockers are listed with a next safe step).
- Changes fit the project’s scene/script style and use modern GDScript 4 patterns.
- Input, layers, and entry scenes are named clearly enough to extend later.
- Export/template version caveats are called out if shipping is in scope.
- No secrets or machine-specific paths left in committed resources.
