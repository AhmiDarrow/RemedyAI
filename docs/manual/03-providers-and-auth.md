# Providers & authentication

Remedy talks to one **active LLM provider** at a time for **chat**. Configure it in the **Setup wizard** or **Settings**.

Separately, Remedy can install an **on-device SmolVLM2** model for vision decode and harness assist — that is not a chat provider. See [Local vision & on-device SmolVLM2](14-visual-decoder).

## Supported chat providers

| Provider | Auth | Notes |
|----------|------|--------|
| OpenAI | API key | Default catalog models |
| Anthropic | API key | Claude models |
| Google | API key | Gemini |
| DeepSeek | API key | |
| **xAI** | OAuth **or** API key | Grok; OAuth recommended for SuperGrok / Premium+ |
| Groq | API key | Fast inference |
| Mistral | API key | |
| OpenRouter | API key | Multi-backend router |
| Ollama | None (local) | Auto-detect when daemon is up |
| Custom | Optional key | Any OpenAI-compatible base URL (Advanced) |

Known brands fill **base URL** for you. Only **Custom** exposes an editable base URL by default.

## API keys

- Entered in Setup or Settings → stored in **`~/.remedy/auth/`** (secure store).  
- **Not** written as plaintext into `config.toml` (keys are scrubbed on save).  
- Environment variables also work for CLI/bootstrap, e.g. `OPENAI_API_KEY`, `XAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, …

## xAI — Sign in with account (OAuth)

1. Choose **xAI** as provider.  
2. Click **Sign in with xAI**.  
3. Browser opens a verification page; note the **user code** if needed.  
4. Approve access; desktop polls until **Connected**.  
5. Or paste a console key from [console.x.ai](https://console.x.ai) instead.

**Token storage:** `~/.remedy/auth/xai.json` (restricted permissions / DPAPI when available).

**CLI equivalent:**

```bash
remedy auth login xai
remedy auth status xai
remedy auth logout xai
remedy auth apikey xai xai-...
```

### OAuth failure checklist

- Local server must be running (status bar not disconnected).  
- Desktop must use a recent build that calls **`auth.x.ai`** (not legacy `accounts.x.ai` device API).  
- Corporate proxies / offline mode will block device-code start.  
- Use **Open verification page** if the browser did not open.  
- Fallback: paste an API key.

## Ollama (local)

1. Install and run [Ollama](https://ollama.com).  
2. Pull a model, e.g. `ollama pull llama3.2`.  
3. In Setup/Settings choose **Ollama** — no API key.  
4. Setup may show “Ollama detected” with model names when the probe succeeds.

Base URL is typically `http://127.0.0.1:11434/v1` (OpenAI-compatible).

## Custom / OpenAI-compatible

Settings → Provider → **Show advanced** → **Custom**:

- Set **Base URL** (e.g. LM Studio, vLLM, local gateways).  
- Optional API key.  
- Type the model name if it is not in the list.

## Switching providers

- Changing provider normalizes model/URL so incompatible pairs are not persisted.  
- Previous provider is remembered as `last_llm_provider` for recovery.  
- Per-provider keys stay in the secret store so switching brands does not reuse the wrong key.

## Local API auth (desktop ↔ sidecar)

The desktop UI authenticates to `http://127.0.0.1:7400` with a **Bearer** token:

- File: `~/.remedy/auth/local_api_token`  
- Bootstrap: `GET /api/auth/local-bootstrap` (loopback only)  
- Disable only for advanced debugging: `REMEDY_API_AUTH=0`  

If you see **Unauthorized**, Retry / re-open the app so the token reloads (common after wipe).

## Related

- [Security & data](04-security-and-data) · [Troubleshooting](09-troubleshooting) · [CLI & API](10-cli-and-api)
