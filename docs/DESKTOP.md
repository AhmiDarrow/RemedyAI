# Remedy Desktop — Architecture & Implementation Plan

## Goal

A **Tauri desktop app** (Windows-first) with an OpenCode-like interactive chat UX,
backed by an **extended Remedy FastAPI** server. v1 = chat core parity.

## Architecture

```
┌─────────────────────────────────────────────┐
│  remedy-desktop (Tauri 2)                   │
│  ┌───────────────────────────────────────┐  │
│  │  Web UI (React 19 + Vite + Tailwind)  │  │
│  │  chat · sessions · slash · markdown   │  │
│  └─────────────────┬─────────────────────┘  │
│                    │ HTTP + SSE             │
│  ┌─────────────────▼─────────────────────┐  │
│  │  Sidecar: `remedy serve` (Python)     │  │
│  │  extended session/message/event API   │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

## API Contract (v1)

### Sessions

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/sessions` | List sessions, sorted by last message |
| `POST` | `/api/sessions` | Create session (`{ title?: string, model?: string }`) |
| `GET` | `/api/sessions/{id}` | Session detail + message count |
| `PATCH` | `/api/sessions/{id}` | Rename session (`{ title }`) |
| `DELETE` | `/api/sessions/{id}` | Delete session + all messages |
| `POST` | `/api/sessions/{id}/abort` | Stop active generation |

### Messages

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/sessions/{id}/messages` | List messages (`?limit=50`) |
| `POST` | `/api/sessions/{id}/messages` | Sync send, returns full response |
| `POST` | `/api/sessions/{id}/messages/stream` | SSE: `thinking`, `token`, `tool_call`, `tool_result`, `done`, `error` |

### Management

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/models` | Available LLM models + default |
| `GET` | `/api/agents` | Available agent profiles |
| `POST` | `/api/sessions/{id}/command` | Execute slash command |

### Events (SSE)

```
event: token         → { text: "Hello" }
event: thinking      → { text: "..." }
event: tool_call     → { name: "read_file", args: {...} }
event: tool_result   → { name: "read_file", output: "..." }
event: done          → { request_id: "..." }
event: error         → { message: "..." }
```

## UI Layout (OpenCode-like)

```
┌──────────────┬──────────────────────────────────────┐
│ Session List │  Message Feed                        │
│              │  ┌──────────────────────────────────┐│
│  + New       │  │ User: "What files are in src?"   ││
│  ──────────  │  │ Agent: "src/ contains..."        ││
│  Session 1   │  │           [markdown + code]       ││
│  Session 2   │  └──────────────────────────────────┘│
│  Session 3   │                                      │
│              │  ┌──────────────────────────────────┐│
│              │  │ Composer                  [model] ││
│              │  │ [multiline input + send/stop]     ││
│              │  └──────────────────────────────────┘│
│              │  Status: ● Connected · remedy v0.7.0 │
└──────────────┴──────────────────────────────────────┘
```

## Slash Commands (v1)

| Command | Action |
|---------|--------|
| `/help` | Show available commands |
| `/new` | Create new session |
| `/sessions` | List all sessions |
| `/compact` | Compact/summarize current session |
| `/models` | List available models |
| `/thinking` | Toggle thinking visibility |
| `/memory` | Search memory |
| `/skills` | List available skills |
| `/handoff` | List handoff notes |

## Implementation Order

### Phase 0 — API Foundation (Python, in-repo)
1. Session/message models in `models.py`
2. Session/message tables in `memory/store.py`
3. Structured SSE streaming in `api.py`
4. Full session/message/command/model REST endpoints

### Phase 1 — Web UI (React + Vite + Tailwind)
5. Scaffold `desktop/` with Vite + React + Tailwind
6. Session sidebar with list & create
7. Message feed with markdown rendering
8. Composer with send/stop + model selector
9. Slash command palette
10. Status bar

### Phase 2 — Tauri Shell (Windows first)
11. Set up `src-tauri/` with sidecar config
12. Bundle/spawn `remedy` process
13. Window management, tray icon (optional)
14. NSIS installer build

### Phase 3 — Polish
15. Tool call cards in message feed
16. Diff rendering (stretch)
17. Dark theme refinement

## Tech Decisions

- **Frontend**: React 19 + Vite + Tailwind CSS — best velocity for chat UIs, excellent Tauri integration docs
- **Markdown**: `react-markdown` + `rehype-highlight` for code blocks
- **Streaming**: Native `fetch` with `ReadableStream` for SSE; no WebSocket needed
- **State**: TanStack Query (React Query) for REST caching; lightweight
- **Sidecar**: `remedy serve` spawned as subprocess; PyInstaller `.exe` as fallback for standalone Windows builds

## Windows Distribution

- **Dev**: `remedy serve` + `pnpm dev` in separate terminals
- **Packaged**: Tauri bundles `remedy` binary via sidecar; NSIS `.exe` installer
- **Config**: Shares `~/.remedy/config.toml` with CLI `remedy`

## Success Criteria (v1)

- [ ] Windows `.exe` launches → auto-starts server → opens chat window
- [ ] Create session → send message → tokens stream in real-time
- [ ] Switch sessions without losing history
- [ ] `/new`, `/help`, stop generation work via UI
- [ ] Same backend usable via `remedy web` in browser
- [ ] No Electron dependency
