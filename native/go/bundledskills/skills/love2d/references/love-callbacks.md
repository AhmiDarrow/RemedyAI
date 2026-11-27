# love callbacks and structure

## Order

`love.load(args)` once → each frame: `love.update(dt)` → `love.draw()`.
Input callbacks fire between frames. `love.quit()` can return `true` to
cancel closing.

## The ones you use

| Callback | When |
|----------|------|
| `love.load(arg)` | startup; load assets here |
| `love.update(dt)` | every frame; `dt` seconds |
| `love.draw()` | every frame; only drawing |
| `love.keypressed(key, scancode, isrepeat)` / `keyreleased` | one-shot keys |
| `love.mousepressed(x, y, button, istouch)` / `mousereleased` / `mousemoved` | mouse |
| `love.wheelmoved(dx, dy)` | scroll |
| `love.resize(w, h)` | window resized |
| `love.focus(f)` | pause when unfocused |
| `love.quit()` | return true to veto |

Held input: `love.keyboard.isDown("a", "left")`,
`love.mouse.isDown(1)`, `love.mouse.getPosition()`.

## Minimal main.lua

```lua
local Player = require("src.player")

local player

function love.load()
  love.graphics.setDefaultFilter("nearest", "nearest")
  player = Player.new(100, 100)
end

function love.update(dt)
  local dx = (love.keyboard.isDown("right") and 1 or 0) - (love.keyboard.isDown("left") and 1 or 0)
  player:update(dt, dx)
end

function love.draw()
  player:draw()
  love.graphics.print(("FPS %d"):format(love.timer.getFPS()), 8, 8)
end

function love.keypressed(key)
  if key == "escape" then love.event.quit() end
  if key == "space" then player:jump() end
end
```

## A module (`src/player.lua`)

```lua
local Player = {}
Player.__index = Player

function Player.new(x, y)
  return setmetatable({ x = x, y = y, vx = 0, vy = 0, speed = 200 }, Player)
end

function Player:update(dt, dx)
  self.vx = dx * self.speed
  self.x = self.x + self.vx * dt
end

function Player:draw()
  love.graphics.rectangle("fill", self.x, self.y, 32, 32)
end

return Player
```

`require("src.player")` — dots for folders, no extension. Paths are
relative to the folder containing `main.lua` (the "source base").

## Scenes / states

Keep a `current` table with the same callbacks and forward to it:

```lua
local scenes = { menu = require("src.scenes.menu"), game = require("src.scenes.game") }
local current

function switch(name, ...) current = scenes[name]; if current.enter then current.enter(...) end end
function love.update(dt) current.update(dt) end
function love.draw() current.draw() end
function love.keypressed(k) if current.keypressed then current.keypressed(k) end end
```


## Timing

`dt` is variable; cap it (`dt = math.min(dt, 1/30)`) after a stall. For a
fixed step, accumulate: `acc = acc + dt; while acc >= STEP do step(STEP); acc = acc - STEP end`.

## Filesystem

`love.filesystem` reads the source base, writes the save dir. `io.open`
cannot see inside a `.love` zip — use `love.filesystem.read`.

## Libraries

`bump` (collision), `anim8` (animation), `hump` (gamestate/timer/camera),
`lume` (utils). Vendor into `lib/`.
