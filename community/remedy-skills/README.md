# remedy-skills

Public **Skills Library** catalog for [RemedyAI](https://github.com/AhmiDarrow/RemedyAI).

Official skills here are **installable workflows** (not thin stubs): each `SKILL.md` has
when-to-use guidance, concrete steps, tool lists, and a definition of done. They are
**not** auto-bundled into Remedy — users install from the Library tab and **Trust**
before scripts/agent use.

Coverage themes (100+ official packs):

| Area | Examples |
|------|----------|
| Git / release | pr-description, changelog-entry, release-checklist, rebase-onto-main, git-bisect-helper |
| Security | dependency-audit, secret-scan-guidance, owasp-web-checklist, auth-session-review, webhook-verify |
| Testing | test-selection, flaky-test-triage, e2e-smoke, browser-automation-safe, contract-test-api |
| Frontend | frontend-a11y, react-performance, bundle-size-check, i18n-extract, form-validation-ux |
| Backend / API | api-contract-review, db-migration-safe, idempotent-api, queue-consumer-safe, multi-tenant-isolation |
| Ops | dockerfile-harden, ci-pipeline-review, k8s-manifest-review, incident-postmortem, runbook-write |
| LLM apps | prompt-eval-harness, rag-chunking, tool-use-spec, llm-cost-guardrails |
| Privacy / product | data-export-user, data-deletion-user, feature-flag-rollout, permissions-matrix |

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

