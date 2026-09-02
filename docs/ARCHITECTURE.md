# Architecture (living)

Pointers, not a second product bible. Public: `docs/DESKTOP.md`,
`docs/TELEPHONY.md`, `docs/manual/`, root `AGENTS.md`.

## Stack

| Layer | Path | Role |
|-------|------|------|
| Python sidecar | `src/remedy/` | FastAPI/uvicorn API, ReAct loop, jail, PolicyEngine, hive, build engine, Soul/Partner memory |
| Desktop SPA | `desktop/` | Tauri 2 + React 19 (Grove / Alongside / Studio) |
| Tests | `tests/` | Jail, SSRF, hive caps, memory authority, plan store, build engine, ReAct policy |
| Gateway | `src/remedy/gateway/` | Serve bootstrap, session bridge, messenger flush |
| Native runtime | `native/` | Versioned Go nervous system + Zig capability core; layered cutover with Python compatibility/ML workers |

Entry: `serve.py` / `uv run` · verify: `uv run pytest -q`

## Next Evolution native boundary

`native/protocol/` defines the versioned binary frame shared across languages.
`native/go/` owns supervised runtime lifecycle, local IPC, deterministic ReAct
state, the Tool ABI, durable state/events/memory, scheduling, scoped agents, and
replaceable Python workers. `native/zig/` exports a small C ABI and independently
checks capabilities before machine-facing execution.

`src/remedy/runtime/native_runtime.py` is the production cutover seam. The
selector accepts `compatibility` (the default), `auto`, or `native` through
`REMEDY_NATIVE_RUNTIME` or `native_runtime` in config. Go protocol/tool ABI 1
and Zig ABI 1 must both probe healthy before the native route becomes effective.
Fallback after a native attempt is allowed only for operations declared
idempotent. The first cutover is read-only system topology; Python/FastAPI stays
available as the compatibility surface and as the long-term AI/ML worker runtime.

Desktop installers bundle the Go executable and Zig shared library on Windows
and Linux alongside the Python sidecar. `/api/ping` reports cached selector and
fallback evidence but never launches a probe, preserving the liveness route's
low-latency contract. Native CI and release builds cover both operating systems.

## Surfaces (one SPA)

| Surface | How UI loads | Voice | Settings |
|---------|----------------|-------|----------|
| Tauri desktop (`tauri:dev`) | Vite `localhost:5173` | Grove + Studio | Grove overlay; Studio rail |
| WebUI `http://127.0.0.1:7400/` | Static `desktop/dist` (mount at **serve start**) | Same SPA — rebuild + **restart serve** | Same |
| CLI | no SPA | **No voice** (owner preference 2026-08-20) | n/a |

Grove unmounts off-surface. Studio must own its own voice instance (`useVoice` in `App.tsx`). Logo-menu Settings must **not** switch Grove → Studio.

## Core control plane

- **ReAct** — `core/react_loop/`, `react_turn.py`, `react_policy.py`, `react_stream.py`, `turn_context.py`. Work signal gates tools; chat-only messages do not start a tool storm.
- **PolicyEngine** — `policy/engine.py`. Deterministic allow / ask / deny. Dangerous host commands denied; mail/pay checkpoints never waived. Trust profiles live in `APPROVALS.needs_ask`.
- **Write jail** — `core/security` + computer executor. Runtime-bin skip requires a real executable extension; cross-session computer state is thread-local per session.
- **Hive** — daughters are capped; no parent Partner Memory writes; no nested spawn. PROCESS_EXEC + FS_WRITE + NETWORK_READ by design for foragers (not a full sandbox).
- **Build engine** — TDD → unit hop → gate-tower verify. Plan steps may record intended / observed / evidence.
- **Soul / Partner memory** — `memory/soul/`, Partner Memory with who/why stamps. Retrieval is labeled context, not a grant.

## Language

`src/remedy/i18n/` — `ui_language` (`auto` or a BCP-47 id). Chrome catalogs overlay English. Reply-language is a system-prompt line (`language_system_line`); it does not strip tools or checkpoints. `GET /api/i18n`. Desktop: `desktop/src/i18n/`. Help manuals stay English until translated.

## Voice stack

- API: `src/remedy/interfaces/routes/voice.py` → `src/remedy/voice/service.py`
- First-run: `ensure_voice_assets` + `maybe_ensure_local_model` on API lifespan (skipped in pytest). Chatterbox HQ is **opt-in**, not first-run.
- UI: `desktop/src/components/settings/VoiceSection.tsx` (HQ toggle), Grove `GroveVoiceControls` (speak/quiet only), Studio composer/status bar
- Smart-turn: pinned ONNX under `~/.remedy/voice/models/smart-turn/`; detector `voice.realtime.turn.make_detector` globs `*.onnx` (no restart)
- Identity: `~/.remedy/voice/identity.json`. Owner clone: `voice/clone.py` (named task, expiry, revoke).
- Telephony loopback: `SipDirectBackend` (`sip_direct`) — UDP/in-process echo on 127.0.0.1, `simulated=True`

## Voice

Install and status are HTTP. Desktop and WebUI share Settings/Grove/Studio.
CLI stays text-only. Capability/policy contracts live in `src/remedy/policy/`
and `src/remedy/tools/`.

## Grove Connect

Phone remote for **this PC** (`src/remedy/connect/`). **Not** the messenger
gateway (`src/remedy/gateway/`). Default **off**. When on, it is a **second
listener** on a chosen IPv4 (never `0.0.0.0`); `:7400` stays loopback.
Owner-run relay (`remedy connect-relay`) forwards framed blobs without
decrypting. Same-LAN mDNS (`_remedy-connect._udp`) advertises a host-pub hash
only. See `docs/manual/29-remedy-connect.md`.

## Versioning

`pyproject.toml`, `desktop/package.json`, and `desktop/src-tauri/Cargo.toml` share one version. Tag + push only when the owner asks; local HEAD may be ahead of origin without a matching tag.
