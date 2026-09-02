# Remedy Native

Remedy Native is the compatibility-preserving path from the Python control plane to a
Go runtime backed by a Zig capability core.

- `go/` owns lifecycle, orchestration, scheduling, events, and language-neutral tool dispatch.
- `zig/` owns machine-facing primitives and enforces capabilities again at the OS boundary.
- `protocol/` is the versioned binary contract shared by every runtime.

The Python product remains authoritative until a native vertical slice passes the same
behavioral contracts and can be disabled with one rollback switch. Native components must
not weaken owner checkpoints, write-jail rules, credential isolation, or Windows/Linux parity.

The Zig library exposes its versioned C surface through `zig/include/remedy_core.h`.
Capability bits are deny-by-default. Path validation is paired with the operating system's
resolve-beneath file-open option; process policy and signed capability tokens are added by
the Phase 3 security boundary before the substrate is connected to production.

Local verification:

```text
cd native/go && go test ./... && go vet ./...
cd native/zig && zig build test && zig build
```
