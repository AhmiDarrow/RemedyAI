# Local inference engine

Remedy's inner mind is one local llama-server process serving a single
Qwen3.5-4B multimodal GGUF (text + vision via `--mmproj`). That host
handles vision, nano, helper, dreams, metabolism, workers, and the
media digest. Frontier models are muscle: they get a compact digest,
not pixels, unless the mind sets `needs_pixels` or the owner turns on
`always_send_images`.

RMB remains optional extra muscle for larger GGUFs (the 9B coding
host on :8787). It no longer evicts the mind. When RMB needs the GPU,
the mind yields its GPU layers and keeps running on CPU with mmap so
wake stays fast.

The language-model sha256 in the catalog is `TODO` until the
fine-tuned GGUF is published. Until then the public unsloth
Qwen3.5-4B GGUF is the install source; the official `mmproj-F16.gguf`
is already pinned by hash and is frozen.

See also `docs/INNER_LANGUAGE.md` and `docs/TOKEN_OPTIMIZATION.md`.
Training-impacting choices for the one fine-tune are listed at the
end of this document and in `/workspace/remedy-mind-ENGINE-NOTES.md`.

## What we researched (2025–Nov 2026)

| Topic | Source | Takeaway |
| --- | --- | --- |
| Dense Qwen3.5 MTP | https://github.com/ggml-org/llama.cpp/pull/20700 | Built-in next-token head; FastMTP trims the draft vocab. Hybrid DeltaNet makes rejected drafts expensive; keep MTP, fall back cleanly. |
| MTP + vision / slots | https://github.com/ggml-org/llama.cpp/pull/22673 | Later MTP path is documented as compatible with vision and parallel slots, not fully optimized. Early (May 2026) Unsloth notes that `--mmproj` / `-np > 1` could not combine with MTP are treated as outdated; we still soft-retry without MTP. |
| Qwen3.5-4B MTP GGUF | https://huggingface.co/unsloth/Qwen3.5-4B-MTP-GGUF | `--spec-type draft-mtp` (renamed from `--spec-type mtp` on 2026-05-13). |
| Official mmproj-F16 | https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/blob/main/mmproj-F16.gguf | 672,423,616 bytes, SHA-256 `cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864`. |
| llama.cpp v1.0.0 | https://github.com/ggml-org/llama.cpp/releases/tag/v1.0.0 | FP8 KV (`-ctk/-ctv f8_e4m3`), reference-counted prompt checkpoints, parent-slot prefix visit, JSON-schema fixes, shared vision compute buffers. Nightly at that release: b12359. |
| llama.cpp b12628 | https://github.com/ggml-org/llama.cpp/releases/tag/b12628 | Pin used here (2026-11-22 post-v1.0.0 nightly). Same asset names as our existing Windows CUDA 12.4 / Linux CPU / Vulkan catalog. |
| llama-server flags | https://github.com/ggml-org/llama.cpp/blob/HEAD/tools/server/README.md | `--parallel`, `--cache-prompt`, `--cache-ram`, `--cache-idle-slots`, `--kv-unified-per-slot`, `--image-max-tokens`, grammar / `json_schema`, `--mmap`. |
| Prefix + slots | https://github.com/ggml-org/llama.cpp/discussions/15530 | Exact prefix reuse per slot; raise `-c` as `-np` grows. Shared-prefix-across-slots is still a draft (https://github.com/ggml-org/llama.cpp/pull/32358). |
| Speculative types | https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md | `draft-mtp`, `ngram-mod` (~16 MB, no extra model), EAGLE-style draft models. We do not distill a draft model in this run. |
| Flash attn + quantized KV | https://github.com/ggml-org/llama.cpp/releases/tag/b10532 | Metal dequant-to-F16 path for Q8_0 KV; CUDA/Vulkan already had usable FA + Q8 KV. |
| Qwen3.5-4B card | https://huggingface.co/unsloth/Qwen3.5-4B-GGUF and https://huggingface.co/vultr/Qwen3.5-4B-GGUF | 32 layers, 262k native context (we do not run that on consumer boxes), Apache-2.0. |

## What we adopted, and why

| Technique | Default | Why |
| --- | --- | --- |
| One host, not two | required | SmolVLM2 + Qwen3.5-9B could not share the GPU. The 4B already sees; one process is her whole mind. |
| Q6_K + Q4_K_M autofit | auto | Q6_K + mmproj is ~4.2 GB, under today's two-model budget (~9.2 GB). Q4_K_M is the weak-machine fallback. |
| llama.cpp b12628 | pinned | Materially newer than b10107: MTP+vision, image token caps, ngram-mod, cache-idle-slots, FP8 KV, schema fixes. |
| Parallel slots | 2 (1–4 autofit) | Concurrent inner tasks (dream + digest + worker) without a second server. |
| Prefix / prompt cache | on | The system prefix above `\n\n[Turn context]\n` is byte-stable (`TURN_CONTEXT_MARKER`). `cache_prompt` is set on local calls. |
| KV quant | `q8_0` | Cuts KV RAM so 2×8k slots fit a 12 GB GPU with the 4B + mmproj. `f16` and `f8_e4m3` are Advanced options. |
| Flash attention | on | Faster prefill / lower memory; flagged off automatically if the binary rejects it. |
| Speculative decode | auto | `draft-mtp` when the GGUF still has the MTP head; otherwise `ngram-mod` (no training). Soft-retry drops MTP, then all spec flags, if spawn exits. |
| JSON schema | on for digests | Locks the media-digest object the muscle sees. Tool-call arguments stay on the official Qwen3.5 jinja / OpenAI tool path. |
| Image token cap | 1024 | Matches the training budget (longest side ≤ 768). |
| Thinking | off | Short inner jobs should not emit `<think>` blocks. |
| mmap + idle-stop | mmap on, 600s idle | The host can sleep to save RAM/GPU; wake is a process start against a warm page cache, not a full cold read. |
| CPU yield when RMB runs | on | Both stay loaded. `mind_keep_gpu` keeps the mind on the GPU if the owner wants that. |
| Media digest default | on | Saves frontier tokens. `always_send_images` is the escape hatch. |

Not adopted (yet): a distilled EAGLE / draft model (would have to be trained alongside the mind); L=2/L=3 inner language; running the official 262k context; hosting a second llama-server for MDL layer tiers (8741/8742). MDL still routes task kinds, but every tier is the same 8740 host.

## Advanced knobs

Settings → Advanced, group **Local mind**. Defaults are safe.

`mind_slots`, `mind_ctx`, `mind_flash_attn`, `mind_kv_cache`,
`mind_cache_prompt`, `mind_cache_ram_mib`, `mind_mmap`, `mind_spec`,
`mind_spec_draft_n`, `mind_image_max_tokens`, `mind_thinking`,
`mind_json_schema`, `mind_idle_stop_s`, `mind_quant`, `mind_keep_gpu`,
`always_send_images`.

## Install and updates

The installer stays tiny. The mind downloads on first run with the
in-app progress bar. There is no option to skip it. Updates are
silent, hash-checked swaps that never touch memory or soul. Old
SmolVLM2 files, if present, are left on disk (zero data loss); the
catalog migrates `smolvlm2-2.2b` and retired Qwen-VL pins to
`qwen35-4b-mind`.

## Benchmarks

CPU-only numbers from this engine work box are in
`docs/INFERENCE_ENGINE_BENCHMARKS.md` (Q4 ~55 tok/s prefill, 0.80 tok/s
generation; Q6 ~40–42 tok/s prefill, 0.72 tok/s generation, 8-core
Xeon, no GPU). GPU numbers (Joe's 3080, 12 GB) are pending — this
change was not run on his PC.

## Training impact (fold into the one fine-tune)

These choices are locked so we do not have to train twice:

1. Official Qwen3.5 chat template only (`--jinja`). No new special tokens.
2. Thinking off by default (no `<think>` on inner jobs).
3. Preserve the MTP head through fine-tune and GGUF export. Do not distill a separate draft model.
4. Inner language must match `src/remedy/core/inner_language/codec.py` (`tag=value`, length-prefixed text, default header omits `L=1 v=dense`). No L=2/L=3.
5. Media digest JSON schema as in `src/remedy/runtime/media_digest.py`.
6. Image-token budget 1024; freeze official mmproj-F16.
7. Context 4096–8192 typical; do not require 32k+ for mind jobs.
8. Cache marker `\n\n[Turn context]\n` — stable identity before it, per-turn material after.
9. Ship Q6_K + Q4_K_M + the same mmproj. Language-model sha256 stays TODO until those files exist.

"Hope this helps" - Ahmi
