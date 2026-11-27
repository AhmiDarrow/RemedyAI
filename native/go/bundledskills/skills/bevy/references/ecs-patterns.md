# ECS patterns (Bevy 0.14–0.16 idioms; verify against Cargo.toml)

## Components, resources, events

```rust
#[derive(Component)] struct Player;
#[derive(Component)] struct Velocity(Vec2);
#[derive(Resource, Default)] struct Score(u32);
#[derive(Event)] struct Hit { entity: Entity, damage: u32 }
```

Register: `app.init_resource::<Score>()`, `app.add_event::<Hit>()`.

## Spawning

```rust
fn setup(mut commands: Commands) {
    commands.spawn((Player, Velocity(Vec2::ZERO), Transform::default()));
}
```

`Commands` are deferred: entities exist after the system finishes (apply
point). `commands.entity(e).insert(..)` / `.despawn()`; `despawn_recursive`
for children pre-0.16 (0.16 despawns children by default).

## Queries

```rust
fn movement(time: Res<Time>, mut q: Query<(&Velocity, &mut Transform), With<Player>>) {
    for (v, mut t) in &mut q {
        t.translation += v.0.extend(0.0) * time.delta_secs();
    }
}
```

- Filters: `With<T>`, `Without<T>`, `Changed<T>`, `Added<T>`, `Or<(…)>`.
- `q.single()` / `q.single_mut()` (returns `Result` in 0.16; panics earlier
  — use `get_single` pre-0.16 for a `Result`).
- Two mutable queries over the same component must be disjoint
  (`Without<>`), or wrap in `ParamSet<(Query<…>, Query<…>)>`.
- `Entity` in a query tuple gives the id: `Query<(Entity, &Health)>`.

## Events

```rust
fn attack(mut w: EventWriter<Hit>) { w.send(Hit { .. }); }   // .write in 0.16
fn apply(mut r: EventReader<Hit>, mut q: Query<&mut Health>) {
    for hit in r.read() { if let Ok(mut h) = q.get_mut(hit.entity) { h.0 -= hit.damage; } }
}
```

Events live two frames; readers that run before the writer see them next
frame. Order systems with `.chain()` or `.after()` when it matters.

## Schedules and ordering

- `Startup`, `Update`, `FixedUpdate` (default 64 Hz; tune via
  `Time::<Fixed>::from_hz`), `PreUpdate`/`PostUpdate` for engine-adjacent
  work.
- `add_systems(Update, (a, b).chain())` runs in order; `a.before(b)`;
  `SystemSet` for groups; `.run_if(cond)` for conditions.
- Physics-like movement in `FixedUpdate` using `time.delta_secs()` from
  `Res<Time>` (inside FixedUpdate it is the fixed delta).

## Change detection

`Query<&Health, Changed<Health>>` matches only entities whose component was
mutably accessed this frame. `Res<T>::is_changed()`. Use `Mut::bypass_change_detection` sparingly.

## Plugins

```rust
pub struct EnemyPlugin;
impl Plugin for EnemyPlugin {
    fn build(&self, app: &mut App) {
        app.add_systems(Update, (spawn_enemies, enemy_ai));
    }
}
```

`app.add_plugins((PlayerPlugin, EnemyPlugin))`. Configure DefaultPlugins:
`DefaultPlugins.set(WindowPlugin { primary_window: Some(Window { title: "x".into(), ..default() }), ..default() })`.

## Hierarchy

`commands.spawn(Parent).with_children(|p| { p.spawn(Child); })`;
`Query<&Children>`, `Query<&Parent>` (`ChildOf` in 0.16). Child
`Transform` is local; `GlobalTransform` is world.
