# Telephony — her voice on the wire

**Status:** planned. This build cannot call a real number.
**Code:** `native/go/httpapi/telephony.go` and its test.
**Terms:** [TELEPHONY_TERMS.md](./TELEPHONY_TERMS.md) — agreed once, in
conversation, before any call.

## What exists

Only the setup half exists. The runtime can:

- Report telephony status: `GET /api/telephony/status`. It says the terms
  state, the line chosen, and the four line options, and it reports
  `real_line: false`.
- Record that you agreed to the phone terms: `POST /api/telephony/terms`. The
  record is `~/.remedy/telephony/consent.json`. If the terms change, the old
  agreement is marked stale and she asks again.
- Record which line you picked: `POST /api/telephony/choose`. The record is
  `~/.remedy/telephony/line.json`.

Nothing here dials. No SIP engine, no phone bridge and no Android image is
installed or started by this build.

## The four lines she offers

She does not pick for you. She says what each one costs and what the catch is.

| Line | What it is | The catch |
|------|------------|-----------|
| **Her own number** (`sip`) | A SIP number over a trunk | A dollar or two a month; works with your phone off |
| **A calling app on this PC** (`vm_voip`) | Android running here, calling through an app | No recurring cost; the number belongs to a cloud service |
| **Your phone, on a cable** (`phone_wired`) | Your real number, over USB | About ten dollars for the adapter; the phone stays plugged in |
| **Your phone, wirelessly** (`bluetooth_hfp`) | The same over Bluetooth | The only one that can drop out; Windows only for now |

Bluetooth is offered last and is never the default.

## Rules that hold

- Nothing for calls is bundled in the installer. Anything extra would be
  fetched only when you ask, after she names its licence and size.
- She agrees to the terms with you first, in conversation.
- She says she is an assistant, does not claim to be human if asked, does not
  read out card numbers or one-time codes, and does not agree to a payment
  without you.
- She is not an emergency service.

## Not built

Placing or answering real calls, turn-taking on a live line, and the SIP,
Android and phone bridges are not in this build. The target timings in the
manual ([Telephony](manual/24-telephony.md)) are goals, not measurements.
