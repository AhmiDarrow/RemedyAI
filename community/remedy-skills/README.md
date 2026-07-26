# Remedy Skills

**Extra playbooks for your local AI partner — install only what you need.**

This is the public **Skills Library** for [Remedy](https://github.com/AhmiDarrow/RemedyAI).
Skills are portable instruction packs the agent can load on demand — same idea as
the ones that ship with the app, except **you** pick what lands on your machine.
Nothing here runs, and nothing runs scripts, until you **Trust** it.

```text
You  →  Skills → Library  →  Install (quarantine)  →  Trust  →  ready to use
```

- **[Latest catalog release](https://github.com/AhmiDarrow/remedy-skills/releases/latest)** — signed `catalog.json` + skill packs  
- **[Full skill index](./SKILLS.md)** — every pack, grouped and alphabetical (kept current automatically)

---

## Continuity, not clutter

Remedy already ships a small set of **bundled** skills for everyday partner work.
This repo is the wider shelf: engineering, gaming, design, content, ops, and
personal workflows — written as real procedures, not empty titles.

The library stays **local-first**, same as Remedy itself:

- **You choose** what to install — nothing bulk-dumps into the agent  
- **Quarantine first** — packs land inert; scripts stay blocked until Trust  
- **Signed catalog** — downloads limited to this project’s GitHub releases  
- **Installs live on your PC** under `~/.remedy/skills/`

Each skill says when to use it, the steps, tool hints, and what “done” looks like.

---

<!-- BEGIN AUTO-SKILLS-LIST -->

## What’s in the library (281 skills)

Snapshot of packs on this branch as of 2026-07-26. Descriptions and the full alphabetical list live in **[SKILLS.md](./SKILLS.md)**.

| Area | # | A few examples |
|------|--:|----------------|
| Backend & APIs | 22 | `api-client-sdk`, `api-contract-review`, `background-job-ui`, `backward-compat-api` · +18 more |
| Content & writing | 35 | `blog-post-draft`, `blog-post-outline`, `case-study-write`, `changelog-user-facing` · +31 more |
| Data | 2 | `csv-data-cleanup`, `encoding-fix` |
| Design & UX | 45 | `a11y-design-review`, `brand-voice-guide`, `call-to-action-copy`, `cli-ux-polish` · +41 more |
| Docs & process | 5 | `adr-write`, `bug-report-template`, `feature-toggle-cleanup`, `markdown-doc-structure` · +1 more |
| Frontend | 7 | `bundle-size-check`, `css-specificity-debug`, `email-template-review`, `frontend-a11y` · +3 more |
| Gaming | 35 | `boss-fight-design`, `combat-feel-tuning`, `game-accessibility`, `game-ai-behavior` · +31 more |
| Git & release | 10 | `branch-hygiene`, `changelog-entry`, `cherry-pick-commit`, `codeowners-setup` · +6 more |
| LLM & agents | 3 | `llm-cost-guardrails`, `rag-chunking`, `tool-use-spec` |
| Ops & reliability | 14 | `backup-restore-drill`, `ci-pipeline-review`, `container-image-harden`, `container-orchestration-review` · +10 more |
| Other | 2 | `hello-library`, `license-compliance` |
| Personal assistant | 54 | `accountability-partnership`, `boundary-setting`, `budget-snapshot`, `caregiver-checklist` · +50 more |
| Security | 16 | `audit-log-design`, `auth-session-review`, `cors-review`, `dependency-audit` · +12 more |
| Testing | 13 | `acceptance-criteria`, `benchmark-micro`, `browser-automation-safe`, `contract-test-api` · +9 more |
| Tooling & languages | 18 | `algorithmic-complexity`, `cross-platform-paths`, `deadlock-debug`, `dev-environment-container` · +14 more |

**281 total** — skim the [full list](./SKILLS.md), or open **Skills → Library** in Remedy Desktop and install only what you need.

<!-- END AUTO-SKILLS-LIST -->

---

## Try it

About a minute if you already have Remedy Desktop with Skills Library support
(see the main [Remedy repo](https://github.com/AhmiDarrow/RemedyAI)):

1. Open **Skills** → **Library**
2. Search or browse, then **Install** (lands in quarantine)
3. Skim the pack — when you’re happy, **Trust**
4. Ask Remedy to use that workflow in chat

Prefer reading first? Start with the [full list](./SKILLS.md) or open any
`skills/<name>/SKILL.md`.

---

## What’s in a skill

A skill is a folder with a `SKILL.md` (short YAML frontmatter + the playbook),
and sometimes a helper script. The agent sees the short description first, then
loads the full body when the task matches — so you don’t pay for a wall of
instructions on every turn.

Packs here are meant to be **usable**: concrete steps, tool hints, and safety
notes (no secrets; confirm before anything destructive).

---

## Security — you hold Trust

Same posture as importing a ZIP in Remedy: power for the owner, not a free pass
for random packs.

| Layer | What we ship |
|-------|----------------|
| **Signed catalog** | Ed25519 signature checked before the client trusts the list |
| **Release assets only** | Installs come from this repo’s GitHub releases (no random hosts) |
| **Checksums** | SHA-256 of each zip required |
| **Quarantine by default** | Scripts blocked until you **Trust** in Remedy |
| **Zip safety** | Zip-slip protected extraction |

Details: [SECURITY.md](./SECURITY.md).

---

## Contribute

Ideas and PRs welcome when they stay practical and safe.

1. Read [CONTRIBUTING.md](./CONTRIBUTING.md)
2. Add `skills/<name>/SKILL.md` (scripts only when they actually help)
3. Open a PR — validation CI should pass

Please don’t ship secrets, or instructions that hinge on another product’s brand
as the only path — keep guidance portable so anyone can follow it.

---

## Maintainers

After you add or edit skills:

```bash
python scripts/generate_skills_list.py   # SKILLS.md + README library section
python scripts/build_catalog.py          # catalog.json (also refreshes the list)

# Public release catalog (download URLs → this repo’s release assets):
python scripts/build_catalog.py --github-urls --release-tag v1.0.0
export REMEDY_SKILLS_SIGNING_KEY="..."   # base64 Ed25519 seed
python scripts/sign_catalog.py
```

CI keeps the docs honest:

| Workflow | Role |
|----------|------|
| **update-skills-list** | On skill changes → refresh `SKILLS.md` + README section |
| **validate-skill-submission** | PR checks for new packs |
| **catalog-sync** | On release → rebuild catalog, list, and signature |

---

## License

Skill packs and catalog scripts in this repository follow the project’s
source-available terms unless a pack says otherwise. Remedy the product is
documented in the [main repo](https://github.com/AhmiDarrow/RemedyAI).

---

*Part of the Remedy local partner stack — continuity on your PC, library on your terms.*
