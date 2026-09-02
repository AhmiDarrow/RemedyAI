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

The security boundary now authenticates short-lived grants with HMAC-SHA-256, binds them
to an agent and workspace scope, consumes each nonce once, and requires a separate signed
owner-checkpoint right for checkpointed operations. Process execution is default-deny and
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
targets and redirects.

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
disconnect policies. Publishing and replay remain race-safe, and slow-consumer loss is
reported rather than silently blocking the runtime.

Persistent runtime state checkpoints goals, tasks, environment, relationships, and
self-state with schema versions and the last applied event sequence. Checkpoints use
atomic replacement on Windows and Linux; restart replay ignores duplicate events and
never treats abandoned temporary files as authoritative state.

The scheduler supports one-shot, recurring, event-triggered, and goal-triggered jobs;
dependency DAGs; priority ordering; deadlines; run/time budgets; cancellation; and
snapshot/restore. Its tick input is deterministic for tests, while its run loop can be
supervised by the persistent runtime.

Hive agents are lightweight supervised goroutines with explicit identity, goals, memory
scope, and capability sets. Child capabilities must be a subset of the parent's delegated
set, mailboxes are bounded, terminal agents remain inspectable without consuming active
quota, crashes are isolated, and parent shutdown cancels the whole tree.

Python is represented as a supervised capability-worker protocol for model, vision,
speech, and research operations. Workers negotiate a protocol version and health state,
support unary and streaming calls, inherit request cancellation/deadlines, restart within
a bounded budget after transport failure, and cannot take down the Go runtime.

Local verification:

```text
cd native/go && go test ./... && go vet ./...
cd native/zig && zig build test && zig build
```
