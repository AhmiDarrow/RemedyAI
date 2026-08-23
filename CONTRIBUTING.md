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

## Inbound contributions

If a contribution is accepted, you grant Ahmi Darrow a perpetual, worldwide,
irrevocable, royalty-free, transferable, sublicensable license to use, copy,
modify, create derivative works of, distribute, and **relicense** that
contribution as part of the Software — including under commercial terms,
paid editions, dual licenses, and later versions of LICENSE. You represent
that you have the right to grant that, and that the contribution is original
or you have permission to submit it.

This exists so the product can stay one owner's copyright and can be sold
or dual-licensed without chasing every patch. If you cannot grant that, do
not submit the patch.

Local-only on the maintainer clone (not GitHub): `community/`, live/soak
scripts, and review dumps.
