# Skills (agent skill packs)

Skills are portable packages (`SKILL.md` + optional scripts) that teach Remedy specialized procedures. They follow progressive disclosure: the agent sees a catalog first, then loads full bodies on demand.

## Skills panel

Open **Skills** on the status bar. Two tabs:

- **Installed** — your local skills (bundled, learned, imported).
- **Library** — browse the signed community catalog (`AhmiDarrow/remedy-skills`), install into quarantine, then **Trust**.

| UI | Meaning |
|----|---------|
| Filters | All / Active / Quarantine / Learned / Archived |
| Status chips | active · discovered · quarantine · archived · … |
| **Trust** | Clear quarantine and activate (library/import packs) |
| **Promote** | Force ACTIVE for probation skills |
| **Quarantine** | Block activate/run until Trust again |
| **Archive** / **Restore** | Soft-hide from the hot set (files stay on disk) |
| **Delete** | Permanently remove user/library skill under `~/.remedy/skills/` |
| **Edit** | Edit `SKILL.md` body |
| **Export** / **Import** | ZIP packs (imports stay quarantined) |
| **Library → Install** | Signed catalog download → quarantine |
| **Library → Update** | Replace install and re-quarantine |

### Skills Library (community)

The library catalog is **Ed25519-signed**. Remedy verifies the signature before listing skills. Installs only use GitHub release assets for `AhmiDarrow/remedy-skills` (or a local monorepo seed for development). Scripts stay blocked until you **Trust**.

API highlights:

- `GET /api/skills/library/catalog` · `…/search` · `POST …/install` · `GET …/updates`  
- `DELETE /api/skills/{name}` — remove user skill  
- `GET /api/skills/learning/summary` — learned snapshot  
- `POST /api/skills/{name}/status` · `…/quarantine` · `PUT …/body`  
- `POST /api/skills/export` · `…/import`
## Lifecycle (simplified)

```
discovered → validated → active
     ↓
quarantined (unsafe import) — needs Trust
     ↓
disabled / deprecated
```

- Bundled skills ship ready for use.  
- Auto-learned skills may start on probation.  
- Stats persist in `~/.remedy/skill_stats.json`.  
- When a bundled skill’s frontmatter **version** is newer than your seeded copy under
  `~/.remedy/skills/`, Remedy refreshes that pack on next discover (add `.user_locked` in
  the skill folder to keep a hand-edited copy).

## Bundled highlights

| Skill | Use for |
|-------|---------|
| **project-etiquette** | Ship discipline for *any* project: test → docs → build → commit → CI → publish only if green |
| **github** | PRs, issues, CI, releases via `gh` + git (safe defaults; no force-push unless you ask) |
| **git-status** / **commit-message** | Local branch hygiene and commit text |
| **comfyui** | Local image gen bootstrap + generate into chat |
| **code-review** / **write-tests** | Engineering loops |
| **session-handoff** | End-of-session notes so the next agent continues cleanly |

## ComfyUI (bundled)

The **comfyui** skill can run **from a blank machine** (with your approval for downloads):

1. Install official Windows portable (or git) ComfyUI  
2. Start the server (`run_*_gpu.bat` / `main.py --listen`)  
3. Place Flux.2 Klein models in the right `models/` folders  
4. Generate via the built-in `comfyui` tool (`status` / `locate` / `generate`) — images attach to chat  

If nothing is installed, ask Remedy to set up local ComfyUI for images; it follows the skill bootstrap instead of only probing a missing server.

## Library skill check (soft suggest)

On tool-ish turns, Remedy may **quietly notice** a signed Library pack that is **not
installed** but looks relevant (name/description/tags from the **cached** catalog only —
no network on the chat path).

- You may see a small **Library** chip under the composer, or a continuity note to the model.  
- **Never auto-installs.** Install still goes through **Skills → Library** (quarantine → Trust).  
- Pure chat (“hi”, “thanks”) never triggers it.  
- Dismiss or install → same skill will not re-nag that session.  
- Config: `[skills.library_suggest]` (`enabled`, `min_score`, `min_query_chars`, `local_rerank`).

## How the agent uses skills

Tools (for the model, not usually typed by you):

- `skill_search` — find relevant packs  
- `skill_library_search` — search Library catalog (cache; no install)
- `skill_activate` — load full instructions into context  
- `skill_run` — execute skill scripts (blocked if quarantined)  

You can also list skills with `/skills` in chat.

## Importing skills

- Place a folder with `SKILL.md` under a skills path, or import packs the app supports.  
- Zip imports are scanned for path traversal (**Zip Slip**).  
- Untrusted packs stay **quarantined** until you Trust them.  

## Writing a skill (power users)

Minimal `SKILL.md`:

```markdown
---
name: my-skill
description: One-line when to use this skill
tags: [example]
---

# My skill

Steps the agent should follow…
```

Discover via CLI: `remedy skill discover ./path` · `remedy skill list`.

## Human vs agent docs

- **This Help chapter** — for you (owners).  
- **SKILL.md bodies** — instructions for the model.  
- Deep design: repo `docs/SKILL_LIFECYCLE.md` (developers).  

## Related

- [Security & data](04-security-and-data) · [CLI & API](10-cli-and-api)
