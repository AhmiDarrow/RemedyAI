//! Unit-test helpers (compiled only for tests).

/// Load a Go fixture from `tests/fixtures/connect/` at the repo root.
pub(crate) fn fixture(name: &str) -> serde_json::Value {
    let p = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/connect")
        .join(name);
    serde_json::from_slice(&std::fs::read(&p).unwrap_or_else(|e| panic!("{}: {e}", p.display()))).unwrap()
}
