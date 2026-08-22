# Vite setup

## Layout

```text
<project>/
  index.html            entry; <div id="app"></div> + <script type="module" src="/src/main.ts">
  src/main.ts           boots the game
  src/scenes/  src/logic/   engine code vs pure logic (testable)
  public/assets/        static files served at /assets/... (not hashed)
  vite.config.ts
  tsconfig.json
  package.json
```

## package.json scripts

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  }
}
```

`dev` serves on `http://localhost:5173` (next free port if taken; read the
console line). `preview` serves `dist/` on 4173 — use it to check the
production bundle before zipping.

## vite.config.ts

```ts
import { defineConfig } from 'vite';

export default defineConfig({
  base: './',                 // relative URLs: works on itch.io, GH Pages sub-paths
  server: { port: 5173, open: false },
  build: {
    target: 'es2022',
    assetsInlineLimit: 0,     // keep images as files (Phaser wants URLs)
    chunkSizeWarningLimit: 2000,
  },
});
```

Phaser is ~1 MB minified; the chunk-size warning is noise, not an error.

## Assets: public vs imported

- `public/assets/x.png` → referenced as `assets/x.png` (relative, with
  `base: './'`) or `/assets/x.png` (root hosts only). Never hashed; good
  for Phaser loaders that take URL strings.
- `import heroUrl from './hero.png'` → hashed, cache-busted; use the
  imported string as the URL. Vite needs `/// <reference types="vite/client" />`
  in a `.d.ts` for TS to accept image imports.

## TypeScript

`tsconfig.json` from the Vite template is fine. Add `"types": ["vite/client"]`.
Phaser ships its own types; Pixi v8 ships types. Run `npx tsc --noEmit` as
the cheap oracle before `vite build`.

## Env and modes

`import.meta.env.DEV` / `.PROD`, custom `VITE_*` vars from `.env`. Useful
for toggling physics debug.

## Install and troubleshooting

- `npm install` (or `pnpm i` / `yarn` — follow the lockfile present).
- Port in use → Vite picks the next one; read stdout.
- `Failed to resolve import` → check the path and that the package is in
  `dependencies`, not only `devDependencies` for runtime code.
- Node version: Vite 5 needs Node 18+; Vite 6/7 need 20+. `node -v` first.
- If the owner's flags look unfamiliar, `npx vite --help` / `npx vite build --help`.
