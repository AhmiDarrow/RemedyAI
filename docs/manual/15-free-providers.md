# Free providers & demo mode

Remedy works without paying. Use a free path until you add a paid key.

## Quick pick

| Option | Signup? | Best for |
|--------|---------|----------|
| **Demo** | No | First minutes — try chat with no keys |
| **Gemini** | Free AI Studio key | Strong free multimodal |
| **Groq** | Free key | Very fast open models |
| **OpenRouter** | Free key | Many models ending in `:free` |
| **Mistral** | Free Experiment key | EU provider free tier |
| **Ollama** | Install app | Private, local, unlimited on your hardware |

## Demo (no signup)

- Provider id: `demo`
- Uses a free third-party OpenAI-compatible gateway (LLM7).
- **No real API key** — Remedy sends a dummy bearer.
- Rate limits and quality vary; not for production agent work.
- **Privacy:** prompts leave your PC. Prefer Ollama for private use.
- Disable guest fallback: set env `REMEDY_DEMO_DISABLED=1`.

On **Skip setup**, Remedy selects Demo so chat is not dead on first launch.

## Free API keys

Open **Settings → Provider** (or first-run **Get started free**):

1. Pick Gemini / Groq / OpenRouter / Mistral.
2. Click **Get free API key / docs**.
3. Paste the key and **Save**.

## Local free (Ollama)

1. Install from https://ollama.com/download  
2. `ollama pull llama3.2` (or another model)  
3. Select **Ollama** in Remedy (auto-detected when running).

## Order of preference (bootstrap)

1. Env keys / saved keys / xAI OAuth  
2. Ollama if you force-prefer it  
3. Other free/paid keys in env  
4. **Demo** (if not disabled)

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Demo 429 / errors | Wait, or switch to Groq/Gemini free key |
| EU Gemini free tier | Use Groq, OpenRouter free, Mistral, or Demo |
| Want privacy | Ollama only |
