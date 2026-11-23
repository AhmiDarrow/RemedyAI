# RemedyConnect — phone remote for this PC

RemedyConnect lets a phone you pair **drive this computer** through an
encrypted tunnel. The phone is a remote control, not a second Remedy. Chat,
memory, Vault, and the model stay on the PC. **Off by default.**

This is **not** Telegram, Discord, or any messenger. Those stay under Settings
→ Messengers. Connect is a second listener you turn on when you want the phone
in your hand.

## What you get

| | |
|--|--|
| **Default** | Off. Nothing listens until you turn Connect on. |
| **Pairing** | A QR on this PC, good for **60 seconds**. Scan it from the phone app. |
| **Same Wi‑Fi** | Works instantly — a direct LAN link, no extra setup. |
| **Mobile data** | Works via **Tailscale** (free): install the app on this PC and phone, sign into the same account, scan once. No VPS, no relay server. |
| **Who runs Remedy** | This PC. The phone is not Remedy and does not keep your chats. |
| **Phone app** | Sideload the RemedyConnect APK next to the Desktop download. Not a store SKU. |

## What the phone shows

The phone app is **a line to Remedy**, not a small copy of the desktop. It
has three tabs at the bottom:

| Tab | What it's for |
|-----|---------------|
| **Talk** | Remedy's own conversation (the same one Grove shows on the PC). Type, or tap the **microphone** to speak. Replies stream in as she writes. If she is already replying when you open the app, you see it live. |
| **Work** | What Remedy is doing: her side jobs and their state, your goals, your routines, and **While you were away**. You can **Stop** a side job, put a goal on or off **autopilot**, and pause, resume or **Run now** a routine. |
| **More** | The full remote from earlier versions: any chat, chat history, the approvals list, the terminal (only if you allowed it), goals, and the model picker / settings. |

**When Remedy needs you**, one question shows at a time above Talk and Work:
what she wants to do in plain words, then **Yes**, **No** or **Explain**
(Explain shows exactly what would run). "1 of 3" means more are waiting; tap
**Later** to tuck them away. A yes from the phone covers **that one action
only**. Choose "allow for this chat" on the PC if you want that.
**Money, password and send** steps get their own look and always ask, every
time, in every mode.

**Notifications:** if the app is open in the background, your phone shows
*Remedy has a question for you*. The lock screen only says *Remedy needs
you*. Tapping the notification opens the app, which asks for your screen lock
first. The notification never answers anything for you. Swiping the app away
ends the remote session, and with it these notifications.

**Starting and stopping:** turning on autopilot, turning a routine back on, or
running one now asks *are you sure* on the phone first. Pausing and stopping
never start new work, so they go straight through. To start a new side job or
routine, ask Remedy in **Talk** ("keep an eye on flight prices for me",
"remind me every morning…"). That way the request goes through her.

**If the connection drops** (Wi‑Fi to mobile data, a tunnel, the PC
sleeping), the app stays where it was and shows *Reconnecting…*. A reply that
was streaming picks up where it left off once the PC is back. If the PC is
off for a long time, the full reply appears in Talk when it returns.

The PC decides which parts the phone may use (**Settings → Connect →** the
phone's panes). A part that is turned off shows a short note saying so. The
terminal stays off until you turn it on for that phone. If a saved PC is
temporarily offline, tap **Reconnect** instead of pairing again.

## Turn it on — 3 steps

1. **On the PC**: **Settings → Connect** and switch it on. (It is off until
   you do.)
2. **Tailscale for mobile data** (optional but recommended): the Tailscale
   card in Connect settings shows the PC's Tailscale state. If it says
   *not installed*, tap **Install Tailscale (free)** — Remedy downloads the
   official installer and launches it for you; approve the prompt, then sign
   in when the app opens (same account as your phone).
3. **On the phone**: install the free **Tailscale** app from the Play Store
   and sign into the **same account** as this PC. Then open RemedyConnect and
   scan the pairing QR. Done — it works on Wi‑Fi *and* mobile data.

The QR expires in 60 seconds — ask for a new one if it times out. Pairing is
not a standing open door: **revoke** a phone anytime in Settings → Connect.

## Network (chosen IPv4 + tailnet)

Connect listens on **one IPv4 address you choose** — typically this PC's LAN
address (`192.168.x.x`), never `0.0.0.0`, never `*`, never `::`. `127.0.0.1`
stays in the list for this computer only; a phone cannot reach it.

When Tailscale is connected, Connect also binds the **tailnet address**
(`100.64.0.0/10`) and the pairing QR advertises it (`ts=`). The phone tries
Tailscale first — it works on Wi‑Fi and, via Tailscale's DERP relays, on
mobile data too. LAN stays the fast path on the same Wi‑Fi.

The usual local API (`http://127.0.0.1:7400/`) does not move. Connect is a
**second listener**.

On the same Wi‑Fi / Ethernet, the PC can advertise `_remedy-connect._udp` so
the phone finds it. That advertisement carries a short **host key hash**, not
your API token, not a Bearer header, and not the pairing secret.

## Owner relay (advanced, optional)

Tailscale is the normal answer for mobile data. There is still an older path:
run a small relay **you own** on a box with a public IPv4
(`remedy connect-relay --host 203.0.113.10 --port 7402`) and paste
`203.0.113.10:7402` into Settings → Connect → **Owner relay**. The phone tries
LAN and tailnet first, then that relay. It copies encrypted bytes only — it
cannot read chats and does not log them. Leave the field empty unless you host
one on purpose.

## Security

- **Encrypted end to end**: sessions are Noise-encrypted between the phone and
  this PC; Tailscale adds its own wire encryption on top. No Remedy cloud in
  the path.
- **Checkpoints stay**: money, passwords, send, and delete still stop for you.
  Connect does not waive approvals.
- **Not a bind of `:7400`**: the local API stays on `127.0.0.1`.
- **Not a way to skip approvals**: computer-use poller and local-bootstrap
  token fetch are **refused** on Connect.
- **No PII in the QR**: the pairing text carries a host key hash, a one-time
  secret, LAN/tailnet addresses, and an expiry — never tokens, keys, or
  account material.
- **Local hardening**: the phone bounds QR, HTTP, MQTT, and loopback-proxy
  inputs; limits concurrent local proxy clients; and excludes device keys and
  pairing records from Android backup and device transfer.
- **Reliable cleanup**: leaving the scanner releases the camera, closing a
  remote closes live streams, and stalled encrypted connections automatically
  redial instead of remaining falsely green.
- **Server isolation**: closing, losing, or revoking the phone closes only its
  encrypted session. If the managed desktop server ever exits unexpectedly,
  the desktop records the exit status and makes three bounded recovery attempts.

## Phone app

The RemedyConnect APK sits **next to the Desktop installers** on the GitHub
Releases page — sideload it, same as any owner-built APK. There is no separate
product SKU and no store listing to wait on.

Until then, Connect settings on the PC can still be off, and nothing extra is
listening.

## Related

- [Grove](22-grove) — partner home on this PC
- [Security & data](04-security-and-data) — loopback API, checkpoints
- [CLI & API](10-cli-and-api) — `remedy` commands
