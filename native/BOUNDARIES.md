# Native language boundaries

The native migration has three non-overlapping responsibilities:

- **Go manages:** lifecycle, cognition, routing, events, memory decisions, scheduling,
  agent supervision, worker supervision, and internal persistence.
- **Zig executes:** owner-machine filesystem/process/system operations, compact record
  validation, capability verification, and deterministic policy enforcement.
- **Python supplies ML:** model-specific inference, vision, speech, research, scientific
  libraries, and experiments, always as a replaceable supervised worker.

Cross-language calls use only the versioned C Tool ABI, the `RMDY` framed IPC protocol,
or the Python worker protocol. The existing Python product remains the compatibility path
until Phase 17 gates a native slice on and proves rollback.

`go run ./cmd/check-boundaries -root ..` enforces the mechanical portion of this contract.
It rejects direct process execution from Go, unsafe/syscall use, misplaced third-party
dependencies, and Python coupling from the native Go runtime.
