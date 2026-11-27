# Phaser 3 scenes

## Config and boot (`src/main.ts`)

```ts
import Phaser from 'phaser';
import { PlayScene } from './scenes/PlayScene';

new Phaser.Game({
  type: Phaser.AUTO,
  width: 800,
  height: 600,
  parent: 'app',                 // id of the div in index.html
  pixelArt: true,
  physics: { default: 'arcade', arcade: { gravity: { x: 0, y: 600 }, debug: false } },
  scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
  scene: [PlayScene],
});
```

## Scene skeleton

```ts
export class PlayScene extends Phaser.Scene {
  private player!: Phaser.Physics.Arcade.Sprite;
  private cursors!: Phaser.Types.Input.Keyboard.CursorKeys;

  constructor() { super('play'); }

  preload() {
    this.load.image('ground', 'assets/ground.png');
    this.load.spritesheet('hero', 'assets/hero.png', { frameWidth: 32, frameHeight: 32 });
  }

  create() {
    const ground = this.physics.add.staticGroup();
    ground.create(400, 580, 'ground').setScale(2).refreshBody();

    this.player = this.physics.add.sprite(100, 450, 'hero');
    this.player.setCollideWorldBounds(true);
    this.physics.add.collider(this.player, ground);

    this.anims.create({ key: 'run', frames: this.anims.generateFrameNumbers('hero', { start: 0, end: 3 }), frameRate: 10, repeat: -1 });
    this.cursors = this.input.keyboard!.createCursorKeys();
  }

  update(_time: number, delta: number) {
    const speed = 200;
    if (this.cursors.left.isDown) this.player.setVelocityX(-speed);
    else if (this.cursors.right.isDown) this.player.setVelocityX(speed);
    else this.player.setVelocityX(0);
    if (this.cursors.up.isDown && this.player.body!.blocked.down) this.player.setVelocityY(-350);
  }
}
```

Lifecycle order: `init(data)` → `preload()` → `create(data)` →
`update(time, delta)` per frame. `delta` is ms since last frame.

## Input

- `this.input.keyboard.createCursorKeys()`; `addKeys('W,A,S,D')`;
  `Phaser.Input.Keyboard.JustDown(key)` for single presses.
- Pointer: `this.input.on('pointerdown', (p) => …)`;
  `sprite.setInteractive().on('pointerdown', …)`.

## Arcade physics

- `this.physics.add.sprite` / `.group` / `.staticGroup`.
- `collider(a, b, cb)` separates; `overlap(a, b, cb)` only reports.
- `body.velocity`, `setBounce`, `setImmovable`, `body.blocked.down`.
- Debug: `arcade.debug: true` draws bodies.

## Scenes and data

- `this.scene.start('gameover', { score })` — receives `data` in `init`/`create`.
- `this.scene.launch('hud')` runs in parallel; `this.scene.pause/resume`.
- `this.registry.set('score', n)` for global state; `this.events.emit/on`.

## Text, camera

`this.add.text(x, y, 'Score: 0', { fontSize: '24px' }).setScrollFactor(0)`.
`this.cameras.main.startFollow(this.player)`, `.setBounds(0, 0, w, h)`.

## Gotchas

- Never create game objects in `preload`; textures are not ready.
- A scene class must be in `config.scene` or started by key.
- `setCollideWorldBounds` does nothing without a physics body.
