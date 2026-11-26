# Local inference engine benchmarks

Measured on 2026-11-22 (America/Chicago) on the engine work box.
**CPU only.** GPU numbers (a 12 GB card) are pending — this
change was not run on his PC.

## Machine

| | |
| --- | --- |
| CPU | Intel Xeon (KVM guest), x86_64, 8 cores, 1 thread/core |
| RAM | 15 GiB, no swap |
| GPU | none |
| Runtime | llama.cpp **b12628** (build `8a97fa98a`) |
| CPU backend | `libggml-cpu-sapphirerapids.so` (AVX-512) |
| Weights | Public interim `unsloth/Qwen3.5-4B-GGUF` (language-model sha256 still `TODO` until the tuned GGUF exists) |

File sizes as downloaded and checked:

| File | Bytes | Notes |
| --- | ---: | --- |
| `Qwen3.5-4B-Q4_K_M.gguf` | 2,740,937,888 | autofit fallback |
| `Qwen3.5-4B-Q6_K.gguf` | 3,525,956,768 | default quant |
| `mmproj-F16.gguf` | 672,423,616 | official frozen projector, SHA-256 `cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864` |

The mmproj hash matched. These `llama-bench` runs are **text-only** and
did not load the projector.

## Method

```
llama-bench -ngl 0 -t 8 -r 3 -p 512,2048 -n 128 \
  -ctk q8_0 -ctv q8_0 -fa on -lm mmap
```

Flags match the product defaults: CPU offload, 8 threads, flash
attention on, KV cache `q8_0`, mmap, three repetitions. `ppN` is prompt
prefill tokens/sec for N tokens; `tg128` is generation tokens/sec for
128 tokens.

## Results

### Q4_K_M (fallback)

| test | t/s |
| --- | ---: |
| pp512 | 55.01 ± 7.87 |
| pp2048 | 54.22 ± 0.90 |
| tg128 | 0.80 ± 0.26 |

### Q6_K (default)

| test | t/s |
| --- | ---: |
| pp512 | 39.97 ± 3.03 |
| pp2048 | 42.29 ± 0.86 |
| tg128 | 0.72 ± 0.31 |

## How to read them

Prefill on this 8-core CPU is about **55 tok/s** at Q4 and **40–42 tok/s**
at Q6. Generation is **under 1 tok/s** here. That is expected on a
CPU-only VM with no GPU; it is not the target figure for an owner PC
with a 12 GB card. GPU numbers are still pending.

Vision / image-token timings with `mmproj-F16` were not collected on
this pass.

These measurements do not change any training-impacting item in
`docs/INFERENCE_ENGINE.md`.

"Hope this helps" - Ahmi
