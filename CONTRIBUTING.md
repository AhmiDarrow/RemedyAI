# Contributing

Remedy is **source-available**, not a drive-by contribution project.

The public tree is the product you can compile, plus the test suite that
proves jail, auth, Build, and the desktop shell. That is for licensees and
reviewers — confidence, not an inbound feature factory.

| Want | Do this |
|------|---------|
| Use / license | [LICENSE](LICENSE) · [COMMERCIAL.md](COMMERCIAL.md) · `ahmitdarrow@gmail.com` |
| Report a security bug | Email the owner. Do not file a public issue with a working exploit. |
| Verify behavior | `uv sync --group dev` then `uv run pytest -q` |

Inbound PRs (including from the self-improve bot) are **scanned, not merged**.
The bot uses `pull_request` (not `pull_request_target`) and never has write
access to `master`. Owner review is required.

Local-only on the maintainer clone (not GitHub): `community/`, live/soak
scripts, and review dumps.
