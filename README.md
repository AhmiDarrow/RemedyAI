# Remedy

<p align="center">
  <img src="https://raw.githubusercontent.com/AhmiDarrow/RemedyAI/master/assets/previews/hero_banner_win_linux.png" alt="Remedy — your personal AI partner on Windows &amp; Linux" width="800" />
</p>

<p align="center">
  <em>A partner for any ability level</em> — she drives this computer to finish the goal you set.
</p>

<p align="center">
  <a href="https://github.com/AhmiDarrow/RemedyAI/releases/latest"><strong>Download for Windows</strong></a>
  ·
  <a href="https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/01-install-linux.md">Install on Linux</a>
  ·
  <a href="https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/00-overview.md">Owner’s manual</a>
  ·
  <a href="https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/13-whats-new.md">What’s new</a>
  ·
  <code>pip install remedy-ai</code>
</p>

**Not** a medical product. **F1** (or **Ctrl+/**) opens the same Help wiki inside the app.

---

Remedy lives on **this computer** (Windows, Linux, WSLg). **Grove** is home. **Studio** is the workbench. Continuity is `~/.remedy`. You bring the model (cloud keys or **RMB** locally). Money, passwords, submit, send, and delete **stop for you**.

| | |
|--|--|
| **This tree** | **v0.41.1** |
| **Last public release** | [v0.38.1](https://github.com/AhmiDarrow/RemedyAI/releases/tag/v0.38.1) · [PyPI](https://pypi.org/project/remedy-ai/) |
| **Owner’s manual** | [docs/manual/](https://github.com/AhmiDarrow/RemedyAI/tree/master/docs/manual/) · [index](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/README.md) |
| **Install** | [Windows](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/01-install-windows.md) · [Linux](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/01-install-linux.md) |
| **Security / data** | [04-security-and-data](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/04-security-and-data.md) |
| **Commands / keys** | [11-reference-commands](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/11-reference-commands.md) · [12-reference-shortcuts](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/12-reference-shortcuts.md) |
| **Changelog** | [CHANGELOG.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/CHANGELOG.md) |

Local API: `127.0.0.1:7400`. Close ✕ hides to the **tray** on Windows and **minimizes** on Linux/WSLg.

From the creator: *My name is Ahmi, I hope you enjoy my Remedy.*

---

## Development

```bash
git clone https://github.com/AhmiDarrow/RemedyAI.git && cd RemedyAI
uv sync --group dev
uv run pytest -q
cd desktop && npm test && npm run build
python scripts/check_docs.py
```

Version / help / docs gates: `scripts/sync_version.py` · `scripts/sync_help_manual.py` · `scripts/check_docs.py`.  
Agent notes: [AGENTS.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/AGENTS.md) · contributing: [CONTRIBUTING.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/CONTRIBUTING.md) · signing: [WINDOWS_SIGNING.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/WINDOWS_SIGNING.md)

---

## Support

[patreon.com/cw/AhmiDarrow](https://www.patreon.com/cw/AhmiDarrow)

## License

**Source-available** — [LICENSE](https://github.com/AhmiDarrow/RemedyAI/blob/master/LICENSE) · [COMMERCIAL.md](https://github.com/AhmiDarrow/RemedyAI/blob/master/COMMERCIAL.md)

Solo / small indies (under $1M revenue **and** under 20 FTE), personal, education, and research: free under LICENSE. Larger orgs, SaaS, commercial resale, or a paid deal: written license — **ahmitdarrow@gmail.com**. Use is at your own risk. Third-party notices: `desktop/public/THIRD_PARTY_NOTICES.txt`.

Copyright © 2025–2026 **Ahmi Darrow**.
