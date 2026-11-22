# Local model bundle (Qwen3.5-4B mind · Apache 2.0)

**Not packaged in the installer** — downloaded on **first run** (or via Settings).
The local mind is required (vision, nano swarm, helper, dreams, workers, and
media digests). License is Apache 2.0 (commercial-friendly). There is no
option to skip installing it.

## Assets

- id: `qwen35-4b-mind`
- `Qwen3.5-4B-Q6_K.gguf` (default)
- `Qwen3.5-4B-Q4_K_M.gguf` (autofit fallback on weak machines)
- `mmproj-F16.gguf` (official frozen vision projector)
- Interim public base: [unsloth/Qwen3.5-4B-GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF)
  until the fine-tuned Remedy GGUF is hashed and published. Language-model
  sha256 is `TODO` until then.

## Layout (install / prebundle)

```
models/qwen35-4b-mind/
  Qwen3.5-4B-Q6_K.gguf
  Qwen3.5-4B-Q4_K_M.gguf
  mmproj-F16.gguf
```

Pin and sizes: `src/remedy/runtime/catalog.py`
