# Remedy Native

Remedy Native is the compatibility-preserving path from the Python control plane to a
Go runtime backed by a Zig capability core.

- `go/` owns lifecycle, orchestration, scheduling, events, and language-neutral tool dispatch.
- `zig/` owns machine-facing primitives and enforces capabilities again at the OS boundary.
- `protocol/` is the versioned binary contract shared by every runtime.

The Python product remains authoritative until a native vertical slice passes the same
behavioral contracts and can be disabled with one rollback switch. Native components must
not weaken owner checkpoints, write-jail rules, credential isolation, or Windows/Linux parity.

Local verification:

```text
cd native/go && go test ./... && go vet ./...
cd native/zig && zig build test && zig build
```
