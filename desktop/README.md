# Remedy Desktop

Native Windows desktop shell for Remedy AI: **Tauri 2 + React 19 + Vite**, with the Python
`remedy serve` process bundled as a sidecar.

## Users

Prefer the prebuilt installer:

**[Download latest release](https://github.com/AhmiDarrow/RemedyAI/releases/latest)**

No Python, Node, or Rust required. In-app updates are minisign-signed.

### First-run / providers

The Setup wizard and Settings load providers from `GET /api/providers`.
**xAI (Grok)** supports **Sign in with xAI** (device-code OAuth) or a console API key.
Custom OpenAI-compatible endpoints are under **Advanced**. Ollama is detected when
running locally. See [docs/DESKTOP.md](../docs/DESKTOP.md) and [docs/USAGE.md](../docs/USAGE.md).

## Developers

### Prerequisites

- Node 20+
- Rust (stable) + MSVC toolchain on Windows
- Python 3.12+ and [uv](https://docs.astral.sh/uv/) (repo root)

### Dev loop

```powershell
# Terminal 1 — API server
cd ..
uv run remedy serve --host 127.0.0.1 --port 7400

# Terminal 2 — Vite UI only
cd desktop
npm install
npm run dev
# http://localhost:5173

# Or full Tauri shell (spawns sidecar when packaged):
npm run tauri dev
```

### Production build (local)

```powershell
# From repo root — build PyInstaller sidecar into desktop/bin/
python scripts/build_desktop.py --clean

cd desktop
npm run tauri build
# Installer: src-tauri/target/release/bundle/nsis/
```

### Versioning

Do not hand-edit versions. From repo root:

```bash
python scripts/sync_version.py patch   # or 0.10.4 / minor / major
```

This updates `pyproject.toml`, `package.json`, `tauri.conf.json`, `Cargo.toml`, and `scripts/latest.json`.

### Auto-update signing

- Public key: `src-tauri/tauri.conf.json` → `plugins.updater.pubkey`
- Private key: **never commit** — store at `~/.tauri/remedy.key` (ignored by git)
- CI secrets: `TAURI_SIGNING_PRIVATE_KEY`, `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`
- Requires `bundle.createUpdaterArtifacts: true` (already set)

See root [README.md](../README.md#desktop-release-maintainers) and [docs/DESKTOP.md](../docs/DESKTOP.md).

## Layout

```
desktop/
├── src/                 # React UI (Composer, sessions, UpdateScreen, …)
├── src-tauri/           # Tauri shell, DnD, updater commands
├── bin/                 # Built sidecar binaries (gitignored)
└── package.json
```
