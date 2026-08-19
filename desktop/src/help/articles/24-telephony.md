# Telephony — a voice on the phone (Phase 0: bench only)

Remedy can hold a phone conversation. Today she can do it **only against a
simulated line**: no hardware, no phone number, no minutes, nobody called.

That is deliberate. The hard part of a phone call is not the words — it is the
timing. Phase 0 proves the timing before a single component is downloaded.

## What "passes for human" means here

A bar with numbers behind it, not a judgement:

| | Bar |
|---|--:|
| Time to first sound | 600 ms (median) |
| Stops when you cut in | 150 ms |
| Talks over you | under 3% of turns |
| Silence you are left in | under 800 ms |

Two scripted calls are measured against it — a clinic receptionist who
interrupts, and an automated menu with unforgiving timing. Both pass.

```bash
python -m remedy.telephony.bench          # run them
python -m remedy.telephony.bench --json   # machine-readable
```

Read the result honestly: the transport, turn-taking and interruption handling
are the real implementation. Speech recognition, the model, and the voice are
stubs that *wait* their declared time rather than compute. Phase 0 proves the
timing architecture, which is what fails first. It says nothing about whether
the voice sounds human to an ear.

## Four ways to get a line — your choice, not ours

Remedy will not pick for you. When the time comes she says what the options are
and what each one costs you:

| | What it is | The catch |
|---|---|---|
| **Her own number** | A SIP number over a trunk | A dollar or two a month — but it works with your phone off, lost, or in another room |
| **A calling app here** | Android running on your PC, calling through something like Google Voice | Nothing recurring, but the number belongs to a cloud service |
| **Your phone, on a cable** | Your real SIM and number | About ten dollars for the adapter; the phone stays plugged in |
| **Your phone, wirelessly** | The same, over Bluetooth | The only one that can drop out — it needs to stay in range and charged |

Bluetooth is listed last and is never the default. It is the only option with a
distance limit, and an owner who cannot easily get up and move a phone should
not have to depend on one.

## Nothing ships until you ask

No SIP engine, no speech models, no Android images are bundled. Each is fetched
from its own publisher, only when you ask, and Remedy names the licence and the
size before downloading:

| | | |
|---|---|--:|
| baresip | the SIP engine | ~6 MB, BSD-3 |
| smart-turn | knowing when you have finished speaking | ~45 MB, BSD-2 |
| Chatterbox | a voice that does not sound synthetic on a phone | ~1.1 GB, MIT |
| Android image | running a calling app here | ~2.6 GB, its publisher's terms |

## Before any real call

A phone-specific agreement, once, in conversation — separate from the general
terms, because agreeing to let Remedy use a file manager is not agreeing to let
it phone people. It covers the things that only matter once a machine can call:

- **Never an emergency service.** Keep a phone that works without her.
- Recording and AI-disclosure rules differ by country and state, and following
  them is your call. She discloses that she is an assistant by default.
- She will not claim to be human if asked, read out card numbers or one-time
  codes, or agree to a payment without you.

The bench needs none of this — simulated calls reach nobody — which is exactly
why a real one is never allowed to slip through on a default.

Full text: `docs/TELEPHONY_TERMS.md`. Design and measurements:
`docs/TELEPHONY.md`.

## What is not built yet

Phase 0 is the bench. A real line, ring handling, answering when you are busy,
and your own number over the phone bridge are Phases 1 to 3, and none of them
are here.

## See also

- [Security & data](04-security-and-data.md) — what stays on your machine
- [Personal assistant](21-personal-assistant.md) — the appointments a call would be about
