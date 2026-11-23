# Coordination — several Remedy sessions, one repo

Remedy can run more than one session against the same project. Coordination is
what keeps two of them from overwriting each other's work.

## The problem it solves

Two chats, both editing the same file. Without coordination the second write
silently discards the first, and neither of you finds out until something
mysterious breaks.

## How it works

Each live session publishes a **beacon** — who it is, what project it is in, and
which files it currently claims. Beacons live in one small file under
`~/.remedy/coordination/`, written under a file lock so two sessions writing at
once cannot corrupt the registry.

Before a write, a session claims the path. If another live session already holds
it, the write is refused with a message naming the holder rather than going
ahead and hoping.

Claims are **per path**, not per project, so two sessions working on different
files in the same repo never block each other.

## When a session dies

Beacons expire on a heartbeat. A session that crashes, is killed, or has its
window closed stops publishing, and its claims lapse — nothing stays locked
because a process went away without tidying up.

## What you see

Studio's status bar shows other live sessions when there are any. A refused
write says which session holds the file, so the fix is a conversation rather
than a mystery.

## If you only ever run one session

Then this does nothing and costs nothing. It is not a mode to enable.

## See also

- [Chat, rails, Plan/Build](05-chat-and-sessions.md)
- [Coding agency](18-agency.md) — the write jail and access scope
- [Hive](28-hive.md) — daughters Remedy hires (they use the same claims)
