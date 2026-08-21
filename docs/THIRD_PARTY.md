# Third-party components

## SmolVLM2 (local vision / nano)

- **Project:** [HuggingFaceTB/SmolVLM2](https://huggingface.co/HuggingFaceTB/SmolVLM2-2.2B-Instruct)
- **GGUF:** [ggml-org/SmolVLM2-2.2B-Instruct-GGUF](https://huggingface.co/ggml-org/SmolVLM2-2.2B-Instruct-GGUF)
- **Use:** Local image briefs + nano-swarm assist via `llama-server`
- **License:** **Apache License 2.0**
- **Redistribution:** Downloaded at first use / prebundle into user vision dir
- **Pin:** `remedy.runtime.catalog` (`smolvlm2-2.2b`)

## Chatterbox (high-quality voice)

- **Project:** [Resemble AI Chatterbox](https://github.com/resemble-ai/chatterbox) — `chatterbox-tts` 0.1.7 from PyPI plus the pinned GitHub source (commit `5de7a54a`, laid over with `--no-deps`); Nano model `ResembleAI/chatterbox-nano`
- **Use:** Remedy's voice (Grove and calls); runs in the managed voice runtime, CPU or GPU
- **License:** **MIT** (code); model weights per the Hugging Face model card
- **Redistribution:** Downloaded at first use into `~/.remedy/voice/chatterbox/`, never bundled

## Reference voices (LibriVox)

- **Files:** `src/remedy/voice/references/female.wav` (Kara Shallenberg, *The Secret Garden*), `male.wav` (Stewart Wills, *Moby Dick*)
- **Use:** 12-second speech samples Remedy's voice clones, one per gender
- **License:** **Public domain** (LibriVox; archive.org items carry the public-domain mark)
- **Source:** https://archive.org/details/secret_garden_librivox · https://archive.org/details/moby_dick_librivox

## llama.cpp

- **Project:** [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp)
- **Use:** Local inference runtime (`llama-server`)
- **License:** MIT (see upstream)
- **Pin:** `LLAMA_CPP_TAG` in `remedy.runtime.catalog`

## ripgrep

- **Project:** [BurntSushi/ripgrep](https://github.com/BurntSushi/ripgrep)
- **Use:** Language-agnostic repository text search (`repo_search`)
- **License:** Dual-licensed **MIT** or **Unlicense** (see `third_party/ripgrep/`)
- **Redistribution:** Official release binaries may be bundled or downloaded at first use into `~/.remedy/bin`
- **Pin:** see `third_party/ripgrep/VERSION` and `remedy.core.rg_binary.RG_VERSION`

## Telephony components (fetched on request, never bundled)

Nothing below ships in the installer. Remedy names each one — purpose, size,
licence — and asks before downloading it (`remedy.telephony.consent.COMPONENTS`).
An owner who never uses the phone features never downloads any of them.

### baresip + libre (SIP engine)

- **Project:** [baresip/baresip](https://github.com/baresip/baresip)
- **Use:** SIP signalling for the `sip` line option (her own number)
- **License:** **BSD-3-Clause**
- **Redistribution:** not bundled; fetched to `~/.remedy/bin` on request
- **Note:** PJSIP was rejected for this role — GPLv2-or-later or a paid
  commercial licence, neither of which suits a source-available product.

### smart-turn (semantic endpointing)

- **Project:** [pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3)
- **Use:** deciding when a speaker has finished, so she does not talk over people
- **License:** **BSD-2-Clause**
- **Pin:** `smart-turn-v3.2-cpu.onnx` (~8.7 MB, int8) at revision
  `f766f81d3cfdf7737ac64aad813d91bbfd56bf93`
- **Redistribution:** not bundled; fetched to `~/.remedy/voice/models/smart-turn` on request

### Chatterbox (TTS + zero-shot voice cloning)

- **Project:** [resemble-ai/chatterbox](https://github.com/resemble-ai/chatterbox)
- **Use:** the human-bar voice tier (Grove HQ + phone pipeline)
- **License:** **MIT**
- **Redistribution:** not bundled; fetched when **Settings → Voice → High quality
  voice** is turned on (`~/.remedy/voice/chatterbox/`)

### Android system image (VM line option)

- **Project:** Android-x86 / BlissOS
- **Use:** running a VoIP calling app on the host, with no phone involved
- **License:** the publisher's terms (AOSP is Apache-2.0; vendor builds vary)
- **Redistribution:** never bundled; the owner is pointed at the publisher
