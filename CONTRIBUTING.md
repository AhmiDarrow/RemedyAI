# Contributing

Remedy is open source under the Apache License 2.0, but it is not a drive-by contribution project.

The public tree is the product you can compile, plus the test suite that
proves jail, auth, Build, and the desktop shell. That is for users and
reviewers — confidence, not an inbound feature factory.

| Want | Do this |
|------|---------|
| Use / license | [LICENSE](LICENSE) (Apache-2.0) · `ahmitdarrow@gmail.com` |
| Report a security bug | Email the owner. Do not file a public issue with a working exploit. |
| Verify behavior | Run `uv run python scripts/prepush.py` (the multi-stack matrix in `README.md`, also the pre-push hook under `.githooks/`); CI covers Python, React, Rust/Tauri, Go, Zig, Android, packaging, Windows, and Linux. |

Inbound PRs (including from the self-improve bot) are **scanned, not merged**.
The bot uses `pull_request` (not `pull_request_target`) and never has write
access to `master`. Owner review is required.

## Inbound contributions

Unless you state otherwise, any contribution you submit is licensed under the
Apache License 2.0 (section 5 of the LICENSE), the same as the rest of the
project. You represent that the contribution is original or that you have
permission to submit it.

Local-only on the maintainer clone (not GitHub): `community/`, live/soak
scripts, and review dumps.
