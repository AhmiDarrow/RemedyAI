# Grove — the partner home

Grove is what opens when you start Remedy. It is the surface built around your
**goals**, not around a chat log: what you are trying to do, what Remedy has
done about it, and what needs you right now.

Studio — the full workbench with terminals, file tree, browser rail and Build
tools — is one tap away, and whichever you pick is remembered.

## The home screen

Your goals appear as **plots**. Each is something you told Remedy you want,
either by typing it or by saying "I want to…" in the talkbar. The talkbar does
double duty: it plants a new goal, or it just talks.

A **needs you** pill sits above them. Anything waiting on your decision —
an approval before a payment, a checkpoint before something irreversible —
comes up as a plain-language card that says what is about to happen, not
which tool is about to run (see *Decisions, one at a time*).

## Remedy and her side jobs

Remedy's own card sits at the top of Grove. She is one partner, not a fleet
of bots. When a job is better done on the side, such as comparing phone plans
or shortlisting hotels, she starts a **side job**. A side job is a separate
conversation with one task, and it reports back to her when it is done. Side
jobs hang beneath her card with a plain status: *Working*, *Waiting for room*,
*Needs you*, *Done*.

- **Give Remedy a side job** starts one yourself. Describe the whole job; it
  works on its own and reports back.
- **Open a side job** to watch it work. Every step it takes shows up there as
  it happens. Press **Stop** to end it.
- **You talk to Remedy, not to the side job.** Anything you type in a side
  job's room goes to Remedy, named for that job. She answers in a small chat
  bubble you can drag or close (Esc), and the same exchange is in her main
  conversation.
- Side jobs cannot start side jobs of their own.

### Keep watch

**Keep watch** has Remedy look around on her own, every hour by default. She
checks for decisions waiting, finished side jobs, goals past their next-step
date, and routines that hit a problem. If anything changed, she leaves one
plain note under **While you were away**. This check runs on your PC and uses
no AI. The **⋯** menu sets how often she looks and your quiet hours, and can
send these notes to a chat app you use. To set that up, message Remedy once
from that chat, then pick it from the list.

### Routines

Routines are standing jobs on a clock, such as "every weekday at 7, brief me".
A **reminder** routine just posts its text and uses no AI. A **task** routine
has Remedy do the work each time, in the background. Remedy can also set up
routines when you ask her to, after you approve them.

### Goals on their own

Each goal card has an **On its own** switch. With it on, Remedy takes a step
on that goal now and then, and stops whenever she needs you. She waits for
your reply before she tries again. If a goal stops making progress, she slows
down instead of repeating herself. When she thinks a goal is done, she says so
and turns the switch off; you confirm. **Step now** asks for one step right
away.

### Sized to your PC

Everything Remedy does in the background shares one queue that fits your
computer. On a cloud model she runs up to three jobs at once, depending on
your processor. A model running on your own PC gets one job at a time, and
none while you are talking to her. If memory runs low, new background work
waits, and the side-job list says *PC busy — jobs wait*. Money, passwords,
sending and deleting still always stop for you, in every mode.

## Decisions, one at a time

When Remedy needs your OK, a single card rises above the talk bar. It says
what she wants to do in plain words, with **Yes**, **No** and **Explain**
(keys **Y**, **N**, **E**). If several decisions are waiting, the card shows
"1 of 3" and they come one after another. **Later** (Esc) tucks the card into
a small pill, and a new decision brings it back. Payment, password and send
steps get their own look and only ever offer a one-time yes.

## Inside a plot

Opening a plot gives you that goal's **room**, with two tabs.

**Alongside** is the live stage. If Remedy is driving a browser you see it
embedded, with the latest exchange as captions underneath and a pause button
while she works. It is meant to be watched, not read.

**Storyline** is the goal's co-written record. Three kinds of moment, in plain
words:

| | |
|---|---|
| **you said** | what you asked for |
| **Remedy said** | what she answered |
| **Remedy did** | what she actually did about it |

Never raw tool JSON. Text you typed into a page is never echoed back — if you
filled in a password, the storyline records that a field was filled, not what
went into it.

Each goal keeps its own conversation, so returning to a plot picks up where you
left off rather than starting cold.

## Settings (stay in Grove)

The logo (top-left) menu **Settings…** and **Ctrl+,** open Settings as a sheet
on Grove. They do **not** switch you to Studio. Speak/quiet is the 🔊/🔇
control in the Grove top bar — there is no extra Voice button.

Voice, vision, and local helpers download when Remedy first starts. Progress
shows in the empty strip of the title bar (next to the logo). If the voice
pack did not arrive, Settings → Voice has **Download Remedy's voice**.
Advanced still shows the pip command for power users.

## Switching to Studio

The window title bar has a **Grove** / **Studio** switch, always on screen on
both surfaces. **Ctrl+1** goes to Grove, **Ctrl+2** to Studio. The logo menu
has the same commands. Switching does not tear anything down — Studio stays mounted
while you are in Grove, so terminals keep running, streamed jobs keep streaming,
and the computer-host loop stays connected.

The choice persists in `remedy.surface`. Clearing browser storage returns you
to Grove, which is the default.

## Which one should you use

Grove when the question is *"is my thing progressing?"* — goals, approvals,
watching work happen.

Studio when the question is *"let me at it"* — editing files, running commands,
driving Build, reading the tool process.

They are the same Remedy, the same sessions, and the same memory. Neither is a
lesser mode.

## See also

- [Chat, rails, Plan/Build](05-chat-and-sessions.md) — Studio in detail
- [Personal assistant](21-personal-assistant.md) — reminders, mail, calendar, paperwork
- [Coding agency](18-agency.md) — what Remedy can do on its own
