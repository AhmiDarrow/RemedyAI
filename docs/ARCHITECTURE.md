# Architecture (living)

Pointers, not a second product bible. Design docs: `docs/LIFE_TASK_PARTNER.md`, `docs/DESKTOP.md`, `docs/TELEPHONY.md`, root `AGENTS.md`.

## Surfaces (one SPA)

| Surface | How UI loads | Voice | Settings |
|---------|----------------|-------|----------|
| Tauri desktop (`tauri:dev`) | Vite `localhost:5173` | Grove + Studio | Grove overlay; Studio rail |
| WebUI `http://127.0.0.1:7400/` | Static `desktop/dist` (mount at **serve start**) | Same SPA — rebuild + **restart serve** | Same |
| CLI | no SPA | **No voice** (owner preference 2026-08-20) | n/a |

Grove unmounts off-surface. Studio must own its own voice instance (`useVoice` in `App.tsx`). Logo-menu Settings must **not** switch Grove → Studio.

## Voice stack

- API: `src/remedy/interfaces/routes/voice.py` → `src/remedy/voice/service.py`
- First-run: `ensure_voice_assets` + `maybe_ensure_local_model` on API lifespan (skipped in pytest). Chatterbox HQ is **opt-in**, not first-run.
- UI: `desktop/src/components/settings/VoiceSection.tsx` (HQ toggle), Grove `GroveVoiceControls` (speak/quiet only), Studio composer/status bar
- Smart-turn: pinned ONNX under `~/.remedy/voice/models/smart-turn/`; detector `voice.realtime.turn.make_detector` globs `*.onnx` (no restart)
- Identity: `~/.remedy/voice/identity.json`. Owner clone: `voice/clone.py` (named task, expiry, revoke).
- Telephony loopback: `SipDirectBackend` (`sip_direct`) — UDP/in-process echo on 127.0.0.1, `simulated=True`

## Dated notes

### 2026-08-20 — Voice is a SPA+API feature, not CLI

Install and status are HTTP. Desktop and WebUI share Settings/Grove/Studio. CLI stays text-only. Phone-line anti-alias and host-session exit detection are runtime, not UI.
