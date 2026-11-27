---
name: bevy
description: >
  Build Rust games with Bevy: ECS mental model, plugins, schedules, states,
  assets, 2D sprites, fast iteration (cargo check, dynamic_linking,
  opt-level), backgrounded cargo run plus game_playtest. Use when the owner
  mentions Bevy or Cargo.toml depends on bevy.
version: 1.0.0
author: Remedy
tags: [game, bevy, rust, ecs, cargo]
requires: []
tools: [game_project_info, bash_exec, file_read, file_write, file_edit, repo_search, list_dir, game_playtest, computer_screenshot, vision_decode]
triggers:
  - "\\bbevy\\b"
---

# Bevy (Rust ECS engine)

Bevy compiles slowly and changes API every minor version. Two habits keep
you honest: check the version in `Cargo.toml` before writing a line, and use
`cargo check` as the oracle between edits instead of full builds.

## Fingerprint

1. `game_project_info(path)`; `file_read Cargo.toml` → `bevy = "0.x"` and
   its `features`. `Cargo.lock` has the exact patch.
2. `src/main.rs` (App builder), `src/**` plugins, `assets/` (default asset
   root, relative to the crate).
3. `.cargo/config.toml` → linker / `-Zshare-generics` tweaks already set.
4. Rust toolchain: `cargo --version`, `rustc --version`. No cargo → say so;
   you can still write code but cannot verify it.

**Version discipline.** Names moved between releases (`SpriteBundle` →
`Sprite` component in 0.15; `Camera2dBundle` → `Camera2d`; `add_system` →
`add_systems(Schedule, …)` in 0.11; `Color::rgb` → `Color::srgb` in 0.14).
When unsure, `repo_search` the crate source under
`~/.cargo/registry/src/*/bevy_*-<ver>/` or read `docs.rs/bevy/<ver>`. Do not
write from memory for a version you have not checked.

## ECS in one breath

Entities are ids; components are plain structs (`#[derive(Component)]`);
systems are functions whose parameters are queries and resources; resources
are singletons (`#[derive(Resource)]`); events are queued messages
(`#[derive(Event)]`, `EventWriter`/`EventReader`). Systems run inside
schedules: `Startup` once, `Update` every frame, `FixedUpdate` at a fixed
rate (`Time<Fixed>`). Patterns, ordering, commands, change detection:
`references/ecs-patterns.md`.

## App skeleton

```rust
use bevy::prelude::*;

fn main() {
    App::new()
        .add_plugins(DefaultPlugins)
        .add_systems(Startup, setup)
        .add_systems(Update, (movement, bounce).chain())
        .run();
}
```

Group features as plugins (`impl Plugin for PlayerPlugin { fn build(&self,
app: &mut App) {…} }`) and keep `main.rs` short.

## Iteration speed (do this first on a new project)

- `cargo check` after every edit: seconds, catches type/borrow errors.
- Dev profile: `[profile.dev] opt-level = 1` and
  `[profile.dev.package."*"] opt-level = 3` so dependencies are fast but
  your code compiles quickly.
- `cargo run --features bevy/dynamic_linking` during development (never
  for release). Faster linker (`lld` / `mold`) via `.cargo/config.toml`.
- Full `cargo test` links the whole engine — slow. Keep logic in plain
  modules and test those; see `references/iteration-speed.md`.

## States, assets, 2D

- `#[derive(States)] enum GameState { Menu, Playing }`, `init_state`,
  `OnEnter(GameState::Playing)`, `run_if(in_state(...))`.
- `AssetServer::load("sprites/hero.png")` returns a `Handle<T>`
  immediately; the asset arrives later — check `asset_server.load_state`
  or use a loading state.
- 2D: `Camera2d` + `Sprite` components (0.15+), `Transform` for position,
  `TextureAtlasLayout` for sheets. Walkthrough: `references/2d-setup.md`;
  states/assets: `references/assets-and-states.md`.

## Verify → playtest

1. `cargo check` (oracle). 2. `cargo clippy` if configured. 3. `cargo test`
   for pure logic crates/modules. 4. Launch: `bash_exec("cargo run")` is
   auto-backgrounded because it opens a window — the first run compiles
   for minutes, so set a long timeout or compile first with `cargo build`.
   Then `game_playtest(command="cargo run", seconds=…, keys=…, question=…)`
   or `computer_screenshot` + `vision_decode`. Read stderr for panics
   (`thread 'main' panicked`) and Bevy `WARN`/`ERROR` lines (missing assets
   log as warnings, not crashes).

## Common faults

| Symptom | Fix |
|---------|-----|
| `B0001` / query conflict panic | two `&mut` queries overlap — add `Without<>` filters or `ParamSet` |
| sprite invisible | no `Camera2d`; z-order (`Transform.translation.z`); asset path wrong (logs WARN) |
| `the trait bound ... IntoSystem` error | system param is not a valid `SystemParam` (e.g. `&World`, owned types) |
| everything too fast/slow | multiply by `time.delta_secs()` (`delta_seconds()` pre-0.15) |
| 5-minute rebuild on each change | dynamic_linking + opt-level tweaks missing |

## Checklist

```text
[ ] bevy version read; API names checked against that version
[ ] dev profile + dynamic_linking set up (or owner declined)
[ ] cargo check green after each edit
[ ] pure logic tested; cargo run backgrounded; playtest screenshot read
[ ] release build without dynamic_linking when shipping
```
