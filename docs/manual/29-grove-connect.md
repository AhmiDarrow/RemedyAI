# Grove Connect — phone remote for this PC

Grove Connect lets a phone you pair **drive this computer**. The phone is a
remote control, not a second Remedy. Chat, memory, Vault, and the model stay
on the PC. **Off by default.**

This is **not** Telegram, Discord, or any messenger. Those stay under Settings
→ Messengers. Connect is a second listener you turn on when you want the phone
in your hand.

## What you get

| | |
|--|--|
| **Default** | Off. Nothing listens until you turn Connect on. |
| **Pairing** | A QR on this PC, good for **60 seconds**. Scan it from the phone app. |
| **Who runs Remedy** | This PC. The phone is not Remedy and does not keep your chats. |
| **Network** | A **chosen IPv4** you pick. The local API on `:7400` stays loopback. |
| **Phone app** | Sideload the APK next to the Desktop download when it ships. Not a store SKU. |

You do **not** install Tailscale, a VPN, or a mesh client for Connect. Same
Wi‑Fi is enough at home. For **mobile data** (LTE/5G, another network), run an
**owner-run relay** on a machine you control that has a public IPv4 (below).
The phone and this PC both dial that relay. The session is still Noise-encrypted
at the ends; the relay only copies ciphertext.

## Turn it on

1. On the PC: **Settings → Connect**. It is **off** until you switch it on.
2. Show the QR (it expires in 60 seconds — ask for a new one if it times out).
3. Scan it with the phone app.
4. Use the phone as a remote for **this** Remedy. Money, passwords, send, and
   delete still **stop for you** — Connect does not waive checkpoints.

Turn Connect off when you are done. Pairing is not a standing open door.

## What it is not

- **Not** the messenger gateway (Telegram / Discord / Slack / …).
- **Not** a second copy of Remedy on the phone.
- **Not** a bind of `:7400` onto your LAN. The local API stays on `127.0.0.1`.
- **Not** a way to skip approvals. Computer-use poller and local-bootstrap
  token fetch are **refused** on Connect.

## Network (chosen IPv4 only)

Connect listens on **one IPv4 address you choose** — typically this PC's LAN
address (`192.168.x.x`), not `0.0.0.0`, not `*`, not `::`. Settings prefers a
LAN address. `127.0.0.1` stays in the list for this computer only; a phone
cannot reach it. If you pick loopback on purpose, Connect warns you.

The usual local API (`http://127.0.0.1:7400/`) does not move. Browser Web UI
and Desktop keep talking to loopback. Connect is a **second listener**.

On the same Wi-Fi / Ethernet, the PC can advertise `_remedy-connect._udp` so
the phone finds it. That advertisement carries a short **host key hash**, not
your API token, not a Bearer header, and not the pairing secret.

## Owner-run relay (mobile data)

If the phone is on cellular or another Wi‑Fi, it cannot reach `192.168.x.x`.
Run a small relay **you own** on a VPS or other always-on box with a **public
IPv4**. It copies framed blobs between two peers that share a session id. It
**does not decrypt** and it **does not log** the payload. There is no Remedy
cloud in the path.

On the relay machine:

```bash
remedy connect-relay --host 203.0.113.10 --port 7402
```

`--host` is that machine's chosen IPv4 (not `0.0.0.0`, not `*`, not `::`).

On this PC, Settings → Connect → **Owner relay**: `203.0.113.10:7402`. Pair the
phone. The QR includes `relay=…` (never a Bearer or API token). The phone tries
the LAN first (fast fail off-network), then the relay.

Reconnect from LTE uses the stored relay and the paired device key — not a
fresh QR unless you unpair.

Same as:

```bash
python -m remedy.connect.relay --host 203.0.113.10 --port 7402
```

## Phone app

When the Android APK is published it will sit **next to the Desktop
installers** on the GitHub Releases page — sideload it, same as any owner-built
APK. There is no separate product SKU and no store listing to wait on.

Until then, Connect settings on the PC can still be off, and nothing extra is
listening.

## Related

- [Grove](22-grove) — partner home on this PC
- [Security & data](04-security-and-data) — loopback API, checkpoints
- [CLI & API](10-cli-and-api) — `remedy` commands
