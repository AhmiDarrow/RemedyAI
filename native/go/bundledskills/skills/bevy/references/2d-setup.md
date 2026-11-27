# 2D setup (Bevy 0.15+ component style; 0.14 uses *Bundle types)

## Camera and window

```rust
App::new()
    .add_plugins(DefaultPlugins
        .set(WindowPlugin { primary_window: Some(Window {
            title: "Game".into(), resolution: (800., 600.).into(), ..default() }), ..default() })
        .set(ImagePlugin::default_nearest()))     // crisp pixel art
    .add_systems(Startup, setup)
    .run();

fn setup(mut commands: Commands) {
    commands.spawn(Camera2d);                    // Camera2dBundle::default() on 0.14
}
```

Origin is the screen centre; +y is up. Zoom via `OrthographicProjection { scale, .. }`.

## Sprite

```rust
commands.spawn((
    Sprite::from_image(asset_server.load("sprites/hero.png")),
    Transform::from_xyz(0.0, 0.0, 1.0),          // z orders draw: higher = on top
    Player,
));
```

0.14: `SpriteBundle { texture: handle, transform, ..default() }`.
Flip: `sprite.flip_x = true`. Tint: `sprite.color = Color::srgb(1., 0.5, 0.5)`.
Colored rectangle with no texture: `Sprite { color, custom_size: Some(Vec2::new(w, h)), ..default() }`.

## Sprite sheets

```rust
let layout = layouts.add(TextureAtlasLayout::from_grid(UVec2::new(32, 32), 4, 1, None, None));
commands.spawn((
    Sprite::from_atlas_image(texture, TextureAtlas { layout, index: 0 }),
    Transform::default(),
    AnimTimer(Timer::from_seconds(0.1, TimerMode::Repeating)),
));

fn animate(time: Res<Time>, mut q: Query<(&mut AnimTimer, &mut Sprite)>) {
    for (mut t, mut s) in &mut q {
        t.0.tick(time.delta());
        if t.0.just_finished() {
            if let Some(atlas) = &mut s.texture_atlas { atlas.index = (atlas.index + 1) % 4; }
        }
    }
}
```

`layouts` is `ResMut<Assets<TextureAtlasLayout>>`.

## Input and movement

```rust
fn movement(keys: Res<ButtonInput<KeyCode>>, time: Res<Time>, mut q: Query<&mut Transform, With<Player>>) {
    let mut dir = Vec2::ZERO;
    if keys.pressed(KeyCode::KeyA) { dir.x -= 1.0; }
    if keys.pressed(KeyCode::KeyD) { dir.x += 1.0; }
    if keys.pressed(KeyCode::KeyW) { dir.y += 1.0; }
    if keys.pressed(KeyCode::KeyS) { dir.y -= 1.0; }
    for mut t in &mut q {
        t.translation += (dir.normalize_or_zero() * 200.0 * time.delta_secs()).extend(0.0);
    }
}
```

`delta_secs()` is 0.15+; `delta_seconds()` before. Mouse:
`Res<ButtonInput<MouseButton>>`, cursor via `Query<&Window>` →
`window.cursor_position()` and `camera.viewport_to_world_2d`.

## Collision (simple)

Bevy ships no physics. For AABB: compare `Transform.translation` and sizes
(`bevy::math::bounding::{Aabb2d, IntersectsVolume}`). For real physics add
`avian2d` (ECS-native) or `bevy_rapier2d`; ask the owner before adding.

## Text

`commands.spawn((Text::new("Score: 0"), TextFont { font_size: 32.0, ..default() }));`
(0.14: `TextBundle::from_section`). Update via `Query<&mut Text>`.

## Audio

`commands.spawn(AudioPlayer::new(asset_server.load("sfx/jump.ogg")));`
(0.14: `AudioBundle`).
