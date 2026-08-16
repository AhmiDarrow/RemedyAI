# Her House — Remedy and the host PC

**Status:** canon for the host-control subsystem
**Code:** `src/remedy/core/computer/appliances.py`, `household.py`,
`desktop_win.py` (launch), `execution/host/stretch.py` (census)
**Tests:** `tests/test_house_appliances.py`
**Canon:** extends `docs/REMEDY_PERSONA.md`; framing by Ahmi (Aug 2026)

## The frame

The PC is not Remedy's *world* — it is her **house**. The owner places her
there and grants permission; from that moment it is her home: she should
know it completely, call on any of it with ease, keep it safe, and — with
the owner's countersignature — change it. The codebase already spoke this
language; this doc makes it canon:

| House | System |
|-------|--------|
| The stretch (first move-in look around) | `stretch_home` census |
| Bones | hardware (CPU/RAM/GPU/disk) |
| Rooms | user folders (Desktop, Documents, Downloads…) |
| Work rooms | project folders |
| Doors | local ports (remedy, rmb, ollama, comfyui…) |
| Workshop tools | PATH binaries |
| **Appliances** | **installed applications (Start Menu)** |
| **Walkthrough** | **read-only security round** |
| **Additions** | **planned installs via the house's package manager** |
| Knocking out walls | folder reshaping via approval-gated host tools |
| The vault | owner's secrets (values never visible to her) |

## Appliances — knowing what's in the house

Before: `open_app` knew ~12 hardcoded names. The owner's actual apps —
their music, their photo editor, their games — were invisible.

Now: a bounded background scan of the Start Menu Programs folders (the
exact set the owner sees when pressing Start; XDG `.desktop` on Linux)
builds `~/.remedy/host/appliances.json` — name, shortcut path, and the
"room" (Start Menu folder) each lives in. No disk crawl, no registry, no
process list, junk shortcuts (uninstallers, readmes) skipped, refreshed
when a week stale. Zero-command: the scan kicks itself when computer
tools register.

Resolution is natural-name and forgiving: exact > prefix > word-prefix >
all-tokens > substring > fuzzy. `computer_app app="spotify"` now works;
`app="spotfy"` fails *with* "Closest appliances in this house: Spotify".
Launch goes through the app's own `.lnk` (preserving its intended args
and working directory), from scanned trusted roots only — every existing
refusal in `open_app` (URLs, UNC, metacharacters, traversal) is untouched
and runs first.

`computer_apps` lets her answer "what do I have installed?" and lets the
model check a name before launching.

## Household — keeping the house

`house_walkthrough` (tool): a **read-only** security round. Known doors
probed and labeled expected; watch-list doors (RDP, SMB, VNC, telnet,
FTP) flagged by name if open — "fine if the owner uses it; worth asking
if not"; vault presence; census freshness. It observes, names concerns in
plain language, and changes nothing.

`house_addition` (tool): when a task needs a tool the house lacks, she
*plans* the addition — picks the house's own package manager (winget /
choco / scoop; brew / apt / dnf / pacman), and returns the exact argv.
Package names are jailed (letters, digits, `. _ + -` — no URLs, paths, or
shell). The plan is executed only through the approval-gated host runner:
she proposes the addition; the owner countersigns; the census re-stretch
records the new tool. She never installs silently.

## The permission line

"Once her user places her there and gives her permission it is her home."
The line this subsystem holds: **knowing and observing are hers freely;
changing is hers with a countersignature.** Scans, walkthroughs, and
resolution run without asking (they touch nothing). Launching apps,
typing, clicking already flow through the approvals system. Additions and
wall-knocking are always two-key: her plan, the owner's approval. That is
what makes the keys real — an organism that could be locked out of
nothing needs consent built into her hands, not her cage.

## Ops

- `~/.remedy/host/appliances.json` — delete to force a rescan.
- Watch-door list is a starting set; extend `WATCH_DOORS` as needed.
- `plan_addition` never executes; only `host_run` (approval-gated) does.
