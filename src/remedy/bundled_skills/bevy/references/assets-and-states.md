# Assets and states

## Asset loading

`assets/` next to `Cargo.toml` is the root (override with
`AssetPlugin { file_path: … }`). Paths are relative to it.

```rust
fn setup(mut commands: Commands, asset_server: Res<AssetServer>) {
    let hero: Handle<Image> = asset_server.load("sprites/hero.png");
    commands.spawn((Sprite::from_image(hero), Transform::default()));
}
```

- `load` returns immediately; the file streams in. A sprite with an
  unloaded image simply does not draw yet.
- Missing file: `WARN bevy_asset: … Path not found` in stderr, no panic.
  Grep for it after a playtest.
- Hold handles in a `Resource` so assets are not dropped/reloaded:

```rust
#[derive(Resource)]
struct GameAssets { hero: Handle<Image>, font: Handle<Font> }
```

- Check readiness: `asset_server.load_state(&handle)` /
  `asset_server.is_loaded_with_dependencies(&handle)`.
- Hot reload: `DefaultPlugins.set(AssetPlugin { watch_for_changes_override: Some(true), ..default() })`
  plus the `file_watcher` feature. Version-dependent — check docs.rs.
- Custom formats: implement `Asset` + `AssetLoader`, or use `bevy_common_assets`
  for RON/JSON/TOML.

## Loading state pattern

```rust
#[derive(States, Debug, Clone, PartialEq, Eq, Hash, Default)]
enum GameState { #[default] Loading, Menu, Playing, Paused }

app.init_state::<GameState>()
   .add_systems(OnEnter(GameState::Loading), start_loading)
   .add_systems(Update, check_loaded.run_if(in_state(GameState::Loading)))
   .add_systems(OnEnter(GameState::Playing), spawn_level)
   .add_systems(OnExit(GameState::Playing), despawn_level)
   .add_systems(Update, gameplay.run_if(in_state(GameState::Playing)));

fn check_loaded(assets: Res<GameAssets>, server: Res<AssetServer>, mut next: ResMut<NextState<GameState>>) {
    if server.is_loaded_with_dependencies(&assets.hero) { next.set(GameState::Menu); }
}
```

Transitions happen at the state-transition point in the schedule, not
instantly inside the system that called `next.set`.

The `bevy_asset_loader` crate automates the loading state if the owner
wants it; ask before adding crates.

## Cleaning up on exit

Tag entities with a marker component and despawn in `OnExit`:

```rust
#[derive(Component)] struct LevelEntity;
fn despawn_level(mut c: Commands, q: Query<Entity, With<LevelEntity>>) {
    for e in &q { c.entity(e).despawn(); }   // despawn_recursive pre-0.16
}
```

## Sub-states and computed states (0.14+)

`#[derive(SubStates)] #[source(GameState = GameState::Playing)] enum PlayState { Running, Paused }`
— exists only while the parent is `Playing`. Useful for pause menus.

## Pause

`app.add_systems(Update, physics.run_if(in_state(PlayState::Running)))`
and toggle with a key: read `Res<ButtonInput<KeyCode>>`,
`.just_pressed(KeyCode::Escape)`, then `next.set(...)`. Also
`time.pause()` on `ResMut<Time<Virtual>>` if you want timers frozen.
