# Sprites, quads, animation

## Images

```lua
love.graphics.setDefaultFilter("nearest", "nearest")   -- before loading, for pixel art
local img = love.graphics.newImage("assets/hero.png")
love.graphics.draw(img, x, y, rotation, sx, sy, ox, oy)
```

Origin (`ox, oy`) is the pivot for rotation/scale; `img:getWidth()/2` for
centre. Load once in `love.load`, never inside `draw`.

## Quads (sheet frames)

```lua
local sheet = love.graphics.newImage("assets/hero_sheet.png")
local fw, fh = 32, 32
local frames = {}
for i = 0, 3 do
  frames[#frames + 1] = love.graphics.newQuad(i * fw, 0, fw, fh, sheet:getDimensions())
end
love.graphics.draw(sheet, frames[1], x, y)
```

Use `sheet:getDimensions()` (11.x) as the last args. Bleeding between
frames at non-integer scales → add 1 px padding in the sheet or use
integer scale factors.

## Animation timer

```lua
local anim = { frames = frames, t = 0, i = 1, fps = 10 }

function anim:update(dt)
  self.t = self.t + dt
  while self.t >= 1 / self.fps do
    self.t = self.t - 1 / self.fps
    self.i = self.i % #self.frames + 1
  end
end

function anim:draw(img, x, y, flip)
  love.graphics.draw(img, self.frames[self.i], x, y, 0, flip and -1 or 1, 1, fw / 2, fh / 2)
end
```

Flip horizontally with `sx = -1` and an origin at the frame centre.

## anim8 (library)

```lua
local anim8 = require("lib.anim8")
local g = anim8.newGrid(32, 32, sheet:getWidth(), sheet:getHeight())
local run = anim8.newAnimation(g("1-4", 1), 0.1)
run:update(dt); run:draw(sheet, x, y)
```

Same idea, with pause/loop/flip built in.

## SpriteBatch

For hundreds of draws from one image (tiles, particles):

```lua
local batch = love.graphics.newSpriteBatch(tileset, 2000)
batch:clear()
for each tile: batch:add(quad, x, y)
love.graphics.draw(batch)
```

Rebuild only when tiles change; static maps build once.

## Camera and scaling

Manual camera:

```lua
love.graphics.push()
love.graphics.scale(scale)
love.graphics.translate(-camX, -camY)
-- world drawing
love.graphics.pop()
-- HUD drawing (unscaled)
```

Pixel-perfect upscaling: draw the world into a `love.graphics.newCanvas(320, 180)`
then draw the canvas scaled by an integer factor to the window. `push`/`pop`
keeps the HUD separate. `hump.camera` / `gamera` wrap this.

## Text and color

`love.graphics.setColor(r, g, b, a)` with 0–1 floats in 11.x (0–255 in
0.10). Reset to white after tinting or every later draw is tinted.
Fonts: `love.graphics.newFont("assets/font.ttf", 16)` then `setFont`;
`love.graphics.print(text, x, y)` / `printf(text, x, y, limit, "center")`.

## Tiled maps

`STI` loads Tiled exports: `local map = sti("assets/map.lua")`.

## Gotchas

- Forgetting `setColor(1,1,1)` before drawing images → everything tinted.
- Loading images every frame → GC stutter, memory climb.
- Non-integer positions with nearest filtering → shimmer; `math.floor` x/y.
- `newQuad` with the wrong reference size → frames sample from nowhere.
