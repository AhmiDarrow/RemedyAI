# arcade basics (3.x; 2.6 differs in camera and draw APIs)

arcade sits on pyglet/OpenGL. Check the version:
`python -c "import arcade; print(arcade.version.VERSION)"`.

## Skeleton

```python
import arcade

W, H, TITLE = 800, 600, "Game"

class GameView(arcade.View):
    def __init__(self):
        super().__init__()
        self.player = arcade.Sprite(":resources:images/tiles/boxCrate.png", scale=0.5)
        self.player.center_x, self.player.center_y = 100, 120
        self.players = arcade.SpriteList()
        self.players.append(self.player)
        self.walls = arcade.SpriteList(use_spatial_hash=True)
        for x in range(32, W, 64):
            wall = arcade.Sprite(":resources:images/tiles/grassMid.png", scale=0.5, center_x=x, center_y=32)
            self.walls.append(wall)
        self.physics = arcade.PhysicsEnginePlatformer(self.player, walls=self.walls, gravity_constant=0.5)
        self.left = self.right = False

    def on_draw(self):
        self.clear()
        self.walls.draw()
        self.players.draw()

    def on_update(self, delta_time: float):
        self.player.change_x = (self.right - self.left) * 5
        self.physics.update()

    def on_key_press(self, key, modifiers):
        if key == arcade.key.LEFT: self.left = True
        elif key == arcade.key.RIGHT: self.right = True
        elif key == arcade.key.UP and self.physics.can_jump(): self.player.change_y = 12

    def on_key_release(self, key, modifiers):
        if key == arcade.key.LEFT: self.left = False
        elif key == arcade.key.RIGHT: self.right = False

def main():
    window = arcade.Window(W, H, TITLE)
    window.show_view(GameView())
    arcade.run()

if __name__ == "__main__":
    main()
```

`:resources:` paths are arcade's bundled sample assets (placeholders).

## Concepts

- `Window` owns the GL context; `View` is a screen. `window.show_view(view)`
  switches. Callbacks: `on_show_view`, `on_draw`, `on_update(delta_time)`,
  `on_key_press`, `on_mouse_press`, `on_resize`.
- `SpriteList` draws in one batch; `use_spatial_hash=True` for static sets.
- Physics: `PhysicsEngineSimple(player, walls)` (top-down),
  `PhysicsEnginePlatformer(player, walls=…, gravity_constant=…)`,
  `PymunkPhysicsEngine` for rigid bodies.
- Collision: `check_for_collision(a, b)`, `check_for_collision_with_list(s, lst)`.
- Camera (3.x): `arcade.Camera2D()`; set `.position`, call `.use()` before
  world draw; a second camera for HUD.
- Tiled maps: `arcade.load_tilemap(path, scaling)` → `arcade.Scene.from_tilemap(tm)`.
- Text: `arcade.Text("Score", x, y, arcade.color.WHITE, 18)` — create
  once, update `.text`, `.draw()` each frame.
- Sound: `arcade.load_sound(path)`; `arcade.play_sound(sound)`.

## Speed

Engine movement (`change_x`) is pixels per update (60/s default), not per
second; use `delta_time` in your own logic.

## Gotchas

- Drawing outside `on_draw` does nothing; `arcade.run()` blocks.
- Headless testing is impractical (needs GL); keep rules in pure modules.
