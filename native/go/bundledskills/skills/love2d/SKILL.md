---
name: love2d
description: >
  Build LÖVE (love2d) games: main.lua callbacks, conf.lua, locating the love
  binary, windowed launch in the background, luac/luacheck as cheap oracles,
  busted tests for pure Lua, sprites/quads/animation, fused executables and
  .love export. Use when the project has main.lua/conf.lua or the owner
  mentions LÖVE, love2d, or Lua games.
version: 1.0.0
author: Remedy
tags: [game, love2d, lua, engine]
requires: []
tools: [game_project_info, local_discover, bash_exec, file_read, file_write, file_edit, repo_search, list_dir, game_playtest, computer_screenshot, vision_decode]
triggers:
  - "\\b(love2d|löve|love 2d|love 11|main\\.lua|conf\\.lua|lua game)\\b"
local:
  binaries:
    - id: love
      names: [love, lovec, love.exe, lovec.exe]
      env: [LOVE, LOVE_BIN]
---

# LÖVE (love2d)

A LÖVE game is a folder with `main.lua`. The engine is one binary; there
is no project file, no build step, and the window opens in under a second.
Lua errors show up as a blue screen in the window and on stderr when you
use `lovec` (Windows console build).

## Fingerprint

1. `game_project_info(path)`; `file_read main.lua`, `conf.lua` (window size,
   title, `t.version = "11.5"`, modules enabled).
2. Layout: `lib/` for third-party (`anim8`, `bump`, `hump`, `lume`),
   `src/` or flat modules, `assets/` for images/sounds/fonts.
3. `.luacheckrc` / `.busted` present ⇒ the project already has oracles;
   use them.

## Find love (never a wizard)

`LOVE` / `LOVE_BIN` env → `local_discover` → PATH (`love`, `lovec`) →
well-known dirs: `%PROGRAMFILES%\LOVE\love.exe` and `lovec.exe` (Windows),
`/Applications/love.app/Contents/MacOS/love` (macOS), `/usr/bin/love`
(Linux packages, AppImage anywhere). Prefer `lovec` on Windows — it keeps
a console so `print` and errors reach stderr. Not found → ask the owner
where it is (or if they want it installed from love2d.org) and continue
writing Lua meanwhile. `love --version` confirms the version.

## Callbacks (the shape)

```lua
function love.load() … end                 -- once
function love.update(dt) … end             -- dt in seconds
function love.draw() … end                 -- every frame after update
function love.keypressed(key) … end        -- one-shot
```

`love.keyboard.isDown("left")` for held keys. Everything else (`mousepressed`,
`resize`, `quit`, `focus`) in `references/love-callbacks.md`, along with a
state/scene pattern and a module layout.

## Verify ladder (cheap → real)

1. `luac -p main.lua src/*.lua` — syntax only, instant. LÖVE 11 is LuaJIT
   (Lua 5.1 syntax): `luac5.1 -p` or `luajit -bl file.lua >nul` if `luac`
   is 5.3+ and complains about `goto`/integer division oddities.
2. `luacheck .` — undefined globals, unused vars. Needs a `.luacheckrc`
   with `std = "love"` or `globals = {"love"}`; write one if missing.
3. `busted` — tests for pure Lua modules (no `love.*` calls). Setup and a
   fake-`love` shim: `references/testing.md`.
4. Launch: `bash_exec("lovec .")` from the project root (or `love .`) —
   auto-backgrounded because it opens a window. Then
   `game_playtest(command="lovec .", seconds=…, keys=…, question=…)` or
   `computer_screenshot` + `vision_decode`. A blue error screen or a
   traceback on stderr means it did **not** run — quote the line.

## Sprites, quads, animation

`love.graphics.newImage`, `newQuad(x, y, w, h, imgW, imgH)` for sheet
frames, `love.graphics.draw(img, quad, x, y, r, sx, sy, ox, oy)`.
`love.graphics.setDefaultFilter("nearest", "nearest")` for pixel art.
`SpriteBatch` for many draws of one image. Animation = list of quads + a
timer accumulated from `dt`, or the `anim8` library. Code in
`references/sprites-and-quads.md`.

## Export

- `.love` = zip of the project folder's **contents** (main.lua at the zip
  root) renamed to `.love`.
- Fused Windows exe: `copy /b love.exe+game.love game.exe` next to the
  DLLs from the LÖVE folder; ship that folder zipped. macOS: copy
  `love.app`, put `game.love` in `Contents/Resources/`, edit `Info.plist`.
- Web: `love.js` (third-party); say it exists, do not promise parity.
Exact steps and `conf.lua` fields: `references/conf-and-export.md`.

## Common faults

| Symptom | Fix |
|---------|-----|
| blue screen `attempt to index nil` | module `require` path wrong (`require "src.player"` uses dots, no `.lua`) |
| window but nothing drawn | drawing in `update`, or `love.draw` misspelled |
| sprites blurry | `setDefaultFilter("nearest")` before loading images |
| speed differs per machine | not multiplying by `dt` |
| `love .` runs the wrong folder | cwd is not the project; pass the absolute path |
| `print` shows nothing on Windows | use `lovec.exe`, or `io.stdout:setvbuf("no")` |

## Checklist

```text
[ ] love binary located (or owner told it is missing); version noted
[ ] luac -p / luacheck clean
[ ] busted green for pure modules (when tests exist or were added)
[ ] lovec . backgrounded; playtest screenshot read; stderr clean
[ ] export only when asked; .love zip root contains main.lua
```
