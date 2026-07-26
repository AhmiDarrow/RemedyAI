# Skills (agent skill packs)

Skills are portable packages (`SKILL.md` + optional scripts) that teach Remedy specialized procedures. They follow progressive disclosure: the agent sees a catalog first, then loads full bodies on demand.

## Skills panel

Open **Skills** on the status bar:

| UI | Meaning |
|----|---------|
| **What I learned** | Counts + recent auto-learned skills (probation reasons) |
| Status chips | active / validated / discovered / disabled / quarantined |
| **Hard-won** badge | Skill earned high effort / recovery value |
| Search | Filter by name / description |
| **Force promote** | Manual override: promote a probation skill to **ACTIVE** now |
| **Quarantine** | Manual override: lock a failing/untrusted skill (blocks script runs) |
| **Edit MD** | Open an embedded CodeMirror editor for the skill’s `SKILL.md` body |
| **Export Pack** | Bundle selected (or all) skills into a portable `.zip` |
| **Import Pack** | Load a pack ZIP; imports stay quarantined until you promote |
| Feedback | Success / fail signals for ranking |

API highlights:

- `GET /api/skills/learning/summary` — “what I learned” snapshot  
- `POST /api/skills/{name}/status` — lifecycle + `force_promote`  
- `POST /api/skills/{name}/quarantine` — human quarantine toggle  
- `PUT /api/skills/{name}/body` — save edited instructions  
- `POST /api/skills/export` / `POST /api/skills/import` — pack portability

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

## How the agent uses skills

Tools (for the model, not usually typed by you):

- `skill_search` — find relevant packs  
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
