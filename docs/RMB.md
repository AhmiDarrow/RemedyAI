# RMB and local muscle

RMB (Remedy Muscle Bridge) is the optional **local muscle**: a larger
GGUF on a managed llama-server (`127.0.0.1:8787`) for coding and long
tool loops. It is not her mind. The mind is the always-on Qwen3.5-4B
host on `127.0.0.1:8740` (see `docs/INFERENCE_ENGINE.md`).

A user-run local server — Ollama, LM Studio, vLLM, or another
llama-server — is also muscle, as long as it speaks OpenAI-compatible
HTTP. Remedy will discover it, measure what she can, and adapt. She
does not start, stop, or tune someone else's server.

## How they share one GPU

A VRAM planner (`remedy.runtime.rmb.coresidence`) probes VRAM, RAM, and
CPU, subtracts any VRAM already used by a discovered local server, and
leaves a small headroom (`muscle_vram_headroom_mib`, default 768).

**Auto policy (default):**

- If both the 4B mind and the muscle fit with their KV caches, they
  **share** the GPU and overlap work. The mind stays responsive.
- If a MoE muscle (for example Qwen3.6-35B-A3B with experts on CPU)
  plus the mind fit, they share. Experts stay in RAM (`--n-cpu-moe`).
- If a dense muscle (the default Qwen3.5-9B Q6_K) cannot sit fully
  beside the mind, the mind **keeps** its GPU layers and the muscle
  takes the leftover layers. On a 12 GB card this is the usual plan.
- Only when even a useful partial offload will not fit (about 8 GB and
  below for a dense 9B) does the mind **yield** to CPU with mmap so the
  muscle can have the card. Wake stays a process start against a warm
  page cache, not a cold read.

`mind_keep_gpu` (or `muscle_gpu_policy=keep_mind`) forces the mind to
keep the GPU; the muscle offloads or runs on CPU. `muscle_gpu_policy=yield_mind`
restores the older "muscle owns the card" behavior. `share` never yields.

The planner also sets whether work **overlaps** or is **serialized**
(`muscle_schedule`). A tight split serializes so the two hosts do not
thrash. Otherwise they overlap and the mind keeps answering inner
jobs while the muscle generates.

## Double-loading

If Ollama (or another discovered server) is already serving the same
GGUF, RMB will **not** start a second copy. Status reports the reused
base URL. The mind still adapts its GPU footprint to the reserved VRAM.

## Routing

`muscle_route=auto` (default):

- Easy or inner work (briefs, continuity, short questions) stays on
  the 4B mind. That path is free and already loaded.
- Coding and heavier tool loops go to RMB if it is up, otherwise to a
  discovered local server.
- The hardest work escalates to a configured frontier provider. If
  there is no frontier, local muscle keeps it.

The owner can force `mind`, `rmb`, `local`, or `frontier`. Auto never
steals a session (or the configured chat provider) that is already a
cloud model; set a forced knob if you want the 4B mind or RMB to take
those turns instead.

The muscle still gets **digests and briefs**, not raw pixels, unless
the mind sets `needs_pixels` or `always_send_images` is on.

Production chat turns go through the Go runtime (`applyMuscleRoute`).
Easy or inner Python jobs stay on the mind via `local_text_complete`.
When the split is tight (`muscle_schedule=serialize` or auto on a full
card), a file lock under the RMB home is shared by the Go runtime and
the Python host so they do not thrash.

## Engine

RMB uses the same pinned llama.cpp **b12628** as the mind (Windows
CUDA 12.4 / Linux CPU / Vulkan). Nightlies after that pin on 22 Nov
2026 were backend-only (Adreno OpenCL, WebGPU, HIP CDNA3). We did not
bump the pin: it would not help this catalog and would disturb the
parallel mind GGUF work.

Autofit still sizes context, batch, and KV from the GGUF. When the
mind is sharing the GPU, autofit plans against the **leftover** VRAM,
not the whole card. Flash attention, `q8_0` KV, MTP / n-gram
speculative decoding, MoE expert offload, and prefix `--cache-reuse`
for ReAct tool loops stay on when the binary and GGUF allow them.

Default catalog muscle is **Qwen3.5-9B Q6_K** (best measured score on
the agent suite at 6-bit). The 35B-A3B MoE is the VRAM-scarce option.
Qwen2.5 Coder 7B / 14B remain available.

## Advanced knobs

Settings → Advanced, group **Local muscle**. Defaults are safe.

| Knob | Default | Meaning |
|------|---------|---------|
| `muscle_gpu_policy` | `auto` | `auto`, `share`, `yield_mind`, or `keep_mind`. |
| `muscle_schedule` | `auto` | `auto`, `overlap`, or `serialize`. |
| `muscle_route` | `auto` | `auto`, `mind`, `rmb`, `local`, or `frontier`. |
| `muscle_discover` | `true` | Probe well-known local ports. Measure only. |
| `muscle_vram_headroom_mib` | `768` | VRAM left free after reservations. |
| `muscle_kv_cache` | `auto` | `auto` (`q8_0`), `q8_0`, `f16`, `bf16`, `f8_e4m3`. |
| `muscle_flash_attn` | `true` | Flash attention on the RMB host. |
| `muscle_spec` | `auto` | MTP if the GGUF has a head, else ngram-mod, or `off`. |

`mind_keep_gpu` in the Local mind group still works: it is a hard
"keep the mind on the GPU" override.

Detailed sampling and rope knobs stay in RMB settings / `rmb.json`.

## Setup

See the owner manual: `docs/manual/20-rmb-local-agent.md`. Start RMB
explicitly; it does not auto-start with serve.

"Hope this helps" - Ahmi
