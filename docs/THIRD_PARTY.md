# Third-party components

Remedy ships as a Go runtime (`remedy-runtime`), a Zig core, a Rust desktop
app built on RemedyUI, a Rust phone app, and a small Rust voice helper
(`remedy-voice`). Go modules and Rust crates are compiled into those
binaries. The older notices file `desktop/public/THIRD_PARTY_NOTICES.txt` is
made by `scripts/gen_third_party_notices.py`; it may be out of date for the
current apps, so check it against the lock files before you rely on it.

## Fonts (bundled with the desktop app)

All under the SIL Open Font License 1.1. The desktop app carries them in
RemedyUI (the `remedy-ui-fonts` crate).

| Font | Source | Role (default) |
|------|--------|----------------|
| Remedy Sans UI | [AhmiDarrow/RemedySansUI](https://github.com/AhmiDarrow/RemedySansUI) | Main text |
| Remedy Sans | [AhmiDarrow/RemedySans](https://github.com/AhmiDarrow/RemedySans) | Headings |
| AhmiPrint | [AhmiDarrow/AhmiPrint](https://github.com/AhmiDarrow/AhmiPrint) | Long-form reading (Help) |
| Ahmi Pixel | [AhmiDarrow/AhmiPixelFont](https://github.com/AhmiDarrow/AhmiPixelFont) | Accents |
| Inter | `@fontsource/inter` | Optional text font |
| Cascadia Code | `@fontsource/cascadia-code` | Code |
| JetBrains Mono | `@fontsource/jetbrains-mono` | Optional code font |
| Fira Code | `@fontsource/fira-code` | Optional code font |

Every role is a setting (Settings → Appearance → Fonts).

The components below are the ones that are **downloaded at first use or on
request** rather than bundled, so they are documented rather than attributed.

## SmolVLM2 (local vision / nano)

- **Project:** [HuggingFaceTB/SmolVLM2](https://huggingface.co/HuggingFaceTB/SmolVLM2-2.2B-Instruct)
- **GGUF:** [ggml-org/SmolVLM2-2.2B-Instruct-GGUF](https://huggingface.co/ggml-org/SmolVLM2-2.2B-Instruct-GGUF)
- **Use:** Local image briefs + nano-swarm assist via `llama-server`
- **License:** **Apache License 2.0**
- **Redistribution:** Downloaded at first use / prebundle into user vision dir
- **Pin:** `native/go/httpapi/vision_catalog.go` (`smolvlm2-2.2b`)

## Speech models (sherpa-onnx)

- **Project:** [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), which runs the models inside the native `remedy-voice` helper (`native/voice`)
- **Models:** Kokoro (speaking), Pocket TTS (the high-quality voice) and Whisper (listening), each as a sherpa-onnx pack
- **Use:** Remedy's voice (Grove and calls), on CPU or GPU
- **License:** see each model's own page; the sherpa-onnx code is Apache-2.0
- **Redistribution:** Downloaded on first use into `~/.remedy/voice/`, never bundled. Sizes and checksums are pinned in `native/go/httpapi/voice_packs.go`. The name "chatterbox" survives only as an old alias for the high-quality voice.

## Reference voices (LibriVox)

- **Files:** `assets/voice-references/female.wav` (Kara Shallenberg, *The Secret Garden*), `male.wav` (Stewart Wills, *Moby Dick*). Kept in this repo and downloaded from it (`native/go/httpapi/voice_packs.go`).
- **Use:** 12-second speech samples Remedy's voice clones, one per gender
- **License:** **Public domain** (LibriVox; archive.org items carry the public-domain mark)
- **Source:** https://archive.org/details/secret_garden_librivox · https://archive.org/details/moby_dick_librivox
- **Redistribution:** Downloaded at first use of her own voice into `~/.remedy/voice/references/` after a sha256 pin check. No silent Kokoro fallback when that download can be attempted.

## llama.cpp

- **Project:** [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp)
- **Use:** Local inference runtime (`llama-server`)
- **License:** MIT (see upstream)
- **Pin:** `visionLlamaCPPTag` in `native/go/httpapi/vision_catalog.go`

## OpenSERP (local web search)

- **Project:** [karust/openserp](https://github.com/karust/openserp)
- **Use:** First-run local search API for `web_search` (`127.0.0.1:17410`)
- **License:** **MIT**
- **Redistribution:** Downloaded at first use into `~/.remedy/bin`, never bundled
- **Pin:** `openserpVersion` in `native/go/httpapi/search_host.go` (`0.8.12`)
- **Note:** Loopback only. If the binary is not ready yet, `web_search` uses
  DuckDuckGo's HTML results page instead.

## Claimidx (local prior-art index)

- **Project:** [claimidx/claimidx](https://github.com/claimidx/claimidx)
- **Use:** Private local prior-art service started with Remedy
  (`127.0.0.1:17340`)
- **License:** **Apache License 2.0**
- **Redistribution:** Downloaded from PyPI into `~/.remedy/claimidx`, never bundled
- **Pin:** `claimidxVersion` and `claimidxWheelSHA` in
  `native/go/httpapi/claimidx_host.go`. This is the only Python Remedy
  installs, and it stays inside `~/.remedy/claimidx/python`.
- **Note:** The managed profile does not inherit global Claimidx credentials;
  writes require its private token and public sharing is disabled by default.

## Tokenizer (compiled into the runtime)

Token counts use the `o200k_base` byte-pair encoding from
[tiktoken-go/tokenizer](https://github.com/tiktoken-go/tokenizer) (MIT), which
builds on [dlclark/regexp2](https://github.com/dlclark/regexp2) (MIT). Both are
Go modules compiled into `remedy-runtime`; the vocabulary is part of the
binary, so nothing is downloaded and no request is made to count tokens.

## Telephony components (fetched on request, never bundled)

Nothing below ships in the installer. Remedy names each one — purpose, size,
licence — and asks before downloading it.
An owner who never uses the phone features never downloads any of them.

### baresip + libre (SIP engine)

- **Project:** [baresip/baresip](https://github.com/baresip/baresip)
- **Use:** SIP signalling for the `sip` line option (her own number)
- **License:** **BSD-3-Clause**
- **Redistribution:** not bundled; fetched to `~/.remedy/bin` on request
- **Note:** PJSIP was rejected for this role — GPLv2-or-later or a paid
  commercial licence, neither of which suits an Apache-2.0 product.

### smart-turn (semantic endpointing)

- **Project:** [pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3)
- **Use:** deciding when a speaker has finished, so she does not talk over people
- **License:** **BSD-2-Clause**
- **Pin:** `smart-turn-v3.2-cpu.onnx` (~8.7 MB, int8) at revision
  `f766f81d3cfdf7737ac64aad813d91bbfd56bf93`
- **Redistribution:** not bundled; fetched to `~/.remedy/voice/models/smart-turn` on request

### Android system image (VM line option)

- **Project:** Android-x86 / BlissOS
- **Use:** running a VoIP calling app on the host, with no phone involved
- **License:** the publisher's terms (AOSP is Apache-2.0; vendor builds vary)
- **Redistribution:** never bundled; the owner is pointed at the publisher
