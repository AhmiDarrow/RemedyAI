# Native language boundaries

The native migration has three non-overlapping responsibilities:

- **Go manages:** lifecycle, cognition, routing, events, memory decisions, scheduling,
  agent supervision, worker supervision, and internal persistence.
- **Zig executes:** owner-machine filesystem/process/system operations, compact record
  validation, capability verification, and deterministic policy enforcement.
- **Python supplies ML:** model-specific inference, vision, speech, research, scientific
  libraries, and experiments, always as a replaceable supervised worker.

Cross-language calls use only the versioned C Tool ABI, the `RMDY` framed IPC protocol,
or the Python worker protocol. Go `remedy-runtime` owns production `:7400`; Python is
not the HTTP authority. Python remains the supervised ML / compatibility worker path
until Phase 17 gates remaining native slices on and proves rollback.

`go run ./cmd/check-boundaries -root ..` enforces the mechanical portion of this contract.
It rejects direct process execution from Go, unsafe/syscall use, misplaced third-party
dependencies, and Python coupling from the native Go runtime. Package `core` may import
`unsafe`, `golang.org/x/sys`, and `github.com/ebitengine/purego` solely to load the Zig
`remedy_core` C ABI (Windows LoadDLL / Linux Dlopen for ConPTY, capture, HostSession,
Tailscale, policy tokens, authorized process spawn); every other package stays deny-by-default.
