# House rules

House rules are how you and Remedy agree to live together: names, tone,
language, privacy, how proactive she is, token-saving, consent, other bots,
and updates. They are yours. She can help you set and change them. Other
bots and inbound peers can never change them.

## First run

On a fresh install, the installer only places the app files. The first time
you open Remedy, a progress screen downloads and verifies her local mind.
As soon as the mind is ready, she starts a short, friendly conversation and
walks through house rules — including the things the old setup wizard asked
(provider and API key, workspace, local muscle, language, interop).

Every question is skippable. **Skip all** keeps the defaults. At the end she
shows a short summary so you can confirm or adjust.

**Traditional setup** is always visible. It is the same settings as a
polished form. You can switch between the conversation and the form at any
point; answers are kept in a shared draft and are not lost.

If the mind cannot start (a failed download or verify, or not enough memory),
Remedy opens Traditional setup, explains why, and offers a retry. Running
without a GPU is fine — the mind can use the CPU.

Existing installs and `remedy --skip-setup` are not pulled back into
first-run setup. Those sessions are marked inherited and are not nagged
in chat. After this update, existing installs get one desktop pop-up
that makes the case for going over house rules (smarter token saving,
local muscle sharing, and talking with other bots over A2A and MCP).
**Let's go** starts the conversation; **Not now** dismisses it.
Either choice is final — the pop-up shows only once. You can still say
“let’s go over house rules again,” or run `/house-rules`.

The full first-launch path, including what the installer does and does not
do, is in [SETUP.md](SETUP.md).

## What she asks

| Topic | What it covers | Default |
|-------|----------------|---------|
| Names | What she calls you, what you call her, her gender | You unnamed; she is Remedy, female |
| Language | UI and conversation language | This computer’s language (`auto`) |
| Tone | Persona and sarcasm | Balanced (`default`) |
| Provider | Cloud / local muscle, model, optional API key | Demo until you add a key |
| Workspace | Default project folder | Empty |
| How proactive | Reach-out between visits, drives | Reach-out off; drives follow existing defaults |
| Privacy and audit | Privacy mode, inner-work audit | Off / existing defaults |
| Token saving | Easy chats and pictures on this PC; muscle gets a description, not the raw image | Do not always send raw images |
| Local muscle and the GPU | How to share the GPU if a local muscle is running | `auto` |
| Consent | Money, sends, and deletes always need a yes. You can also ask her to confirm other tools. | `approval_mode=auto`, `trust_profile=balanced` |
| Other bots | Inbound interop and auto-reply | Inbound off; auto-reply is stored but inert until inbound is on |
| Updates | In-app updates only; check automatically or only when you ask | `auto` |
| Start with the computer | Launch at login | Off |

Interop and local-muscle GPU answers are stored under `[advanced]` even when
those features have not landed yet, so they take effect once they ship.
This document does not define those feature knobs.

## Talk to change them later

In an ordinary chat you can say things like “call me Ahmi,” “be more concise,”
or “don’t reach out unless I ask.” Remedy shows the change and applies it.

- Ordinary preferences (name, tone, language, launch at login, `update_check`)
  can apply on a clear request.
- Sensitive rules always need an explicit confirmation, even if you have
  turned the permission knob down: consent (`approval_mode`, `trust_profile`),
  inbound interop, money / sends / deletes, and privacy (`privacy_mode`,
  `inner_audit`).
- `house_rules_permission = confirm_all` asks her to confirm every change.
- `plain` still confirms the sensitive set above.

Peers, messengers, A2A, MCP, hive, and partner children cannot change house
rules. Those calls are refused; they are never turned into an approval prompt.

## Revisit

Say “let’s go over house rules again,” or run `/house-rules` (alias
`/houserules`). That opens the same conversation (or the form, if you prefer)
without wiping what you already set, unless you skip or change a topic.

`/house-rules` without “again” / “revisit” prints the current summary and
how to change things.

## Where they are stored

Under your Remedy home (usually `~/.remedy/` or `%USERPROFILE%\.remedy\`):

| File | Role |
|------|------|
| `house_rules.toml` | Current answers plus provenance (who set each key, and when) |
| `setup_session.json` | Shared first-run draft (conversation ↔ form) |
| `house_rules_pending.json` | A proposed change waiting for confirmation |
| `house_rules_prev.json` | Previous version, for rollback |
| `house_rules_audit.jsonl` | Append-only audit of applies, declines, and rollbacks |
| `config.toml` | The live settings those answers map to |
| Secure store | Provider API keys — never the files above |

API keys and other secrets are written only to the existing secure store
(via `set_provider_key` / Settings). They are never written to
`house_rules.toml`, the setup draft, the audit log, or tool logs. A GET of
the setup session may list that a key is set (`secrets_set`), never the value.

Root-level settings go in `config.toml`. Interop knobs (`interop_*`), local-muscle knobs (`muscle_*`, `mind_keep_gpu`, `mind_handles_easy_chats`), and other organism tunables live under `[advanced]` and bind to the live advanced-knob table as soon as they are saved.

## Rollback

After a change, you can ask Remedy to undo it. She restores the previous
`house_rules.toml` and the matching config values. The audit trail records
the rollback. Secrets are not replayed from the audit file (they were never
written there).

## Advanced knobs

In `config.toml` under `[advanced]`:

| Knob | Default | Meaning |
|------|---------|---------|
| `onboarding` | `true` | When false, first-run does not auto-offer the conversation (Traditional setup still works). |
| `onboarding_reask` | `false` | When true, if the first offer was ignored (`status = offered`), she may offer once more. She does not re-ask after complete, skipped, or inherited. |
| `house_rules_permission` | `confirm_sensitive` | `confirm_sensitive`, `confirm_all`, or `plain` (sensitive keys still confirm). |
| `house_rules_audit` | `true` | When false, new audit lines are not written. Existing files are left alone. |

These follow the app's rule: no forced nags. The first-run auto-offer is
a single non-repeating prompt. Existing installs get one update pop-up
(Let's go / Not now) and then are left alone.

## Tools and API

- Remedy uses the `house_rules` tool (propose, confirm, decline, rollback,
  revisit, skip, summary) from an owner chat. The runtime binds
  `caller_origin` and the home directory; the model cannot spoof them.
- `settings.patch` on the local API follows the same permission and
  confirmation rules when the change touches house-rules keys.
- `GET` / `PUT /api/setup` and `POST /api/setup/mind/retry` drive the
  first-run session and the mind retry, including the shared draft.

## Related

- [SETUP.md](SETUP.md) — installer handoff and first-run setup
- [First run](manual/02-first-run.md) — desktop steps, Traditional setup included
- Settings later: the desktop Settings window, `~/.remedy/config.toml`, or chat
