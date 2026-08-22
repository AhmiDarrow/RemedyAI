# Testing LÖVE code

## Syntax oracle

```
luac -p main.lua src/*.lua lib/*.lua
```

LÖVE 11 embeds LuaJIT 2.1 (Lua 5.1 semantics + some 5.2 extensions).
If only `luac` 5.3/5.4 is installed, `-p` still catches most errors; for
exact parity use `luajit -bl file.lua > /dev/null` (`>nul` on Windows).
No Lua on the machine at all → `lovec .` is the parser (errors on stderr).

## luacheck

`luarocks install luacheck` (or a standalone `luacheck.exe`). `.luacheckrc`
at the project root:

```lua
std = "luajit"
globals = { "love" }
ignore = { "212" }           -- unused argument (callbacks have many)
exclude_files = { "lib/**" }
max_line_length = false
```

Run `luacheck .`. Warnings about undefined globals are the useful ones —
they are how typos in LÖVE function names surface before runtime.

## busted (unit tests)

`luarocks install busted`. Layout:

```text
src/grid.lua
spec/grid_spec.lua
.busted            (optional)
```

```lua
-- spec/grid_spec.lua
local Grid = require("src.grid")

describe("Grid", function()
  it("places and reads a cell", function()
    local g = Grid.new(4, 4)
    g:set(1, 2, "x")
    assert.are.equal("x", g:get(1, 2))
  end)

  it("rejects out of range", function()
    local g = Grid.new(2, 2)
    assert.has_error(function() g:set(3, 1, "x") end)
  end)
end)
```

Run `busted` from the project root (it adds `./?.lua;./?/init.lua` to the
path). Filter: `busted --filter="places"`. Output for CI:
`busted -o TAP` or `-o junit`.

## Keeping modules testable

Modules under test must not call `love.*` at load time. Separate:

- `src/grid.lua`, `src/rules.lua`, `src/inventory.lua` — pure; busted-safe.
- `src/scenes/*.lua`, `src/render.lua` — touch `love.graphics`; playtest-only.

When a pure module needs a tiny piece of the API (e.g. `love.math.random`),
inject it: `Grid.new(w, h, rng)` with `rng = rng or math.random`.

## Fake `love` table

For modules that cannot avoid the API, give busted a stub in
`spec/helper.lua` and load it with `busted --helper=spec/helper.lua`:

```lua
love = {
  graphics = setmetatable({}, { __index = function() return function() end end }),
  timer = { getTime = function() return 0 end },
  math = { random = math.random },
}
```

Tests then exercise logic; draw calls become no-ops. Do not assert on
rendering this way — that is what the playtest is for.

## Running tests inside LÖVE

Alternatives that execute under the real runtime: `lovetest`, `lust`, or a
`--test` arg handled in `main.lua` that runs assertions and calls
`love.event.quit(exitcode)`. Useful when behaviour depends on LuaJIT
specifics. Exit code reaches the shell only with `lovec`.
