# Testing without a window

## The split

```python
# game/logic.py — no pygame / arcade import
from dataclasses import dataclass, field

@dataclass
class Player:
    x: float = 0.0
    y: float = 0.0
    vy: float = 0.0
    on_ground: bool = False

@dataclass
class World:
    width: int
    height: int
    player: Player = field(default_factory=Player)
    gravity: float = 1200.0
    score: int = 0

    def jump(self) -> None:
        if self.player.on_ground:
            self.player.vy = -450.0
            self.player.on_ground = False

    def move(self, dx: int, dt: float) -> None:
        self.player.x = max(0.0, min(self.width - 32, self.player.x + dx * 200 * dt))

    def update(self, dt: float) -> None:
        p = self.player
        p.vy += self.gravity * dt
        p.y += p.vy * dt
        floor = self.height - 32
        if p.y >= floor:
            p.y, p.vy, p.on_ground = floor, 0.0, True
```

`game/render.py` draws a `World` onto a Surface; `main.py` wires input.
Tests import `game.logic` only.

## Tests

```python
# tests/test_logic.py
from game.logic import World

def step(world, n, dt=1/60):
    for _ in range(n):
        world.update(dt)

def test_player_lands_on_floor():
    w = World(800, 600)
    step(w, 120)
    assert w.player.on_ground and w.player.y == 600 - 32

def test_jump_only_from_ground():
    w = World(800, 600); step(w, 120)
    w.jump(); assert w.player.vy < 0
    vy = w.player.vy; w.jump(); assert w.player.vy == vy
```

Run `python -m pytest -q`. Fixed-step loops make physics assertions
deterministic; never sleep or use wall-clock time in tests.

## When a test must import pygame

Some code (e.g. `Rect` math, `Vector2`) is pygame without a display. Set
the dummy drivers before import — `tests/conftest.py`:

```python
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame
pygame.init()
```

`pygame.display.set_mode((1, 1))` under the dummy driver gives a working
Surface for `convert()` calls. In CI:

```yaml
env:
  SDL_VIDEODRIVER: dummy
  SDL_AUDIODRIVER: dummy
run: python -m pytest -q
```

Also export `PYGAME_HIDE_SUPPORT_PROMPT=1` to silence the banner.

## arcade

Needs a real GL context; the dummy driver does not help. Keep arcade
classes as thin adapters over pure logic, test the logic, and cover the
window with a playtest. If a smoke test of import is wanted, limit it to
`import arcade` and class construction without a `Window`.

## Randomness

Pass a `random.Random(seed)` into the world (`World(…, rng=Random(1))`)
so spawn tests are reproducible.

## Smoke-run the real loop

A one-second headless run catches crashes on startup:

```
SDL_VIDEODRIVER=dummy python -c "import main; main.main_frames(60)"
```

…if `main` exposes a frame-limited entry. Worth adding: `main(max_frames=None)`.
