# pygame loop

Applies to pygame 2.x and pygame-ce (same API; ce adds extras).

## Skeleton (`main.py`)

```python
import sys
import pygame
from game.logic import World

WIDTH, HEIGHT, FPS = 800, 600, 60

def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Game")
    clock = pygame.time.Clock()
    world = World(WIDTH, HEIGHT)
    running = True
    while running:
        dt = clock.tick(FPS) / 1000.0          # seconds since last frame
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE:
                world.jump()
        keys = pygame.key.get_pressed()
        world.move(dx=(keys[pygame.K_RIGHT] - keys[pygame.K_LEFT]), dt=dt)
        world.update(dt)
        screen.fill((20, 20, 40))
        world.draw(screen)
        pygame.display.flip()
    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
```

`KEYDOWN` for one-shot presses; `key.get_pressed()` for held keys.
`clock.tick(FPS)` caps the rate and returns ms elapsed.

## Surfaces and rects

- `pygame.image.load("assets/hero.png").convert_alpha()` after `set_mode`.
- `surface.get_rect(center=(x, y))`; move with `rect.move_ip(dx, dy)` or set
  `rect.center`. Keep float positions in `pygame.Vector2` and copy into
  `rect` each frame — rects are ints.

## Sprites and groups

```python
class Player(pygame.sprite.Sprite):
    def __init__(self, pos):
        super().__init__()
        self.image = pygame.Surface((32, 32)); self.image.fill("orange")
        self.rect = self.image.get_rect(center=pos)
        self.pos = pygame.Vector2(pos); self.vel = pygame.Vector2()

    def update(self, dt):
        self.pos += self.vel * dt
        self.rect.center = self.pos

players = pygame.sprite.GroupSingle(Player((100, 100)))
enemies = pygame.sprite.Group()
enemies.update(dt); enemies.draw(screen)
```

Collisions: `pygame.sprite.spritecollide(sprite, group, dokill)`,
`groupcollide(g1, g2, kill1, kill2)`, `collide_mask` (needs `self.mask`).

Sheets: `sheet.subsurface(pygame.Rect(x, y, w, h))` per frame; advance an
index on a `dt` timer.

## Text, sound, timers

- `font = pygame.font.Font(None, 32)`; `screen.blit(font.render("Score", True, "white"), (8, 8))`.
- `pygame.mixer.Sound("assets/jump.wav").play()`; music via
  `pygame.mixer.music.load/play(-1)`.
- `pygame.time.set_timer(pygame.USEREVENT + 1, 1000)` posts an event/second.

## Low-res scaling

`pygame.SCALED` flag on `set_mode`, or draw to a small Surface and
`transform.scale` it onto the screen.

## Gotchas

- `convert()` before `set_mode` → `pygame.error: No video mode`.
- No `pygame.display.flip()` → nothing appears.
- `rect` truncates floats → jitter; keep a `Vector2`. Load assets once.
