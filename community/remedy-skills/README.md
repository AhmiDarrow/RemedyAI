# remedy-skills

Public **Skills Library** catalog for [RemedyAI](https://github.com/AhmiDarrow/RemedyAI).

Official skills here are **installable workflows** (not thin stubs): each `SKILL.md` has
when-to-use guidance, concrete steps, tool lists, and a definition of done. They are
**not** auto-bundled into Remedy — users install from the Library tab and **Trust**
before scripts/agent use.

Coverage (~280 official packs; **no third-party product branding** in skill text):

| Area | Examples |
|------|----------|
| Git / release | pr-description, changelog-entry, release-checklist, rebase-onto-main |
| Security | dependency-audit, secret-scan-guidance, owasp-web-checklist, payment-webhook-flow |
| Testing / frontend | test-selection, e2e-smoke, frontend-a11y, react-performance |
| Backend / ops | api-contract-review, db-migration-safe, container-image-harden, incident-postmortem |
| **Gaming** | game-design-document, game-loop-design, boss-fight-design, playtest-protocol, loot-table-design |
| **Design** | design-brief, color-system, wireframe-flow, design-handoff, data-viz-design |
| **Content** | content-strategy, blog-post-draft, video-script, newsletter-issue, content-edit-pass |
| **Personal assistant** | daily-planning, weekly-review, email-draft, travel-itinerary, job-application-tailor |
| LLM apps | prompt-eval-harness, rag-chunking, tool-use-spec, llm-cost-guardrails |

Regenerate domain packs:

```bash
python scripts/generate_domain_skills.py
python scripts/scrub_brands.py   # keep copy brand-free
python scripts/build_catalog.py && python scripts/sign_catalog.py
```

Regenerate skill set (maintainers):

```bash
python scripts/generate_official_skills.py
python scripts/build_catalog.py
export REMEDY_SKILLS_SIGNING_KEY="..."   # base64 32-byte Ed25519 seed
python scripts/sign_catalog.py
```

## Build & sign

```bash
python scripts/build_catalog.py --skills-dir skills --output catalog.json
export REMEDY_SKILLS_SIGNING_KEY="<base64 32-byte Ed25519 seed>"
python scripts/sign_catalog.py --catalog catalog.json
```

Use `--github-urls` when publishing release asset URLs instead of `local:` dogfood URLs.

## Submit

See [CONTRIBUTING.md](./CONTRIBUTING.md) and [SECURITY.md](./SECURITY.md).

