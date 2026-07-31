# First run & Setup wizard

The **Setup wizard** is the desktop first-run experience. The CLI setup wizard is skipped when the desktop sidecar starts (`--skip-setup`) so the API is always available for the UI.

## When the wizard appears

Setup opens when **any** of these is true:

- No `config.toml` yet  
- `setup_completed = false`  
- Config file is **corrupt / unreadable** (e.g. bad TOML after an older bug)  
- Settings cannot load yet on a fresh install (wizard still opens so you are not stuck)

After you finish or **Skip**, `setup_completed = true` is written and the wizard will not auto-show again.

## Wizard steps

### 1. Welcome

- Short product intro  
- **Get Started** or **Skip setup (configure later)**  

### 2. Provider

| Choice | Notes |
|--------|--------|
| OpenAI / Anthropic / Google / DeepSeek / Groq / Mistral / OpenRouter | Paste API key |
| **xAI (Grok)** | **Sign in with xAI** (OAuth) *or* console API key |
| **Ollama** | No key if Ollama is running locally |
| **Custom** (Advanced) | OpenAI-compatible base URL |

- Model list follows the selected provider.  
- Local URLs (`127.0.0.1` / `localhost`) do not require a key.  
- You cannot proceed without a key (or xAI sign-in / Ollama) unless you **Skip**.

### 3. Workspace

Optional default **project folder** — working directory for tools, shell, and `@file` search. Leave empty to use the current default later.

### 4. Persona & name

- **Your name** — what Remedy calls you (also in Settings later)  
- **Communication style** — Balanced, Efficient, Detailed, Playful  

### 5. Ready

- Optional **Keep Remedy ready (Start with Windows)**  
- **Start Chatting** saves settings and closes the wizard  

**Always-ready after first run:** the title-bar **✕** hides Remedy to the **system tray**
and keeps the local API running. Use **tray → Quit** for a full stop (stops the server).
Optional: **Start hidden in tray** (Settings) if you want only a tray icon at login.

## Skip setup

Skip marks setup complete without a provider. Chat stays offline until you configure **Settings → Provider**. Use Skip if you want to explore the UI first.

## Error screen: Open setup

If the server fails or settings fail to load:

1. **Retry** — restarts / waits for the local API  
2. **Open setup** — opens the wizard and warms the API token  
3. **Open data folder** — jumps to user data for support  

## After setup

- Empty chat shows starters and a pointer to `/help` and **F1**  
- You may be asked for your name if it was left blank  
- Secondary loads (models list) must not block the wizard  

## Corrupt config recovery

Older builds could write root keys after TOML `[table]` sections, breaking parse. Current builds:

1. Detect unreadable config → treat as needs setup  
2. Write **scalars before tables** on every save  
3. Completing setup rewrites a healthy `config.toml`  

See [Troubleshooting](09-troubleshooting) if save still fails.

## Related

- [Providers & auth](03-providers-and-auth) · [Security & data](04-security-and-data)
