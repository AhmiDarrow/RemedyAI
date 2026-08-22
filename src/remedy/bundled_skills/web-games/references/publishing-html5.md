# Publishing HTML5 builds

## Build

```
npm run build          # → dist/
npm run preview        # serve dist/ locally on http://localhost:4173
```

Preview before shipping: open it with `computer_navigate`, check the
console, play a few seconds. `vite preview` is a server — background it.

## `base` matters

Static hosts that serve from a sub-path (itch.io, GitHub Pages project
sites, any `/game/` folder) break absolute `/assets/...` URLs. Set
`base: './'` in `vite.config.ts` so the built `index.html` references
`./assets/...`. For a root-domain host either value works.

## itch.io

1. `npm run build`.
2. Zip the **contents** of `dist/` so `index.html` sits at the zip root
   (not `dist/index.html` inside a `dist/` folder).
   - PowerShell: `Compress-Archive -Path dist\* -DestinationPath game.zip`
   - bash: `cd dist && zip -r ../game.zip .`
3. On the project page: Kind of project = HTML; upload the zip; tick "This
   file will be played in the browser"; set viewport size to the game's
   canvas size; enable fullscreen button if the game handles resize.
4. `SharedArrayBuffer support` option only if you use workers/WASM threads
   — it changes headers and can break third-party embeds.
5. Uploading from a shell: `butler push game.zip user/game:html5`
   (itch.io's CLI; needs login; `butler --help`).

Size limit is generous (hundreds of MB) but the first load is the whole
zip; keep textures compressed and audio as OGG/MP3.

## GitHub Pages

Build with `base: '/<repo>/'` or `'./'`, publish `dist/` to the `gh-pages`
branch (e.g. `npx gh-pages -d dist`) or use the Pages Actions workflow.
A `.nojekyll` file in `dist/` avoids Jekyll dropping `_`-prefixed files.

## Any static host (Netlify, Vercel, S3, nginx)

Upload `dist/`. No server-side code. Set `Cache-Control` long for hashed
assets and short for `index.html`. MIME: `.wasm` as `application/wasm` if
you ship WASM.

## Viewport and scaling

- Phaser: `scale: { mode: Phaser.Scale.FIT, autoCenter: CENTER_BOTH }`.
- Pixi: `resizeTo: window` in `app.init` plus your own letterboxing.
- `<meta name="viewport" content="width=device-width, initial-scale=1">`
  in `index.html` for mobile.
- Audio on the web needs a user gesture first — start sound after the first
  click/keypress or browsers block it.

## Checklist

```text
[ ] base './' (sub-path host)
[ ] npm run build green; preview plays; console clean
[ ] zip root contains index.html
[ ] viewport size on the store page matches the canvas
```
