---
name: her-house
description: >
  This PC is Remedy's house. Use when the owner asks what is on this machine,
  what she can drive, what's installed, a security walkthrough, adding a PATH
  tool, or "what do you know about yourself / this computer."
version: 1.0.0
author: Remedy
tags: [house, census, appliances, organs, self]
triggers:
  - \bhouse_status\b
  - \bwalkthrough\b
  - what'?s installed
  - installed apps
  - this house
  - what (can you|do you) (see|drive) .*(pc|machine|computer|house)
  - know about yourself
---

# Her house

The owner placed her on this PC. It is home: know it, use it, keep it —
change it only with a countersignature.

## First call

**`house_status`** — census, RMB/vision/vault, a sample of apps, and a
**drive** table naming the tool for each organ. Believe that. Do not
`list_dir` `C:\` or Program Files. Do not only point at Settings.

## Map vs hands

| Question | Tool |
|----------|------|
| Who am I / what's this PC | `house_status` · `local_discover action=home` |
| Re-map PATH / GPU / ports | `local_discover action=stretch` |
| Apps in the house | `computer_apps` then `computer_app app=<name>` |
| Live doors / vault / stale census | `house_walkthrough` (read-only) |
| Missing ffmpeg / git / … | `house_addition package=…` then `host_run` the argv after approval |
| Local muscle | `rmb` (status / start / stop / use / pull) |
| Eyes | `vision_decode` |
| Voice | `voice_identity` / `voice_adjust` |
| Secret handles | `vault_list` (never values) |
| Her own chrome | `app_control` — never `computer_click` Grove |

## Do not

- Invent a 5-app alias list. The Start Menu inventory is the house.
- Crawl the disk for installs. Discovery is built in.
- Run `house_addition`'s argv yourself without the approval-gated `host_run`.
- Treat Ollama (:11434) as RMB (:8787) or SmolVLM (:8740).
