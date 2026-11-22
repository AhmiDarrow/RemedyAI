# Talking with other bots

Remedy can talk with a user's other bot systems — Claude, ChatGPT, Cursor,
Grok Bot, and anyone else that speaks an open standard — as a *peer*, not as
a muscle they plug in. Muscles are models she runs. Peers are independent
bots that keep their own memory and permissions.

She uses two open protocols:

- **A2A** (Agent2Agent, Linux Foundation), version 1.0, for
  agent-to-agent tasks, streaming status, and discovery.
- **MCP** (Model Context Protocol) so apps that already speak MCP can add
  "talk to Remedy" as a server.

She does not ship a built-in adapter for any one vendor's messaging grid.
If a service speaks A2A or MCP, it can be added as a contact. If it only
speaks something else, a user can put an MCP or A2A bridge in front of it.

The inner language (`ril1`) stays inside her. Peers only see plain English
plus structured JSON. Every inbound and outbound exchange is written to an
audit log under `{home}/interop/audit.jsonl`.

## Safe defaults

Inbound is **off** until you turn it on (`interop_inbound`, or
`REMEDY_INTEROP_INBOUND=1`). When it is on, it still binds to
`127.0.0.1` and requires a bearer token (`REMEDY_INTEROP_TOKEN`, or a
file at `{home}/interop/inbound_token` that Remedy mints with mode 0600).
The token is never logged or written to git.

Money, sending messages, and deleting things always wait for the owner,
even when the peer is verified and listed with those permissions.

## A2A

When inbound is on, `remedy serve` (the Go runtime on `:7400`) publishes:

| Path | Role |
|------|------|
| `GET /.well-known/agent-card.json` | Agent Card (A2A v1.0). Also served at `/.well-known/agent.json` for older clients. |
| `POST /a2a` | JSON-RPC. Methods: `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `GetExtendedAgentCard`, `SubscribeToTask`. |
| `GET /extendedAgentCard` | Authenticated extended card. |

Method names are PascalCase as in A2A 1.0. The older slash-form names
(`message/send`, `tasks/get`, …) are accepted as aliases. Task states are
emitted as `TASK_STATE_*`. Clients should send `A2A-Version: 1.0`.

The loopback Agent Card is readable without a token so a peer can see that
Bearer auth is required. Every other A2A route needs the token. Non-loopback
binds still require a token, and the `Origin` header is checked to reduce
DNS rebinding.

## MCP

`remedy mcp serve` already exported this machine's skills over stdio. It now
also exposes talk-to-Remedy tools:

- `remedy_ask` — ask her a question
- `remedy_delegate` — hand her a task and get a task id
- `remedy_task_status` — poll that task
- `remedy_share_context` — share context for a task (not a new permission)

Stdio is opt-in: you launch the process from Claude Desktop, ChatGPT,
Cursor, or another MCP client. Example:

```json
{
  "mcpServers": {
    "remedy": {
      "command": "remedy-mcp",
      "args": []
    }
  }
}
```

Streamable HTTP (MCP revision 2026-07-28, still compatible with
2024-11-05) is available in two places:

- On `:7400` as `POST /mcp`, only when inbound is on, with the same
  token and loopback default as A2A.
- As a standalone listener: `remedy mcp serve --http` (default
  `127.0.0.1:7401`). Port 7400 is reserved for `remedy serve`.

## Contacts and orchestration

Peer contacts live in `{home}/interop/peers.json`. Each entry has a name,
protocol (`a2a` or `mcp`), endpoint, skills, and the permissions that
peer may *ask of her*. Credentials are never stored there; `auth_ref` may
name an environment variable.

When Remedy needs help from a peer, she picks one by skill overlap,
delegates over A2A, and merges the replies. The model-facing tools are
`peer.list`, `peer.ask`, `peer.delegate`, and `peer.status`. They are not
muscles: they talk to another bot.

Rate limits, hop limits, and a ping-pong guard stop two bots from looping.
See the knobs below.

## Identity (optional)

A peer may present a DID such as `did:claimidx:…` or `did:key:…`. Trust
policy (`interop_trust_policy`):

- `gated` (default) — unverified peers may only ask, share context, or
  poll status, and unknown ones are queued for you.
- `verified` — only peers that pass a verifier are accepted.
- `open` — known or unknown peers may ask or delegate; money, send, and
  delete still need you.

ClaimIDX verification is **off** by default (`interop_claimidx`) and is
never required. The verifier is a plug: you can leave it off, turn it on,
or replace it. A down host leaves the peer *unverified*, it does not
break inbound.

## Advanced knobs

All of these live under `[advanced]` in `config.toml` (Settings → Advanced).
Environment variables, when set, still win.

| Knob | Default | Meaning |
|------|---------|---------|
| `interop_inbound` | `false` | Accept inbound A2A and MCP. Also `REMEDY_INTEROP_INBOUND`. |
| `interop_bind` | `127.0.0.1` | `127.0.0.1`, `lan`, or `0.0.0.0`. The last two still need a token. |
| `interop_a2a` | `true` | Serve and use A2A. |
| `interop_mcp` | `true` | Serve talk-to-Remedy MCP tools. |
| `interop_claimidx` | `false` | Optionally verify peer DIDs with ClaimIDX. |
| `interop_trust_policy` | `gated` | `open`, `gated`, or `verified`. |
| `interop_auto_reply` | `false` | When inbound is on, answer allowed asks automatically. Off: queue them. |
| `interop_audit` | `true` | Write the audit log. |
| `interop_rate_per_min` | `30` | Max inbound requests per peer per minute. |
| `interop_hop_limit` | `3` | Max forwarding hops. |
| `interop_max_peers` | `64` | Cap on saved contacts. |
| `interop_task_ttl_s` | `86400` | How long peer tasks are kept. |

Automatic replies stay off until you turn them on. Inbound messages are
queued in `{home}/interop/inbox.jsonl` and recorded as A2A tasks in
`{home}/interop/tasks.json`.

## What this is not

- Not a muscle. She does not run the other bot, and it does not run her.
- Not a way around approvals. The existing policy engine still gates
  money, mail, and delete.
- Not a release. This is on `master` only after review and testing.
