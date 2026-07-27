# third_party/ripgrep

Remedy may ship or download [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) for language-agnostic repository search.

## License

ripgrep is dual-licensed under the **MIT License** or the **Unlicense** (see upstream). Redistribution of official release binaries is permitted.

Upstream: https://github.com/BurntSushi/ripgrep

## Version pin

See `VERSION` in this directory and `remedy.core.rg_binary.RG_VERSION`.

## Layout

- `LICENSE-MIT` / `UNLICENSE` — upstream license texts  
- `VERSION` — pinned release  
- `bin/` — optional pre-staged `rg` / `rg.exe` for packaging (gitignored binary preferred; CI stages at build time)

Runtime install path: `~/.remedy/bin/rg` (or `rg.exe` on Windows) via `ensure_rg()`.
