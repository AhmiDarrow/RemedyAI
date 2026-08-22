# Iteration speed

A cold Bevy build is minutes; an unconfigured incremental build can still be
30–90 s. Set this up before the first feature.

## Cargo.toml profiles

```toml
[profile.dev]
opt-level = 1            # your code: quick compile, acceptable speed

[profile.dev.package."*"]
opt-level = 3            # dependencies compiled once, fast at runtime

[profile.release]
codegen-units = 1
lto = "thin"
```

## Dynamic linking (dev only)

```
cargo run --features bevy/dynamic_linking
```

Links Bevy as a shared library so your crate relinks in seconds. Do **not**
put `dynamic_linking` in the default features of a shipped crate; the
release binary would need the `.dll/.so` next to it. An alias keeps it out
of the way — `.cargo/config.toml`:

```toml
[alias]
dev = "run --features bevy/dynamic_linking"
```

## Faster linker

`.cargo/config.toml` (pick what is installed; `lld` ships with recent Rust
toolchains on Windows via `rust-lld`):

```toml
[target.x86_64-pc-windows-msvc]
linker = "rust-lld.exe"
rustflags = ["-Zshare-generics=off"]   # nightly-only flag; drop on stable

[target.x86_64-unknown-linux-gnu]
linker = "clang"
rustflags = ["-C", "link-arg=-fuse-ld=lld"]
```

Check the Bevy book "Setup" page for the current recommendation for the
version in use; this changes between releases. Nightly-only flags fail on
stable — do not add them without checking `rustc --version`.

## The oracle ladder

1. `cargo check` — seconds; type and borrow errors. Run after every edit.
2. `cargo clippy -- -D warnings` if the project already uses it.
3. `cargo test -p <logic-crate>` or `cargo test --lib` — only if the tested
   code does not pull in rendering. Tests that build an `App` with
   `DefaultPlugins` are slow and need a display; use `MinimalPlugins`:

```rust
#[test]
fn score_increments() {
    let mut app = App::new();
    app.add_plugins(MinimalPlugins).init_resource::<Score>()
       .add_systems(Update, add_point);
    app.update();
    assert_eq!(app.world().resource::<Score>().0, 1);
}
```

4. `cargo build` — link the real binary (minutes cold).
5. `cargo run` — opens a window; auto-backgrounded. Compile first with
   `cargo build` so the playtest timer is not eaten by compilation.

## Workspace split

For bigger games: `game_logic` crate (no bevy dep, fast tests) +
`game` crate (bevy, thin systems that call logic). `cargo test -p game_logic`
stays instant.

## Cache

`target/` is large (GBs). Share it across projects with
`CARGO_TARGET_DIR` if disk is tight; `cargo clean` only when asked.
`sccache` helps across clean builds.

## Reading build output

- `error[E0502]`/`E0499` borrow errors usually mean overlapping queries →
  `Without<>` or `ParamSet`.
- `unresolved import bevy::prelude::X` → the name moved in this version;
  search the registry source, do not guess.
- Link errors mentioning `dynamic_linking` in release → remove the feature.
