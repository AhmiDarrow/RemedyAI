# PixiJS basics (v8)

Pixi renders; it does not give you scenes, physics, or input helpers beyond
pointer events. Check `package.json` — v7 and v8 differ (`new Application()`
+ `await app.init(...)` is v8; v7 takes options in the constructor).

## Boot (`src/main.ts`, v8)

```ts
import { Application, Assets, Container, Sprite, Text } from 'pixi.js';

const app = new Application();
await app.init({ width: 800, height: 600, background: '#101020', antialias: false });
document.getElementById('app')!.appendChild(app.canvas);

const world = new Container();
app.stage.addChild(world);

const tex = await Assets.load('/assets/hero.png');
tex.source.scaleMode = 'nearest';          // pixel art
const hero = new Sprite(tex);
hero.anchor.set(0.5);
hero.position.set(400, 300);
world.addChild(hero);

const keys = new Set<string>();
addEventListener('keydown', (e) => keys.add(e.code));
addEventListener('keyup', (e) => keys.delete(e.code));

app.ticker.add((ticker) => {
  const dt = ticker.deltaMS / 1000;
  const speed = 200;
  if (keys.has('ArrowLeft')) hero.x -= speed * dt;
  if (keys.has('ArrowRight')) hero.x += speed * dt;
});
```

Top-level `await` needs `"target": "ES2022"` or later in `tsconfig`; Vite
handles it.

## Concepts

- `Container` = scene graph node; `Sprite`, `Graphics`, `Text`,
  `AnimatedSprite`, `TilingSprite` are children. Transform is inherited.
- `app.ticker` runs every animation frame; `ticker.deltaTime` is frames
  (1 = 60 fps), `ticker.deltaMS` is ms. Use `deltaMS` for real-time motion.
- `Assets.load(url | [urls])`; `Assets.addBundle` + `loadBundle` for
  grouped loading; returns `Texture`s. Spritesheets: load the JSON and use
  `sheet.textures['frame.png']` or `sheet.animations['run']`.
- Pointer: `sprite.eventMode = 'static'; sprite.on('pointerdown', …)`.
  `app.stage.eventMode = 'static'; app.stage.hitArea = app.screen` for
  stage-wide clicks.
- Resize: `app.renderer.resize(w, h)` or `resizeTo: window` in `init`.

## Minimal state/scene pattern

```ts
interface Scene { root: Container; update(dt: number): void; destroy(): void }
let current: Scene | null = null;
function switchTo(next: Scene) {
  if (current) { app.stage.removeChild(current.root); current.destroy(); }
  current = next; app.stage.addChild(next.root);
}
app.ticker.add((t) => current?.update(t.deltaMS / 1000));
```

## Physics

None built in. Options: hand-rolled AABB for simple games (testable with
vitest), or `matter-js` / `planck` with a sync step that copies body
positions into sprites each tick.

## Gotchas

- Forgetting `await app.init()` in v8 → `app.canvas` undefined.
- Removing a sprite from the stage does not free its texture; call
  `destroy({ texture: false })` for objects, `Assets.unload` for textures.
- `Graphics` API changed in v8: `g.rect(x,y,w,h).fill(0xff0000)` (chain
  shape then `fill`), not `beginFill`.
- Text is rasterized; do not re-create it every frame — set `.text`.
