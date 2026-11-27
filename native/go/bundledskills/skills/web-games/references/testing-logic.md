# Testing game logic

Engines need a canvas and a window; unit tests should not. Split the code
so the interesting parts run in Node.

## The split

```text
src/logic/     pure TS: grid, scoring, inventory, enemy AI, level parsing
src/scenes/    Phaser/Pixi code that calls into logic and draws
```

Rule: nothing under `src/logic/` imports `phaser` or `pixi.js`. Pass in
plain numbers/objects; return plain values. Scenes translate.

Example:

```ts
// src/logic/match.ts
export type Grid = number[][];
export function findMatches(g: Grid): [number, number][] { /* … */ }

// src/scenes/PlayScene.ts
const hits = findMatches(this.gridModel);
hits.forEach(([r, c]) => this.tiles[r][c].setTint(0xff0000));
```

## vitest

```
npm install -D vitest
```

`vite.config.ts` (vitest reads it):

```ts
import { defineConfig } from 'vitest/config';
export default defineConfig({
  base: './',
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
});
```

`src/logic/match.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { findMatches } from './match';

describe('findMatches', () => {
  it('finds a horizontal three', () => {
    expect(findMatches([[1, 1, 1], [0, 2, 0]])).toEqual([[0, 0], [0, 1], [0, 2]]);
  });
});
```

Run: `npx vitest run` (CI) or `npx vitest` (watch). `--reporter=verbose`
for full names; `-t "<pattern>"` to filter.

## When a test touches the DOM

`npm install -D jsdom` and set `environment: 'jsdom'` (or per-file
`// @vitest-environment jsdom`). Phaser still will not boot under jsdom —
WebGL/canvas are missing. Do not try; keep engine code out of tests.

## Deterministic randomness

Inject the RNG: `spawnWave(rng: () => number)`; tests pass a seeded
function (e.g. mulberry32) so assertions are stable.

## Fixed-step simulation tests

Logic that depends on time should take `dt`: `step(state, dt)`. Tests call
it N times with `1/60` and assert positions — no ticker needed.

## What not to test

Rendering, tweens, camera, audio. Cover those with the browser playtest
(`computer_navigate` + screenshot + `vision_decode`) and say that is what
you did.
