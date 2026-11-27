# conf.lua and export

## conf.lua

Runs before modules load; cannot use `love.graphics` etc.

```lua
function love.conf(t)
  t.identity = "mygame"            -- save directory name
  t.version = "11.5"               -- LÖVE version this was written for
  t.console = false                -- Windows: open a console (alt: use lovec)
  t.window.title = "My Game"
  t.window.icon = "assets/icon.png"
  t.window.width = 800
  t.window.height = 600
  t.window.resizable = false
  t.window.vsync = 1
  t.window.fullscreen = false
  t.window.highdpi = false
  t.modules.joystick = true
  t.modules.physics = false        -- disable unused modules to speed startup
end
```

Full field list: `love.conf` on the wiki for the version in use. A version
mismatch prints a warning, not an error.

## `.love` file

A zip of the project **contents** — `main.lua` must be at the zip root.

```
# PowerShell (from the project folder)
Compress-Archive -Path * -DestinationPath ..\game.zip; Rename-Item ..\game.zip game.love
# bash
zip -9 -r ../game.love . -x '*.git*' 'tests/*'
```

Test it: `love game.love`. Exclude `.git`, tests, and raw source art.

## Windows fused exe

From the LÖVE install folder (`%PROGRAMFILES%\LOVE`):

```
copy /b love.exe+game.love game.exe
```

Ship `game.exe` together with every `.dll` and `license.txt` from that
folder, zipped. `lovec.exe` fused the same way keeps a console (debug
builds only). `love.filesystem.isFused()` is true inside; the save
directory moves to `%APPDATA%\<identity>`.

## macOS app

1. Copy `love.app` → `MyGame.app`.
2. Put `game.love` into `MyGame.app/Contents/Resources/`.
3. Edit `Contents/Info.plist`: `CFBundleIdentifier`, `CFBundleName`, remove
   the `UTExportedTypeDeclarations` block so it stops claiming `.love`.
4. Unsigned apps trigger Gatekeeper; the owner signs/notarizes or tells
   users to right-click → Open.

## Linux

AppImage from the LÖVE releases: extract (`./love.AppImage --appimage-extract`),
append the `.love` to `squashfs-root/bin/love`, repackage with
`appimagetool`. Or ship the `.love` and ask users to install `love` from
their distro.

## Web

`love.js` (Davidobot/love.js) compiles to WASM: `npx love.js game.love out
-c -t "My Game"`. Audio and threads have caveats; test in a browser before
promising it. Not an official target.

## Android / iOS

Official `love-android` / `love-ios` repos; needs a native toolchain. Out of
scope unless the owner asks; point at the wiki "Game Distribution" page.

## Automation

A small `Makefile` / `build.ps1` that zips, fuses, and copies DLLs is worth
adding when the owner exports more than once. `love --version` in the
script guards against the wrong engine. Tools like `boon` or
`makelove` (pip) automate all targets; ask before adding a dependency.
