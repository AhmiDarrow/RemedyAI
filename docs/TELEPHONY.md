# Telephony — her voice on the wire

**Status:** Phase 0 built and passing; HQ TTS (Chatterbox) wired for Grove
and the duplex pipeline; Phases 1–4 (a real PSTN/SIP/VM line) still planned
**Code:** `src/remedy/telephony/`, `src/remedy/voice/realtime/`
**Tests:** `tests/test_telephony_*.py`, `tests/test_voice_realtime_*.py`, `tests/test_terms.py` (111)
**Bench:** `python -m remedy.telephony.bench`
**Provenance:** `grok_report.pdf` (Aug 2026) — "telephony + smartphone proxying"
proposed as the highest-ROI next capability after host control + proactive
messengers.
**Terms:** [TELEPHONY_TERMS.md](./TELEPHONY_TERMS.md) — agreed once, in
conversation, before any call
**Depends on:** `gateway/` (channel adapters), `voice/service.py` (Kokoro +
faster-whisper), `core/approvals.py` (owner checkpoints),
`runtime/catalog.py` (pinned binaries), `interfaces/secret_store.py` (DPAPI)

## What this is

Remedy owns the desktop and can talk *to* her owner. She cannot talk *as* her
owner into the one layer the physical world still forces: the telephone. Clinics,
banks, government lines, small businesses, IVR trees, SMS 2FA — none of it yields
to a browser. This plan gives her a mouth and an ear on that network, without
giving up local-first, without a copyleft dependency, and without a setup wizard.

## Decisions taken (and why)

| Question | Decision |
|---|---|
| Transport | **Four, offered as a choice** — SIP, VoIP in a local Android VM, wired phone, Bluetooth. The owner picks on first run |
| What is never a dependency | **Proximity.** Bluetooth is offered last, recommended never, and required by nothing |
| SIP engine | **baresip** (BSD-3), swappable behind an interface; own stack only if it fights us |
| SIP-without-PSTN | Optional mode, **not installed by default**; doubles as the bench harness |
| Voice pipeline | **Local**, tuned hard for turn-taking. No audio leaves the machine |
| Her voice | Persistent identity that **evolves**, survives engine upgrades; gender configurable, female default |
| Owner's voice | Cloneable **for a named task only**, consent-gated, expiring; her own voice otherwise |
| Disclosure | **On by default**, per-contact override by the owner |
| Setup | **Conversation, never a wizard** |

### The license constraint is load-bearing

Remedy is source-available and commercially licensed (`LICENSE`, `COMMERCIAL.md`).
Copyleft is disqualifying, not merely inconvenient — this eliminates the default
choice before any design starts.

| Component | Role | License | Verdict |
|---|---|---|---|
| PJSIP / pjproject | the usual SIP stack | GPLv2+ **or paid commercial** | **Rejected** |
| baresip + libre | SIP stack | BSD-3 | Adopted |
| aiortc, pylibsrtp | RTP/SRTP/jitter/codecs | BSD | Adopted |
| Pipecat | realtime voice orchestration | BSD-2 | Reference architecture |
| smart-turn v3.2 | semantic endpointing (CPU int8 ONNX, ~8 MB) | BSD-2 | Adopted — the anti-robotic piece |
| silero-vad | speech onset | MIT | Adopted |
| Kokoro-82M | TTS (already shipped) | Apache-2.0 | Kept as the low-VRAM tier |
| Chatterbox-Turbo | TTS + zero-shot clone | MIT | Adopted as the human-bar tier |
| Orpheus | TTS for voice agents | Apache-2.0 | Candidate |
| faster-whisper / distil | STT | MIT | Adopted |

Every addition lands in `docs/THIRD_PARTY.md` with license and pin, matching the
existing ripgrep / llama.cpp / SmolVLM2 rows.

### Nothing telephony-related ships in the installer

No SIP engine, no speech models, no Android images. Each is fetched **only when
the owner asks for it**, from its own publisher, and she says what it is, how
big it is, and under what licence before fetching (`telephony/consent.py`,
`COMPONENTS`). Same pattern as ripgrep and llama-server.

This is not only about installer size. **Bundling is redistribution**, and
redistribution drags in every upstream licence's obligations — notices,
attribution, and in the case of some model weights, terms that a commercial
product cannot satisfy at all. Fetching a component at the owner's instruction
largely does not. It also means a component can be re-pinned without a release,
and that an owner who never wants a phone never downloads a byte of this.

### The owner agrees to terms before the first call

`docs/TELEPHONY_TERMS.md`, agreed once in conversation and recorded with its
version under `~/.remedy/telephony/consent.json`. The gate lives at the choke
point — `Line.place()` — so a new backend cannot forget it, and bench fixtures
declare themselves `simulated` so testing never requires agreeing to terms about
calls nobody is making.

Said aloud, not buried: **she is not an emergency service**, she can be wrong,
recording and AI-disclosure law is the owner's responsibility, phone service is
the owner's account with that provider, and there are limits no setting unlocks.

## Architecture

Three layers, each replaceable without touching the others.

```
src/remedy/telephony/
  line.py             Line + Call abstraction: place/answer/hangup/dtmf,
                      duplex audio frames, event stream. Every backend
                      implements this; nothing above it knows the transport.
  registry.py         backend selection + capability probe
  options.py          the four lines, their honest trades, the owner's pick
  registry.py         fast never-raising probes -> the spoken setup offer
  backends/
    fake.py           bench: scripted counterpart, 8 kHz mu-law simulation
    sip_baresip.py    baresip subprocess over its control socket
    vm_voip.py        VoIP app in a local Android VM; host audio devices
    phone_wired.py    real SIM: ADB over Wi-Fi for control, cable for audio
    bluetooth_hfp.py  same phone, wirelessly. Offered last, never required
    sip_direct.py     optional, unbundled: python SIP + aiortc RTP, no PSTN
  android/
    bridge.py         ADB: dial, answer, hangup, SMS, notifications, apps
    vm.py             provision + drive the local Android VM
    bringup.py        conversational provisioning (probe -> ask -> verify)
  policy.py           per-contact identity / disclosure / voice policy
  transcript.py       live transcript -> session events + memory
  checkpoints.py      never speak card/SSN/2FA or agree to pay on a live line

src/remedy/voice/
  realtime/
    pipeline.py       duplex loop: capture -> vad -> turn -> stt -> llm -> tts
    turn.py           smart-turn v3.2 endpointing + silero onset
    stt_stream.py     WhisperStream — live STT for the duplex loop
    tts.py            LocalTts — Kokoro or Chatterbox HQ (same path as Grove)
  identity.py         her voice: timbre seed, prosody profile, evolution
  clone.py            task-scoped owner clone, consent-gated, expiring
```

### Four lines, offered as a choice

There is no single right transport, so Remedy offers all of them and the owner
picks during the setup conversation (`telephony/options.py`,
`registry.line_options`). Ordered as she offers them — least dependence on a
second device first:

| Line | Number | Recurring | Depends on |
|---|---|---|---|
| **sip** — her own number over a trunk | hers; yours can forward to it | ~$1–2/mo + per-minute | nothing but this PC and the network |
| **vm_voip** — a VoIP app in an Android VM here | a cloud service's | nothing | this PC; the provider's service |
| **phone_wired** — real SIM, audio on a cable | **yours** | ~$10 once | the phone staying docked |
| **bluetooth_hfp** — real SIM, wirelessly | **yours** | nothing | the phone staying *within a few metres and charged* |

**Why proximity is disqualifying as a dependency.** An owner who cannot easily
cross a room cannot be asked to keep a phone in Bluetooth range and charged for
her to work. Accessibility is a headline track
(`docs/LIFE_TASK_PARTNER.md` §4), so the design carries this rule: Bluetooth is
offered last, suggested never, hidden entirely on a machine with no radio, and
**nothing else in the system requires it**.

### Control and audio are separate paths

On both phone-backed options:

- **ADB controls the call** — dial, answer, hangup, DTMF, read state. Over
  Wi-Fi, so the phone can be in another room.
- **A wired headset link carries the audio** — the phone routes call audio to
  what it believes is a headset, which is ordinary OS behaviour.

This sidesteps the restriction that kills every naive approach: since Android
10, third-party apps cannot capture the remote party's audio.
`VOICE_UPLINK`/`VOICE_DOWNLINK` require `CAPTURE_AUDIO_OUTPUT`, a
signature-level permission, and **ADB and Shizuku cannot grant it** — they grant
runtime permissions only. The common workaround is speakerphone-into-the-mic:
echo-prone, half-duplex, and audibly not human. Routing to a headset — wired or
Bluetooth — is not a workaround but the supported path, and it is full-duplex.

### What an Android VM can and cannot do

Running Android on the host removes proximity, phone battery, and pairing
entirely, and it is the right home for mobile-only apps. But it **cannot place a
cellular call on the owner's number**, and no amount of engineering changes
that: a VM has no baseband, and carrier voice registers to IMS authenticated
against the long-term secret inside the physical SIM (ISIM/USIM AKA). Cloning
the phone's software does not clone its SIM. So the VM calls over IP through an
app whose number lives in the cloud.

(Windows note: WSA reached end of support on 5 March 2025, so this means
BlissOS/Android-x86 under a hypervisor, or Waydroid inside a Linux VM.)

Narrowband applies to every option: 8 kHz on the wire cuts fidelity, and it also
masks synthesis artifacts, because everyone on a phone line already sounds like
a phone line.

### Gateway integration

`ChannelKind.PHONE` and `ChannelKind.SMS` join the existing enum and register
through `channel_registry.py` like any messenger. This is deliberate: the
proactive-messenger loop, session bridge, allowlists, and rate limits already
work, so mid-call escalation ("clinic wants Tuesday instead — ok?") is an
existing code path, not a new one.

## The human bar

"Passes for human" is a measurable gate, not a vibe. The giveaway is almost never
synthesis quality — it is the two-second silence before she answers.

| Metric | Target |
|---|---|
| Time-to-first-audio after counterpart stops | p50 <= 600 ms, p95 <= 1000 ms |
| Barge-in: playout stops after speech onset | <= 150 ms |
| False interrupt (she talks over them) | < 3% of turns |
| False wait (dead air > 1.5 s) | < 5% of turns |
| Unfilled dead air | never > 800 ms without backchannel |
| Blind A/B, 10 naive listeners, 60 s call | >= 70% cannot tell |

Techniques that buy this: streaming everything (no full-utterance waits),
semantic endpointing rather than silence timers, first-syllable-out synthesis,
and covering think-time with the noises humans actually make — "mm-hm", "sure,
one sec", a breath. Latency hiding is a feature of the design, not a hack in it.

Tiering: this bar is set on the dev host (RTX 3080 12 GB). `runtime/gpu_probe.py`
already sizes RMB from VRAM; the voice stack uses the same probe to pick an
engine tier, and **tells the owner in plain words** when their hardware cannot
hold the bar rather than silently sounding robotic.

## Her voice

Two separate things that must not be confused.

**Her own voice — persists and evolves.** A voice identity record in
`~/.remedy/voice/identity.json`: a reference sample she owns, a prosody profile
(pace, pitch range, warmth, articulation), and the configured gender (female by
default, and any evolution stays inside that choice). The identity is stored
engine-independently — a reference sample plus numeric parameters — so an engine
swap re-derives the embedding and **she still sounds like herself afterward**.
Evolution is slow and bounded: hard caps per axis (roughly +/-15% pace, +/-2
semitones), every change journaled and reversible. She may grow; she may not
become a stranger.

**The owner's voice — borrowed, never worn.** `clone.py` holds owner samples
encrypted at rest (DPAPI, same store as provider keys). A grant names a task, has
an expiry, and is revocable in one sentence. She uses it for that task and
reverts to her own voice the moment it closes. It is never a default, never
silent, and never survives the task that justified it.

## Setup is a conversation

There is no wizard section for this and there will not be one. Provisioning is a
tool surface Remedy calls during ordinary talk, following one pattern:

> **probe -> explain in a sentence -> ask only for what a human must do -> verify -> confirm**

Concretely, when the owner says "I want you to be able to make calls":

1. She probes: `adb devices`, Bluetooth radio, audio endpoints, VRAM.
2. She reports only what is missing, in plain language — not a checklist.
3. For anything only a human can do (enabling USB debugging, accepting the RSA
   prompt, pairing Bluetooth), she gives the exact taps, waits, and re-probes.
4. Secrets she asks for in chat, writes to the DPAPI store, and never echoes.
5. She confirms with a real test — a call to a known-good number, not a green tick.

Everything she configures she can explain and undo. Failure states are sentences
("this PC has no Bluetooth radio; a ~$10 USB dongle unlocks calls — want me to
find one?"), never error codes.

## Phases

Ordered as the owner chose: prove the voice first, then talk, then listen.

**Phase 0 — Bench. Done (Aug 2026).** `fake.py` transport, 8 kHz narrowband
simulation, the full duplex pipeline, and the human-bar harness. No telephony,
no hardware, no minutes. `sip_direct.py` is the loopback path (UDP PCM on
127.0.0.1, nobody is called). Results below.

**Phase 1 — A line that does not depend on anything nearby.** The setup
conversation (`offer_lines`) plus the first two backends: `vm_voip` (Android VM
on this host, calling through a VoIP app) and `sip_baresip` (her own number,
pinned in `runtime/catalog.py` and fetched to `~/.remedy/bin` like ripgrep and
llama-server). Disclosure policy, call checkpoints, live transcript.
**Exit:** she books a real appointment end-to-end, with the owner approving from
a message, and the phone could have been switched off the whole time.

**Phase 2 — She takes a call.** Ring handling, screening policy, answering when
the owner is busy, escalation, message-taking. **Exit:** a week of real inbound
with no call the owner wishes she had not taken.

**Phase 3 — The owner's real number.** `phone_wired` (ADB over Wi-Fi for
control, cable for audio) and SMS/2FA relay from the real SIM.
`bluetooth_hfp` lands here too as the convenience option, never the default.
**Exit:** every backend serves the same `line.py` interface with no caller-side
changes.

**Phase 4 — The apps we cannot otherwise reach.** Deeper into the VM: banking,
government and health apps with no web equivalent, notification awareness,
app automation.

## What Phase 0 measured

`python -m remedy.telephony.bench` runs scripted calls over the simulated
circuit. **Both scenarios pass.** Median of three runs on an otherwise idle
host; the spread across runs is under 20 ms on every latency figure.

| Metric | clinic-booking | ivr-menu | Bar |
|---|--:|--:|--:|
| Turns completed | 5 of 5 | 4 of 4 | all |
| Turns with no answer | 0 | 0 | 0 |
| Time to first audio, p50 | 401 ms | 401 ms | 600 ms |
| Time to first audio, p95 | 420 ms | 419 ms | 1000 ms |
| Time to the answer itself, p95 | 1035 ms | 906 ms | 2200 ms |
| Barge-in | 0 ms | — | 150 ms |
| Talks over people | 0.0% | 0.0% | 3.0% |
| Long gaps | 0.0% | 0.0% | 5.0% |
| Worst uncovered silence | 420 ms | 419 ms | 800 ms |
| Frames late inside a chunk | 0 | 0 | < 40 ms |

The first two rows are there because they used to be the gap in the gate rather
than a line in the table. A turn she never answered has no latency, so every
percentile skipped it and nothing else counted it — five turns with one answered
scored a clean pass. So did a scenario that hung and got scored on whatever it
had managed first, which is what `ivr-menu` did on every run: it stalled after
one exchange and reported the timing of that one exchange as the result.

Read honestly: the transport, turn-taking, speculation, backchannels and
barge-in are the real implementation; STT, the model, and synthesis are stubs
that *wait* their declared latency rather than compute. Phase 0 proves the
**timing architecture**, which is what fails first. It says nothing about
whether the voice sounds human to an ear, or whether recognition survives an
accent — real engines drop into the same harness unchanged.

### Three findings that changed the design

**1. A naive loop cannot pass, and shortening the endpointer does not save it.**
Measured, time-to-answer is `hangover + ~730 ms` of engines:

| Endpointer hangover | 150 ms | 350 ms | 700 ms | 900 ms |
|---|--:|--:|--:|--:|
| Time to answer | 887 ms | 1083 ms | 1427 ms | 1626 ms |

The hangover is the larger half of the latency at 700 ms, but cutting it to
150 ms still lands at 887 ms — well over the 600 ms bar — and starts answering
into people's mid-sentence pauses. Hence speculation (start the answer during
the pause, hold it back until the endpoint confirms) and backchannels timed from
when sound stopped rather than from when the endpointer noticed.

These are 140 ms higher than first published, at every hangover, because the
first measurement charged synthesis its 140 ms warm-up once per call instead of
once per request. A constant offset across four independent rows is what made
the cause obvious.

**2. The engine budget is ~730 ms, with headroom under 2×.**

| Engines | 1.0× | 1.25× | 1.5× | 2.0× |
|---|--:|--:|--:|--:|
| Verdict | pass | pass | pass | fail |

At 2× the answer lands so late that every turn is a long gap and the worst
uncovered silence reaches 1030 ms, against an 800 ms bar. Note the failure mode:
long gaps, not talking over people. It used to read as the latter, because the
scripted far end had been mistaking her backchannels for a finished turn and
cutting in on top of the answer — a harness bug, not the engines.

This is still a hard constraint on engine selection: STT + model + first audio
must total around 730 ms on the owner's machine. Kokoro and Chatterbox-Turbo
fit; a large LLM-based synthesizer or a cloud model with 800 ms
time-to-first-token does not, on its own.

**3. The transport costs nothing; the engines cost everything.** Per 20 ms
frame, against a 20 ms budget:

| Stage | Median | Share of budget |
|---|--:|--:|
| Energy / turn detection | 0.014 ms | 0.07% |
| mu-law round trip | 0.058 ms | 0.29% |
| 16 kHz resample + codec | 0.217 ms | 1.09% |
| 24 kHz resample + codec | 0.353 ms | 1.77% |

The always-on path is **0.35% of one core** at 16 kHz and **1.8%** at 24 kHz —
still roughly 55x headroom on the expensive path. Pure-Python codec and VAD are
not a bottleneck and do not need numpy or a C extension. Every real constraint
is in STT, the model, and synthesis, which is where the ~620 ms budget above
belongs.

The 24 kHz row is the one that matters in practice: Chatterbox speaks at 24 kHz
and a phone line does not, so every frame she says goes through that conversion.
`narrowband.resample()` handles any rate pair; the 2x paths stay exact.

**4. Windows cannot pace a 20 ms frame by default.** `asyncio.sleep(20 ms)`
takes **31.3 ms** — a 56% overshoot that makes the RTP frame interval
unrepresentable, so playout stutters and arrives late. With the timer resolution
raised it is 20.6 ms. `timing.call_timing()` raises it for the duration of a
call and paces on absolute deadlines so error cannot accumulate. This is a
production requirement, not a bench detail.

Contention is a separate problem from granularity, and `call_timing()` also
joins the Windows "Pro Audio" scheduling class (MMCSS) that media apps use.
Measured honestly, that part earns less than expected: under 40 busy processes
on 20 cores the overshoot was already only 1.45 ms at p95, and Pro Audio
improved it by ~10% — within noise. It is kept because it costs nothing and
should matter more on weaker machines, not because it was the fix.

Note what this is *not*: the Time Crystal (`core/metabolism/time_crystal.py`) is
memory promotion across `turn → session → project_week → life`. Frame pacing is
about which millisecond the OS wakes a thread, during which Remedy is not
running at all. A diary does not keep eighth notes.

**5. The harness has to be faster than what it measures.** Generating a
three-second utterance cost ~20 ms of pure Python — a full frame — blocking the
event loop at the exact moment the far end began speaking, and distorting the
timings the bench exists to measure. Cached, it is ~0.0002 ms. A measurement
harness that competes with the thing under test produces flaky failures that
look like product bugs; this one did, and the caching turned an intermittent
"talks over people" into a reproducible one, which is how it was found.

### Bugs the bench caught before any hardware existed

- **She answered half a sentence.** A mid-sentence hesitation is shorter than
  the hangover, so the detector never leaves the speaking state and never
  re-fires onset — a speculation begun during the pause survived to be
  committed. Speculation is now invalidated by audio resuming.
- **A call that opened mid-speech went half-deaf.** With almost no history the
  noise floor was computed from the first frame; if that frame was speech (they
  answer mid-word, an IVR starts on connect) the floor latched onto speech
  energy and later utterances read as silence for the rest of the call.
- **Two thresholds disagreed.** The loop ran its own energy test while the
  detector ran another; on a quiet frame one read "they resumed" and the other
  "they stopped", discarding one turn and committing another on the same 20 ms.
  The detector is now the single authority.
- **A backchannel could game the bar.** "Mm-hm" set time-to-first-audio while
  the actual answer was three seconds away. Substantive speech is now timed
  separately, and a backchannel over a pause that never became a turn is
  counted as a backchannel rather than scored as one.
- **The scripted interruption landed on the wrong millisecond.** "Interrupt her
  500 ms in" measured from her *backchannel*, so the far end cut in at ~900 ms —
  the same instant her actual answer began — and she was scored as talking over
  someone roughly a quarter of the time. The counterpart now tracks runs of
  speech separated by a 250 ms gap, so a backchannel is its own run and an
  interruption means 500 ms into what she is actually saying. A bench that
  measures the wrong moment is worse than no bench: it fails the product for
  the harness's mistake.

### And a second pass, from reading it

The five above were found by running the bench. The rest were found by reading
it, and the harness ones are worse than product bugs, because each made the
bench *quieter* rather than noisier — a gate that passes what it should fail
teaches you nothing.

**In the bench:**

- **A turn she never answered scored as a pass.** It has no latency, so every
  percentile skipped it, and nothing else counted it: five turns with one
  answered came back with zero failures. Unanswered turns are now counted as
  the longest wait there is, and named in the failure list.
- **A call that hung scored as a pass.** The timeout was logged and the call
  scored on whatever it had managed first. `ivr-menu` had been doing exactly
  that on every run since the scenario was written — stalling after one
  exchange and reporting that one exchange as the result.
- **…and the reason it hung was a 40 ms window.** A turn had to be 500 ms of
  audio to count, to stop a "mm-hm" being read as an answer. But the filler is
  260 ms and a one-word reply — "Appointments." — is 300 ms, so the threshold
  sat above both: the scripted far end waited for a turn that could not arrive.
  It is 280 ms now, and a test pins it between the two using the audio the
  bench actually generates.
- **The median was not the median.** `round(k + 0.5)` stands in for `ceil`
  everywhere except on exact half-integers, where Python rounds to even — so
  the median of two turns came back as the slower one, and of six as the
  fourth. Every latency figure in the table above was computed that way.
- **The playout gate measured the engine, not the wire.** Every late frame was
  the *first* frame of a synthesis chunk; not one was late inside a chunk. The
  counter was reporting a synthesizer warming up as a transport stutter, while
  the per-turn reset threw away the history that would have shown it: 28 late
  frames and a 60 ms worst case reported as 1 and 22.6 ms. Pacing is now judged
  within a contiguous run, and the audible gap between runs stays where it
  belongs, in dead air.
- **Synthesis warm-up was charged once per call.** One `ToneTts` serves every
  turn and its "first chunk" flag was per instance, so every turn after the
  first got its opening chunk 100 ms early — flattering exactly the
  time-to-first-audio number Phase 0 is gated on.
- **The gate itself ran unguarded.** Every other timing test skips when the host
  cannot hold a 20 ms frame. The one the phase is judged on did not, so load
  manufactured the overlaps it measures and it failed intermittently with
  nothing wrong in the pipeline.

**In the product:**

- **One engine hiccup ended the call.** A responder or synthesis error during a
  speculative turn left the state at SPECULATING with the task already dead;
  the next endpoint committed to a task that no longer existed, and since a turn
  can only begin from LISTENING she never spoke again. One transient error, and
  the rest of the call was silence.
- **A 24 kHz voice could not reach the wire.** Playout framed at the line rate
  and labelled at the synthesizer's. Fine while both were 8 kHz; with Chatterbox
  at 24 kHz each frame carried 6.7 ms of audio paced as though it were 20 ms —
  playout at a third speed, the speech budget counted three times over, and a
  rate the backend rejects outright. `narrowband.resample()` now converts any
  rate pair and playout frames what it actually sends.
- **A click of silence in every sentence.** Synthesis chunks do not land on
  20 ms boundaries, and each tail was padded out with zeroes rather than carried
  into the next chunk — up to 20 ms of digital silence spliced into the middle
  of her voice, at every chunk a real synthesizer emits.
- **Hanging up could hang the call object.** The end-of-call sentinel was posted
  with a suppressed QueueFull while ordinary frames drop the oldest to make
  room. A far end that rings off while the consumer is behind left the loop
  awaiting a producer that had gone.
- **Semantic endpointing switched itself off on long sentences.** The model's
  veto was bounded by the length of the utterance rather than by how long the
  veto had lasted, so anything past ~2.4 s — most real sentences, and exactly
  the trailing-off case the model exists for — silently fell back to a silence
  timer.
- **She could not see a radio she had.** The Bluetooth probe closed the radio
  handle with a `bthprops.cpl` export that does not exist, so on any PC that
  *does* have one the call raised, the catch turned it into "I cannot tell", and
  she told the owner she could not make calls. (This host has no radio, so the
  blocker list below was right by luck.)
- **Two lines were offered but never probed.** `line_options()` offers four ways
  to get a line; `probe_all()` checked two of them, and `can_call` recognised
  only the phone bridge and SIP. A signed-in Android VM or a wired phone — both
  complete paths — would have shown as ready on the menu while she insisted she
  could not call anyone. A test now requires every offered line to be probed.
- **She could not find an engine she had downloaded.** `probe_sip` looked for
  `baresip`, never `baresip.exe`, and skipped the `~/.remedy/bin` fallback
  entirely unless a home was passed explicitly — which is where Phase 1 puts it,
  on the platform this ships on first.

## Boundaries

- **No copyleft, and nothing bundled.** Every dependency permissive, pinned,
  listed in `docs/THIRD_PARTY.md`, and downloaded only when asked for.
- **Never an emergency service.** Stated in the terms, and never softened by a
  setting.
- **Hard call checkpoints**, mirroring `approvals.SENSITIVE_COMPUTER_RE` and
  bypassable by no approval mode: never speak card numbers, SSNs, passwords, or
  2FA codes aloud; never agree to a payment, contract, or cancellation; never
  answer identity-verification challenges without the owner present.
- **Never claims to be human when directly asked.** Disclosure is configurable;
  this line is not.
- **Recording follows the owner's policy** and defaults to a notice, because
  two-party-consent jurisdictions exist and the owner, not Remedy, carries that
  exposure.
- **One call at a time**, owner-initiated or inside an owner-set policy. No
  dialing lists, no campaigns, no unattended outbound to strangers.
- **Audio stays local.** If a cloud realtime pipeline is ever added it is opt-in,
  per-call, and announced.

## Known blockers on this host

Found by probe, Aug 2026 — all cheap, none discovered late:

1. **No Bluetooth radio on this PC.** No PnP Bluetooth device; `bthserv`
   stopped. No longer a blocker for anything — Bluetooth is simply not offered
   on this machine, and Phase 1 does not touch it.
2. **USB debugging is off.** `adb devices` is empty though platform-tools is
   installed at `scoop/apps/android-clt`. Thirty seconds of taps — and by design
   it must be done by hand, which is exactly the conversational-setup pattern.
3. **Phone is MediaTek-based** (`VID_0E8D`, reports as `G84`, MTP only). Vendor
   skins vary on notification and SMS access; the bring-up probe must detect
   rather than assume.

## Open risks

- **Injecting her voice as the VM's microphone.** Capturing VM output is
  ordinary WASAPI loopback, but presenting synthesized audio *to* the VM as a
  microphone normally wants a virtual audio driver, and the common Windows ones
  are proprietary. Running the VM under a hypervisor we launch ourselves avoids
  this — the audio backend can be wired directly — which is a reason to prefer
  that over a stock emulator.
- **VoIP provider terms.** A cloud number can be rate-limited, region-locked, or
  withdrawn; `sip` exists partly so the owner is never trapped on one.
- **Owner picks up the handset mid-call** on the phone-backed options. Needs a
  clean handoff, not an error.
- **Turn-taking on a real IVR** differs from turn-taking with a human. Both need
  bench fixtures before Phase 1 is trusted.
