---
name: webui-parity
description: >
  When changing Grove, Studio, Settings, or chat chrome, keep WebUI
  (browser on :7400) at parity with Tauri. Grove unmounts off-surface;
  serve mounts SPA once at start. Use after any desktop/src UI change
  the owner will also see in the browser.
---

# WebUI parity (same SPA, different load path)

Desktop and WebUI share `desktop/src/`. Vite (`tauri:dev`) is always fresh.
WebUI is whatever `find_webui_dir()` mounted at **API start** (prefer `desktop/dist`).

## Checklist (every owner-visible UI change)

1. **Does Grove unmount?** `{surface === 'grove' && <GroveApp />}` — Studio cannot use Grove’s hooks. Put speak/mic/status in `App.tsx` (or a shared hook used by both).
2. **Can Grove open Settings without leaving Grove?** Logo menu / Ctrl+, / About must open the Grove Settings overlay (`data-testid="grove-settings"`). Do **not** switch to Studio. Do **not** add extra section buttons (the Voice button was a workaround for a broken logo menu).
3. **Is the control Tauri-only?** `isTauri()` / native pickers need an HTML fallback (already true for attach). Voice (mic, `speechSynthesis`, `/api/voice`) works on localhost WebUI.
4. **CLI:** do not add voice (or other GUI-only) commands unless the owner asks.
5. **Ship the SPA to WebUI:** `cd desktop && npm run build`, stage `dist` → `desktop/bin/webui` if that tree is used, **restart serve/Desktop**, hard-refresh the browser. Refresh alone is not enough.

## Voice (current)

- Settings id `voice` — Simple: speak-replies; TTS/STT/smart-turn auto-download on first run. Advanced: STT, speed, smart-turn. Retry only if a download failed.
- API: `/api/voice/status|settings|install|speak|transcribe`. `install` `component=all` retries every missing piece.
- Owner `reason`; Advanced `hint` for pip.
