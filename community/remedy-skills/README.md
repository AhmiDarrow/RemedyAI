# remedy-skills

Public **Skills → Library** for [RemedyAI](https://github.com/AhmiDarrow/RemedyAI).

Official packs are **installable workflows** (not stubs): each `skills/*/SKILL.md` has when-to-use guidance, concrete steps, tools, and a definition of done. They are **not** auto-bundled into Remedy — users install from **Skills → Library** and **Trust** before use.

- **Live release:** [v1.0.0](https://github.com/AhmiDarrow/remedy-skills/releases/tag/v1.0.0) (signed catalog + per-skill zips)
- **Full list:** [SKILLS.md](./SKILLS.md) (auto-generated, always current)

<!-- BEGIN AUTO-SKILLS-LIST -->

### Skills in this library (**280**)

_Auto-updated 2026-07-26 22:56 UTC from skill packs. Full detail: [SKILLS.md](./SKILLS.md)._

| Area | Count | Sample skills |
|------|------:|---------------|
| backend | 22 | `api-client-sdk`, `api-contract-review`, `background-job-ui`, `backward-compat-api`, `cache-invalidation`, … (+17) |
| content | 35 | `blog-post-draft`, `blog-post-outline`, `case-study-write`, `changelog-user-facing`, `community-ama-prep`, … (+30) |
| data | 2 | `csv-data-cleanup`, `encoding-fix` |
| design | 45 | `a11y-design-review`, `brand-voice-guide`, `call-to-action-copy`, `cli-ux-polish`, `color-system`, … (+40) |
| docs | 5 | `adr-write`, `bug-report-template`, `feature-toggle-cleanup`, `markdown-doc-structure`, `retro-notes` |
| frontend | 7 | `bundle-size-check`, `css-specificity-debug`, `email-template-review`, `frontend-a11y`, `i18n-extract`, … (+2) |
| gaming | 34 | `boss-fight-design`, `combat-feel-tuning`, `game-accessibility`, `game-ai-behavior`, `game-audio-direction`, … (+29) |
| git | 10 | `branch-hygiene`, `changelog-entry`, `cherry-pick-commit`, `codeowners-setup`, `conventional-commits`, … (+5) |
| llm | 3 | `llm-cost-guardrails`, `rag-chunking`, `tool-use-spec` |
| ops | 14 | `backup-restore-drill`, `ci-pipeline-review`, `container-image-harden`, `container-orchestration-review`, `incident-postmortem`, … (+9) |
| other | 2 | `hello-library`, `license-compliance` |
| personal | 54 | `accountability-partnership`, `boundary-setting`, `budget-snapshot`, `caregiver-checklist`, `celebration-plan`, … (+49) |
| security | 16 | `audit-log-design`, `auth-session-review`, `cors-review`, `dependency-audit`, `enterprise-sso-notes`, … (+11) |
| testing | 13 | `acceptance-criteria`, `benchmark-micro`, `browser-automation-safe`, `contract-test-api`, `coverage-gap`, … (+8) |
| tooling | 18 | `algorithmic-complexity`, `cross-platform-paths`, `deadlock-debug`, `dev-environment-container`, `editorconfig-setup`, … (+13) |

**Total: 280** — see the [complete list](./SKILLS.md) (grouped + alphabetical).

<!-- END AUTO-SKILLS-LIST -->

## How clients load the catalog

Remedy verifies an **Ed25519** signature on `catalog.json` (release assets preferred). Installs download only from this repository’s release assets and land **quarantined** until Trust.

## Maintainers

```bash
# After adding/editing skills:
python scripts/generate_skills_list.py          # SKILLS.md + README section
python scripts/build_catalog.py                 # catalog.json (+ list regen)
# optional: local: dogfood URLs (default) or release URLs:
python scripts/build_catalog.py --github-urls --release-tag v1.0.0
export REMEDY_SKILLS_SIGNING_KEY="..."          # base64 32-byte Ed25519 seed
python scripts/sign_catalog.py
```

`build_catalog.py` also regenerates the skills list unless you pass `--skip-docs`.

CI:

- **update-skills-list** — on push to `skills/**`, regenerates and commits `SKILLS.md` + README section
- **validate-skill-submission** — PR checks for skill packs
- **catalog-sync** — on release: rebuild catalog, list docs, sign

## Submit

See [CONTRIBUTING.md](./CONTRIBUTING.md) and [SECURITY.md](./SECURITY.md).
