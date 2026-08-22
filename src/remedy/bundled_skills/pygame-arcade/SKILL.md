---
name: pygame-arcade
description: >
  Build Python games with pygame (or pygame-ce) and arcade: event loop, clock
  and delta time, surfaces and sprite groups, arcade Window/View/SpriteList,
  a main.py layout whose logic stays testable without a display, pytest with
  SDL_VIDEODRIVER=dummy, windowed playtest, pyinstaller packaging. Use when
  the owner mentions pygame, arcade, or a Python game.
version: 1.0.0
author: Remedy
tags: [game, python, pygame, arcade, pytest, pyinstaller]
requires: []
tools: [game_project_info, bash_exec, file_read, file_write, file_edit, repo_search, list_dir, game_playtest, computer_screenshot, vision_decode]
triggers:
  - "\\b(pygame|pygame-ce|arcade game|python game|python arcade)\\b"
---

# pygame / arcade (Python games)

Python games are quick to scaffold and quick to verify: the logic runs
headless under pytest, and the window opens in a second for a playtest.

## Fingerprint

1. `game_project_info(path)`; `file_read pyproject.toml` / `requirements.txt`
   → `pygame`, `pygame-ce` (drop-in fork, same `import pygame`), or
   `arcade`. `main.py` or `<pkg>/__main__.py` is the entry.
2. `python -c "import pygame; print(pygame.version.ver)"` or
   `import arcade; print(arcade.version.VERSION)` inside the project's
   environment (`.venv`, `uv run`, `poetry run` — follow what exists).
3. `assets/` for images/sounds; `tests/` for pytest.

Install when missing (ask first if the env is shared): `pip install pygame-ce`
or `pip install arcade` (arcade 3.x needs Python 3.9+ and OpenGL 3.3+).

## Layout that stays testable

```text
<game>/
  main.py            creates the window, runs the loop — thin
  game/
    logic.py         rules, state, grid, scoring — no pygame import
    entities.py      dataclasses for player/enemies; pure math
    render.py        draws state onto a Surface / arcade window
    input.py         maps pygame events → logic commands
  assets/
  tests/test_logic.py
```

The split is the whole trick: `logic.py` never touches a display, so
`pytest` runs anywhere. Details: `references/testing-without-a-window.md`.

## pygame loop (the shape)

`pygame.init()` → `screen = pygame.display.set_mode((w, h))` →
`clock = pygame.time.Clock()` → loop: `dt = clock.tick(60) / 1000` →
`for event in pygame.event.get()` (handle `QUIT`, `KEYDOWN`) →
`keys = pygame.key.get_pressed()` → update logic with `dt` → draw to
`screen` → `pygame.display.flip()`. Sprites: subclass `pygame.sprite.Sprite`
with `image` + `rect`, keep them in `pygame.sprite.Group`, call
`group.update(dt)` and `group.draw(screen)`. Full skeleton and collision
helpers: `references/pygame-loop.md`.

## arcade (the shape)

`arcade.Window(w, h, title)` subclass or `arcade.View` per screen; override
`on_draw` (`self.clear()` then draw), `on_update(delta_time)`,
`on_key_press/on_key_release`. Sprites go in `arcade.SpriteList` (batched
GPU draw); `arcade.PhysicsEngineSimple` / `PhysicsEnginePlatformer` handle
walls and gravity; `arcade.check_for_collision_with_list`. Skeleton:
`references/arcade-basics.md`.

## Verify ladder

1. `python -m py_compile main.py game/*.py` — syntax in a second.
2. `python -m pytest -q` — logic. For anything that imports pygame in a
   test, set `SDL_VIDEODRIVER=dummy` (and `SDL_AUDIODRIVER=dummy`) so it
   runs on CI with no display. arcade needs a GL context and is harder to
   run headless; keep arcade out of tests.
3. `ruff check .` / `mypy` if the project has them configured.
4. Launch: `bash_exec("python main.py")` — auto-backgrounded because it
   opens a window (pass `background=true` if unsure). Then
   `game_playtest(command="python main.py", seconds=…, keys=…, question=…)`
   or `computer_screenshot` + `vision_decode`. Read stderr: a traceback
   means the window closed on its own; say so instead of "it ran".

## Packaging

`pyinstaller --onefile --windowed main.py` plus `--add-data "assets;assets"`
(Windows `;`, POSIX `:`). Resolve asset paths through `sys._MEIPASS` at
runtime. pygame ships hooks; arcade needs `--collect-all arcade` and
`pyglet`. Walkthrough and gotchas: `references/packaging.md`.

## Common faults

| Symptom | Fix |
|---------|-----|
| window opens and freezes | event loop missing `pygame.event.get()` each frame |
| 100% CPU, speed varies by machine | no `clock.tick`; movement not scaled by `dt` |
| `pygame.error: No available video device` in CI | `SDL_VIDEODRIVER=dummy` before `pygame.init()` |
| sprite never moves | updating `x` but not `rect.x` / `rect.center` |
| `convert()` error | `set_mode` must run before `Surface.convert()` |
| arcade black window | forgot `self.clear()` or drawing before `on_draw` |
| pyinstaller exe missing assets | `--add-data` + `_MEIPASS` path helper |

## Checklist

```text
[ ] engine + version identified; env located (venv/uv/poetry)
[ ] logic module has no display import; pytest green (dummy driver if needed)
[ ] python main.py backgrounded; playtest screenshot read; stderr clean
[ ] packaging only when asked; exe launched once to confirm
```
