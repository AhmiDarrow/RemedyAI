---
name: web-games
description: >
  Build and verify browser games with Phaser 3 or PixiJS on Vite: scaffold,
  scene lifecycle, assets, input, arcade physics, logic tests with vitest,
  dev-server playtest through the browser, production build, static hosting
  and itch.io HTML5 zips. Use when the owner mentions Phaser, Pixi, an HTML5
  or browser game, or the project has a package.json with phaser/pixi.js.
version: 1.0.0
author: Remedy
tags: [game, web, phaser, pixi, vite, html5, browser]
requires: []
tools: [game_project_info, bash_exec, file_read, file_write, file_edit, repo_search, list_dir, computer_navigate, computer_screenshot, computer_key, computer_click, vision_decode, game_playtest]
triggers:
  - "\\b(phaser|pixi(js|\\.js)?|browser game|html5 game|web game|itch\\.io)\\b"
---

# Web games (Phaser 3 / PixiJS + Vite)

Everything here is plain Node tooling, so you can verify nearly all of it
headless. The only window is the browser — drive it with `computer_navigate`.

## Fingerprint

1. `game_project_info(path)`; `file_read package.json` → `phaser` or
   `pixi.js` in deps, `vite` in devDeps, `scripts` (`dev`, `build`, `test`).
2. `index.html` at the root (Vite entry), `src/main.(ts|js)`, `public/`
   for static assets served at `/`.
3. No `node_modules/` → `npm install` first (ask if offline is a concern).

## Scaffold (new project)

```
npm create vite@latest <name> -- --template vanilla-ts
cd <name> && npm install && npm install phaser        # or: npm install pixi.js
```

Phaser also offers `npm create @phaserjs/game@latest` (interactive — run
it only if the owner wants its templates; otherwise the Vite route above).
Then write `src/main.ts` per `references/phaser-scenes.md` or
`references/pixi-basics.md`, and `references/vite-setup.md` for config.

## Core loop (Phaser)

Scenes own the lifecycle: `preload()` queues assets, `create()` builds
objects, `update(time, delta)` runs every frame. Use `delta` (ms) for
movement. Arcade physics for most 2D; Matter for polygons/constraints.
Input via `this.input.keyboard.createCursorKeys()` or `addKeys`. Details and
a runnable skeleton: `references/phaser-scenes.md`.

## Core loop (Pixi)

Pixi is a renderer, not a game framework: `Application`, `Container`,
`Sprite`, `app.ticker.add((ticker) => ...)`. You write your own scene/state
management and physics. Skeleton: `references/pixi-basics.md`.

## Verify — cheap to expensive

1. `npx tsc --noEmit` (TS projects) — type errors without a build.
2. `npx vitest run` — pure logic (scoring, grid, AI, inventory). Keep that
   logic in modules that never import `phaser`/`pixi.js`; see
   `references/testing-logic.md` for the split and a jsdom note.
3. `npm run build` — the real gate: Vite bundles to `dist/`. A green build
   means the import graph and assets resolve.
4. Playtest: `npm run dev` is a **server** — run it with `bash_exec(...,
   background=true)`, read the printed URL (default `http://localhost:5173`),
   then `computer_navigate(url)` and `computer_screenshot`; press keys with
   `computer_key`; ask `vision_decode` what is on screen. Or hand the whole
   thing to `game_playtest(command="npm run dev", seconds=…, keys=…,
   question=…)` when it can reach the browser. Watch the browser console
   for red errors (open DevTools with F12 via `computer_key`).

Never say "it runs" on the strength of `npm run dev` starting; the bundle
can serve and still throw on load.

## Assets

Put files under `public/assets/...` and load with root-relative paths
(`/assets/player.png`) or import them from `src/` so Vite hashes them.
Phaser: `this.load.image('player', 'assets/player.png')` in `preload`, use
the key later. Pixi: `await Assets.load('/assets/player.png')`. Sprite
sheets: `this.load.spritesheet(key, url, { frameWidth, frameHeight })` or
`this.load.atlas` for TexturePacker JSON.

## Publish

`npm run build` → `dist/`. For a sub-path host (GitHub Pages, itch.io) set
`base: './'` in `vite.config`, or assets 404. itch.io wants a zip whose
**root** contains `index.html`. Steps and gotchas (SharedArrayBuffer,
viewport, fullscreen button): `references/publishing-html5.md`.

## Common faults

| Symptom | Look at |
|---------|---------|
| Black canvas, no errors | scene not added to `config.scene`, or `create` threw before first draw — check console |
| Assets 404 in build only | `base` not `./`; or paths starting with `/` on a sub-path host |
| Sprites blurry | `pixelArt: true` in Phaser config / `TextureSource` scale mode `nearest` in Pixi |
| Game runs too fast on 144 Hz | multiply by `delta`, not per-frame constants |
| `window is not defined` in vitest | the test imported Phaser; move logic out or use `environment: 'jsdom'` |

## Checklist

```text
[ ] package.json read; deps installed
[ ] logic in plain modules; vitest green
[ ] npm run build green
[ ] dev server backgrounded → browser screenshot/playtest, console clean
[ ] base './' + zip root index.html when publishing
```
