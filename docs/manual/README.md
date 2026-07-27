# Remedy Owner’s Manual

This folder is the **canonical technical owner’s manual** for Remedy Desktop.

## In the app

Open **Help** ( **F1** or **Ctrl+/** ) for a searchable wiki UI that loads these chapters offline.

## Chapters

| File | Title |
|------|--------|
| [00-overview.md](00-overview.md) | Overview |
| [16-continuity-philosophy.md](16-continuity-philosophy.md) | How Remedy works (continuity) |
| [01-install-windows.md](01-install-windows.md) | Install (Windows) |
| [02-first-run.md](02-first-run.md) | First run & setup |
| [03-providers-and-auth.md](03-providers-and-auth.md) | Providers & auth |
| [04-security-and-data.md](04-security-and-data.md) | Security & data |
| [05-chat-and-sessions.md](05-chat-and-sessions.md) | Chat & sessions |
| [06-memory-and-harness.md](06-memory-and-harness.md) | Memory & harness |
| [07-skills.md](07-skills.md) | Skills |
| [08-updates-and-uninstall.md](08-updates-and-uninstall.md) | Updates & uninstall |
| [09-troubleshooting.md](09-troubleshooting.md) | Troubleshooting |
| [10-cli-and-api.md](10-cli-and-api.md) | CLI & API |
| [11-reference-commands.md](11-reference-commands.md) | Slash commands |
| [12-reference-shortcuts.md](12-reference-shortcuts.md) | Shortcuts |
| [13-whats-new.md](13-whats-new.md) | What’s new |
| [14-visual-decoder.md](14-visual-decoder.md) | Local vision & on-device Qwen |
| [15-free-providers.md](15-free-providers.md) | Free providers & demo |
| [17-nanoswarm.md](17-nanoswarm.md) | Continuity workers (operators) |
| [18-agency.md](18-agency.md) | Coding agency (Build-class tools) |

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
