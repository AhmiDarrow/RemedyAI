# Native language boundaries

The native runtime has two non-overlapping responsibilities, and starts no interpreter:

- **Go manages:** lifecycle, cognition, routing, events, memory decisions, scheduling,
  agent supervision, helper supervision, and internal persistence.
- **Zig executes:** owner-machine filesystem/process/system operations, compact record
  validation, capability verification, and deterministic policy enforcement.

Speech runs in the Rust `remedy-voice` helper and the image model in a `llama-server`
the runtime downloads, verifies and supervises; the local search engine is managed the
same way. Go reaches Zig only through the versioned C Tool ABI. Go `remedy-runtime` owns
production `:7400`. The Python package under `src/remedy` is only the `remedy` launcher (and the bundled
skills, as data); the runtime does not start, attach to or ship any Python of ours. The one
Python the runtime may install is ClaimIDX's own, into its private folder, when the owner
has ClaimIDX on.

`go run ./cmd/check-boundaries -root ..` enforces the mechanical portion of this contract.
It rejects direct process execution from Go, unsafe/syscall use, misplaced third-party
dependencies, and Python coupling from the native Go runtime. Package `core` may import
`unsafe`, `golang.org/x/sys`, and `github.com/ebitengine/purego` solely to load the Zig
`remedy_core` C ABI (Windows LoadDLL / Linux Dlopen for ConPTY, capture, HostSession,
Tailscale, policy tokens, authorized process spawn); every other package stays deny-by-default.
