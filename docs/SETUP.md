# Install and first-run setup

This is how a new Remedy install hands off to her, and how you can finish
setup as a conversation or as a form.

## What the installer does

The packaged installer (Windows NSIS, Linux `.deb` / AppImage, macOS) only
installs the app files. It does **not** download the multi-gigabyte local
mind during setup — that would be fragile, blocking, and Windows-only.

On the finish page you can launch Remedy. The app is single-instance: a
second launch focuses the one that is already open. There is no console
window and no extra popup for setup.

The mind download, checksum verify, and start happen on first launch,
inside the app, with a visible progress bar: **Getting Remedy’s mind
ready.** That screen is the installer handoff on every platform.

Silent / update installs do not repeat this conversation. Updates themselves
are in-app (see [Updates & uninstall](manual/08-updates-and-uninstall.md)).

## As soon as the mind is ready

Remedy launches and setup continues as a conversation with her. She covers
house rules and everything the older setup wizard asked:

- Names, tone, and language (default: this computer’s language)
- Providers and API keys
- Workspace folder
- How proactive she should be
- Privacy and audit
- Token saving (easy chats and pictures on this PC)
- Local-muscle / GPU preference
- Consent (money, sends, and deletes always need a yes)
- Other-bot interop
- In-app updates (`auto` or only when you ask)
- Start with the computer

Skipped items use the defaults. She shows a short summary at the end so you
can confirm.

Details of the questions, storage, talk-to-change, and knobs:
[HOUSE_RULES.md](HOUSE_RULES.md).

## Traditional setup

A clearly visible **Traditional setup** option is always on screen. It opens
the polished form / wizard with the same settings. **Talk with Remedy
instead** takes you back to the conversation.

You can switch at any point. Both sides read and write the same draft in
`setup_session.json`, so answers are not lost. API keys are not kept in that
draft; they go to the secure store when you save them.

## If the mind cannot start

A missing GPU is not a failure — the mind can run on the CPU. These are:

- Not enough memory
- The download failed
- The download could not be verified

Remedy then opens Traditional setup, explains why, and offers **Retry**. You
can keep going in the form and retry the mind later. First-run chat uses the
local mind when no cloud provider is set yet, so a failed mind simply means
the conversation is not available until a retry succeeds or you add a
provider.

## Skip, defaults, and existing installs

- **Skip all** (conversation or form) writes `setup_completed` and keeps
  defaults (plus anything you already answered).
- `remedy --skip-setup` (and the desktop sidecar, which uses it so the API
  is up for the UI) marks onboarding as `inherited`. It does not rewrite
  your config and does not nag later.
- Existing installs that already finished setup are not sent through
  first-run again. After this update they get one pop-up: smarter token
  saving, local muscle sharing, and talking with other bots (A2A and MCP)
  are in, and it's worth a few minutes to go over the house rules.
  **Let's go** starts the conversation; **Not now**
  dismisses it. It shows only once. You can still say “let’s go over
  house rules again.”

## Language and updates

The default language is this computer’s language (`ui_language = auto`).
Change it in the conversation, Traditional setup, or Settings → Language.

Updates are in-app only. `update_check = auto` (the default) checks in the
background and lets you know when something is ready. `update_check = ask`
turns off the automatic check; logo menu, Settings → About, and the tray
**Check for updates** still work, and never force a nag.

## CLI

`remedy setup` is the traditional form in the terminal. On the desktop app,
the default is the conversation, with this same form always available.
`remedy setup --quick` applies defaults without prompts.

## Files

| Path | Role |
|------|------|
| App install | Platform-specific (see the install chapters) |
| `~/.remedy/config.toml` | Live settings, including `setup_completed` |
| `~/.remedy/setup_session.json` | Shared first-run draft (no secrets) |
| `~/.remedy/house_rules.toml` | Confirmed house rules and provenance |
| Secure store under `~/.remedy/auth/` | API keys |

`GET` / `PUT /api/setup` and `POST /api/setup/mind/retry` are the local-API
surface the desktop uses. Secrets are scrubbed from responses and logs.

## Related

- [Install (Windows)](manual/01-install-windows.md)
- [Install (Linux)](manual/01-install-linux.md)
- [First run](manual/02-first-run.md)
- [HOUSE_RULES.md](HOUSE_RULES.md)
