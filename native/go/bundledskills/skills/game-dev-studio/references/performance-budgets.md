# Performance budgets

Budget the weakest target, not the dev machine. Measure before optimising;
never optimise placeholder content.

## Per-platform targets (2D unless noted)

| Target | FPS | Frame budget | Headroom | Memory | Notes |
|--------|-----|--------------|----------|--------|-------|
| Windows/Linux/macOS desktop | 60 | 16.6 ms | ≥ 4 ms | < 1 GB | Vsync on; test on an iGPU laptop |
| Web (itch.io, browser) | 60, accept 30 | 16.6 / 33 ms | ≥ 6 ms | < 512 MB | No threads by default; GL Compatibility renderer; first load < 20 MB |
| Android mid-range | 60 | 16.6 ms | ≥ 5 ms | < 400 MB | Thermal throttling after ~10 min; test a 15-minute session |
| iOS | 60 | 16.6 ms | ≥ 5 ms | < 400 MB | Metal; same as Android for budgeting |
| Steam Deck | 60 (40 acceptable) | 16.6 / 25 ms | ≥ 4 ms | < 2 GB | 1280×800; gamepad only |
| 3D desktop | 60 | 16.6 ms | ≥ 3 ms | < 2 GB | Forward+; cap shadow-casting lights |

Fixed physics at 60 Hz (`physics/common/physics_ticks_per_second=60`);
never raise it to fix jitter, use physics interpolation or fix the update.

## Soft limits (2D)
- Draw calls: < 200/frame web/mobile, < 1000 desktop. Shared atlases
  batch; different textures/materials break batches.
- Nodes in the active scene < 2000; `_process` callbacks < 300. More
  bullets/particles → `MultiMeshInstance2D`, `GPUParticles2D`.
- `PointLight2D` < 8 on mobile/web, < 32 desktop; shadows on a few.
- Atlas pages ≤ 2048² on web/mobile.
- Audio: < 16 simultaneous players; pool them (audio.md).
- Scene load under 1 s, else a loading screen with `load_threaded_request`.
- Web package under 20 MB initial; OGG audio, indexed PNG art.

## Measuring in Godot
```gdscript
# diag: print once per second, tools/diag_perf.gd or a debug overlay
var fps := Engine.get_frames_per_second()
var proc := Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0
var phys := Performance.get_monitor(Performance.TIME_PHYSICS_PROCESS) * 1000.0
var draws := Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME)
var objs := Performance.get_monitor(Performance.OBJECT_NODE_COUNT)
var mem := Performance.get_monitor(Performance.MEMORY_STATIC) / 1048576.0
print("fps %d proc %.2fms phys %.2fms draws %d nodes %d mem %.0fMB"
    % [fps, proc, phys, draws, objs, mem])
```
Editor Debugger → Monitors graphs the same counters; Profiler attributes
time to scripts. Headless numbers only cover script/physics time.

## Fixing, in order of payoff
1. Remove work: disable off-screen nodes (`VisibleOnScreenNotifier2D`),
   no per-frame `get_node`/`find_child`, no allocation in hot loops.
2. Batch: one atlas per layer, same material, few texture switches.
3. Pool: bullets, particles, sound players — reuse, do not instantiate.
4. Physics: simple shapes, fewer layers checked, `Area2D` monitoring off
   when idle.
5. Only then: shaders, threading, custom servers.

Report numbers, not adjectives: "draw calls 1400 → 180, frame 22 → 9 ms".
