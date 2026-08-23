# Hive — daughters that report to Remedy

Remedy can hire **silent daughters** for independent work. They are not extra
chats and not a farm of bots. You still talk to **one Remedy**. Daughters
report a compact packet to her; she speaks to you.

This chapter is for owners who want to understand the hive, and for operators
who open **Diagnostics** to see who is hired. Everyday chat does not surface
internal tool names.

## Why it exists

A long job often has a piece that can run on its own: review this module,
watch a log, keep an eye on a mailbox. If Remedy does that in the same
context window as the conversation with you, the two tasks fight for space
and she stalls waiting on herself.

A daughter gets **her own session and memory**. Remedy is the mother of that
hive: she hires, she collects the packet, she decides what to tell you.

This is not `spread_run` (cheap parallel jobs that return a digest in the
same turn) and not the nano swarm (silent continuity workers). Those stay.

## Two cadences

| Cadence | Job | Ends when |
|---------|-----|-----------|
| **Forager** | One bounded pass | She reports a packet (or you Stop that chat) |
| **Post** | Standing watch | Remedy retires her — **Stop does not** |

A post is continuous as a *job*. The model is not on 24/7: she pulses
(default a couple of minutes, never faster than 30 seconds), writes a short
journal, and sleeps until the next pulse. Serve restart wakes live posts.

## What you see

You should not see extra sidebar sessions named `hive_…`. Those ids are
internal. Remedy may say, in plain language, that she has someone covering a
slice of the work — not a second personality asking you questions.

**Diagnostics** (command palette → Diagnostics) has an Advanced **Hive**
roster: cadence, status, goal, last outcome. You can retire a daughter
there. There is no transcript.

## What daughters cannot do

They cannot hire further daughters (depth 1). They cannot see mother-only
tools: hive controls, mail send, computer click/type, settings, session
import/export. Money, credentials, and irreversible send still stop at
Remedy — no mode waives those checkpoints.

If two daughters would write the same file, coordination claims block the
second write the same way two of your chats already do.

## For Remedy (tools)

Mother-only, never listed to you as a product surface:

- `hive_spawn` — hire forager or post
- `hive_collect` — read the capped packet (not a tool log)
- `hive_assign` — replace a post's job
- `hive_status` / `hive_retire`

After a hire she is told to **keep working** and collect later. Packets land
on her evidence ledger.

## See also

- [Continuity workers (nano swarm)](17-nanoswarm.md) — not chat agents
- [Coordination](25-coordination.md) — several sessions, one repo
- [Coding agency](18-agency.md) — `spread_run` for cheap parallel jobs
