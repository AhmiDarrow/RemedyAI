---
name: pixellab-game-assets
version: 1.0.0
description: >
  Generate pixel art game assets with PixelLab: MCP server tools and REST API v2 —
  characters, animations, tilesets, map objects, UI, rotate/edit; job workflow and safe auth.
author: Remedy Official
license: LicenseRef-Proprietary
tags:
  - gaming
  - pixel-art
  - assets
  - pixellab
  - mcp
  - api
  - tooling
kind: native
status: discovered
tools:
  - file_read
  - file_write
  - file_edit
  - shell
  - web_fetch
metadata:
  source: library
  library_id: pixellab-game-assets
  official: true
  domain: gaming
  security_flags: []
  service: pixellab
---

# PixelLab Game Assets (API + MCP)

Practical playbook for generating **pixel art game assets** with [PixelLab](https://www.pixellab.ai/):
the **MCP server** (AI assistants) and the **REST API v2** (scripts, CI, in-game tools).

**Product / API:** https://www.pixellab.ai/  
**OpenAPI docs:** https://api.pixellab.ai/v2/docs  
**MCP endpoint:** `https://api.pixellab.ai/mcp`  
**MCP tool overview (for agents):** https://api.pixellab.ai/mcp/docs  

## When to use

- Generate characters, rotations, animations, tilesets, map objects, UI panels
- Wire PixelLab into an AI coding client via **MCP**
- Script batch generation via **REST** (`/v2/...`)
- Keep style consistent across a game project’s asset pipeline
- Debug failed jobs, downloads, auth, or credit balance

## Operating rules

- **Never commit API tokens.** Use env vars or the client’s secret store.
- Prefer **MCP** when an assistant is generating interactively; prefer **REST** for reproducible scripts and CI.
- Treat generation as **paid/credit-consuming** — check balance before large batches; confirm with the user for bulk runs.
- Prefer **non-blocking** workflows: queue work, poll status, then download.
- Keep reference images **small enough** for the transport (large base64 over MCP can truncate — prefer ~64px refs for MCP when possible).
- Save assets under the project’s asset tree with stable names; keep a small metadata sidecar (IDs, prompts) when re-generation matters.
- Do not invent endpoint paths or tool names — verify against OpenAPI or live MCP tool schemas.

## Auth (shared by MCP and REST)

1. Sign in at PixelLab and create an API token / secret.
2. Send: `Authorization: Bearer <token>`
3. Store only in:
   - environment: `PIXELLAB_API_KEY` or `PIXELLAB_TOKEN` (pick one name and stick to it)
   - MCP client config headers (not committed plaintext if the repo is shared)
4. Never print full tokens in logs, chats, or commits. Redact to last 4 chars if debugging.

```bash
# Example shell (token from env — do not hardcode)
export PIXELLAB_API_KEY="..."   # user supplies
curl -sS -H "Authorization: Bearer $PIXELLAB_API_KEY" \
  -H "Content-Type: application/json" \
  https://api.pixellab.ai/v2/...
```

## Path A — MCP (assistant / “vibe coding”)

### Configure the server

HTTP MCP with bearer auth (shape used by common clients):

```json
{
  "mcpServers": {
    "pixellab": {
      "type": "http",
      "url": "https://api.pixellab.ai/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_SECRET"
      }
    }
  }
}
```

CLI-style (Claude Code example pattern):

```bash
# Replace YOUR_SECRET; do not commit the real value
claude mcp add pixellab https://api.pixellab.ai/mcp -t http \
  -H "Authorization: Bearer YOUR_SECRET"
```

After connect, tools may appear as `pixellab__<name>`, `mcp__pixellab__<name>`, or bare names depending on the client.

### Non-blocking job model

Most creation tools return **immediately** with IDs; generation finishes in the background (often ~2–5 minutes for heavier character/tileset work; some object jobs are faster).

```text
Create  →  job / character / object / tileset ID
Queue more (anims, states, chained tiles) without waiting
get_* / list_* until ready
Download via returned URLs / UUID download keys
```

**Do not** block the whole session on one job if more assets can queue in parallel.

### Asset lifecycle (typical)

| Kind | Persistence notes |
|------|-------------------|
| Characters | Long-lived; animate / state later |
| Tilesets | Long-lived; chain via base tile IDs |
| UI assets | Long-lived |
| Map objects | May expire (often hours) — download promptly |

### MCP tool map (common)

Exact schemas vary by server version — **list tools in the client** before calling. Typical set:

#### Characters & animation

| Tool | Use for |
|------|---------|
| `create_character` | New multi-direction character from description |
| `create_character_state` | Outfit/variant keeping identity |
| `animate_character` | Walk / idle / attack / custom action |
| `get_character` | Status, rotation URLs, download links |
| `list_characters` | Inventory of recent characters |
| `delete_character` | Remove character + associated data |

Example parameters (illustrative):

```text
create_character(
  description="brave knight in battered plate",
  n_directions=8,          # 4 or 8
  size=48,                 # canvas; character often ~60% height
  proportions='{"type":"preset","name":"heroic"}',
  body_type="humanoid",    # or quadruped + template
  view="low top-down"
)

animate_character(character_id="<uuid>", template_animation_id="walking")
animate_character(character_id="<uuid>", action_description="casting a spell")
```

Proportion presets often include: `default`, `chibi`, `cartoon`, `stylized`, `realistic_male`, `realistic_female`, `heroic`.  
Common animation templates: `walking`, `running`, `idle`, `attacking`, `jumping`, `dying` (confirm live list).

#### Tiles & maps

| Tool | Use for |
|------|---------|
| `create_topdown_tileset` | Wang-style dual-terrain top-down set |
| `get_topdown_tileset` | Status + `base_tile_ids` for chaining biomes |
| `create_sidescroller_tileset` | Platformer tiles, transparent bg |
| `create_isometric_tile` | Single iso tile (`thin` / `thick` / `block`) |

Chain biomes by feeding previous **base tile IDs** into the next tileset so transitions stay coherent:

```text
ocean → beach → grass → forest
(each step reuses lower/upper base ids from the previous job)
```

#### Objects & UI

| Tool | Use for |
|------|---------|
| `create_map_object` | Props (tree, chest, rock); optional style bg |
| `create_1_direction_object` / `create_8_direction_object` | Multi-gen / multi-dir objects |
| `animate_object` | Animate an existing object |
| `create_ui_asset` | Health bars, panels, buttons |
| `create_font` | Pixel font (when available / plan allows) |

#### Utility

| Tool | Use for |
|------|---------|
| `get_balance` | Credits remaining before large batches |
| `agent_help` | PixelLab knowledge agent |
| `agent_feedback` | Report issues |
| `chat_send_message` / `sandbox_*` | Agent chat / remote sandboxes (if exposed) |

### MCP recommended sequence

1. `get_balance` (if available) — abort or downsize if credits are low.
2. Agree **view** (`low top-down` vs `high top-down` vs side) and **pixel size** with the user.
3. Characters first → queue **idle + walk + attack** (and death if needed) immediately after create.
4. Tilesets next — chain multi-biome transitions.
5. Props / UI last, style-matched (reference or shared prompt language).
6. Download to project paths; write a one-line note of IDs + prompts for regeneration.

## Path B — REST API v2 (scripts / automation)

**Base URL:** `https://api.pixellab.ai/v2`  
**Auth:** `Authorization: Bearer <token>`  
**Docs:** https://api.pixellab.ai/v2/docs  

### Model families (pick by job)

| Family | Examples | Good for |
|--------|----------|----------|
| Create image | `POST /create-image-pixflux`, `/create-image-pixen`, `/create-image-bitforge` | One-off sprites, props, concept pixels |
| Image ops | `/image-to-pixelart`, `/resize`, `/remove-background` | Convert/cleanup existing art |
| Animate | `/animate-with-text`, `/animate-with-text-v3`, `/animate-with-skeleton` | Standalone frame sequences |
| Rotate | `/rotate`, `/generate-8-rotations-v3` | Directional sheets from a south view |
| Characters | `/create-character-v3`, `/create-character-pro`, 4/8-dir templates, `/animate-character`, `/create-character-state` | Reusable cast + anims |
| Map | `/create-tileset`, `/create-tileset-sidescroller`, `/create-isometric-tile`, `/map-objects` | Terrain + props |
| Objects | `/create-1-direction-object`, `/create-8-direction-object`, animations/states under `/objects/{id}/...` | Persistent object pipeline |
| Pro tools | `/generate-image-v2`, `/generate-ui-v2`, `/generate-with-style-v2`, … | Higher quality / larger canvas (higher cost) |
| Prompt helpers | `/enhance-pixen-prompt`, `/enhance-character-v3-prompt`, … | Cheap prompt expansion |

Prices are **estimates** and scale with size, frames, and mode — always check live docs / balance.

### Minimal REST pattern (Python sketch)

```python
import base64, os, pathlib, requests

BASE = "https://api.pixellab.ai/v2"
TOKEN = os.environ["PIXELLAB_API_KEY"]  # required
H = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

def create_pixflux(description: str, w: int = 128, h: int = 128) -> bytes:
    r = requests.post(
        f"{BASE}/create-image-pixflux",
        headers=H,
        json={
            "description": description,
            "image_size": {"width": w, "height": h},
            "no_background": True,
            "view": "low top-down",
            "direction": "south",
        },
        timeout=120,
    )
    r.raise_for_status()
    b64 = r.json()["image"]["base64"]
    return base64.b64decode(b64)

# png = create_pixflux("top-down pixel art wooden chest, closed, simple")
# pathlib.Path("assets/props/chest.png").write_bytes(png)
```

Notes:

- Some endpoints return **images inline (base64)**; others return **job IDs** — branch on response shape.
- Set timeouts generously for sync endpoints; use poll loops for async ones.
- Cap concurrency (e.g. 5–10) so you don’t thrash rate limits or burn credits by accident.

### Character pipeline (REST)

1. Create with v3 / Pro / 4-dir / 8-dir endpoint appropriate to quality budget.
2. Store returned **character id**.
3. Call animate endpoints per action (or batch if API allows).
4. Download sheets/frames; pack for the engine (see integration below).

### Tileset pipeline (REST)

1. `POST /create-tileset` with lower/upper descriptions, tile size, transition size, view.
2. Capture **base tile ids**; chain next biome.
3. Download Wang atlas; map corner rules in the engine’s tile system.

## Prompt & style consistency

- Lock **view language**: e.g. always `low top-down` + `south` for characters until the set is complete.
- Lock **palette / mood** words: “muted post-apoc earth tones”, “1px dark outline”, “no anti-alias glow”.
- Prefer **one style reference** (image or Bitforge/style Pro tools) over re-rolling prompts randomly.
- Describe **silhouette and props**, not brand names of other games.
- For transparent sprites: `no_background=True` (or remove-background op afterward).
- Keep canvas sizes power-friendly for engines (16/32/48/64/128) unless Pro requires larger.

## Download & project layout

Suggested project-side layout:

```text
assets/
  characters/<id_or_name>/
    base.png | rotations/ | anims/walk/ | meta.json
  tilesets/<biome>/
    atlas.png | wang_meta.json
  objects/<name>.png
  ui/<name>.png
```

`meta.json` should store: PixelLab ids, endpoint/tool used, size, view, prompt, date — so you can regenerate without reverse-engineering files.

## Engine integration (high level)

### Godot 4.x

- **Tilesets:** import atlas → `TileSetAtlasSource`; configure Wang / terrain peering if using dual-terrain sets.
- **Characters:** multi-dir sheets → `AnimatedSprite2D` frames or `Sprite2D` + `AnimationPlayer`.
- **Objects/UI:** `Sprite2D` / `TextureRect` with transparency; keep filter **Nearest** for pixel art.
- Stretch: Project Settings → Window stretch mode/aspect so pixels stay crisp.

### Generic

- Nearest-neighbor sampling; integer scale when possible.
- Separate collision shapes from art when generative edges are noisy.
- Normalize pivot (feet center for characters) across the set.

## Failure modes & fixes

| Symptom | Likely cause | Next step |
|---------|--------------|-----------|
| 401 / unauthorized | Bad or missing bearer token | Fix env/MCP header; rotate if leaked |
| Empty / truncated image over MCP | Huge base64 reference | Shrink ref (~64px), use REST for large payloads |
| Job stuck “processing” | Queue load or failed worker | Re-`get_*`; don’t spam recreate |
| Style drift across assets | Inconsistent prompts/views | Fix view + reference image; regenerate outliers |
| Export looks fine, engine blurry | Linear filter / non-integer scale | Nearest filter; integer zoom |
| Surprise credit drain | Parallel over-queue | `get_balance`; lower concurrency; prefer smaller canvases |

## Security

- Tokens are **secrets** — same class as provider API keys.
- Do not paste tokens into skills, READMEs, or shared issues.
- If a token appears in chat or a file, **rotate it** in the PixelLab account and scrub history when possible.
- Quarantine/review any helper scripts that call paid endpoints before Trust in Remedy.

## Anti-patterns

- Hardcoding API keys in repo scripts
- Waiting serially on every animation when they can queue
- Generating 512px Pro assets “just because” for a 32px game
- Skipping downloads for expiring map objects
- Mixing top-down and side views in one character set without a plan
- Committing giant raw base64 blobs instead of PNG files

## Done when

- Requested assets exist on disk (or URLs delivered) with agreed size/view.
- MCP tools or REST calls used correctly with **env-based auth** (no leaked secrets).
- Character/tileset **IDs and prompts** recorded if regeneration is likely.
- Engine import notes given when the user is dropping art into a game project.
- Credit impact acknowledged for non-trivial batches.
