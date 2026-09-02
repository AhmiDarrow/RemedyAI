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
resolve-beneath file-open option and handle-relative, no-follow parent traversal for every
mutation; process policy and signed capability tokens are added by the Phase 3 security
boundary before the substrate is connected to production.

The security boundary now authenticates short-lived grants with HMAC-SHA-256, binds them
to an agent and workspace scope, consumes each nonce once under synchronization, prunes
expired nonces, and requires a separate signed owner-checkpoint right for checkpointed
operations. Process execution is default-deny and
uses exact executable rules plus per-argument prefix constraints. Keys are supplied by the
host secret store; no signing key is compiled into the library or repository.

The Go runtime owns explicit new/running/stopping/stopped transitions, named worker
supervision, bounded restarts, panic recovery, cancellation causes, and deadline-aware
shutdown. Provider selection is bound per session so desktop, messenger, and background
work cannot overwrite one another through process-global model state.

Local IPC uses the shared bounded frame format over current-user Windows named pipes or
mode-0600 Unix sockets. Calls retain correlation IDs across concurrent work, propagate
cancellation, and unblock on disconnect. A loopback-only HTTP compatibility handler keeps
the existing FastAPI product reachable during reversible migration and blocks off-machine
targets and redirects. Duplicate active correlation IDs are rejected and each connection
has a fixed in-flight ceiling.

The Go cognition engine represents observation, model streaming, policy, action, state
update, pause, completion, and failure as traceable transitions. It supports bounded model
retries, deterministic tool batching with a concurrency ceiling, owner checkpoints,
tool/iteration ceilings, and repeated-batch no-progress detection.

The Tool ABI registers immutable, versioned descriptors carrying runtime, risk,
capability, permission, and Draft 2020-12 input/output schemas. Every request and result
is validated, external schema loading is disabled, and Go, Zig, Python, and WASM
executors share the same registry contract.

Native memory uses a language-neutral append log with length and CRC32 framing, file-sync
durability, recoverable partial tails, hard failure on checksum corruption, typed memory
namespaces, latest-key indexing, and bounded lexical retrieval. Zig validates the same
record framing while Go decides which episodic, semantic, procedural, working, or
relational memories to retrieve.

The durable event bus assigns monotonic sequence IDs, replays after restart, filters by
type and source, and exposes bounded subscriber queues with drop-newest, drop-oldest, or
disconnect policies. Failed writes roll back their partial record before a sequence is
committed. Publishing and replay remain race-safe, and slow-consumer loss is reported
rather than silently blocking the runtime.

Persistent runtime state checkpoints goals, tasks, environment, relationships, and
self-state with schema versions and the last applied event sequence. Checkpoints use
atomic replacement on Windows and Linux; restart replay ignores duplicate events and
never treats abandoned temporary files as authoritative state. A gap stops replay instead
of silently advancing past missing mutations.

The scheduler supports one-shot, recurring, event-triggered, and goal-triggered jobs;
dependency DAGs; priority ordering; deadlines; run/time budgets; cancellation; and
snapshot/restore. Its tick input is deterministic for tests, while its run loop can be
supervised by the persistent runtime. Canceling a running or queued job cancels its context,
preserves the canceled status, and never rearms recurring work.

Hive agents are lightweight supervised goroutines with explicit identity, goals, memory
scope, and capability sets. Child capabilities must be a subset of the parent's delegated
set, mailboxes are bounded, terminal agents remain inspectable without consuming active
quota, crashes are isolated, and parent shutdown cancels the whole tree.
Messages sent by agents are identity-bound and remain within the same memory scope and
delegation tree; the trusted manager retains an explicit control-plane delivery path.

Python is represented as a supervised capability-worker protocol for model, vision,
speech, and research operations. Workers negotiate a protocol version and health state,
support unary and streaming calls, inherit request cancellation/deadlines, restart within
a bounded budget after transport failure, and cannot take down the Go runtime. Automatic
replay after an uncertain unary failure requires an explicit idempotency declaration;
incomplete streams are retired and reconnected without removing streaming capability.

Language ownership is machine-checked: Go cannot directly spawn processes or use
unsafe/syscall packages, third-party dependencies have named package owners, and native
Go cannot import Python packages. Command entry points are checked too. See `BOUNDARIES.md`;
the same check runs in Windows and Linux CI.

RDNA v1 represents semantic action, target, constraints, expected evidence, and fallback
intent without embedding executable code. Compilation is deterministic and preserves
read-only, credential, capability, and owner-checkpoint constraints. RDNVM execution is
hard-disabled unless an explicit experimental flag is set and is not on the production path.

Native performance has a reproducible benchmark scorecard and broad CI regression ceilings
for framing, lifecycle, IPC, validated tool dispatch, memory retrieval, durable events,
scheduling, and ReAct overhead. Python comparison kernels are recorded separately, and
microbenchmark results are never treated as proof of complete-task speed.

Local verification:

```text
cd native/go && go test ./... && go vet ./...
cd native/zig && zig build test && zig build
```
