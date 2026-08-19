# Remedy Owner’s Manual

This folder is the **canonical technical owner’s manual** for Remedy Desktop.

**Browse on GitHub:** [Overview](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/00-overview.md) · [this index](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/README.md) · [folder](https://github.com/AhmiDarrow/RemedyAI/tree/master/docs/manual)

## In the app

Open **Help** ( **F1** or **Ctrl+/** ) for a searchable wiki UI that loads these chapters offline.

## Chapters

| File | Title |
|------|--------|
| [00-overview.md](00-overview.md) | Overview — what Remedy can do (**0.20.0** metabolism + tray) |
| [16-continuity-philosophy.md](16-continuity-philosophy.md) | How Remedy works (continuity) |
| [14-visual-decoder.md](14-visual-decoder.md) | Local vision & on-device SmolVLM2 |
| [17-nanoswarm.md](17-nanoswarm.md) | Continuity workers (operators) |
| [01-install-windows.md](01-install-windows.md) | Install (Windows) |
| [01-install-linux.md](01-install-linux.md) | Install (Linux / WSLg) |
| [02-first-run.md](02-first-run.md) | First run & setup |
| [03-providers-and-auth.md](03-providers-and-auth.md) | Providers & auth |
| [04-security-and-data.md](04-security-and-data.md) | Security & data (✕→tray, SSRF, jails) |
| [05-chat-and-sessions.md](05-chat-and-sessions.md) | Chat, rails, Plan/Build |
| [06-memory-and-harness.md](06-memory-and-harness.md) | Memory & harness |
| [21-personal-assistant.md](21-personal-assistant.md) | Personal assistant (reminders, mail, calendar, money, paperwork) |
| [18-agency.md](18-agency.md) | Coding agency (Build-class tools) |
| [19-metabolism.md](19-metabolism.md) | Partner Metabolism L0–L3 (**0.20.0**, Advanced) |
| [07-skills.md](07-skills.md) | Skills & Library |
| [08-updates-and-uninstall.md](08-updates-and-uninstall.md) | Updates & uninstall |
| [09-troubleshooting.md](09-troubleshooting.md) | Troubleshooting |
| [10-cli-and-api.md](10-cli-and-api.md) | CLI & API |
| [11-reference-commands.md](11-reference-commands.md) | Slash commands |
| [12-reference-shortcuts.md](12-reference-shortcuts.md) | Shortcuts |
| [13-whats-new.md](13-whats-new.md) | What’s new |
| [15-free-providers.md](15-free-providers.md) | Free providers & demo |

## Keeping docs in sync

This folder is the **canonical** source for Help wiki chapter bodies. After edits:

```bash
python scripts/sync_help_manual.py   # copy → desktop/src/help/articles/
python scripts/check_docs.py         # CI gate: help, versions, slash cmds, hotkeys, catalog, test count
```

If you add a chapter file, also add a matching `META` entry in `desktop/src/help/catalog.ts`.

## Related maintainer docs

- `../USAGE.md` — extended CLI reference  
- `../DESKTOP.md` — desktop architecture / build  
- `../SKILL_LIFECYCLE.md` — skills design  
- `../WINDOWS_SIGNING.md` — signing  
