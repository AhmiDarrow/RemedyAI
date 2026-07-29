# Third-party components

## SmolVLM2 (local vision / nano)

- **Project:** [HuggingFaceTB/SmolVLM2](https://huggingface.co/HuggingFaceTB/SmolVLM2-2.2B-Instruct)
- **GGUF:** [ggml-org/SmolVLM2-2.2B-Instruct-GGUF](https://huggingface.co/ggml-org/SmolVLM2-2.2B-Instruct-GGUF)
- **Use:** Local image briefs + nano-swarm assist via `llama-server`
- **License:** **Apache License 2.0**
- **Redistribution:** Downloaded at first use / prebundle into user vision dir
- **Pin:** `remedy.runtime.catalog` (`smolvlm2-2.2b`)

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
